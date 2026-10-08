"""The prompt editor: every model-facing prompt, its saved versions, and a dry run of a draft.

Serves Settings > Prompts (list, history, save, reset, restore, test). The prompt texts and
their checks live in `app/prompts.py`; this module only routes. Every route needs the admin cookie.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Request

from .. import alerts, auth, config, limits, llm, logistics, prompts, settings_store, storage, supa, usage, web_answer
from ..main import (
    RetrievalNotReady,
    Retriever,
    answer,
    clean_course,
    clean_question,
    get_completer,
    get_embedder,
    get_retriever,
)
from .common import PromptBody, PromptNoteBody, PromptRestoreBody, PromptTestBody
from .settings import FIRST_SLIDES_NOTE, _first_slides_answer

router = APIRouter(prefix="/api/admin")

KEYWORD_NOTE = "The keyword pre-check caught this question, so the prompt was not used."


def _prompt_name(name: str) -> str:
    if name not in prompts.REGISTRY:
        raise HTTPException(404, "There is no prompt with that name.")
    return name


def _prompt_write(fn, *args) -> dict[str, Any]:
    try:
        fn(*args)
    except prompts.PromptError as exc:
        raise HTTPException(400, str(exc)) from exc
    except supa.SupabaseError as exc:
        config.log.warning("prompt save failed: %s", exc)
        raise HTTPException(502, "Could not save the prompt to storage. Nothing changed.") from exc
    return prompts.view(args[0])


# ---------------------------------------------------------------- list, history, save, reset, restore

@router.get("/prompts")
def list_prompts(_: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
    """Every model-facing prompt: current text, default, and whether Ben has edited it."""
    return {"prompts": prompts.all_views(), "max_chars": prompts.MAX_CHARS}


@router.get("/prompts/{name}/history")
def prompt_history(name: str, _: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
    """The saved versions of one prompt, newest first. 404 for an unknown name, 502 when storage fails."""
    try:
        return {"versions": prompts.history(_prompt_name(name))}
    except supa.SupabaseError as exc:
        config.log.warning("prompt history read failed: %s", exc)
        raise HTTPException(502, "Could not read the prompt history from storage.") from exc


@router.put("/prompts/{name}")
def save_prompt(name: str, body: PromptBody, _: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
    """Save a new version. The text is checked (length, placeholders, reply words) before anything is written."""
    return _prompt_write(prompts.save, _prompt_name(name), body.text, body.note or "")


@router.post("/prompts/{name}/reset")
def reset_prompt(
    name: str, body: PromptNoteBody | None = None, _: auth.Session = Depends(auth.require_admin)
) -> dict[str, Any]:
    note = body.note if body else None
    return _prompt_write(lambda n, t: prompts.save(n, "", t, reset=True), _prompt_name(name), note or "")


@router.post("/prompts/{name}/restore")
def restore_prompt(
    name: str, body: PromptRestoreBody, _: auth.Session = Depends(auth.require_admin)
) -> dict[str, Any]:
    return _prompt_write(prompts.restore, _prompt_name(name), body.version, body.note or "")


# ---------------------------------------------------------------- testing a draft

@router.post("/prompts/{name}/test")
def test_prompt(
    name: str,
    body: PromptTestBody,
    request: Request,
    session: auth.Session = Depends(auth.require_admin),
    retriever: Retriever = Depends(get_retriever),
    embedder: Callable[[str], np.ndarray] = Depends(get_embedder),
    completer: Callable[..., str] = Depends(get_completer),
) -> dict[str, Any]:
    """Run one question through the real path with the draft text, for this request only. Nothing is saved.

    The draft goes through the same checks as a save, and the answer through the
    same validators as any student's (grounding, caps, PG, codes, name tokens).
    """
    spec = prompts.spec(_prompt_name(name))
    if not spec.testable:
        raise HTTPException(400, "Eval prompts are tested by an eval run, not here.")
    try:
        text = prompts.check(name, body.text)
    except prompts.PromptError as exc:
        raise HTTPException(400, str(exc)) from exc
    question = clean_question(body.question)
    course = clean_course(body.course)
    provider, model = settings_store.llm_choice()
    if not llm.key_configured(provider):
        raise HTTPException(400, f"{llm.KEY_VARS[provider]} is not set, so the prompt cannot be tested.")
    limits.check_ask_rate(auth.visitor_key(session), limits.client_hash(request))  # counts against limits; not logged
    started = time.monotonic()
    with prompts.draft(name, text), usage.purpose("prompt_test", sticky=True):  # test spend, not student spend
        if name == web_answer.SCOPE_PROMPT:
            ok, errors, output = _test_scope_prompt(question, completer, provider, model)
        elif name == logistics.PROMPT_NAME:
            ok, errors, output = _test_logistics_prompt(question, completer, provider, model)
        elif name == alerts.PROMPT_NAME:
            ok, errors, output = _test_alert_prompt(question, course, completer, provider, model)
        else:
            ok, errors, output = _test_answer_prompt(question, course, retriever, embedder, completer, provider,
                                                     model)
    return {
        "ok": ok,
        "name": name,
        "provider": provider,
        "model": model,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "errors": errors,
        "output": output,
    }


def _test_scope_prompt(question: str, completer: Callable[..., str], provider: str,
                       model: str) -> tuple[bool, list[str], dict[str, Any]]:
    scope = web_answer.classify(question, completer, provider=provider, model=model)
    output: dict[str, Any] = {"kind": scope.scope, "source": scope.source, "reason": scope.reason}
    if scope.source == "keyword":
        output["note"] = KEYWORD_NOTE
    elif scope.source == "error":
        output["note"] = "The reply was not a valid scope, so the question would be declined."
    return scope.source != "error", [], output


def _test_logistics_prompt(question: str, completer: Callable[..., str], provider: str,
                           model: str) -> tuple[bool, list[str], dict[str, Any]]:
    kind = logistics.classify(question, completer, provider=provider, model=model)
    output: dict[str, Any] = {"kind": kind.kind, "source": kind.source, "reason": kind.reason}
    if kind.source == "keyword":
        output["note"] = KEYWORD_NOTE
    elif kind.source == "error":
        output["note"] = "The reply was not a valid kind, so the question would be answered as course content."
    return kind.source != "error", [], output


def _test_alert_prompt(question: str, course: str | None, completer: Callable[..., str], provider: str,
                       model: str) -> tuple[bool, list[str], dict[str, Any]]:
    """The incident classifier on its own: a test never sends a text."""
    content = storage.store.get_or_503()
    output = alerts.detect(question, course, content, completer, provider=provider, model=model).public()
    if output["keyword"] is None:
        output["note"] = "The keyword pre-check found no problem phrase, so the prompt was not used."
    elif output["classifier_source"] == "error":
        output["note"] = "The reply was not usable, so only a strong keyword hit would alert."
    output["note"] = output.get("note") or "A test never sends a text."
    return output["classifier_source"] != "error", [], output


def _test_answer_prompt(question: str, course: str | None, retriever: Retriever, embedder: Any,
                        completer: Callable[..., str], provider: str,
                        model: str) -> tuple[bool, list[str], dict[str, Any]]:
    """Any other prompt: a whole answer through the real path, with the draft in place."""
    result, info = _answer_for_test(question, course, retriever, embedder, completer, provider, model)
    output = {
        "kind": info.get("kind"),
        "narration_source": info.get("narration"),
        "covered": result.get("covered"),
        "message": result.get("message"),
        "segments": [{"slide_id": s["slide_id"], "narration": s["narration"]} for s in result["segments"]],
        "follow_ups": result.get("follow_ups") or [],
        "links": result.get("links") or [],
        "note": info.get("note"),
    }
    ok = info.get("narration") in ("llm", "stored") or not result["covered"]
    return ok, info.get("errors") or [], output


def _answer_for_test(question: str, course: str | None, retriever: Retriever, embedder: Any,
                     completer: Callable[..., str], provider: str, model: str) -> tuple[dict[str, Any], dict[str, Any]]:
    content = storage.store.get_or_503()
    try:
        return answer(question, course, content, retriever, embedder, completer, provider, model)
    except RetrievalNotReady:
        result, res = _first_slides_answer(content, question, completer, provider, model)
        return result, {"narration": res.source, "errors": res.errors, "note": FIRST_SLIDES_NOTE,
                        "kind": "course_content"}
