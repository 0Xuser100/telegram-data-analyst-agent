"""One trace per task, across the pause for approval.

Nothing here talks to LangSmith: the run tree and the client are doubles, which
is also how the "never break a run" promise is tested.
"""

from contextlib import contextmanager

import pytest

from tracing import NullTracer, TaskTracer, TraceStore, build_tracer

THREAD = "555-1"


class FakeRunTree:
    """Stands in for `langsmith.RunTree`: records the post, hands out headers."""

    posted: list["FakeRunTree"] = []

    def __init__(self, name, run_type=None, inputs=None, project_name=None):
        self.name = name
        self.run_type = run_type
        self.inputs = inputs
        self.project_name = project_name
        self.id = f"run-{len(FakeRunTree.posted) + 1}"

    def post(self):
        FakeRunTree.posted.append(self)

    def to_headers(self):
        return {"langsmith-trace": f"dotted-{self.id}", "baggage": "k=v"}


class FakeClient:
    def __init__(self):
        self.updates: list[tuple] = []

    def update_run(self, run_id, outputs=None, end_time=None):
        self.updates.append((run_id, outputs, end_time))


class BrokenRunTree(FakeRunTree):
    def post(self):
        raise RuntimeError("langsmith is down")


@pytest.fixture
def store(tmp_path):
    return TraceStore(str(tmp_path / "traces.sqlite"))


@pytest.fixture
def client():
    return FakeClient()


@pytest.fixture
def tracer(store, client):
    FakeRunTree.posted = []
    return TaskTracer(store, client=client, run_tree_factory=FakeRunTree)


# --------------------------------------------------------------------------
# the store
# --------------------------------------------------------------------------

def test_nothing_is_stored_for_an_unknown_thread(store):
    assert store.load(THREAD) is None


def test_saving_then_loading_round_trips(store):
    store.save(THREAD, "run-1", "dotted\nk=v")
    assert store.load(THREAD) == ("run-1", "dotted\nk=v")


def test_saving_again_replaces(store):
    store.save(THREAD, "run-1", "a")
    store.save(THREAD, "run-2", "b")
    assert store.load(THREAD) == ("run-2", "b")


def test_dropping_forgets(store):
    store.save(THREAD, "run-1", "a")
    store.drop(THREAD)
    assert store.load(THREAD) is None


def test_the_table_survives_a_restart(tmp_path):
    path = str(tmp_path / "traces.sqlite")
    TraceStore(path).save(THREAD, "run-1", "a")
    assert TraceStore(path).load(THREAD) == ("run-1", "a")


# --------------------------------------------------------------------------
# opening and re-attaching
# --------------------------------------------------------------------------

def test_starting_a_task_posts_one_parent_run(tracer):
    tracer.start(THREAD, "upload: sales.csv", {"task": "analyse"})
    assert len(FakeRunTree.posted) == 1
    assert FakeRunTree.posted[0].name == "upload: sales.csv"
    assert FakeRunTree.posted[0].run_type == "chain"


def test_the_parent_headers_are_handed_out(tracer):
    tracer.start(THREAD, "task")
    parent = tracer.parent_for(THREAD)
    assert parent["langsmith-trace"].startswith("dotted-")
    assert parent["baggage"] == "k=v"


def test_a_resume_after_a_restart_finds_the_same_parent(store, client):
    """The headers are on disk, so a new process attaches to the same trace."""
    FakeRunTree.posted = []
    TaskTracer(store, client=client, run_tree_factory=FakeRunTree).start(THREAD, "task")

    restarted = TaskTracer(store, client=client, run_tree_factory=FakeRunTree)
    assert restarted.parent_for(THREAD) == {"langsmith-trace": "dotted-run-1",
                                            "baggage": "k=v"}


def test_an_unstarted_thread_has_no_parent(tracer):
    assert tracer.parent_for("999-1") is None


def test_threads_do_not_share_a_parent(tracer):
    tracer.start("555-1", "one")
    tracer.start("777-1", "two")
    assert tracer.parent_for("555-1") != tracer.parent_for("777-1")


# --------------------------------------------------------------------------
# closing
# --------------------------------------------------------------------------

def test_finishing_closes_the_parent_run(tracer, client):
    tracer.start(THREAD, "task")
    tracer.finish(THREAD, {"reply": "**Revenue $840**"})

    run_id, outputs, end_time = client.updates[0]
    assert run_id == "run-1"
    assert outputs == {"reply": "**Revenue $840**"}
    assert end_time is not None


def test_a_finished_task_is_forgotten(tracer):
    """Otherwise the next task's runs would attach to a closed parent."""
    tracer.start(THREAD, "task")
    tracer.finish(THREAD)
    assert tracer.parent_for(THREAD) is None


def test_abandoning_records_why(tracer, client):
    tracer.start(THREAD, "task")
    tracer.abandon(THREAD, reason="conversation restarted")
    assert client.updates[0][1] == {"status": "conversation restarted"}


def test_closing_an_unknown_thread_does_nothing(tracer, client):
    tracer.finish("999-1")
    assert client.updates == []


def test_starting_a_second_task_closes_the_first(tracer, client):
    """A task that never got its approval must not adopt the next one's runs."""
    tracer.start(THREAD, "first")
    tracer.start(THREAD, "second")

    assert client.updates[0][1] == {"status": "superseded by a new task"}
    assert tracer.parent_for(THREAD)["langsmith-trace"] == "dotted-run-2"


# --------------------------------------------------------------------------
# tracing must never break a run
# --------------------------------------------------------------------------

def test_a_failure_to_open_is_swallowed(store, client, capsys):
    tracer = TaskTracer(store, client=client, run_tree_factory=BrokenRunTree)
    tracer.start(THREAD, "task")                # must not raise
    assert tracer.parent_for(THREAD) is None
    assert "could not start" in capsys.readouterr().out


def test_a_failure_to_close_still_forgets_the_trace(tracer, store):
    class Broken:
        def update_run(self, *args, **kwargs):
            raise RuntimeError("nope")

    tracer._client = Broken()
    tracer.start(THREAD, "task")
    tracer.finish(THREAD)                       # must not raise
    assert store.load(THREAD) is None


def test_attached_is_a_no_op_without_a_parent(tracer):
    with tracer.attached("999-1"):
        pass


def test_attached_sets_the_parent_when_there_is_one(tracer, monkeypatch):
    seen = {}

    @contextmanager
    def fake_tracing_context(**kwargs):
        seen.update(kwargs)
        yield

    monkeypatch.setattr("tracing.tracing_context", fake_tracing_context)
    tracer.start(THREAD, "task")
    with tracer.attached(THREAD):
        pass
    assert seen["parent"]["langsmith-trace"] == "dotted-run-1"


# --------------------------------------------------------------------------
# the null tracer
# --------------------------------------------------------------------------

def test_the_null_tracer_accepts_everything():
    """Chosen when tracing is off, so no caller needs a special case."""
    null = NullTracer()
    null.start(THREAD, "task", {"a": 1})
    null.finish(THREAD, {"b": 2})
    null.abandon(THREAD)
    with null.attached(THREAD):
        pass
    assert null.parent_for(THREAD) is None


def test_build_tracer_returns_the_null_one_when_tracing_is_off(tmp_path):
    tracer = build_tracer(str(tmp_path / "x.sqlite"), enabled=False)
    assert isinstance(tracer, NullTracer)


def test_build_tracer_returns_a_real_one_when_tracing_is_on(tmp_path):
    tracer = build_tracer(str(tmp_path / "x.sqlite"), enabled=True,
                          project_name="test-project")
    assert isinstance(tracer, TaskTracer)
