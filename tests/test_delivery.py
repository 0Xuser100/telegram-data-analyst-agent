"""What the user sees: the final reply, the figures, or the next approval card."""

import os

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from conftest import EXECUTE_ACTION, WRITE_FILE_ACTION, interrupt_result
from analyst.conversation.delivery import ResultDelivery, final_text

CHAT = 555


# --------------------------------------------------------------------------
# final_text
# --------------------------------------------------------------------------

def test_only_the_last_ai_message_is_relayed():
    """After a few tool calls the history runs to dozens of messages."""
    text = final_text({"messages": [
        HumanMessage("analyse this"),
        AIMessage("thinking out loud"),
        ToolMessage("stdout", tool_call_id="1"),
        AIMessage("**Revenue $840**"),
    ]})
    assert text == "**Revenue $840**"


def test_content_blocks_are_flattened():
    text = final_text({"messages": [AIMessage(
        [{"type": "text", "text": "part one"}, {"type": "text", "text": "part two"}])]})
    assert text == "part one\npart two"


def test_empty_ai_messages_are_skipped():
    assert final_text({"messages": [AIMessage("real answer"), AIMessage("")]}) == "real answer"


def test_no_ai_message_gives_an_empty_string():
    assert final_text({"messages": []}) == ""
    assert final_text({}) == ""


# --------------------------------------------------------------------------
# deliver
# --------------------------------------------------------------------------

def test_a_finished_run_sends_the_answer(delivery, client):
    delivery.deliver(CHAT, {"messages": [AIMessage("done")]})
    assert client.texts == ["done"]
    assert client.html == []


def test_a_silent_result_still_gets_an_answer(delivery, client):
    delivery.deliver(CHAT, {"messages": []})
    assert client.said("no text reply")


def test_a_paused_run_asks_for_approval(delivery, client):
    delivery.deliver(CHAT, interrupt_result(WRITE_FILE_ACTION))
    assert client.texts == []
    assert len(client.html) == 1
    assert "Write a file" in client.cards[0]


def test_several_pending_actions_each_get_a_card(delivery, client):
    delivery.deliver(CHAT, interrupt_result(WRITE_FILE_ACTION, EXECUTE_ACTION))
    assert len(client.html) == 2


def test_an_approval_card_carries_buttons(delivery, client):
    delivery.deliver(CHAT, interrupt_result(EXECUTE_ACTION, allowed=("approve",)))
    keyboard = client.html[0][2]
    assert [b["callback_data"] for b in keyboard[0]] == ["approve"]


# --------------------------------------------------------------------------
# images
# --------------------------------------------------------------------------

def test_the_figure_is_uploaded_with_the_answer(delivery, client, clean_output):
    delivery.start_run(CHAT)
    open(os.path.join(clean_output, "plot.png"), "wb").close()
    delivery.deliver(CHAT, {"messages": [AIMessage("**Revenue $840**")]})

    assert client.texts == ["**Revenue $840**"]
    assert len(client.photos) == 1


def test_photos_are_sent_without_a_filename_caption(delivery, client, clean_output):
    """The summary deliberately never names the file, so echoing it under the
    picture would put the noise straight back."""
    delivery.start_run(CHAT)
    open(os.path.join(clean_output, "plot.png"), "wb").close()
    delivery.send_images(CHAT, "**Revenue $840**")
    assert client.photos == [(CHAT, os.path.join(clean_output, "plot.png"))]
    assert client.texts == []


def test_a_failed_upload_does_not_lose_the_answer(delivery, client, clean_output):
    delivery.start_run(CHAT)
    open(os.path.join(clean_output, "plot.png"), "wb").close()
    client.photo_error = RuntimeError("413 too large")

    delivery.deliver(CHAT, {"messages": [AIMessage("**Revenue $840**")]})

    assert client.said("Revenue $840")
    assert client.said("couldn't upload")


def analysed(text):
    """A result that ran a script, which is what makes figures owed."""
    reply = AIMessage("")
    reply.tool_calls = [{"name": "execute", "args": {}, "id": "1"}]
    return {"messages": [reply, AIMessage(text)]}


