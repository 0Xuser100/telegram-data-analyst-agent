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
from langchain.agents.middleware import (
    InterruptOnConfig,
    ModelCallLimitMiddleware,
    TodoListMiddleware,
    ToolCallLimitMiddleware,
)
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite import SqliteSaver

from analyst.plumbing.backend import backend
from analyst.config import get_settings
from analyst.agent.policy import (
    is_routine_execute,
    is_routine_write,
    is_sensitive_read,
)
from analyst.agent.prompts import SYSTEM_RULES

CHECKPOINT_DB = "checkpoints.sqlite"
OUTPUT_DIR = "./output"

# Compact well below the deepagents default (170k tokens for this model), which
# never fires at ~40k per turn: the conversation just grew until it was
# expensive. Evicted messages are written to conversation_history/ first.
COMPACT_AT_TOKENS = 40_000
KEEP_MESSAGES = 6

# What bounds one run. Nothing did before: deepagents sets recursion_limit to
# 9_999, and the CLI's own round cap never sat on the bot's path. A measured
# single-pass analysis on this model takes 15 approval rounds and 29 tool
# calls, so these leave real headroom and only a stuck agent reaches them.
# Both are per-run, never per-thread: follow-up questions share a thread, and
# a thread-scoped budget would strangle a long conversation.
MODEL_CALLS_PER_RUN = 40
EXECUTIONS_PER_RUN = 15

# Which tools stop and ask. Read-only tools do not, or every run turns into a
# button-tapping session.
#
# `FilesystemPermission` would have owned path containment, but deepagents
# refuses `permissions` alongside a backend that can execute commands -- which
# is the only backend this app can use. So every rule below is a `when`
# predicate from agent/policy.py, and containment is ours to get right.
INTERRUPT_ON = {
    "execute": True,       # pause before running commands
    "write_file": True,    # pause before writing files
    "edit_file": True,     # an edit is a write
    "read_file": False,    # no pause
    "ls": False,           # no pause
}


def _asks_unless(predicate):
    """An approval rule that stays out of the way while `predicate` holds.

    A `when` returning False keeps the call out of the interrupt batch
    entirely, so the reviewer only sees what actually needs a decision.
    """
    return InterruptOnConfig(
        allowed_decisions=["approve", "reject"],
        when=lambda request: not predicate(request),
    )


def build_interrupt_on(auto_approve: bool = True) -> dict:
    """Approval rules for the graph.

    With auto-approval off, every write and every command asks, as before --
    except that reads of the credentials file still raise a card, which they
    never did.
    """
    rules = {**INTERRUPT_ON, "read_file": _asks_unless(
        lambda request: not is_sensitive_read(request))}
    if not auto_approve:
        return rules
    return {
        **rules,
        "execute": _asks_unless(is_routine_execute),
        "write_file": _asks_unless(is_routine_write),
        "edit_file": _asks_unless(is_routine_write),
    }





def build_model(settings=None) -> ChatOpenAI:
    """The key is passed explicitly: pydantic-settings reads .env without
    exporting to os.environ, so ChatOpenAI's own lookup would miss it."""
    settings = settings or get_settings()
    return ChatOpenAI(
        model=settings.OPENAI_MODEL,
        api_key=settings.OPENAI_API_KEY,
        # gpt-5.x refuses function tools on /v1/chat/completions whenever a
        # reasoning effort is in play, and langchain-openai sends that key
        # whatever we pass. Without this the first tool call fails outright.
        # reasoning_effort="none" silences it too, by switching off the
        # reasoning we changed model for — so: the Responses API.
        use_responses_api=True,
    )


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
                python_path: str = sys.executable, output_dir: str = OUTPUT_DIR,
                auto_approve: bool | None = None):
    """The compiled graph. Execution rules live in the system prompt, so they
    apply to any free-form task arriving from Telegram."""
    model = model or build_model()
    target_backend = target_backend or backend
    if auto_approve is None:
        auto_approve = get_settings().ANALYST_AUTO_APPROVE
    return create_deep_agent(
        model=model,
        tools=[],
        backend=target_backend,
        checkpointer=checkpointer if checkpointer is not None else build_checkpointer(),
        system_prompt=SYSTEM_RULES.format(python_path=python_path, output_dir=output_dir),
        interrupt_on=build_interrupt_on(auto_approve),
        middleware=[TodoListMiddleware(),
                    # `end` rather than `error`: the measured run that the old
                    # cap would have killed had produced the right analysis.
                    # A bounded run answers with what it has.
                    ModelCallLimitMiddleware(run_limit=MODEL_CALLS_PER_RUN,
                                             exit_behavior="end"),
                    # `continue` feeds "do not call execute again" back to the
                    # model, so a script stuck in fix-rerun-fail stops burning
                    # the budget without losing the run.
                    ToolCallLimitMiddleware(tool_name="execute",
                                            run_limit=EXECUTIONS_PER_RUN,
                                            exit_behavior="continue"),
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
