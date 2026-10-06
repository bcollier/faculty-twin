"""Provider request building and parsing, with httpx mocked (no network)."""

from __future__ import annotations

import json

import httpx
import numpy as np
import pytest

from app import embed, llm, settings_store


def mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_anthropic_request(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-test")
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": '{"segments": []}'}],
                "stop_reason": "end_turn",
            },
        )

    out = llm.complete_json("SYS", "USER", 500, provider="anthropic", model="claude-sonnet-5-5", client=mock_client(handler))
    assert out == '{"segments": []}'
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "ant-test"
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["headers"]["anthropic-beta"] == "server-side-fallback-2026-07-01"
    body = seen["body"]
    assert body["model"] == "claude-sonnet-5-5" and body["max_tokens"] == 500
    assert body["system"] == "SYS"
    assert body["messages"] == [{"role": "user", "content": "USER"}]
    assert body["output_config"] == {"effort": "low"}
    assert body["fallbacks"] == "default"
    assert "thinking" not in body and "temperature" not in body


def test_anthropic_haiku_has_no_effort(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    req = llm.build_anthropic("claude-haiku-4-5", "s", "u", 10)
    assert "output_config" not in req.body and "fallbacks" not in req.body


def test_anthropic_refusal(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    client = mock_client(lambda r: httpx.Response(200, json={"content": [], "stop_reason": "refusal"}))
    with pytest.raises(llm.LLMError, match="declined"):
        llm.complete_json("s", "u", 10, provider="anthropic", model="claude-sonnet-5-5", client=client)


def test_openai_request(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "oa-test")
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]})

    assert llm.complete_json("S", "U", 300, provider="openai", model="gpt-6.1-sol", client=mock_client(handler)) == "{}"
    assert seen["url"] == "https://api.openai.com/v1/chat/completions"
    assert seen["auth"] == "Bearer oa-test"
    assert seen["body"] == {
        "model": "gpt-6.1-sol",
        "messages": [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}],
        "response_format": {"type": "json_object"},
        "max_completion_tokens": 300,
    }


def test_openrouter_request(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{\"a\":1}"}}]})

    out = llm.complete_json("S", "U", 300, provider="openrouter", model="anthropic/claude-sonnet-5.5",
                            client=mock_client(handler))
    assert out == '{"a":1}'
    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert seen["headers"]["authorization"] == "Bearer or-test"
    assert seen["headers"]["x-title"] == "Faculty Twin"
    assert seen["body"]["max_tokens"] == 300
    assert seen["body"]["response_format"] == {"type": "json_object"}


def test_http_error_and_missing_key(monkeypatch):
    with pytest.raises(llm.LLMError, match="OPENAI_API_KEY"):
        llm.complete_json("s", "u", 10, provider="openai", model="x")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    client = mock_client(lambda r: httpx.Response(429, json={"error": "slow down"}))
    with pytest.raises(llm.LLMError, match="429"):
        llm.complete_json("s", "u", 10, provider="openai", model="x", client=client)


def test_active_provider_from_settings_then_env(monkeypatch):
    assert settings_store.llm_choice() == ("anthropic", "claude-sonnet-5-5")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-6-luna")
    assert settings_store.llm_choice() == ("openai", "gpt-6-luna")
    settings_store.put({"provider": "openrouter", "model": "openai/gpt-6-luna"})
    assert settings_store.llm_choice() == ("openrouter", "openai/gpt-6-luna")


def test_openrouter_model_list():
    llm._openrouter_cache = None
    data = {"data": [{"id": "z/model", "name": "Z", "context_length": 1000}, {"id": "a/model", "name": "A"}]}
    out = llm.list_models("openrouter", client=mock_client(lambda r: httpx.Response(200, json=data)))
    assert [m["id"] for m in out["models"]] == ["a/model", "z/model"]
    assert out["source"] == "live"
    llm._openrouter_cache = None


def test_curated_list_without_key():
    out = llm.list_models("anthropic", client=mock_client(lambda r: httpx.Response(500)))
    assert out["source"] == "curated" and out["models"][0]["id"] == "claude-sonnet-5-5"


def test_voyage_request(monkeypatch):
    monkeypatch.setenv("VOYAGE_API_KEY", "voy-test")
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2, 0.3], "index": 0}]})

    vec = embed.embed_question("what is k-means", client=mock_client(handler))
    assert vec.dtype == np.float32 and vec.shape == (3,)
    assert seen["url"] == "https://api.voyageai.com/v1/embeddings"
    assert seen["auth"] == "Bearer voy-test"
    assert seen["body"] == {"input": ["what is k-means"], "model": "voyage-3.5", "input_type": "query"}
