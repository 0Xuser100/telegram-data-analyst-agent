"""What the user sees: the final reply, the figures, or the next approval card."""

import os

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from conftest import EXECUTE_ACTION, WRITE_FILE_ACTION, interrupt_result
from delivery import ResultDelivery, final_text

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


def test_a_hallucinated_chart_is_called_out(delivery, client, clean_output):
    delivery.start_run(CHAT)
    delivery.deliver(CHAT, {"messages": [AIMessage("A chart has been saved.")]})
    assert client.photos == []
    assert client.said("no image was produced")


def test_an_honest_reply_gets_no_warning(delivery, client, clean_output):
    delivery.start_run(CHAT)
    delivery.deliver(CHAT, {"messages": [AIMessage("Nothing worth plotting here.")]})
    assert len(client.texts) == 1


def test_forget_clears_what_was_already_sent(delivery, client, clean_output):
    delivery.start_run(CHAT)
    open(os.path.join(clean_output, "plot.png"), "wb").close()
    delivery.send_images(CHAT, "")
    delivery.forget(CHAT)
    delivery.start_run(CHAT)
    delivery.send_images(CHAT, "")
    assert len(client.photos) == 2
