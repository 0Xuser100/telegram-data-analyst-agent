"""Where "still working" news goes.

An adapter, so the runner can announce a slow run without knowing whether it is
talking to a chat, a terminal, or nothing at all.
"""

from typing import Protocol


class Progress(Protocol):
    def busy(self) -> None: ...
    def note(self, message: str) -> None: ...


class SilentProgress:
    """For the terminal, which shows its own prompts."""

    def busy(self) -> None:
        pass

    def note(self, message: str) -> None:
        pass


class ChatProgress:
    """One chat: a typing dot first, then a short notice if it drags on."""

    def __init__(self, client, chat_id: int):
        self._client = client
        self._chat_id = chat_id

    def busy(self) -> None:
        self._client.send_typing(self._chat_id)

    def note(self, message: str) -> None:
        self._client.send_message(self._chat_id, message)
