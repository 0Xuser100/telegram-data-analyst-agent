"""Assembling the agent: model, memory, compaction, approval rules.

Each piece has its own builder, so a test or a script can swap one without
copying the rest. The module-level objects at the bottom are the app's defaults.
"""

import sqlite3
import sys

from deepagents import create_deep_agent
from deepagents.middleware.summarization import (
    SummarizationMiddleware,
    compute_summarization_defaults,
)
from langchain.agents.middleware import TodoListMiddleware
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite import SqliteSaver

from analyst.plumbing.backend import backend
from analyst.config import get_settings
from analyst.agent.prompts import SYSTEM_RULES

CHECKPOINT_DB = "checkpoints.sqlite"
OUTPUT_DIR = "./output"

# Compact well below the deepagents default (170k tokens for this model), which
# never fires at ~40k per turn: the conversation just grew until it was
# expensive. Evicted messages are written to conversation_history/ first.
COMPACT_AT_TOKENS = 40_000
KEEP_MESSAGES = 6

# Which tools stop and ask. Read-only tools do not, or every run turns into a
# button-tapping session.
INTERRUPT_ON = {
    "execute": True,       # pause before running commands
    "write_file": True,    # pause before writing files
    "read_file": False,    # no pause
    "ls": False,           # no pause
}


def build_model(settings=None) -> ChatOpenAI:
    """The key is passed explicitly: pydantic-settings reads .env without
    exporting to os.environ, so ChatOpenAI's own lookup would miss it."""
    settings = settings or get_settings()
    return ChatOpenAI(model=settings.OPENAI_MODEL, api_key=settings.OPENAI_API_KEY)


def build_checkpointer(db_path: str = CHECKPOINT_DB) -> SqliteSaver:
    """On disk, not in memory: an approval button must still work after a
    restart. check_same_thread=False because the graph runs on a worker
    thread; SqliteSaver serialises access with its own lock."""
    return SqliteSaver(sqlite3.connect(db_path, check_same_thread=False))


def build_summarizer(model, target_backend=None) -> SummarizationMiddleware:
    """Same `.name` as the deepagents default, so it replaces that one instead
    of stacking a second summarizer."""
    target_backend = target_backend or backend
    defaults = compute_summarization_defaults(model)
    return SummarizationMiddleware(
        model=model,
        backend=target_backend,
        trigger=("tokens", COMPACT_AT_TOKENS),
        keep=("messages", KEEP_MESSAGES),
        # Explicit: the constructor defaults it to None, which would drop the
        # tool-arg clipping deepagents normally configures.
        truncate_args_settings=defaults["truncate_args_settings"],
    )


def build_agent(model=None, checkpointer=None, target_backend=None, summarizer=None,
                python_path: str = sys.executable, output_dir: str = OUTPUT_DIR):
    """The compiled graph. Execution rules live in the system prompt, so they
    apply to any free-form task arriving from Telegram."""
    model = model or build_model()
    target_backend = target_backend or backend
    return create_deep_agent(
        model=model,
        tools=[],
        backend=target_backend,
        checkpointer=checkpointer if checkpointer is not None else build_checkpointer(),
        system_prompt=SYSTEM_RULES.format(python_path=python_path, output_dir=output_dir),
        interrupt_on=INTERRUPT_ON,
        middleware=[TodoListMiddleware(),
                    summarizer or build_summarizer(model, target_backend)],
    )


def thread_config(thread_id: str) -> dict:
    """Config for one conversation. Both front-ends key their history on this."""
    return {"configurable": {"thread_id": str(thread_id)}}


# -- the app's defaults ------------------------------------------------------

model = build_model()
checkpointer = build_checkpointer()
summarizer = build_summarizer(model)
agent = build_agent(model=model, checkpointer=checkpointer, summarizer=summarizer)
