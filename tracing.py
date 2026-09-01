"""One LangSmith trace per task, even though the task pauses for approval.

Every `invoke` is its own root run by default, so one analysis shows up as four
or five unrelated traces. This opens a parent run when a task starts, saves its
trace headers next to the conversation, and re-attaches to it on every resume —
including after a restart, which is why the headers go on disk rather than
staying in memory.

Tracing must never take a run down: every call here is wrapped, and a failure
is logged and ignored.
"""

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from langsmith import Client
from langsmith.run_helpers import tracing_context
from langsmith.run_trees import RunTree

_DDL = """
CREATE TABLE IF NOT EXISTS thread_traces (
    thread_id TEXT PRIMARY KEY,
    run_id    TEXT NOT NULL,
    headers   TEXT NOT NULL
)
"""


class NullTracer:
    """What you get when tracing is off. Every method does nothing."""

    def start(self, thread_id, name, inputs=None) -> None:
        pass

    def parent_for(self, thread_id):
        return None

    def finish(self, thread_id, outputs=None) -> None:
        pass

    def abandon(self, thread_id, reason: str = "abandoned") -> None:
        pass

    @contextmanager
    def attached(self, thread_id):
        yield


class TraceStore:
    """Which open parent run belongs to which conversation.

    Same sqlite file as the checkpointer, its own connection and lock — the same
    arrangement `ThreadStore` uses, and for the same reason.
    """

    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.execute(_DDL)

    def save(self, thread_id: str, run_id: str, headers: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO thread_traces (thread_id, run_id, headers) VALUES (?, ?, ?) "
                "ON CONFLICT(thread_id) DO UPDATE SET run_id = ?, headers = ?",
                (str(thread_id), str(run_id), headers, str(run_id), headers),
            )

    def load(self, thread_id: str) -> tuple[str, str] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT run_id, headers FROM thread_traces WHERE thread_id = ?",
                (str(thread_id),),
            ).fetchone()
        return (row[0], row[1]) if row else None

    def drop(self, thread_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM thread_traces WHERE thread_id = ?",
                               (str(thread_id),))


class TaskTracer:
    """Opens a parent run per task and re-attaches to it on every resume."""

    TRACE_HEADER = "langsmith-trace"
    BAGGAGE_HEADER = "baggage"

    def __init__(self, store: TraceStore, client=None, run_tree_factory=RunTree,
                 project_name: str | None = None):
        self._store = store
        self._client = client if client is not None else Client()
        self._run_tree = run_tree_factory
        self._project = project_name

    # -- lifecycle ---------------------------------------------------------

    def start(self, thread_id: str, name: str, inputs: dict | None = None) -> None:
        """Open the parent run. Replaces any previous one for this conversation:
        a task that never finished would otherwise capture the next one."""
        self.abandon(thread_id, reason="superseded by a new task")
        try:
            root = self._run_tree(
                name=name,
                run_type="chain",
                inputs=inputs or {},
                project_name=self._project,
            )
            root.post()
            headers = root.to_headers()
            self._store.save(thread_id, str(root.id),
                             f"{headers[self.TRACE_HEADER]}\n{headers.get(self.BAGGAGE_HEADER, '')}")
        except Exception as exc:                    # never break the run
            print(f"[trace] could not start {name!r}: {exc}")

    def parent_for(self, thread_id: str) -> dict | None:
        """Headers that make the next invoke a child of this task's parent."""
        stored = self._store.load(thread_id)
        if not stored:
            return None
        _, blob = stored
        trace, _, baggage = blob.partition("\n")
        headers = {self.TRACE_HEADER: trace}
        if baggage:
            headers[self.BAGGAGE_HEADER] = baggage
        return headers

    def finish(self, thread_id: str, outputs: dict | None = None) -> None:
        """Close the parent run: the task produced its answer."""
        self._close(thread_id, outputs=outputs or {})

    def abandon(self, thread_id: str, reason: str = "abandoned") -> None:
        """Close it without an answer — `/new`, or a fresh upload."""
        self._close(thread_id, outputs={"status": reason})

    @contextmanager
    def attached(self, thread_id: str):
        """Run a block with this task's parent as the current trace parent."""
        parent = self.parent_for(thread_id)
        if parent is None:
            yield
            return
        with tracing_context(parent=parent):
            yield

    # -- internals ---------------------------------------------------------

    def _close(self, thread_id: str, outputs: dict) -> None:
        stored = self._store.load(thread_id)
        if not stored:
            return
        run_id, _ = stored
        try:
            self._client.update_run(run_id, outputs=outputs,
                                    end_time=datetime.now(timezone.utc))
        except Exception as exc:
            print(f"[trace] could not close {run_id}: {exc}")
        finally:
            self._store.drop(thread_id)


def build_tracer(db_path: str, enabled: bool, project_name: str | None = None):
    """A real tracer when tracing is on, a null one when it is off."""
    if not enabled:
        return NullTracer()
    return TaskTracer(TraceStore(db_path), project_name=project_name)
