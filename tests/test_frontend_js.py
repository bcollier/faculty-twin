"""Behaviour tests for the page scripts in public/, run under node with a TEST FAKE DOM (tests/js/fakedom.mjs).

Each tests/js/*.test.mjs file loads one page script, drives it with fake fetch replies, and exits
non-zero on a failed assertion. Skipped when node is not installed.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = sorted((ROOT / "tests" / "js").glob("*.test.mjs"))
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("script", SCRIPTS, ids=[s.stem for s in SCRIPTS])
def test_page_script(script: Path) -> None:
    run = subprocess.run([NODE, str(script)], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, f"{script.name} failed:\n{run.stdout}\n{run.stderr}"
