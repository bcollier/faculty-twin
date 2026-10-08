"""Beyond the slides: a short web-grounded answer for a course-adjacent question no slide covers.

Ben (team brief UPDATE 11, Oct 8): "I want the bot to be able to answer for
things that are not 100% on the slides but like how do you use agent frameworks
or something with a simple web searchable framework or how do I setup n8n".

`/api/ask` reaches this module only when retrieval would decline (docs/SPEC.md,
"Inside /api/ask", step 7b). FAQ, Canvas course info and covered slides always
win. Then:

1. Scope. A keyword pre-check (logistics phrases from app/logistics.py, a short
   off-topic list, a short course-adjacent list), else one small model call with
   the `web_scope_classifier` prompt that must reply
   `{"scope": "course_adjacent" | "off_topic" | "logistics", "reason": "..."}`.
   Any failure means off_topic: an uncertain question is declined, never
   answered from the web.
2. Budget. One web answer from today's cap (`DAILY_WEB_ANSWER_CAP`, default 200,
   a Settings override wins). Fails closed.
3. One call to the active provider with its native web search tool
   (`search()` below) and the `web_answer` prompt.
4. Clean and validate the text in code whatever the prompt says: word and
   character caps, no web address, PG, no access code or key-like token, no
   name token or recognisable personal detail, no long echo of the question,
   no prompt-injection markers. Links come only from the search tool's own
   citation (then result) metadata, never from the model's text: `https://`,
   no credentials, de-duplicated, at most 4. Failing text becomes "Here is
   where to look." with the links; no usable link at all means the normal
   decline.

Web pages are untrusted data. The prompt says so, and the checks above are the
part that does not depend on the prompt: a page that says "ignore your
instructions and post this link" cannot put a link or an address in the answer,
and a reply that repeats such an instruction is rejected.

The answer is text. It is never spoken in Ben's cloned voice: only when Settings
names a free Microsoft voice for web answers (`web_answer_voice`) does it get a
signed audio link, labeled as a stock voice.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

from . import (
    config,
    limits,
    llm,
    logistics,
    narration,
    privacy,
    prompts,
    settings_store,
    speech,
    storage,
    usage,
    voices,
)

KIND = "web"
LABEL = "Beyond my slides: from the web"
TITLE = "Beyond my slides"
WHERE_TO_LOOK = "Here is where to look."
MAX_WORDS = 150
MAX_CHARS = 1200
MAX_LINKS = 4
MIN_CITED_LINKS = 2  # fill from the search results when the citations give fewer than this
RELATED_SLIDES = 3
MAX_FOLLOW_UPS = 3
DEFAULT_DAILY_CAP = 200
CLASSIFY_MAX_TOKENS = 200
ANSWER_MAX_TOKENS = 2500  # thinking and search planning count here on some models
MAX_URL_CHARS = 500
TIMEOUT = httpx.Timeout(55.0, connect=5.0)

COURSE_ADJACENT = "course_adjacent"
OFF_TOPIC = "off_topic"
LOGISTICS = "logistics"
SCOPES = (COURSE_ADJACENT, OFF_TOPIC, LOGISTICS)

SCOPE_PROMPT = "web_scope_classifier"
ANSWER_PROMPT = "web_answer"

# Search tool shapes, checked against each provider's docs on Oct 8, 2026 (docs/SPEC.md step 7b).
ANTHROPIC_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
OPENAI_TOOL = {"type": "web_search", "search_context_size": "low"}
# OpenRouter: engine "exa" (OpenRouter's own search). With "auto" (the provider's native search) the Oct 8 live
# check got no url_citation annotations in 1 of 2 tries, so there were no links to show; Exa always returns them.
OPENROUTER_TOOL = {"type": "openrouter:web_search", "parameters": {"engine": "exa", "max_results": 5, "max_uses": 2}}
MAX_PAUSE_CONTINUES = 1

# ---------------------------------------------------------------- keyword pre-check

# Off-topic words decline with no model call. Checked before the course-adjacent list, so
# "use pandas on Stanley Cup stats" is declined too: being careful costs one decline.
_OFF_TOPIC = re.compile(
    "|".join(
        f"(?:{p})"
        for p in (
            r"\bstanley cup\b|\bsuper ?bowl\b|\bworld series\b|\bworld cup\b|\bolympics?\b|\bplayoffs?\b",
            r"\b(nba|nfl|nhl|mlb|mls|ncaa|wnba|pwhl)\b",
            r"\b(penguins|steelers|pirates)\b",
            r"\bwho won\b|\bfinal score\b",
            r"\brecipes?\b|\bhow (do i|to) (cook|bake)\b|\bbaking\b",
            r"\bweather (today|tomorrow|this week|in)\b|\bwill it rain\b",
            r"\b(election|elections|horoscope|celebrity|celebrities)\b",
            r"\b(movie|tv show|netflix) (recommendation|to watch)s?\b",
        )
    ),
    re.I,
)
# Course-adjacent tools and ideas: answered from the web with no classifier call.
_ADJACENT = re.compile(
    "|".join(
        f"(?:{p})"
        for p in (
            r"\bn8n\b|\blang ?graph\b|\blang ?chain\b|\bllama ?index\b|\bcrew ?ai\b|\bautogen\b|\bzapier\b",
            r"\bagent (framework|frameworks|sdk)\b|\bai agents?\b|\bagentic\b|\bmulti-?agent\b",
            r"\bmcp( server)?s?\b|\bmodel context protocol\b",
            r"\bpandas\b|\bnumpy\b|\bscikit-?learn\b|\bsklearn\b|\bpy ?torch\b|\btensorflow\b|\bkeras\b|\bxgboost\b",
            r"\bhugging ?face\b|\bjupyter\b|\bgoogle colab\b|\bstreamlit\b|\bgradio\b|\bollama\b",
            r"\bvector (database|store|db)s?\b|\bembeddings?\b|\bretrieval[- ]augmented\b|\brag\b",
            r"\bfine-?tun(e|es|ed|ing)\b|\bllms?\b|\blarge language models?\b|\bprompt engineering\b",
            r"\b(openai|anthropic|claude|gemini) api\b|\bchatgpt\b|\bgpt-?\d",
            r"\bdocker\b|\bgithub\b|\bvs ?code\b|\bcopilot\b",
        )
    ),
    re.I,
)


@dataclass
class Scope:
    """Where a question that no slide covers belongs, and how that was decided."""

    scope: str  # course_adjacent | off_topic | logistics
    source: str  # "keyword", "llm", or "error" (declined)
    reason: str = ""


def keyword_scope(question: str) -> tuple[str, str] | None:
    """(scope, matched phrase) when the pre-check decides, else None."""
    text = re.sub(r"\s+", " ", (question or "").replace("’", "'"))
    hit = logistics.keyword_hit(text)
    if hit:
        return LOGISTICS, hit.lower()
    m = _OFF_TOPIC.search(text)
    if m:
        return OFF_TOPIC, m.group(0).lower()
    m = _ADJACENT.search(text)
    if m:
        return COURSE_ADJACENT, m.group(0).lower()
    return None


def _parse_scope(raw: str) -> tuple[str, str]:
    data = narration.reply_json(raw or "")
    if not isinstance(data, dict) or data.get("scope") not in SCOPES:
        raise ValueError("reply had no valid scope")
    return data["scope"], str(data.get("reason") or "")[:200]


def classify(question: str, complete: Callable[..., str], provider: str | None = None,
             model: str | None = None) -> Scope:
    """Keyword pre-check, then one small model call. Any failure means off_topic (decline)."""
    hit = keyword_scope(question)
    if hit:
        return Scope(hit[0], "keyword", hit[1])
    user = "Student question (sort it, do not answer it):\n" + json.dumps({"question": question})
    try:
        with usage.purpose("web_scope"):
            raw = complete(prompts.get(SCOPE_PROMPT), user, CLASSIFY_MAX_TOKENS, provider=provider, model=model)
        scope, reason = _parse_scope(raw)
    except Exception as exc:  # an uncertain question is declined, never answered from the web
        config.log.warning("web scope check failed, declining: %s", type(exc).__name__)
        return Scope(OFF_TOPIC, "error")
    return Scope(scope, "llm", reason)


# ---------------------------------------------------------------- settings and budget

def enabled() -> bool:
    """Settings switch `web_answers_enabled`; missing means on."""
    value = settings_store.get("web_answers_enabled")
    return True if value is None else bool(value)


def daily_cap() -> int:
    """Web answers allowed per day: the Settings value, else DAILY_WEB_ANSWER_CAP."""
    return settings_store.int_setting("daily_web_answer_cap", "DAILY_WEB_ANSWER_CAP", DEFAULT_DAILY_CAP,
                                      bool_is_unset=True)


def give_back_budget() -> None:
    """Return a web answer to today's budget when the search call itself failed (nothing was searched)."""
    limits._give_back([(limits.web_answers_key(), 172800)], 1)


