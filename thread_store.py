"""Which thread each Telegram chat is on.

On disk because an in-memory counter reset on restart. Uses the checkpointer's
sqlite file but its own connection, since SqliteSaver locks the one it owns.
"""

import sqlite3
import threading

_DDL = """
CREATE TABLE IF NOT EXISTS chat_threads (
    chat_id    INTEGER PRIMARY KEY,
    generation INTEGER NOT NULL
)
"""


class ThreadStore:
    """Per-chat generation counter. Bumping it starts a fresh thread."""

    def __init__(self, db_path: str) -> None:
        # check_same_thread=False: read from the worker thread too. Every
        # access below holds _lock, so only one thread uses it at a time.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.execute(_DDL)

    def generation(self, chat_id: int) -> int:
        """0 for a chat that has never started over."""
        with self._lock:
            row = self._conn.execute(
                "SELECT generation FROM chat_threads WHERE chat_id = ?",
                (int(chat_id),),
            ).fetchone()
        return row[0] if row else 0

    def start_new(self, chat_id: int) -> int:
        """Move this chat to a fresh generation and return the new value."""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO chat_threads (chat_id, generation) VALUES (?, 1) "
                "ON CONFLICT(chat_id) DO UPDATE SET generation = generation + 1",
                (int(chat_id),),
            )
            row = self._conn.execute(
                "SELECT generation FROM chat_threads WHERE chat_id = ?",
                (int(chat_id),),
            ).fetchone()
        return row[0]

    def thread_id(self, chat_id: int) -> str:
        """The thread key for this chat. Generation 0 keeps the bare chat id, so
        older threads stay reachable."""
        gen = self.generation(chat_id)
        return f"{chat_id}-{gen}" if gen else str(chat_id)
