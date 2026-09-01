"""Routing: which incoming update does what."""

import pytest

from conftest import (
    ALLOWED_CHAT,
    BLOCKED_CHAT,
    EXECUTE_ACTION,
    WRITE_FILE_ACTION,
    ai_result,
    callback_update,
    document_update,
    interrupt_result,
    text_update,
)


# --------------------------------------------------------------------------
# access control and commands
# --------------------------------------------------------------------------

def test_unknown_chat_is_dropped_silently(router, client, graph):
    """Anyone can find a public bot by username, and this one runs shell
    commands, so an unauthorised chat is not even told it was refused."""
    router.handle_message(text_update("hi", chat_id=BLOCKED_CHAT))
    assert client.messages == []
    assert graph.invocations == []


@pytest.mark.parametrize("command", ["/start", "/help"])
def test_help_is_answered_without_running_the_agent(router, client, graph, command):
    router.handle_message(text_update(command))
    assert client.said("send me a .csv file")
    assert graph.invocations == []


def test_help_mentions_the_details_button(router, client):
    router.handle_message(text_update("/help"))
    assert "🔍" in client.texts[0]


def test_new_starts_a_fresh_conversation(router, client, threads, graph):
    router.handle_message(text_update("/new"))
    assert client.said("fresh conversation")
    assert threads.thread_id(ALLOWED_CHAT) == f"{ALLOWED_CHAT}-1"
    assert graph.invocations == []


def test_a_message_after_new_lands_on_the_new_conversation(router, graph):
    router.handle_message(text_update("/new"))
    router.handle_message(text_update("hello"))
    assert graph.threads == [f"{ALLOWED_CHAT}-1"]


def test_commands_work_even_while_an_approval_is_pending(router, client, graph):
    """/help and /new are checked before the pending guard, so a stuck
    conversation can always be abandoned."""
    graph.results = [interrupt_result(WRITE_FILE_ACTION)]
    router.handle_message(text_update("analyse this"))
    router.handle_message(text_update("/new"))
    assert client.said("fresh conversation")


# --------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------

def test_plain_text_becomes_the_task(router, client, graph):
    graph.results = [ai_result("**Revenue $840**")]
    router.handle_message(text_update("what is the revenue?"))

    payload, _ = graph.invocations[0]
    assert payload["messages"][0]["content"] == "what is the revenue?"
    assert client.texts == ["**Revenue $840**"]


def test_a_greeting_is_answered_in_one_message(router, client, graph):
    """The reported regression: "hi" produced "Working on it…" and then the
    actual reply."""
    graph.results = [ai_result("Hi! What would you like to do?")]
    router.handle_message(text_update("hi"))
    assert client.texts == ["Hi! What would you like to do?"]


def test_pending_approval_blocks_a_new_task(router, client, graph):
    graph.results = [interrupt_result(WRITE_FILE_ACTION)]
    router.handle_message(text_update("analyse this"))
    before = len(graph.invocations)

    router.handle_message(text_update("do something else"))

    assert client.said("approval waiting")
    assert len(graph.invocations) == before


def test_empty_message_gets_a_hint(router, client, graph):
    router.handle_message({"chat": {"id": ALLOWED_CHAT}})
    assert client.said("send me a .csv file")
    assert graph.invocations == []


def test_handle_routes_messages_and_callbacks(router, client, graph):
    graph.results = [ai_result("done")]
    router.handle({"message": text_update("hi")})
    router.handle({"callback_query": callback_update("approve")})
    assert client.said("done")
    assert client.said("no longer pending")     # nothing was paused


def test_handle_ignores_other_update_types(router, client, graph):
    router.handle({"edited_message": text_update("hi")})
    assert client.messages == []
    assert graph.invocations == []


# --------------------------------------------------------------------------
# uploads
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["notes.pdf", "photo.png", "archive.zip", "script.py", "noext"])
def test_unreadable_file_types_are_refused(router, client, graph, name):
    router.handle_message(document_update(file_name=name))
    assert client.said("can't read")
    assert graph.invocations == []


@pytest.mark.parametrize("name", ["a.csv", "a.tsv", "a.json", "a.xlsx", "a.xls", "a.txt"])
def test_readable_file_types_are_accepted(router, graph, name):
    graph.results = [ai_result("done")]
    router.handle_message(document_update(file_name=name))
    assert len(graph.invocations) == 1


def test_oversized_upload_is_refused(router, client, graph):
    router.handle_message(document_update(size=21 * 1024 * 1024))
    assert client.said("20 mb")
    assert graph.invocations == []


def test_download_failure_is_reported_and_stops_the_run(router, client, graph):
    client.download_error = RuntimeError("timeout")
    router.handle_message(document_update())
    assert client.said("couldn't download")
    assert graph.invocations == []


def test_upload_without_a_caption_uses_the_canned_task(router, graph):
    graph.results = [ai_result("done")]
    router.handle_message(document_update())

    task = " ".join(graph.invocations[0][0]["messages"][0]["content"].split())
    assert "./data/sales.csv" in task
    assert "chart-choice rules" in task