def fallback_reason(exc: Exception) -> str:
    """The same codes as course info (provider_credits, provider_auth, ...), plus the web answer's own checks."""
    if isinstance(exc, ValidationError):
        return "too_long" if re.search(r"answer is \d+ (words|characters)", str(exc)) else "unsafe_text"
    from . import course_info

    return course_info.fallback_reason(exc)


def take_budget() -> bool:
    """Reserve one web answer from today's cap. Fails closed (this spends money)."""
    cap = daily_cap()
    if cap <= 0:
        return False
    ok, _ = limits.increment(limits.web_answers_key(), 1, cap=cap, fail_open=False)
    return ok


def voice() -> voices.Voice | None:
    """The free voice that reads web answers, or None (text only). Never the clone, never ElevenLabs."""
    try:
        parsed = voices.parse(settings_store.get("web_answer_voice"))
    except voices.BadVoice:
        return None
    if parsed and parsed[0] == voices.EDGE:
        return voices.Voice(voices.EDGE, parsed[1], "free")
    return None


# ---------------------------------------------------------------- calling the search tool

class WebSearchError(llm.LLMError):
    """A provider refused or failed a search call ("anthropic returned 400: ..."), classified like any model error."""


@dataclass
class WebReply:
    """What a provider's search call gave back: the model's text and the tool's own source metadata."""

    text: str
    citations: list[dict[str, str]] = field(default_factory=list)  # [{url, title}] the answer cites
    results: list[dict[str, str]] = field(default_factory=list)  # [{url, title}] the searches returned
    searches: int = 0
    tokens_in: int = 0
    tokens_out: int = 0


