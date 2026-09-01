"""Thread boundaries: the generation counter that decides which thread a chat
is on. Its whole reason for existing is surviving a restart, so that is what
most of these assert.
"""

import sqlite3
import threading

import pytest

from thread_store import ThreadStore


@pytest.fixture
def db(tmp_path) -> str:
    return str(tmp_path / "threads.sqlite")


# --------------------------------------------------------------------------
# unit
# --------------------------------------------------------------------------

def test_unknown_chat_starts_at_generation_zero(db):
    assert ThreadStore(db).generation(1) == 0


def test_generation_zero_keeps_the_bare_chat_id(db):
    """Threads created before this table existed must stay reachable, including
    one holding a pending approval."""
    assert ThreadStore(db).thread_id(1079144310) == "1079144310"


def test_start_new_returns_the_new_generation(db):
    store = ThreadStore(db)
    assert store.start_new(1) == 1
    assert store.start_new(1) == 2
    assert store.start_new(1) == 3


def test_thread_id_gains_a_suffix_after_the_first_reset(db):
    store = ThreadStore(db)
    store.start_new(42)
    assert store.thread_id(42) == "42-1"
    store.start_new(42)
    assert store.thread_id(42) == "42-2"


def test_chats_are_independent(db):
    store = ThreadStore(db)
    store.start_new(1)
    store.start_new(1)
    store.start_new(2)
    assert (store.thread_id(1), store.thread_id(2)) == ("1-2", "2-1")


@pytest.mark.parametrize("chat_id", [7, "7", 7.0])
def test_chat_id_is_coerced_to_int(db, chat_id):
    """Telegram ids arrive as ints, but a str from a config file must not create
    a second row for the same chat."""
    store = ThreadStore(db)
    store.start_new(7)
    assert store.generation(chat_id) == 1


# --------------------------------------------------------------------------
# the regression this module exists for
# --------------------------------------------------------------------------

def test_generation_survives_a_restart(db):
    first = ThreadStore(db)
    first.start_new(555)
    first.start_new(555)

    restarted = ThreadStore(db)          # a new process would do exactly this
    assert restarted.thread_id(555) == "555-2"


def test_restart_does_not_reset_to_the_oldest_thread(db):
    """The old in-memory dict reset to 0 on restart, silently reattaching the
    chat to its first thread and replaying all of it."""
    ThreadStore(db).start_new(555)
    assert ThreadStore(db).thread_id(555) != "555"


# --------------------------------------------------------------------------
# storage details
# --------------------------------------------------------------------------

def test_table_is_created_idempotently(db):
    ThreadStore(db)
    ThreadStore(db)                      # second __init__ must not raise
    names = [
        row[0]
        for row in sqlite3.connect(db).execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    ]
    assert "chat_threads" in names


def test_one_row_per_chat(db):
    store = ThreadStore(db)
    for _ in range(5):
        store.start_new(1)
    rows = sqlite3.connect(db).execute("SELECT chat_id, generation FROM chat_threads").fetchall()
    assert rows == [(1, 5)]


def test_shares_a_database_with_the_checkpointer_without_clashing(db):
    """It lives in checkpoints.sqlite in production, so it must coexist with
    tables it does not own."""
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE checkpoints (thread_id TEXT)")
    conn.commit()

    store = ThreadStore(db)
    store.start_new(1)

    assert store.thread_id(1) == "1-1"
    assert conn.execute("SELECT count(*) FROM checkpoints").fetchone() == (0,)


# --------------------------------------------------------------------------
# concurrency: the graph runs on a worker thread, the poller on the main one
# --------------------------------------------------------------------------

def test_reads_from_several_threads_agree(db):
    store = ThreadStore(db)
    store.start_new(1)
    seen: list[str] = []

    workers = [
        threading.Thread(target=lambda: seen.append(store.thread_id(1)))
        for _ in range(8)
    ]
    for w in workers:
        w.start()
    for w in workers:
        w.join()

    assert seen == ["1-1"] * 8


def test_concurrent_bumps_do_not_lose_increments(db):
    store = ThreadStore(db)
    workers = [threading.Thread(target=store.start_new, args=(1,)) for _ in range(20)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()

    assert store.generation(1) == 20
