"""Running the graph: the progress notice, thread ids, and error handling."""

import time

import pytest

from conftest import (
    EXECUTE_ACTION,
    WRITE_FILE_ACTION,
    FakeProgress,
    ai_result,
    interrupt_result,
)
from analyst.agent.runner import AgentRunner, SilentProgress, TooManyApprovals, run_to_completion

THREAD = "555-1"


# --------------------------------------------------------------------------
# the progress notice
# --------------------------------------------------------------------------

def test_quick_reply_sends_no_notice(runner, progress):
    runner.start(THREAD, "hi")
    assert progress.notes == []
    assert progress.busy_calls == 1          # the typing dot covers it


def test_slow_run_announces_itself(runner, graph, progress):
    graph.delay = 0.2                        # announce_after is 0.05 in tests
    runner.start(THREAD, "analyse this")
    assert progress.notes == ["Working on it…"]


def test_zero_delay_announces_immediately(runner, progress):
    """The upload path knows the run is slow, so it does not wait to find out."""
    runner.start(THREAD, "analyse this", announce_after=0)
    assert progress.notes == ["Working on it…"]


def test_notice_wording_is_configurable(runner, graph, progress):
    graph.delay = 0.2
    runner.resume(THREAD, [{"type": "approve"}], notice="Still working…")
    assert progress.notes == ["Still working…"]


def test_notice_can_be_suppressed(runner, graph, progress):
    graph.delay = 0.2
    runner.start(THREAD, "hi", notice="")
    assert progress.notes == []


def test_progress_can_be_overridden_per_call(runner, graph):
    """The bot only learns which chat to talk to when an update arrives."""
    graph.delay = 0.2
    other = FakeProgress()
    runner.start(THREAD, "hi", progress=other)
    assert other.notes == ["Working on it…"]


def test_silent_progress_is_a_valid_reporter(graph):
    """The terminal front-end shows its own prompts, so it reports nothing."""
    graph.delay = 0.1
    quiet = AgentRunner(graph, progress=SilentProgress(), announce_after=0)
    assert quiet.start(THREAD, "hi") is not None


def test_a_slow_run_is_not_abandoned_after_the_notice(runner, graph):
    graph.delay = 0.2
    graph.results = [{"messages": [], "marker": "done"}]
    started = time.time()
    result = runner.start(THREAD, "hi")
    assert result["marker"] == "done"
    assert time.time() - started >= 0.2


# --------------------------------------------------------------------------
# payloads and threads
# --------------------------------------------------------------------------

def test_a_string_task_becomes_one_human_message(runner, graph):
    runner.start(THREAD, "what is the revenue?")
    payload, _ = graph.invocations[0]
    assert payload["messages"] == [{"role": "user", "content": "what is the revenue?"}]


def test_a_message_list_is_passed_through(runner, graph):
    """main.py builds messages from a prompt template."""
    messages = [{"role": "user", "content": "one"}, {"role": "user", "content": "two"}]
    runner.start(THREAD, messages)
    assert graph.invocations[0][0]["messages"] == messages


def test_every_call_carries_the_thread_id(runner, graph):
    runner.start(THREAD, "hi")
    runner.resume(THREAD, [{"type": "approve"}])
    assert graph.threads == [THREAD, THREAD]


def test_thread_ids_are_strings(runner, graph):
    """LangGraph rejects a non-string thread id."""
    runner.start(555, "hi")
    assert graph.threads == ["555"]


def test_resume_sends_a_command(runner, graph):
    runner.resume(THREAD, [{"type": "approve"}])
    payload, _ = graph.invocations[0]
    assert payload.resume == {"decisions": [{"type": "approve"}]}


# --------------------------------------------------------------------------
# reading what a run is waiting for
# --------------------------------------------------------------------------