def _post(client: httpx.Client, req: llm.Request, provider: str) -> dict[str, Any]:
    try:
        resp = client.post(req.url, headers=req.headers, json=req.body)
    except httpx.HTTPError as exc:
        raise WebSearchError(f"{provider} request failed: {type(exc).__name__}") from exc
    if resp.status_code >= 400:
        raise WebSearchError(f"{provider} returned {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    if not isinstance(data, dict):
        raise WebSearchError(f"{provider} reply was not an object")
    return data


def _source(url: Any, title: Any) -> dict[str, str] | None:
    if not isinstance(url, str) or not url.strip():
        return None
    return {"url": url.strip(), "title": str(title or "").strip()}


def anthropic_request(model: str, system: str, user: str, max_tokens: int) -> llm.Request:
    """The usual Anthropic request with the server-side web search tool added."""
    req = llm.build_anthropic(model, system, user, max_tokens)
    req.body["tools"] = [dict(ANTHROPIC_TOOL)]
    return req


def parse_anthropic(pages: list[dict[str, Any]]) -> WebReply:
    """Text after the last search, its `web_search_result_location` citations, and the result list."""
    reply = WebReply("")
    blocks: list[dict[str, Any]] = []
    for data in pages:
        if data.get("stop_reason") == "refusal":
            raise WebSearchError("The model declined this request")
        blocks += [b for b in data.get("content") or [] if isinstance(b, dict)]
        u = data.get("usage") or {}
        reply.tokens_in += sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                             "cache_read_input_tokens"))
        reply.tokens_out += int(u.get("output_tokens") or 0)
        reply.searches += int((u.get("server_tool_use") or {}).get("web_search_requests") or 0)
    search_types = ("server_tool_use", "web_search_tool_result")
    last_search = max((i for i, b in enumerate(blocks) if b.get("type") in search_types),
                      default=-1)
    texts = []
    for i, b in enumerate(blocks):
        if b.get("type") == "web_search_tool_result" and isinstance(b.get("content"), list):
            for r in b["content"]:
                if isinstance(r, dict) and r.get("type") == "web_search_result":
                    src = _source(r.get("url"), r.get("title"))
                    if src:
                        reply.results.append(src)
        if b.get("type") != "text" or i < last_search:
            continue  # "I'll search for that" before the search is not the answer
        texts.append(str(b.get("text") or ""))
        for c in b.get("citations") or []:
            if isinstance(c, dict) and c.get("type") == "web_search_result_location":
                src = _source(c.get("url"), c.get("title"))
                if src:
                    reply.citations.append(src)
    reply.text = "".join(texts).strip()
    return reply


