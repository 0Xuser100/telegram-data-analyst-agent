"""Turning a graph result into chat messages.

One job: decide what the user sees. Either the next approval card, or the final
reply plus whatever figures the run produced.
"""

import os

from analyst.conversation.approvals import TelegramApprover, actions_in
from analyst.plumbing.artifacts import ArtifactCollector
from analyst.plumbing.telegram_client import MEDIA_GROUP_MAX, PHOTO_MAX_BYTES


def _batched(items: list, size: int) -> list[list]:
    return [items[start:start + size] for start in range(0, len(items), size)]

NO_TEXT_REPLY = "Done, but the agent produced no text reply."


def ran_an_analysis(result: dict) -> bool:
    """True when THIS turn executed something.

    The hallucinated-chart check keys off this rather than off the words in the
    reply: a greeting owes no figures, an analysis owes at least two.

    Scoped to the messages after the last human turn, because `result` is the
    whole checkpointed thread. Reading all of it would mean that once a
    conversation had ever run a script, every later reply was expected to carry
    a figure -- including the follow-ups the prompt explicitly tells the agent
    not to redraw for.
    """
    messages = result.get("messages", []) or []
    for index in range(len(messages) - 1, -1, -1):
        if type(messages[index]).__name__ == "HumanMessage":
            messages = messages[index + 1:]
            break
    for message in messages:
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
        """Upload the figures, then warn if none arrived from a real analysis.

        Several figures go as one group, so an answer is one notification
        rather than five. No caption: every figure carries its own title, and a
        caption under it is a second title in a smaller font.
        """
        paths = self._artifacts.new_images(chat_id, text)
        grouped = [p for p in paths if self._fits_in_a_group(p)]
        oversized = [p for p in paths if p not in grouped]

        sent = 0
        # In batches, because Telegram caps a group at ten and silently
        # dropping the eleventh figure would also inflate the count feeding
        # the missing-chart check below.
        for batch in _batched(grouped, MEDIA_GROUP_MAX):
            if len(batch) == 1:
                sent += self._send_each(chat_id, batch)
                continue
            try:
                self._client.send_media_group(chat_id, batch)
                sent += len(batch)
            except Exception as exc:            # fall back rather than lose them
                print(f"[image] group send failed, sending singly: {exc}")
                sent += self._send_each(chat_id, batch)

        sent += self._send_each(chat_id, oversized)

        warning = self._artifacts.warning_for(text, sent, analysed)
        if warning:
            self._client.send_message(chat_id, warning)

    def _send_each(self, chat_id: int, paths: list[str]) -> int:
        """One at a time. A failed upload must never cost the user the text."""
        sent = 0
        for path in paths:
            try:
                self._client.send_photo(chat_id, path)
                sent += 1
            except Exception as exc:
                print(f"[image] failed to send {path}: {exc}")
                self._client.send_message(
                    chat_id, f"Couldn't upload {os.path.basename(path)}.")
        return sent

    @staticmethod
    def _fits_in_a_group(path: str) -> bool:
        """A media group carries photos only, and send_photo silently falls
        back to a document above the photo ceiling. Such a file has to travel
        on its own."""
        try:
            return os.path.getsize(path) <= PHOTO_MAX_BYTES
        except OSError:
            return False