def test_an_analysis_that_produced_no_chart_is_called_out(delivery, client, clean_output):
    delivery.start_run(CHAT)
    delivery.deliver(CHAT, analysed("Heart disease leads at 33.7%."))
    assert client.photos == []
    assert client.said("produced no chart")


def test_a_greeting_is_not_asked_for_a_chart(delivery, client, clean_output):
    """No execute call, so nothing was analysed and nothing is owed."""
    delivery.start_run(CHAT)
    delivery.deliver(CHAT, {"messages": [AIMessage("Hello! Send me a CSV.")]})
    assert len(client.texts) == 1


def test_forget_clears_what_was_already_sent(delivery, client, clean_output):
    delivery.start_run(CHAT)
    open(os.path.join(clean_output, "plot.png"), "wb").close()
    delivery.send_images(CHAT, "")
    delivery.forget(CHAT)
    delivery.start_run(CHAT)
    delivery.send_images(CHAT, "")
    assert len(client.photos) == 2


# --------------------------------------------------------------------------
# several figures
# --------------------------------------------------------------------------

def figure(directory, name, size=1024):
    path = os.path.join(directory, name)
    with open(path, "wb") as fh:
        fh.write(b"x" * size)
    return path


def test_several_figures_arrive_as_one_block(delivery, client, clean_output):
    """Five separate uploads means five notifications, which buries the
    message they belong to."""
    delivery.start_run(CHAT)
    for name in ("01_quality.png", "02_by_cause.png", "03_trend.png"):
        figure(clean_output, name)
    delivery.deliver(CHAT, analysed("Heart disease leads."))

    assert len(client.groups) == 1
    assert client.photos == []


def test_the_block_is_in_reading_order(delivery, client, clean_output):
    """Written out of order on purpose: the number is what fixes the order,
    not the filesystem timestamp."""
    delivery.start_run(CHAT)
    for name in ("03_trend.png", "01_quality.png", "02_by_cause.png"):
        figure(clean_output, name)
    delivery.deliver(CHAT, analysed("Heart disease leads."))

    names = [os.path.basename(p) for p in client.groups[0][1]]
    assert names == ["01_quality.png", "02_by_cause.png", "03_trend.png"]


def test_a_single_figure_does_not_need_a_block(delivery, client, clean_output):
    delivery.start_run(CHAT)
    figure(clean_output, "01_quality.png")
    delivery.deliver(CHAT, analysed("Not much here."))

    assert client.groups == []
    assert len(client.photos) == 1


def test_a_failed_block_falls_back_to_one_at_a_time(delivery, client, clean_output):
    """Losing three figures because a group call failed would be worse than
    three notifications."""
    client.group_error = RuntimeError("group send failed")
    delivery.start_run(CHAT)
    for name in ("01_a.png", "02_b.png", "03_c.png"):
        figure(clean_output, name)
    delivery.deliver(CHAT, analysed("Heart disease leads."))

    assert len(client.photos) == 3
    assert client.said("Heart disease leads")


def test_an_oversized_figure_travels_on_its_own(delivery, client, clean_output):
    """A media group carries photos only, and send_photo falls back to a
    document above the ceiling -- such a file cannot ride in the group."""
    from analyst.plumbing.telegram_client import PHOTO_MAX_BYTES
    delivery.start_run(CHAT)
    figure(clean_output, "01_a.png")
    figure(clean_output, "02_b.png")
    figure(clean_output, "03_huge.png", size=PHOTO_MAX_BYTES + 1)
    delivery.deliver(CHAT, analysed("Heart disease leads."))

    grouped = [os.path.basename(p) for p in client.groups[0][1]]
    assert grouped == ["01_a.png", "02_b.png"]
    assert [os.path.basename(p) for _, p in client.photos] == ["03_huge.png"]


def test_a_delivered_block_counts_as_images_for_the_warning(delivery, client, clean_output):
    delivery.start_run(CHAT)
    figure(clean_output, "01_a.png")
    figure(clean_output, "02_b.png")
    delivery.deliver(CHAT, analysed("Heart disease leads."))

    assert not client.said("produced no chart")
