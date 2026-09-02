"""Turning a graph result into chat messages.

One job: decide what the user sees. Either the next approval card, or the final
reply plus whatever figures the run produced.
"""

import os

from analyst.conversation.approvals import TelegramApprover, actions_in
from analyst.plumbing.artifacts import ArtifactCollector

NO_TEXT_REPLY = "Done, but the agent produced no text reply."


def ran_an_analysis(result: dict) -> bool:
    """True when the run actually executed something.

    The hallucinated-chart check keys off this rather than off the words in the
    reply: a greeting owes no figures, an analysis owes at least two.
    """
    for message in result.get("messages", []) or []:
        for call in (getattr(message, "tool_calls", None) or []):
            if call.get("name") == "execute":
                return True
    return False


def final_text(result: dict) -> str:
    """The last AI message. The full history is dozens of messages after a few
    tool calls, so only the answer is relayed."""
    for message in reversed(result.get("messages", []) or []):
        if type(message).__name__ != "AIMessage":
            continue
        content = message.content
        if not isinstance(content, str):            # content blocks
            content = "\n".join(block.get("text", "") for block in content
                                if isinstance(block, dict))
        if content and content.strip():
            return content
    return ""


class ResultDelivery:
    """Sends what a finished or paused run produced."""

    def __init__(self, client, artifacts: ArtifactCollector):
        self._client = client
        self._artifacts = artifacts

    def start_run(self, chat_id: int) -> None:
        """From now on, a new figure in output/ belongs to this answer."""
        self._artifacts.start_run(chat_id)

    def forget(self, chat_id: int) -> None:
        """Called when a conversation restarts, so old figures can be re-sent."""
        self._artifacts.forget(chat_id)

    def deliver(self, chat_id: int, result: dict) -> None:
        """Ask for the next approval, or send the answer."""
        actions = actions_in(result)
        if actions:
            TelegramApprover(self._client, chat_id).ask(actions)
            return

        text = final_text(result)
        self._client.send_message(chat_id, text or NO_TEXT_REPLY)
        self.send_images(chat_id, text, analysed=ran_an_analysis(result))

    def send_images(self, chat_id: int, text: str, analysed: bool = False) -> None:
        """Upload the figures, then warn if the reply claimed one that is not
        there. No caption: the reply never names files, and repeating the
        filename under the picture would add that noise back."""
        sent = 0
        for path in self._artifacts.new_images(chat_id, text):
            try:
                self._client.send_photo(chat_id, path)
                sent += 1
            except Exception as exc:                # a failed upload must not
                print(f"[image] failed to send {path}: {exc}")   # lose the text
                self._client.send_message(
                    chat_id, f"Couldn't upload {os.path.basename(path)}.")

        warning = self._artifacts.warning_for(text, sent, analysed)
        if warning:
            self._client.send_message(chat_id, warning)
