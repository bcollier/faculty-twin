"""app/llm.py and app/embed.py beyond the happy paths: overrides, parsers, caps, errors, and model lists.

httpx is mocked throughout (no network).
"""

from __future__ import annotations

import json

import httpx
import numpy as np
import pytest

from app import embed, llm, settings_store


def mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _fresh_openrouter_cache():
    llm._openrouter_cache = None
    yield
    llm._openrouter_cache = None


# ---------------------------------------------------------------- per-request override

def test_model_override_is_scoped_and_validated():
    assert llm.current_override() is None
    with llm.model_override("openai", "gpt-6-luna"):
        assert llm.current_override() == ("openai", "gpt-6-luna")
        assert settings_store.llm_choice() == ("openai", "gpt-6-luna")
        with llm.model_override("anthropic", "claude-opus-5-5"):
            assert llm.current_override() == ("anthropic", "claude-opus-5-5")
        assert llm.current_override() == ("openai", "gpt-6-luna")
    assert llm.current_override() is None
    with pytest.raises(llm.LLMError, match="Unknown provider"):
        with llm.model_override("mystery", "m"):
            pass
    with pytest.raises(llm.LLMError, match="model id"):
        with llm.model_override("openai", ""):
            pass


def test_key_configured(monkeypatch):
    assert not llm.key_configured("anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert llm.key_configured("anthropic")


def test_openrouter_referer_header_when_site_url_set(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    assert "HTTP-Referer" not in llm.build_openrouter("m", "s", "u", 5).headers
    monkeypatch.setenv("PUBLIC_SITE_URL", "https://faculty-twin.example")
    assert llm.build_openrouter("m", "s", "u", 5).headers["HTTP-Referer"] == "https://faculty-twin.example"


# ---------------------------------------------------------------- parsers

def test_parse_anthropic_joins_text_blocks_and_rejects_empty():
    data = {"content": [{"type": "text", "text": " {\"a\""}, {"type": "tool_use"}, {"type": "text", "text": ":1} "}]}
    assert llm.parse_anthropic(data) == '{"a":1}'
    with pytest.raises(llm.LLMError, match="no text"):
        llm.parse_anthropic({"content": [{"type": "text", "text": "   "}]})
    with pytest.raises(llm.LLMError, match="no text"):
        llm.parse_anthropic({})


@pytest.mark.parametrize("data", [{}, {"choices": []}, {"choices": [{}]}, {"choices": [None]}, {"choices": "x"}])
def test_parse_chat_malformed(data):
    with pytest.raises(llm.LLMError, match="no message"):
        llm.parse_chat(data)


def test_parse_chat_filter_and_empty():
    with pytest.raises(llm.LLMError, match="declined"):
        llm.parse_chat({"choices": [{"message": {"content": "x"}, "finish_reason": "content_filter"}]})
    with pytest.raises(llm.LLMError, match="no text"):
        llm.parse_chat({"choices": [{"message": {"content": None}}]})
    assert llm.parse_chat({"choices": [{"message": {"content": "  {} \n"}}]}) == "{}"


# ---------------------------------------------------------------- complete_json

def test_complete_json_uses_the_active_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "oa")
    settings_store.put({"provider": "openai", "model": "gpt-6-astra"})
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["model"] = json.loads(request.content)["model"]
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    assert llm.complete_json("s", "u", 10, client=mock_client(handler)) == "{}"
    assert seen == {"url": llm.OPENAI_URL, "model": "gpt-6-astra"}


def test_complete_json_unknown_provider():
    with pytest.raises(llm.LLMError, match="Unknown provider"):
        llm.complete_json("s", "u", 10, provider="mystery", model="m")


def test_complete_json_cap_reached_makes_no_call(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("DAILY_LLM_CALL_CAP", "0")
    calls = []
    client = mock_client(lambda r: calls.append(r) or httpx.Response(200, json={}))
    with pytest.raises(llm.LLMError, match="cap"):
        llm.complete_json("s", "u", 10, provider="anthropic", model="claude-sonnet-5-5", client=client)
    assert calls == []


def test_complete_json_daily_cap_counts_calls(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("DAILY_LLM_CALL_CAP", "2")
    ok = {"content": [{"type": "text", "text": "{}"}]}
    client = mock_client(lambda r: httpx.Response(200, json=ok))
    for _ in range(2):
        llm.complete_json("s", "u", 10, provider="anthropic", model="claude-haiku-4-5", client=client)
    with pytest.raises(llm.LLMError, match="cap"):
        llm.complete_json("s", "u", 10, provider="anthropic", model="claude-haiku-4-5", client=client)


def test_complete_json_transport_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

    def boom(request):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(llm.LLMError, match="anthropic request failed: ReadTimeout"):
        llm.complete_json("s", "u", 10, provider="anthropic", model="claude-sonnet-5-5", client=mock_client(boom))


def test_complete_json_error_body_never_includes_the_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-secret-key")
    client = mock_client(lambda r: httpx.Response(402, text="insufficient credits"))
    with pytest.raises(llm.LLMError) as err:
        llm.complete_json("s", "u", 10, provider="openrouter", model="m", client=client)
    assert "402" in str(err.value) and "or-secret-key" not in str(err.value)


# ---------------------------------------------------------------- model lists

def test_list_models_unknown_provider():
    with pytest.raises(llm.LLMError, match="Unknown provider"):
        llm.list_models("mystery", client=mock_client(lambda r: httpx.Response(200)))


def test_openrouter_list_is_cached_for_ten_minutes():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"data": [{"id": "a/m"}, {"name": "no id"}]})

    first = llm.list_models("openrouter", client=mock_client(handler))
    second = llm.list_models("openrouter", client=mock_client(handler))
    assert first["models"] == [{"id": "a/m", "name": "a/m", "context_length": None, "pricing": None}]
    assert second["models"] == first["models"] and len(calls) == 1


def test_openrouter_list_errors():
    with pytest.raises(llm.LLMError, match="503"):
        llm.list_models("openrouter", client=mock_client(lambda r: httpx.Response(503)))

    def boom(request):
        raise httpx.ConnectError("down")

    with pytest.raises(llm.LLMError, match="ConnectError"):
        llm.list_models("openrouter", client=mock_client(boom))


def test_anthropic_live_list_merges_after_curated(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant")
    seen = {}

    def handler(request):
        seen["key"] = request.headers["x-api-key"]
        seen["limit"] = request.url.params["limit"]
        return httpx.Response(200, json={"data": [
            {"id": "claude-sonnet-5-5", "display_name": "dup"},
            {"id": "claude-zeta-9", "display_name": "Claude Zeta 9"},
            {"id": "claude-alpha-9"},
        ]})

    out = llm.list_models("anthropic", client=mock_client(handler))
    assert out["source"] == "curated+live"
    ids = [m["id"] for m in out["models"]]
    curated = [m["id"] for m in llm.CURATED["anthropic"]]
    assert ids[: len(curated)] == curated
    assert ids[len(curated):] == ["claude-alpha-9", "claude-zeta-9"]
    assert out["models"][-1]["name"] == "Claude Zeta 9"
    assert seen == {"key": "ant", "limit": "100"}


def test_openai_live_list_keeps_chat_models_only(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "oa")
    data = {"data": [{"id": "gpt-7"}, {"id": "o9-mini"}, {"id": "whisper-1"}, {"id": "text-embedding-3"},
                     {"id": "gpt-6.1-sol"}]}
    out = llm.list_models("openai", client=mock_client(lambda r: httpx.Response(200, json=data)))
    extra = [m["id"] for m in out["models"]][len(llm.CURATED["openai"]):]
    assert extra == ["gpt-7", "o9-mini"]


@pytest.mark.parametrize("response", [
    httpx.Response(401, json={"data": []}),
    httpx.Response(200, text="not json"),
    httpx.Response(200, json={"data": [{"no": "id"}]}),
])
def test_live_list_failures_fall_back_to_curated(monkeypatch, response):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant")
    out = llm.list_models("anthropic", client=mock_client(lambda r: response))
    assert out == {"provider": "anthropic", "models": list(llm.CURATED["anthropic"]), "source": "curated"}


def test_live_list_transport_error_falls_back(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "oa")

    def boom(request):
        raise httpx.ConnectError("down")

    out = llm.list_models("openai", client=mock_client(boom))
    assert out["source"] == "curated"


# ---------------------------------------------------------------- embeddings

def test_embed_needs_key_and_model_name(monkeypatch):
    with pytest.raises(embed.EmbeddingError, match="VOYAGE_API_KEY"):
        embed.build_request("q")
    assert embed.model_name() == "voyage-3.5"
    monkeypatch.setenv("VOYAGE_MODEL", "voyage-4")
    assert embed.model_name() == "voyage-4"


@pytest.mark.parametrize("data", [{}, {"data": []}, {"data": [{}]}, {"data": None}])
def test_embed_parse_rejects_missing_vectors(data):
    with pytest.raises(embed.EmbeddingError, match="no embedding"):
        embed.parse_response(data)


def test_embed_cap_reached_makes_no_call(monkeypatch):
    monkeypatch.setenv("VOYAGE_API_KEY", "v")
    monkeypatch.setenv("DAILY_EMBED_CAP", "0")
    calls = []
    client = mock_client(lambda r: calls.append(r) or httpx.Response(200, json={}))
    with pytest.raises(embed.EmbeddingCapReached):
        embed.embed_question("q", client=client)
    assert calls == []


def test_embed_http_and_transport_errors(monkeypatch):
    monkeypatch.setenv("VOYAGE_API_KEY", "v")
    with pytest.raises(embed.EmbeddingError, match="429"):
        embed.embed_question("q", client=mock_client(lambda r: httpx.Response(429, text="rate limited")))

    def boom(request):
        raise httpx.ConnectTimeout("slow")

    with pytest.raises(embed.EmbeddingError, match="ConnectTimeout"):
        embed.embed_question("q", client=mock_client(boom))


def test_embed_returns_float32(monkeypatch):
    monkeypatch.setenv("VOYAGE_API_KEY", "v")
    client = mock_client(lambda r: httpx.Response(200, json={"data": [{"embedding": [1, 2]}], "usage": {"total_tokens": 3}}))
    vec = embed.embed_question("q", client=client)
    assert vec.dtype == np.float32 and vec.tolist() == [1.0, 2.0]