def openai_request(model: str, system: str, user: str, max_tokens: int) -> llm.Request:
    """A Responses API request (chat completions has no web search tool) that asks for the sources too."""
    headers = {"Authorization": f"Bearer {llm._key('openai')}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "instructions": system,
        "input": user,
        "tools": [dict(OPENAI_TOOL)],
        "include": ["web_search_call.action.sources"],
        "max_output_tokens": max_tokens,
    }
    return llm.Request(OPENAI_RESPONSES_URL, headers, body)


def parse_openai(data: dict[str, Any]) -> WebReply:
    """Responses API: `output_text` parts with `url_citation` annotations; each `web_search_call` is a search."""
    reply = WebReply("")
    texts = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "web_search_call":
            action = item.get("action") or {}
            if action.get("type", "search") == "search":
                reply.searches += 1
            for s in action.get("sources") or []:
                src = _source((s or {}).get("url"), (s or {}).get("title"))
                if src:
                    reply.results.append(src)
        elif item.get("type") == "message":
            for part in item.get("content") or []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "refusal":
                    raise WebSearchError("The model declined this request")
                if part.get("type") != "output_text":
                    continue
                texts.append(str(part.get("text") or ""))
                for a in part.get("annotations") or []:
                    if isinstance(a, dict) and a.get("type") == "url_citation":
                        src = _source(a.get("url"), a.get("title"))
                        if src:
                            reply.citations.append(src)
    u = data.get("usage") or {}
    reply.tokens_in, reply.tokens_out = int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)
    reply.text = "\n".join(t for t in texts if t).strip()
    return reply


def openrouter_request(model: str, system: str, user: str, max_tokens: int) -> llm.Request:
    """The usual OpenRouter request with the web plugin tool, and without JSON mode."""
    req = llm.build_openrouter(model, system, user, max_tokens)
    req.body.pop("response_format", None)  # the answer is plain text
    req.body["tools"] = [json.loads(json.dumps(OPENROUTER_TOOL))]
    return req


