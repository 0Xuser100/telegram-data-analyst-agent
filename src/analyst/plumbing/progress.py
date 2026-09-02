"""Where "still working" news goes.

An adapter, so the runner can announce a slow run without knowing whether it is
talking to a chat, a terminal, or nothing at all.
"""

import time
from typing import Protocol


class Progress(Protocol):
    def busy(self) -> None: ...
    def note(self, message: str) -> None: ...
    def step(self, message: str) -> None: ...
    def done(self) -> None: ...


class SilentProgress:
    """For the terminal, which shows its own prompts."""

    def busy(self) -> None:
        pass

    def note(self, message: str) -> None:
        pass

    def step(self, message: str) -> None:
        pass

    def done(self) -> None:
        pass


# Telegram rate-limits edits, and a run makes far more steps than a reader can
# follow. Skip an update rather than queue it.
EDIT_INTERVAL = 3.0


class ChatProgress:
    """One chat: a typing dot, a notice if it drags on, then a status line.

    The status line is a single message edited in place. An analysis makes
    dozens of tool calls, so one message per step would be a denial of service
    on the conversation; and after the approval cards stopped appearing, this
    is the only sign the bot is alive.

    Every call here is best-effort. A status line that fails to update is a
    cosmetic problem; a run that dies because of one is not.
    """

    def __init__(self, client, chat_id: int, clock=time.monotonic):
        self._client = client
        self._chat_id = chat_id
        self._clock = clock
        self._message_id: int | None = None
        self._last_text: str | None = None
        self._last_edit = 0.0

    def busy(self) -> None:
        self._client.send_typing(self._chat_id)

    def note(self, message: str) -> None:
        self._client.send_message(self._chat_id, message)

    def step(self, message: str) -> None:
        """Show what the agent is working on now."""
        if not message or message == self._last_text:
            return
        now = self._clock()
        if self._message_id is not None and now - self._last_edit < EDIT_INTERVAL:
            return
        try:
            if self._message_id is None:
                self._message_id = self._client.send_message(
                    self._chat_id, message, markdown=False)
            else:
                self._client.edit_message(self._chat_id, self._message_id, message)
        except Exception as exc:                # never take the run down
            print(f"[progress] {exc}")
            return
        self._last_text = message
        self._last_edit = now

    def done(self) -> None:
        """Clear the status line, so a finished chat is just the answer."""
        if self._message_id is None:
            return
        try:
            self._client.delete_message(self._chat_id, self._message_id)
        except Exception as exc:                # a leftover line is survivable
            print(f"[progress] {exc}")
        self._message_id = None
        self._last_text = None
