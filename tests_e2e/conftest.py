"""Setup for the real end-to-end run.

Unlike `tests/`, this talks to the real OpenAI API with the real key from `.env`,
so it costs money and needs the network. It only runs when RUN_E2E=1.

The key is read out of `.env` into the environment before anything is imported,
then the session moves to a temp directory: the agent writes its script, its
chart and its checkpoints there instead of into the project.
"""

import atexit
import os
import shutil
import tempfile

import pytest

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPECTED_MODEL = "gpt-4.1-mini-2025-04-14"


def _load_dotenv(path: str) -> dict:
    """Minimal .env reader. Values stay in this process; nothing is written out."""
    values = {}
    if not os.path.isfile(path):
        return values
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


if os.environ.get("RUN_E2E") == "1":
    for key, value in _load_dotenv(os.path.join(PROJECT_DIR, ".env")).items():
        os.environ.setdefault(key, value)

    _SANDBOX = tempfile.mkdtemp(prefix="deepagents-e2e-")
    atexit.register(shutil.rmtree, _SANDBOX, ignore_errors=True)
else:
    _SANDBOX = None


def pytest_collection_modifyitems(config, items):
    """Skip everything here unless RUN_E2E=1, so a plain `pytest` costs nothing."""
    if os.environ.get("RUN_E2E") == "1":
        return
    skip = pytest.mark.skip(reason="set RUN_E2E=1 to run the real API end-to-end test")
    for item in items:
        item.add_marker(skip)


@pytest.fixture(scope="session", autouse=True)
def sandbox_cwd():
    """Run in a temp directory so the project's own files are untouched."""
    if _SANDBOX is None:
        yield None
        return
    original = os.getcwd()
    os.chdir(_SANDBOX)
    try:
        yield _SANDBOX
    finally:
        os.chdir(original)


@pytest.fixture(scope="session")
def live_agent(sandbox_cwd):
    """The real agent, with the real model. Imported late, inside the sandbox."""
    if os.environ.get("OPENAI_API_KEY", "").startswith("sk-test"):
        pytest.skip("fake credentials are loaded; run this file on its own")

    from analyst.agent import builder as agent
    from analyst.plumbing.backend import ensure_sample_data

    ensure_sample_data()          # the sandbox needs something to analyse

    assert agent.model.model_name == EXPECTED_MODEL, (
        f"expected {EXPECTED_MODEL}, got {agent.model.model_name}. "
        "Set OPENAI_MODEL in .env."
    )
    return agent