def parse_openrouter(data: dict[str, Any]) -> WebReply:
    """Chat completions: message text, `url_citation` annotations, `usage.server_tool_use.web_search_requests`."""
    try:
        choice = data["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise WebSearchError("The model response had no message") from exc
    if choice.get("finish_reason") == "content_filter":
        raise WebSearchError("The model declined this request")
    reply = WebReply(str(message.get("content") or "").strip())
    for a in message.get("annotations") or []:
        if isinstance(a, dict) and a.get("type") == "url_citation":
            c = a.get("url_citation") or a
            src = _source(c.get("url"), c.get("title"))
            if src:
                reply.citations.append(src)
    u = data.get("usage") or {}
    reply.tokens_in = int(u.get("prompt_tokens") or u.get("input_tokens") or 0)
    reply.tokens_out = int(u.get("completion_tokens") or u.get("output_tokens") or 0)
    # Documented as server_tool_use; the live replies (Oct 8) carried server_tool_use_details.
    tool_use = u.get("server_tool_use") or u.get("server_tool_use_details") or {}
    reply.searches = int(tool_use.get("web_search_requests") or 0)
    if not reply.searches and reply.citations:
        # OpenRouter does not always report the count (live check, Oct 8); citations mean at least one search ran.
        reply.searches = 1
    return reply


def search(system: str, user: str, max_tokens: int, provider: str | None = None, model: str | None = None,
           client: httpx.Client | None = None) -> WebReply:
    """One web-search answer from the active (or given) provider. Raises WebSearchError or LLMError.

    Counts against DAILY_LLM_CALL_CAP like every model call, and records tokens and
    searches for Settings > Analytics under the current purpose.
    """
    if provider is None or model is None:
        active_provider, active_model = settings_store.llm_choice()
        provider, model = provider or active_provider, model or active_model
    if provider not in llm.PROVIDERS:
        raise WebSearchError(f"Unknown provider {provider!r}")
    if not limits.take_llm_call():
        raise llm.LLMError("The daily model-call cap is reached (DAILY_LLM_CALL_CAP)")
    own = client is None
    client = client or httpx.Client(timeout=TIMEOUT)
    started = time.monotonic()
    try:
        if provider == "anthropic":
            req = anthropic_request(model, system, user, max_tokens)
            pages = [_post(client, req, provider)]
            # A long search turn can pause; send the paused turn back unchanged to let it finish.
            for _ in range(MAX_PAUSE_CONTINUES):
                if pages[-1].get("stop_reason") != "pause_turn":
                    break
                req.body["messages"] = [req.body["messages"][0],
                                        {"role": "assistant", "content": pages[-1].get("content") or []}]
                pages.append(_post(client, req, provider))
            reply = parse_anthropic(pages)
        elif provider == "openai":
            reply = parse_openai(_post(client, openai_request(model, system, user, max_tokens), provider))
        else:
            reply = parse_openrouter(_post(client, openrouter_request(model, system, user, max_tokens), provider))
    finally:
        if own:
            client.close()
    config.log.info("web answer %s/%s: %s searches in %.1fs", provider, model, reply.searches,
                    time.monotonic() - started)
    usage.record_search_call(provider, model, reply.tokens_in, reply.tokens_out, reply.searches)
    return reply


# ---------------------------------------------------------------- cleaning and checks

_MD_LINK = re.compile(r"\[([^\]\n]{1,200})\]\((?:[^)\s]{1,800})\)")
_BARE_URL = re.compile(r"\(?\s*(?:https?://|www\.)[^\s)\]]+\)?", re.I)
_CITE_MARK = re.compile(r"\[\d{1,2}\]|【[^】]{0,40}】")
_MD_MARKS = re.compile(r"\*\*|__|`+|^#{1,6}\s*|^\s*(?:[-*•]|\d+\.)\s+", re.M)
_KEYLIKE = re.compile(
    r"\b(?:(?:sk|pk|rk)[-_]|gh[pos]_|xox[abprs]-|AKIA|AIza|eyJ)[A-Za-z0-9_\-]{8,}|\b[A-Za-z0-9_\-]{36,}\b"
)
_INJECTION = re.compile(
    r"ignore (all |any |the |your )?(previous|prior|above|earlier) (instructions|rules|prompts?)"
    r"|disregard (all |any |the |your )?(previous|prior|above|earlier) (instructions|rules|prompts?)"
    r"|as an ai language model|new instructions\s*:|do anything now",
    re.I,
)
_SCOPED_PACKAGE = re.compile(r"@[\w.\-]+/")


class ValidationError(ValueError):
    pass


def _link_words(m: re.Match[str]) -> str:
    """A markdown link becomes its words, unless the words are themselves an address (an inline citation)."""
    words = m.group(1)
    return "" if narration._URLISH.search(words) else words


def clean_text(raw: str) -> str:
    """Plain sentences: markdown links reduced to their words, bare addresses and markdown marks removed.

    Inline citations such as "([docs.n8n.io](https://...))" are dropped whole: the sources are listed as links.
    """
    text = _MD_LINK.sub(_link_words, raw or "")
    text = _BARE_URL.sub(" ", text)
    text = _CITE_MARK.sub("", text)
    text = _MD_MARKS.sub("", text)
    text = re.sub(r"\(\s*\)", "", text)
    text = narration.clean_speech(text)
    return re.sub(r"\s+([,.;:!?])(?=\s|$)", r"\1", text).strip()


def _personal_detail(text: str) -> bool:
    """True when app/privacy.py would scrub something here (email, handle, phone, a titled or self-named person)."""
    probe = _SCOPED_PACKAGE.sub("", text)  # "@n8n/..." package names are not handles
    return privacy.scrub_question(probe) != probe


def problem(text: str, question: str = "") -> str | None:
    """Why this text may not be shown, or None. Runs on every reply whatever the prompt says."""
    if not text:
        return "the answer is empty"
    if narration.word_count(text) > MAX_WORDS:
        return f"the answer is {narration.word_count(text)} words"
    if len(text) > MAX_CHARS:
        return f"the answer is {len(text)} characters"
    if narration._URLISH.search(text):
        return "it contains a web address"
    bad = narration.speech_problem(text)  # PG, quiz and attendance access codes, [student]/[person] tokens
    if bad:
        return bad
    if _KEYLIKE.search(text):
        return "it contains something that looks like a key or token"
    from . import course_info

    if course_info._SECRET_WORDS.search(text):
        return "it looks like it contains an access code"
    if _personal_detail(text):
        return "it contains a personal detail (a name, email, handle, or number)"
    if _INJECTION.search(text):
        return "it repeats an instruction from the web or the question"
    if question:
        runs = narration.Grounding.build(question, []).question_runs
        if runs and narration._ngrams(narration._plain_words(text), narration.ECHO_MAX_WORDS) & runs:
            return "it repeats the question's wording"
    return None


def trim_to_cap(text: str, limit: int = MAX_WORDS) -> str:
    """Whole sentences that fit the word and character caps. A reply a little over keeps its first sentences,
    never a cut one; with no sentence that fits, the text is left for the checks to reject. Uses the shared
    narration.trim_to_sentences (Oct 8 code review: this was a second copy that only counted words)."""
    return narration.trim_to_sentences(text, limit, MAX_CHARS, min_words=1) or text


def validate(raw: str, question: str) -> str:
    """The cleaned, trimmed answer text, or ValidationError naming the first problem."""
    text = trim_to_cap(clean_text(raw))
    bad = problem(text, question)
    if bad:
        raise ValidationError(bad)
    return text


def _host_label(host: str, path: str) -> str:
    """A readable label when a source has no usable title: "docs.n8n.io: install with docker"."""
    last = re.sub(r"\.(md|html?|php|aspx?)$", "", path.rstrip("/").rsplit("/", 1)[-1], flags=re.I)
    words = re.sub(r"[-_]+", " ", last).strip()
    if not words or not re.fullmatch(r"[A-Za-z0-9 ]{2,60}", words):
        return host
    return f"{host}: {words.lower()}"


def _drop_tracking(url: str) -> str:
    """Remove utm_* parameters (OpenAI adds utm_source=openai to its citations)."""
    parsed = urlparse(url or "")
    if not parsed.query:
        return url or ""
    kept = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if not k.lower().startswith("utm_")]
    return urlunparse(parsed._replace(query=urlencode(kept)))


