"""The terminal front-end: the approval loop that replaced the broken one.

The old version called `result.interrupts` and `result.value["messages"]` on a
plain dict, so it raised AttributeError at the first approval.

Marked `slow` because importing it builds the graph. No model call is made.
"""

import pytest

pytestmark = pytest.mark.slow

from conftest import (  # noqa: E402
    EXECUTE_ACTION,
    WRITE_FILE_ACTION,
    FakeAgent,
    ai_result,
    interrupt_result,
)

THREAD = "cli-thread"


@pytest.fixture(scope="module")
def cli():
    import main
    return main


@pytest.fixture
def approver():
    """Approves everything, silently."""
    from approvals import ConsoleApprover
    return ConsoleApprover(prompt=lambda _: "y", out=lambda *args: None)


@pytest.fixture
def rejecter():
    from approvals import ConsoleApprover
    return ConsoleApprover(prompt=lambda _: "n", out=lambda *args: None)


def runner_for(graph):
    from runner import AgentRunner
    return AgentRunner(graph, announce_after=0)


# --------------------------------------------------------------------------
# the loop
# --------------------------------------------------------------------------

def test_a_clean_run_returns_the_answer(cli, approver):
    graph = FakeAgent()
    graph.results = [ai_result("**Revenue $840**")]
    assert cli.run_analysis(runner_for(graph), approver, THREAD) == "**Revenue $840**"


def test_two_approvals_then_the_answer(cli, approver):
    """Write, then run, then the summary — the normal shape of a real run."""
    graph = FakeAgent()
    graph.results = [
        interrupt_result(WRITE_FILE_ACTION),
        interrupt_result(EXECUTE_ACTION),
        ai_result("**Revenue $840**"),
    ]
    answer = cli.run_analysis(runner_for(graph), approver, THREAD)
    assert answer == "**Revenue $840**"
    assert len(graph.invocations) == 3


def test_the_decision_is_sent_back(cli, approver):
    graph = FakeAgent()
    graph.results = [interrupt_result(WRITE_FILE_ACTION), ai_result("done")]
    cli.run_analysis(runner_for(graph), approver, THREAD)
    assert graph.invocations[-1][0].resume == {"decisions": [{"type": "approve"}]}


def test_rejecting_is_passed_through(cli, rejecter):
    graph = FakeAgent()
    graph.results = [interrupt_result(EXECUTE_ACTION), ai_result("understood")]
    cli.run_analysis(runner_for(graph), rejecter, THREAD)
    decisions = graph.invocations[-1][0].resume["decisions"]
    assert decisions[0]["type"] == "reject"


def test_one_decision_per_pending_action(cli, approver):
    graph = FakeAgent()
    graph.results = [interrupt_result(WRITE_FILE_ACTION, EXECUTE_ACTION), ai_result("done")]
    cli.run_analysis(runner_for(graph), approver, THREAD)
    assert graph.invocations[-1][0].resume["decisions"] == [{"type": "approve"}] * 2


def test_a_stuck_loop_gives_up(cli, approver):
    """A run that keeps asking must not spend money forever."""
    graph = FakeAgent()
    graph.results = [interrupt_result(EXECUTE_ACTION)] * 50
    with pytest.raises(SystemExit, match="Still asking for approval"):
        cli.run_analysis(runner_for(graph), approver, THREAD)


def test_a_silent_result_gives_an_empty_answer(cli, approver):
    graph = FakeAgent()
    graph.results = [{"messages": []}]
    assert cli.run_analysis(runner_for(graph), approver, THREAD) == ""


# --------------------------------------------------------------------------
# the task it sends
# --------------------------------------------------------------------------

def test_the_prompt_is_filled_in(cli, approver):
    graph = FakeAgent()
    graph.results = [ai_result("done")]
    cli.run_analysis(runner_for(graph), approver, THREAD)

    task = graph.invocations[0][0]["messages"][0].content
    assert cli.DATA_FILE in task
    assert cli.PLOT_NAME in task


def test_the_run_uses_the_interpreter_it_is_running_under(cli, approver):
    """'python' may not be on PATH; sys.executable always is."""
    import sys
    graph = FakeAgent()
    graph.results = [ai_result("done")]
    cli.run_analysis(runner_for(graph), approver, THREAD)
    assert sys.executable in graph.invocations[0][0]["messages"][0].content


def test_the_thread_is_the_one_it_was_given(cli, approver):
    graph = FakeAgent()
    graph.results = [ai_result("done")]
    cli.run_analysis(runner_for(graph), approver, THREAD)
    assert graph.threads == [THREAD]


# --------------------------------------------------------------------------
# main()
# --------------------------------------------------------------------------

def test_main_writes_the_sample_data_and_prints_the_answer(cli, monkeypatch, capsys):
    graph = FakeAgent()
    graph.results = [ai_result("**Revenue $840**")]
    written = []

    monkeypatch.setattr(cli, "agent", graph)
    monkeypatch.setattr(cli, "ensure_sample_data", lambda: written.append(True))
    monkeypatch.setattr(cli, "ConsoleApprover",
                        lambda: __import__("approvals").ConsoleApprover(
                            prompt=lambda _: "y", out=lambda *a: None))

    cli.main()

    assert written == [True]
    assert "Revenue $840" in capsys.readouterr().out


def test_main_says_so_when_there_is_no_reply(cli, monkeypatch, capsys):
    graph = FakeAgent()
    graph.results = [{"messages": []}]
    monkeypatch.setattr(cli, "agent", graph)
    monkeypatch.setattr(cli, "ensure_sample_data", lambda: None)

    cli.main()

    assert "no text reply" in capsys.readouterr().out