def test_waiting_for_reads_the_checkpointer(runner, graph):
    """Not memory: an approval must still resolve after a restart."""
    graph.results = [interrupt_result(WRITE_FILE_ACTION)]
    runner.start(THREAD, "analyse this")
    assert [a.name for a in runner.waiting_for(THREAD)] == ["write_file"]


def test_nothing_pending_on_a_finished_run(runner, graph):
    runner.start(THREAD, "hi")
    assert runner.waiting_for(THREAD) == []


def test_several_actions_are_all_reported(runner, graph):
    graph.results = [interrupt_result(WRITE_FILE_ACTION, EXECUTE_ACTION)]
    runner.start(THREAD, "analyse this")
    assert len(runner.waiting_for(THREAD)) == 2


# --------------------------------------------------------------------------
# errors
# --------------------------------------------------------------------------

def test_worker_errors_surface_on_the_caller(runner, graph):
    """The graph runs on another thread; an exception there must not vanish."""
    graph.error = RuntimeError("kaboom")
    with pytest.raises(RuntimeError, match="kaboom"):
        runner.start(THREAD, "hi")


def test_a_failed_run_still_reported_progress(runner, graph, progress):
    graph.error = RuntimeError("kaboom")
    with pytest.raises(RuntimeError):
        runner.start(THREAD, "hi")
    assert progress.busy_calls == 1


# --------------------------------------------------------------------------
# run_to_completion — one loop, whoever is deciding
# --------------------------------------------------------------------------

class AlwaysApproves:
    """The simplest Approver there is; the terminal one asks a human instead."""

    def __init__(self):
        self.rounds = 0
        self.decisions: list[dict] = []

    def ask(self, actions):
        self.rounds += 1
        self.decisions = [{"type": "approve"} for _ in actions]


def test_a_run_without_gates_returns_straight_away(runner, graph):
    graph.results = [ai_result("done")]
    approver = AlwaysApproves()
    result = run_to_completion(runner, THREAD, "analyse this", approver)
    assert approver.rounds == 0
    assert result["messages"][-1].content == "done"


def test_each_gate_is_put_to_the_approver(runner, graph):
    graph.results = [
        interrupt_result(WRITE_FILE_ACTION),
        interrupt_result(EXECUTE_ACTION),
        ai_result("done"),
    ]
    approver = AlwaysApproves()
    run_to_completion(runner, THREAD, "analyse this", approver)
    assert approver.rounds == 2
    assert len(graph.invocations) == 3


def test_one_decision_per_pending_action(runner, graph):
    graph.results = [interrupt_result(WRITE_FILE_ACTION, EXECUTE_ACTION), ai_result("done")]
    run_to_completion(runner, THREAD, "analyse this", AlwaysApproves())
    assert graph.invocations[-1][0].resume["decisions"] == [{"type": "approve"}] * 2


def test_the_recursion_backstop_is_a_top_level_config_key(runner, graph):
    """LangGraph reads recursion_limit from the top level. Nested inside
    `configurable` it is silently ignored, which looks identical to working."""
    runner.start(THREAD, "analyse this")
    config = graph.invocations[-1][1]
    assert config["recursion_limit"] == 200
    assert "recursion_limit" not in config["configurable"]


def test_the_default_round_cap_clears_a_real_analysis(runner, graph):
    """A measured single-pass run on the current model needed 15 rounds; the
    old default of 12 killed work that was going fine."""
    graph.results = [interrupt_result(EXECUTE_ACTION)] * 20 + [ai_result("done")]
    result = run_to_completion(runner, THREAD, "analyse this", AlwaysApproves())
    assert result["messages"][-1].content == "done"


def test_a_stuck_run_gives_up(runner, graph):
    """Money, not patience: an agent that keeps asking must be stopped."""
    graph.results = [interrupt_result(EXECUTE_ACTION)] * 20
    with pytest.raises(TooManyApprovals):
        run_to_completion(runner, THREAD, "analyse this", AlwaysApproves(), max_rounds=3)
    assert len(graph.invocations) == 4          # the start plus three resumes