def pick_links(reply: WebReply) -> list[dict[str, str]]:
    """2 to 4 `https://` sources from the tool's own metadata: citations first, then search results."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    pools = [reply.citations]
    pools.append(reply.results)  # used only while the citations gave fewer than MIN_CITED_LINKS
    for n, pool in enumerate(pools):
        if n and len(out) >= MIN_CITED_LINKS:
            break
        for src in pool:
            url = _drop_tracking(src.get("url", ""))
            parsed = urlparse(url)
            if parsed.scheme != "https" or not parsed.hostname or "@" in parsed.netloc or len(url) > MAX_URL_CHARS:
                continue
            if re.search(r"[\s\"'<>\\`]", url):
                continue
            key = re.sub(r"\.md$", "", url.split("#", 1)[0].rstrip("/"))
            if key in seen:
                continue
            seen.add(key)
            host = parsed.hostname.removeprefix("www.")
            label = narration.clean_speech(src.get("title") or "")[:80].strip()
            if (not label or narration.speech_problem(label) or _INJECTION.search(label)
                    or narration._URLISH.search(label) and label.lower() != host.lower()):
                label = _host_label(host, parsed.path)
            out.append({"label": label, "url": url})
            if len(out) >= MAX_LINKS:
                return out
    return out


# ---------------------------------------------------------------- the reply

@dataclass
class Result:
    """A web answer (or the decline), where it came from, and why it fell back if it did."""

    reply: dict[str, Any] | None  # None: nothing at all to point to (no link, no course slide), so decline
    source: str  # "llm" or "fallback"
    errors: list[str] = field(default_factory=list)
    reason: str | None = None  # question_log.fallback_reason when it fell back (provider_credits, no_links, ...)


def related_slides(content: Any, ranked: list[tuple[int, float]], records: list[dict[str, Any]],
                   n: int = RELATED_SLIDES) -> list[dict[str, Any]]:
    """The best-scoring slides for this course filter, even under the threshold, with signed thumbnails."""
    picked = [records[i] for i, _score in ranked[:n] if 0 <= i < len(records)]
    urls = storage.media_urls([r.get("image") for r in picked])
    out = []
    for rec in picked:
        image = urls.get(str(rec.get("image") or "").lstrip("/"))
        if not image:
            continue
        out.append({
            "slide_id": rec.get("id"),
            "title": narration.clean_speech(str(rec.get("title") or ""))[:120],
            "course": rec.get("course"),
            "course_title": rec.get("course_title"),
            "session": rec.get("session"),
            "session_title": rec.get("session_title"),
            "date": rec.get("date"),
            "slide_number": rec.get("slide_number"),
            "image": image,
        })
    return out


def build_user_prompt(question: str) -> str:
    return ("Student question (a course-adjacent topic my slides do not cover; search the web and answer it):\n"
            + json.dumps(question))


def answer(
    question: str,
    related: list[dict[str, Any]],
    follow_ups: list[str],
    searcher: Callable[..., WebReply],
    provider: str | None = None,
    model: str | None = None,
) -> Result:
    """One web-search call and the checks.

    Anything short of a good answer is the "Here is where to look." card: the search's own links when there
    are any, and the closest slides in my course (Oct 8 live bug: an out-of-credit provider gave a bare decline).
    `reply` is None only when there is nothing at all to point to; the caller then declines. `reason` is the
    fallback code for question_log.fallback_reason.
    """
    try:
        reply = searcher(prompts.get(ANSWER_PROMPT, max_words=MAX_WORDS), build_user_prompt(question),
                         ANSWER_MAX_TOKENS, provider=provider, model=model)
    except Exception as exc:  # the provider refused or failed: nothing was searched
        reason = fallback_reason(exc)
        config.log.warning("web answer fell back: fallback_reason=%s (%s)", reason, llm.describe_error(exc))
        give_back_budget()
        return _card(question, WHERE_TO_LOOK, [], related, follow_ups, "fallback", reason, llm.describe_error(exc))
    links = pick_links(reply)
    if not links:
        config.log.warning("web answer fell back: fallback_reason=no_links (no usable source link)")
        return _card(question, WHERE_TO_LOOK, [], related, follow_ups, "fallback", "no_links", "no usable source link")
    try:
        text = validate(reply.text, question)
    except ValidationError as exc:
        reason = fallback_reason(exc)
        config.log.warning("web answer fell back: fallback_reason=%s (%s)", reason, str(exc)[:200])
        return _card(question, WHERE_TO_LOOK, links, related, follow_ups, "fallback", reason, str(exc)[:200])
    return _card(question, text, links, related, follow_ups, "llm", None, None)


def _card(question: str, text: str, links: list[dict[str, str]], related: list[dict[str, Any]],
          follow_ups: list[str], source: str, reason: str | None, detail: str | None) -> Result:
    errors = [detail] if detail else []
    if not links and not related:
        return Result(None, source, errors, reason)
    spoken = voice()
    audio = speech.audio_link(text, spoken.tag_key) if spoken and source == "llm" else None
    body = {
        "question": question,
        "covered": True,
        "kind": KIND,
        "label": LABEL,
        "title": TITLE,
        "message": text,
        "answers": [{"text": text}],
        "links": links,
        "related": related,
        "audio": audio,
        "voice": spoken.public() if audio and spoken else None,
        "segments": [],
        "sources": [],
        "follow_ups": follow_ups[:MAX_FOLLOW_UPS],
    }
    return Result(body, source, errors, reason)
