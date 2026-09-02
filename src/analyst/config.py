"""Settings from .env, validated on startup."""

import os
from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # --- langsmith tracing ---
    LANGSMITH_TRACING: bool
    LANGSMITH_ENDPOINT: str
    LANGSMITH_API_KEY: str
    LANGSMITH_PROJECT: str

    # --- openai ---
    OPENAI_API_KEY: str
    OPENAI_MODEL: str

    # Routine analysis steps -- a script written into output/ and run with the
    # pinned interpreter -- proceed without an approval card. Set to false to
    # gate every write and every command again, for data or a model you trust
    # less. See agent/policy.py for exactly what "routine" means, and for why
    # the narrowed gate is a scope control rather than a security one.
    ANALYST_AUTO_APPROVE: bool = True

    # --- telegram ---
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    # Comma-separated chat ids allowed to talk to the bot. The agent can run
    # shell commands on this machine, so an empty list means nobody is served.
    TELEGRAM_ALLOWED_CHAT_IDS: str = ""
    TELEGRAM_POLL_TIMEOUT: int = 30

    @property
    def allowed_chat_ids(self) -> set[int]:
        return {
            int(part)
            for part in self.TELEGRAM_ALLOWED_CHAT_IDS.replace(" ", "").split(",")
            if part
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()


def apply_tracing_env(settings: Settings | None = None) -> None:
    """Copy the LangSmith settings into the environment.

    pydantic-settings reads .env without exporting it, and the tracing client
    only looks at the environment. Entry points call this before importing the
    graph.
    """
    settings = settings or get_settings()
    os.environ["LANGSMITH_TRACING"] = str(settings.LANGSMITH_TRACING).lower()
    os.environ["LANGSMITH_ENDPOINT"] = settings.LANGSMITH_ENDPOINT
    os.environ["LANGSMITH_API_KEY"] = settings.LANGSMITH_API_KEY
    os.environ["LANGSMITH_PROJECT"] = settings.LANGSMITH_PROJECT
