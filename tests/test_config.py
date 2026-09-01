"""Settings loading and the chat allowlist parser."""

import pytest
from pydantic import ValidationError

from analyst.config import Settings, apply_tracing_env, get_settings

BASE = {
    "LANGSMITH_TRACING": True,
    "LANGSMITH_ENDPOINT": "https://api.smith.langchain.com",
    "LANGSMITH_API_KEY": "lsv2_pt_x",
    "LANGSMITH_PROJECT": "p",
    "OPENAI_API_KEY": "sk-x",
}


BASE_WITH_MODEL = {**BASE, "OPENAI_MODEL": "gpt-4.1-mini-2025-04-14"}

# conftest sets these for the whole suite, and pydantic-settings reads env vars
# even with _env_file=None, so a test about a missing value must clear them.
ENV_KEYS = (
    "LANGSMITH_TRACING", "LANGSMITH_ENDPOINT", "LANGSMITH_API_KEY", "LANGSMITH_PROJECT",
    "OPENAI_API_KEY", "OPENAI_MODEL",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_CHAT_IDS", "TELEGRAM_POLL_TIMEOUT",
)


@pytest.fixture
def no_env(monkeypatch):
    """A process environment with none of these variables set."""
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def settings(**overrides) -> Settings:
    """Built without reading .env, so the developer's own file cannot change
    the outcome of a test."""
    return Settings(_env_file=None, **{**BASE_WITH_MODEL, **overrides})


# --------------------------------------------------------------------------
# required and defaulted fields
# --------------------------------------------------------------------------

@pytest.mark.parametrize("missing", ["OPENAI_API_KEY", "OPENAI_MODEL", "LANGSMITH_API_KEY"])
def test_required_fields_fail_loudly(no_env, missing):
    """Better a startup crash naming the variable than a run that dies later
    inside the model call."""
    kwargs = {k: v for k, v in BASE_WITH_MODEL.items() if k != missing}
    with pytest.raises(ValidationError, match=missing):
        Settings(_env_file=None, **kwargs)


def test_the_model_comes_from_configuration():
    assert settings(OPENAI_MODEL="gpt-4o-mini").OPENAI_MODEL == "gpt-4o-mini"


def test_telegram_fields_are_optional(no_env):
    """The CLI entry point runs with no Telegram configuration at all."""
    s = Settings(_env_file=None, **BASE_WITH_MODEL)
    assert s.TELEGRAM_BOT_TOKEN is None
    assert s.TELEGRAM_ALLOWED_CHAT_IDS == ""
    assert s.TELEGRAM_POLL_TIMEOUT == 30


@pytest.mark.parametrize("raw", ["true", "True", "1", "yes"])
def test_tracing_accepts_the_usual_truthy_spellings(raw):
    assert settings(LANGSMITH_TRACING=raw).LANGSMITH_TRACING is True


# --------------------------------------------------------------------------
# allowed_chat_ids
# --------------------------------------------------------------------------

@pytest.mark.parametrize(("raw", "expected"), [
    ("", set()),
    ("555", {555}),
    ("555,777", {555, 777}),
    (" 555 , 777 ", {555, 777}),
    ("555,,777", {555, 777}),
    ("555,555", {555}),
    ("-1001234567890", {-1001234567890}),      # a Telegram group id
])
def test_allowlist_parsing(raw, expected):
    assert settings(TELEGRAM_ALLOWED_CHAT_IDS=raw).allowed_chat_ids == expected


def test_an_empty_allowlist_serves_nobody():
    """The agent runs shell commands on this machine, so "unset" must mean
    "nobody", never "everybody"."""
    assert settings(TELEGRAM_ALLOWED_CHAT_IDS="").allowed_chat_ids == set()


def test_a_non_numeric_id_fails_loudly():
    """Better a startup crash than a silently narrower allowlist."""
    with pytest.raises(ValueError):
        settings(TELEGRAM_ALLOWED_CHAT_IDS="555,oops").allowed_chat_ids


# --------------------------------------------------------------------------
# caching
# --------------------------------------------------------------------------

def test_get_settings_is_cached():
    assert get_settings() is get_settings()


# --------------------------------------------------------------------------
# apply_tracing_env
# --------------------------------------------------------------------------

def test_tracing_settings_are_copied_into_the_environment(monkeypatch):
    """The tracing client only reads os.environ, and pydantic-settings does not
    export .env there."""
    import os
    for key in ("LANGSMITH_TRACING", "LANGSMITH_ENDPOINT", "LANGSMITH_API_KEY",
                "LANGSMITH_PROJECT"):
        monkeypatch.delenv(key, raising=False)

    apply_tracing_env(settings(LANGSMITH_PROJECT="my-project"))

    assert os.environ["LANGSMITH_PROJECT"] == "my-project"
    assert os.environ["LANGSMITH_TRACING"] == "true"


def test_tracing_off_is_written_as_lowercase_false(monkeypatch):
    import os
    apply_tracing_env(settings(LANGSMITH_TRACING=False))
    assert os.environ["LANGSMITH_TRACING"] == "false"
