"""Running the graph: start a task, resume it, read what it is waiting for.

Knows nothing about Telegram. It takes an agent and an optional progress
reporter, so both the bot and the terminal use the same code path.
"""

import threading
from contextlib import nullcontext

from langgraph.types import Command

from analyst.agent.pending import PendingAction, pending_actions
from analyst.plumbing.progress import Progress, SilentProgress

# How long a run may take before it announces itself. A greeting comes back in
# about a second, and announcing first turned every reply into two messages.
ANNOUNCE_AFTER = 4.0

# A graph-level backstop for a loop the middleware limits cannot see. deepagents
# sets 9_999, which is a ceiling in name only. This sits far above the per-run
# middleware bounds so those are always what actually fires.
RECURSION_LIMIT = 200

# A measured single-pass analysis needs 15 rounds, so the old default of 12
# stopped work that was going fine. After the approval policy narrowed, most
# rounds disappear and this is a backstop rather than a live constraint.
MAX_APPROVAL_ROUNDS = 40


def run_to_completion(runner: "AgentRunner", thread_id: str, task,
                      approver, max_rounds: int = MAX_APPROVAL_ROUNDS,
                      **kwargs) -> dict:
    """Run a task to the end, asking `approver` at every gate.

    The loop is the same whoever is deciding — a person at a prompt, or a test
    approving everything — so only the approver changes. `max_rounds` stops a
    stuck agent from asking forever.
    """
    result = runner.start(thread_id, task, **kwargs)
    for _ in range(max_rounds):
        actions = pending_actions(result.get("__interrupt__"))
        if not actions:
            return result
        approver.ask(actions)
        result = runner.resume(thread_id, approver.decisions, **kwargs)
    raise TooManyApprovals(f"Still asking for approval after {max_rounds} rounds.")


class TooManyApprovals(RuntimeError):
    """A run that keeps asking must not spend money forever."""


class AgentRunner:
    """Invokes the graph and hides the slow-run announcement.

    The graph runs on a daemon thread so the announcement can be sent while it
    is still working, and so Ctrl-C mid-analysis does not hang.
    """

    def __init__(self, agent, progress: Progress | None = None,
                 announce_after: float = ANNOUNCE_AFTER):
        self._agent = agent
        self._progress = progress or SilentProgress()
        self._announce_after = announce_after

    # -- reading state -----------------------------------------------------

    def waiting_for(self, thread_id: str) -> list[PendingAction]:
        """What this conversation is paused on, read from the checkpointer
        rather than memory, so approvals still work after a restart."""
        state = self._agent.get_state(self._config(thread_id))
        interrupts = [it for task in state.tasks
                      for it in getattr(task, "interrupts", ())]
        return pending_actions(interrupts)

    # -- running -----------------------------------------------------------

    def start(self, thread_id: str, task, **kwargs) -> dict:
        messages = task if isinstance(task, list) else [{"role": "user", "content": task}]
        return self._invoke(thread_id, {"messages": messages}, **kwargs)

    def resume(self, thread_id: str, decisions: list[dict], **kwargs) -> dict:
        return self._invoke(thread_id, Command(resume={"decisions": decisions}), **kwargs)

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _config(thread_id: str, run_name: str | None = None,
                metadata: dict | None = None) -> dict:
        """`run_name` and `metadata` are what LangSmith shows in the trace list.
        Without them every row is called "LangGraph"."""
        # recursion_limit is read from the top level. Nested inside
        # `configurable` it is silently ignored, which looks exactly like
        # working.
        config: dict = {"configurable": {"thread_id": str(thread_id)},
                        "recursion_limit": RECURSION_LIMIT}
        if run_name:
            config["run_name"] = run_name
        if metadata:
            config["metadata"] = metadata
        return config

    def _invoke(self, thread_id: str, payload, notice: str = "Working on it…",
                announce_after: float | None = None, progress=None,
                run_name: str | None = None, metadata: dict | None = None,
                attach=None) -> dict:
        """`notice` is sent once the run outlives the delay; 0 sends it up front.

        `progress` overrides the default reporter, because the bot only knows
        which chat to talk to once an update arrives.
        """
        delay = self._announce_after if announce_after is None else announce_after
        progress = progress or self._progress
        progress.busy()

        box: dict = {}

        config = self._config(thread_id, run_name, metadata)
        # Entered inside the worker: a new thread does not inherit contextvars,
        # so attaching on this thread would leave the run parentless.
        attach = attach or nullcontext

        def work() -> None:
            try:
                with attach():
                    box["value"] = self._agent.invoke(payload, config=config)
            except BaseException as exc:            # re-raised on the caller
                box["error"] = exc

        worker = threading.Thread(target=work, daemon=True)
        worker.start()

        if notice and delay <= 0:
            # Always slow: announce before waiting. join(0) would race the
            # worker and skip the notice whenever it finished first.
            progress.note(notice)
        else:
            worker.join(delay)
            if worker.is_alive() and notice:
                progress.note(notice)

        worker.join()

        if "error" in box:
            raise box["error"]
        return box["value"]