def test_a_caption_replaces_the_canned_task(router, graph):
    graph.results = [ai_result("done")]
    router.handle_message(document_update(caption="just count the rows"))

    task = " ".join(graph.invocations[0][0]["messages"][0]["content"].split())
    assert "just count the rows" in task
    assert "./data/sales.csv" in task
    assert "chart-choice rules" not in task


def test_upload_starts_a_new_conversation(router, graph):
    """A new file has nothing to learn from the previous one, and carrying that
    history is what grew one chat to hundreds of thousands of input tokens."""
    graph.results = [ai_result("first"), ai_result("second")]
    router.handle_message(document_update(file_name="one.csv"))
    router.handle_message(document_update(file_name="two.csv"))
    assert graph.threads == [f"{ALLOWED_CHAT}-1", f"{ALLOWED_CHAT}-2"]


def test_follow_up_questions_stay_on_the_upload_conversation(router, graph):
    """The printed results live there, which is what makes a follow-up
    answerable without re-running anything."""
    graph.results = [ai_result("**Revenue $840**"), ai_result("Widget C.")]
    router.handle_message(document_update())
    router.handle_message(text_update("which product earns most per unit?"))
    assert len(set(graph.threads)) == 1


def test_upload_announces_the_fresh_start_without_an_extra_message(router, client, graph):
    graph.results = [ai_result("done")]
    router.handle_message(document_update())
    assert client.texts[0].startswith("Downloading sales.csv")
    assert "fresh start" in client.texts[1]


def test_a_refused_upload_does_not_burn_a_conversation(router, threads):
    router.handle_message(document_update(file_name="bad.pdf"))
    assert threads.generation(ALLOWED_CHAT) == 0


def test_the_upload_lands_in_the_data_directory(router, client, graph):
    graph.results = [ai_result("done")]
    router.handle_message(document_update())
    assert client.downloads[0][1] == "./data"


# --------------------------------------------------------------------------
# buttons
# --------------------------------------------------------------------------

def test_callback_from_an_unknown_chat_is_ignored(router, client):
    router.handle_callback(callback_update("approve", chat_id=BLOCKED_CHAT))
    assert client.messages == []
    assert client.answered == []


def test_the_button_spinner_is_always_cleared(router, client, graph):
    graph.results = [interrupt_result(WRITE_FILE_ACTION)]
    router.handle_message(text_update("analyse this"))
    router.handle_callback(callback_update("approve"))
    assert client.answered == ["CB-1"]


def test_unknown_callback_data_is_reported(router, client, graph):
    router.handle_callback(callback_update("delete-everything"))
    assert client.said("unknown action")
    assert graph.invocations == []


def test_a_stale_button_says_so(router, client):
    """Inline buttons stay tappable forever, so an old card does get tapped."""
    router.handle_callback(callback_update("approve"))
    assert client.said("no longer pending")


def test_approve_resumes_with_one_decision_per_action(router, client, graph):
    graph.results = [interrupt_result(WRITE_FILE_ACTION, EXECUTE_ACTION),
                     ai_result("all done")]
    router.handle_message(text_update("analyse this"))

    router.handle_callback(callback_update("approve"))

    resume_payload = graph.invocations[-1][0]
    assert resume_payload.resume["decisions"] == [{"type": "approve"}] * 2
    assert client.said("approved")
    assert client.said("all done")


def test_reject_tells_the_agent_not_to_retry(router, client, graph):
    graph.results = [interrupt_result(WRITE_FILE_ACTION), ai_result("understood")]
    router.handle_message(text_update("analyse this"))

    router.handle_callback(callback_update("reject"))

    decisions = graph.invocations[-1][0].resume["decisions"]
    assert decisions[0]["type"] == "reject"
    assert "Do not retry" in decisions[0]["message"]
    assert client.said("rejected")


def test_approving_can_lead_to_another_approval(router, client, graph):
    """Approving write_file typically yields an interrupt for execute."""
    graph.results = [interrupt_result(WRITE_FILE_ACTION), interrupt_result(EXECUTE_ACTION)]
    router.handle_message(text_update("analyse this"))
    router.handle_callback(callback_update("approve"))
    assert "Run a command" in client.cards[-1]


def test_details_prints_the_request_without_resuming(router, client, graph):
    """🔍 is not a decision: the card above must stay live."""
    graph.results = [interrupt_result(WRITE_FILE_ACTION)]
    router.handle_message(text_update("analyse this"))
    before = len(graph.invocations)

    router.handle_callback(callback_update("details"))

    assert len(graph.invocations) == before
    assert "import pandas" in client.cards[-1]      # the full args


def test_details_on_a_stale_card_says_so(router, client):
    router.handle_callback(callback_update("details"))
    assert client.said("no longer pending")


