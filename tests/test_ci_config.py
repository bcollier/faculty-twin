"""The CI workflow and the deploy ignore list stay in line with the documented test command."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"


def _lines(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]


def test_vercel_never_uploads_ci_or_test_files():
    ignored = set(_lines(ROOT / ".vercelignore"))
    for entry in [".github/", "tests/", "requirements-test.txt", "requirements-e2e.txt"]:
        assert entry in ignored, entry


def test_workflow_runs_on_prs_and_main_without_secrets():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request:" in text and "branches: [main]" in text
    assert "secrets." not in text  # the suite needs no keys
    assert "permissions:\n  contents: read" in text


def test_workflow_installs_what_the_documented_command_installs():
    text = WORKFLOW.read_text(encoding="utf-8")
    doc = (ROOT / "docs" / "TESTING_AND_SCORES.md").read_text(encoding="utf-8")
    command = "--with-requirements requirements.txt --with-requirements requirements-test.txt"
    assert command in text and command in doc
    test_reqs = (ROOT / "requirements-test.txt").read_text(encoding="utf-8")
    for pkg in ["pytest", "pytest-cov", "hypothesis", "rapidfuzz", "nicknames", "nbformat", "scikit-learn",
                "pillow", "scipy"]:
        assert f"\n{pkg}==" in test_reqs, pkg
    assert "--cov=app" in text and "--e2e tests/e2e" in text and "node --check" in text
    assert "enable-cache: true" in text  # uv cache keeps runs short


def test_workflow_lints_with_the_repo_ruff_rules():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "ruff" in text and "check" in text  # the rules live in ruff.toml (docs/CODE_STYLE.md)
    rules = (ROOT / "ruff.toml").read_text(encoding="utf-8")
    for code in ['"E"', '"F"', '"I"', '"B"', '"UP"', '"SIM"']:
        assert code in rules, code
    assert '"app/retrieval.py" = ["ALL"]' in rules  # Ben's hand-written retrieval is never linted into edits
    assert "ruff.toml" in _lines(ROOT / ".vercelignore")


def test_python_version_comes_from_the_repo():
    assert (ROOT / ".python-version").read_text().strip() == "3.12"
    assert "uv python install" in WORKFLOW.read_text(encoding="utf-8")
