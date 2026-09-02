"""Whole conversations through the real router, with only the graph and the
network faked. These are the tests that would have caught the reported bugs.
"""

import os

import pytest
from langchain_core.messages import AIMessage

from conftest import (
    ALLOWED_CHAT,
    EXECUTE_ACTION,
    WRITE_FILE_ACTION,
    ai_result,
    callback_update,
    document_update,
    interrupt_result,
    text_update,
)


def approve(router):
    router.handle_callback(callback_update("approve"))


# --------------------------------------------------------------------------
# the full analysis, gate by gate
# --------------------------------------------------------------------------

def test_upload_two_approvals_then_a_chart(router, client, graph, clean_output):
    # 1. the upload pauses on write_file
    graph.results = [interrupt_result(WRITE_FILE_ACTION)]
    router.handle_message(document_update())

    assert client.texts[0].startswith("Downloading")
    assert "fresh start" in client.texts[1]
    assert "Write a file" in client.cards[-1]

    # 2. approving it pauses again, on execute
    graph.results = [interrupt_result(EXECUTE_ACTION)]
    approve(router)
    assert "Run a command" in client.cards[-1]

    # 3. approving that finishes the run and produces the figure
    def run_and_answer(payload, config=None):
        open(os.path.join(clean_output, "dashboard.png"), "wb").close()
        return ai_result("**Revenue $840 over 5 days**\n\n- Widget A leads at 50.6%")

    graph.invoke = run_and_answer
    approve(router)

    assert client.said("Revenue $840")
    assert len(client.photos) == 1
    assert not any("output/" in text for text in client.texts)   # no paths
    assert not client.said("does not exist")


def test_rejecting_a_write_ends_the_run_cleanly(router, client, graph):
    graph.results = [interrupt_result(WRITE_FILE_ACTION)]
    router.handle_message(document_update())

    graph.results = [ai_result("Understood — I did not write anything.")]
    router.handle_callback(callback_update("reject"))

    assert client.said("rejected")
    assert client.said("did not write anything")
    assert client.photos == []


def test_inspecting_a_request_before_approving_it(router, client, graph):
    graph.results = [interrupt_result(WRITE_FILE_ACTION)]
    router.handle_message(document_update())

    router.handle_callback(callback_update("details"))          # 🔍
    assert "import pandas" in client.cards[-1]

    graph.results = [ai_result("done")]                         # card still live
    approve(router)
    assert client.said("done")


# --------------------------------------------------------------------------
# conversation shape
# --------------------------------------------------------------------------

def test_a_greeting_costs_one_message_and_one_turn(router, client, graph):
    graph.results = [ai_result("Hello! How can I help?")]
    router.handle_message(text_update("hi"))

    assert client.texts == ["Hello! How can I help?"]
    assert len(graph.invocations) == 1


def test_follow_up_questions_reuse_the_upload_conversation(router, client, graph):
    """The printed results live there, which is what makes a follow-up
    answerable without re-running anything."""
    graph.results = [ai_result("**Revenue $840**"), ai_result("Widget C, at $30 per unit.")]
    router.handle_message(document_update())
    router.handle_message(text_update("which product earns most per unit?"))

    assert len(set(graph.threads)) == 1
    assert client.said("Widget C")


def test_a_second_upload_forgets_the_first(router, graph):
    graph.results = [ai_result("first file"), ai_result("second file")]
    router.handle_message(document_update(file_name="one.csv"))
    router.handle_message(document_update(file_name="two.csv"))
    assert graph.threads == [f"{ALLOWED_CHAT}-1", f"{ALLOWED_CHAT}-2"]


def test_new_abandons_a_stuck_approval(router, client, graph):
    graph.results = [interrupt_result(EXECUTE_ACTION)]
    router.handle_message(text_update("analyse this"))

    router.handle_message(text_update("do something else"))     # refused
    assert client.said("approval waiting")

    router.handle_message(text_update("/new"))
    graph.results = [ai_result("fresh start")]
    router.handle_message(text_update("hello again"))
    assert client.said("fresh start")


def test_an_analysis_that_produced_no_chart_is_called_out(router, client, graph, clean_output):
    """The model sometimes answers without ever running the script. Sending
    nothing at all would look like a bug in the bot.

    The check is structural rather than word-based: the reply voice no longer
    says "saved" or "created", so looking for those words would miss this."""
    reply = AIMessage("**Revenue $840**\n\nWidget A leads at 50.6%.")
    reply.tool_calls = [{"name": "execute", "args": {}, "id": "1"}]
    graph.results = [{"messages": [reply]}]
    router.handle_message(text_update("analyse the sales file"))

    assert client.photos == []
    assert client.said("produced no chart")


# --------------------------------------------------------------------------
# resilience
# --------------------------------------------------------------------------

def test_a_graph_error_reaches_the_caller(router, graph):
    """The polling loop turns this into a message; the router lets it out."""
    graph.error = RuntimeError("model exploded")
    with pytest.raises(RuntimeError):
        router.handle_message(text_update("analyse this"))


def test_a_restart_mid_approval_keeps_the_button_working(router, client, graph, threads):
    """Pending interrupts are read from the checkpointer, not memory."""
    graph.results = [interrupt_result(EXECUTE_ACTION)]
    router.handle_message(text_update("analyse this"))

    # A "restart": the router and delivery are rebuilt, the store is not.
    from analyst.conversation.delivery import ResultDelivery
    from analyst.plumbing.artifacts import ArtifactCollector
    from analyst.conversation.router import UpdateRouter
    from analyst.agent.runner import AgentRunner
    from analyst.agent.prompts import UPLOADED_FILE_TASK

    rebuilt = UpdateRouter(
        client=client,
        runner=AgentRunner(graph, announce_after=0.05),
        threads=threads,
        delivery=ResultDelivery(client, ArtifactCollector("./output")),
        allowed_chats={ALLOWED_CHAT},
        upload_task_template=UPLOADED_FILE_TASK,
    )

    graph.results = [ai_result("resumed and finished")]
    rebuilt.handle_callback(callback_update("approve"))
    assert client.said("resumed and finished")
