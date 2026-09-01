"""Every declared dependency must appear in the lock the image installs.

The Docker image installs ``requirements.lock.txt``, never
``requirements.txt`` (see backend/Dockerfile). Adding a package to
``requirements.txt`` without running ``make lock-backend`` therefore changes
nothing about the container, and the gap is invisible on a developer host where
the package was pip-installed by hand.

That is not hypothetical: the TCBS connector declared ``mcp>=2.1.0`` without
relocking, so the container had neither ``mcp`` nor its ``httpx2`` dependency
and every TCBS call died on ``No module named 'httpx2'`` while working fine
locally. This test fails on that class of drift.
"""
from __future__ import annotations

import re
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = BACKEND_ROOT / "requirements.txt"
LOCK = BACKEND_ROOT / "requirements.lock.txt"


def _normalize(name: str) -> str:
    """PEP 503 normalisation: ``PyJWT`` and ``pyjwt`` are the same package."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _declared() -> dict[str, str]:
    """Normalised name -> the line it came from, for readable failures."""
    declared: dict[str, str] = {}
    for raw in REQUIREMENTS.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            # Comments, blanks, and the ``-e ./libs/...`` editable install,
            # which the Dockerfile strips out of the lock on purpose.
            continue
        name = re.split(r"[\[<>=!~;]", line, maxsplit=1)[0].strip()
        if name:
            declared[_normalize(name)] = line
    return declared


def _locked() -> set[str]:
    locked = set()
    for raw in LOCK.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-")):
            continue
        name = re.split(r"[\[<>=!~;]", line, maxsplit=1)[0].strip()
        if name:
            locked.add(_normalize(name))
    return locked


def test_requirements_and_lock_both_exist():
    assert REQUIREMENTS.is_file()
    assert LOCK.is_file()


def test_the_parser_finds_the_requirements():
    """Guard the test itself: a parser returning nothing would pass vacuously."""
    declared = _declared()
    assert len(declared) > 30
    assert "fastapi" in declared
    assert "pyjwt" in declared  # declared as PyJWT — normalisation works
    assert "uvicorn" in declared  # declared with a [standard] extra


def test_every_declared_dependency_is_in_the_lock():
    missing = {name: line for name, line in _declared().items() if name not in _locked()}
    assert not missing, (
        "These are in requirements.txt but absent from requirements.lock.txt, so "
        "the Docker image does not install them:\n"
        + "\n".join(f"  {line}" for line in sorted(missing.values()))
        + "\n\nRun `make lock-backend` and rebuild the backend image."
    )
