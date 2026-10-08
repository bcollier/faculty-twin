"""Settings page API (admin passcode, `ft_admin` cookie). Never linked from the student UI.

One module per Settings section, each with its own `APIRouter(prefix="/api/admin")`:

| Module | Serves |
| --- | --- |
| `login.py` | signing in and out |
| `settings.py` | reading and saving settings, the model picker, the model test (Model, Voice, Limits and access) |
| `prompt_editor.py` | Prompts |
| `status_activity.py` | Keys, the page's first load, Activity |
| `voice_picker.py` | the voice list and free-voice previews (Voice) |
| `courses.py` | Courses and source material: the catalog |
| `uploads.py` | Courses and source material: uploads and their processing status |
| `price_guard.py` | the model price ceiling (no routes) |
| `common.py` | shared checks, constants and request bodies (no routes) |

`router` below joins the section routers for `app/main.py`. FastAPI matches routes in
the order they were added, so the sections are included in the order the routes were
first written: login, settings, prompts, status and activity, voice, courses, uploads.

The package imports `app.main` (for `answer()` and the request cleaners) and `app.main`
imports this package at its very end, after everything the sections need is defined.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import courses, login, prompt_editor, settings, status_activity, uploads, voice_picker
from .common import MODEL_ID_RE
from .price_guard import check_model_price
from .status_activity import activity_row, infer_kind
from .uploads import safe_filename

__all__ = ["MODEL_ID_RE", "activity_row", "check_model_price", "infer_kind", "router", "safe_filename"]

router = APIRouter()
router.include_router(login.router)
router.include_router(settings.router)
router.include_router(prompt_editor.router)
router.include_router(status_activity.router)
router.include_router(voice_picker.router)
router.include_router(courses.router)
router.include_router(uploads.router)