def test_resume_uses_the_current_conversation(router, graph, threads):
    threads.start_new(ALLOWED_CHAT)
    graph.results = [interrupt_result(EXECUTE_ACTION), ai_result("done")]
    router.handle_message(text_update("analyse this"))
    router.handle_callback(callback_update("approve"))
    assert set(graph.threads) == {f"{ALLOWED_CHAT}-1"}


# --------------------------------------------------------------------------
# the command registry
# --------------------------------------------------------------------------

def test_every_command_maps_to_a_method(router):
    """A typo in the table would otherwise only show up when someone types it."""
    from analyst.conversation.router import UpdateRouter

    for command, method in UpdateRouter.COMMANDS.items():
        assert command.startswith("/")
        assert callable(getattr(router, method))


def test_a_new_command_needs_only_a_table_entry(router, client, monkeypatch):
    """Open for extension: no new branch in handle_message."""
    seen = []
    monkeypatch.setattr(router, "_command_status", lambda chat_id: seen.append(chat_id),
                        raising=False)
    monkeypatch.setitem(router.COMMANDS, "/status", "_command_status")

    router.handle_message(text_update("/status"))

    assert seen == [ALLOWED_CHAT]


def test_an_unknown_command_is_treated_as_a_task(router, graph):
    """Telegram sends any /word; it becomes the instruction rather than an error."""
    graph.results = [ai_result("I do not know that command")]
    router.handle_message(text_update("/wat"))
    assert graph.invocations[0][0]["messages"][0]["content"] == "/wat"


# --------------------------------------------------------------------------
# one trace per task
# --------------------------------------------------------------------------

def test_a_task_opens_one_trace(router, graph, tracer):
    graph.results = [ai_result("done")]
    router.handle_message(text_update("what is the revenue?"))
    assert [name for _, name in tracer.started] == ["ask: what is the revenue?"]


def test_an_upload_names_the_trace_after_the_file(router, graph, tracer):
    graph.results = [ai_result("done")]
    router.handle_message(document_update(file_name="sales.csv"))
    assert [name for _, name in tracer.started] == ["upload: sales.csv"]


def test_a_long_message_is_shortened_for_the_trace_name(router, graph, tracer):
    graph.results = [ai_result("done")]
    router.handle_message(text_update("please " * 30))
    assert len(tracer.started[0][1]) <= 45


def test_a_finished_task_closes_its_trace(router, graph, tracer):
    graph.results = [ai_result("**Revenue $840**")]
    router.handle_message(text_update("analyse this"))
    assert tracer.finished == [f"{ALLOWED_CHAT}"]


def test_a_paused_task_keeps_its_trace_open(router, graph, tracer):
    """The approvals still have to land inside it."""
    graph.results = [interrupt_result(WRITE_FILE_ACTION)]
    router.handle_message(text_update("analyse this"))
    assert tracer.finished == []


def test_every_invoke_attaches_to_the_task_trace(router, graph, tracer):
    """This is what makes the resumes children instead of new traces."""
    graph.results = [interrupt_result(WRITE_FILE_ACTION), ai_result("done")]
    router.handle_message(text_update("analyse this"))
    router.handle_callback(callback_update("approve"))
    assert len(tracer.attached_to) == 2


def test_the_last_approval_closes_the_trace(router, graph, tracer):
    graph.results = [interrupt_result(EXECUTE_ACTION), ai_result("done")]
    router.handle_message(text_update("analyse this"))
    router.handle_callback(callback_update("approve"))
    assert tracer.finished == [f"{ALLOWED_CHAT}"]


def test_a_middle_approval_does_not_close_it(router, graph, tracer):
    graph.results = [interrupt_result(WRITE_FILE_ACTION), interrupt_result(EXECUTE_ACTION)]
    router.handle_message(text_update("analyse this"))
    router.handle_callback(callback_update("approve"))
    assert tracer.finished == []


def test_new_abandons_the_open_trace(router, graph, tracer):
    """Otherwise a task nobody approved stays open for ever."""
    graph.results = [interrupt_result(EXECUTE_ACTION)]
    router.handle_message(text_update("analyse this"))
    router.handle_message(text_update("/new"))
    assert tracer.abandoned[0][1] == "conversation restarted"


def test_an_upload_closes_the_previous_conversation_trace(router, graph, tracer):
    """A new file switches conversation, so the old trace is closed on the way
    out. (An upload while an approval is pending is refused instead, by the
    guard in handle_message.)"""
    graph.results = [ai_result("first"), ai_result("second")]
    router.handle_message(text_update("analyse this"))
    router.handle_message(document_update())
    assert tracer.abandoned == [(f"{ALLOWED_CHAT}", "conversation restarted")]


def test_the_details_button_does_not_touch_the_trace(router, graph, tracer):
    graph.results = [interrupt_result(WRITE_FILE_ACTION)]
    router.handle_message(text_update("analyse this"))
    before = (len(tracer.attached_to), len(tracer.finished))

    router.handle_callback(callback_update("details"))

    assert (len(tracer.attached_to), len(tracer.finished)) == before
