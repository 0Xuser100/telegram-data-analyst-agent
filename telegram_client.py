"""The Telegram Bot API, and nothing else.

Knows how to talk HTTP to Telegram. Knows nothing about agents, approvals or
charts, so everything above it can be tested without a network.

`requests` rather than python-telegram-bot: the agent's `invoke` blocks anyway,
so an async SDK would buy nothing.
"""

import os

import requests

from formatting import chunk_text, strip_html, to_html

# sendPhoto's documented ceiling; larger files must go as documents.
PHOTO_MAX_BYTES = 10 * 1024 * 1024


class TelegramError(RuntimeError):
    pass


class TelegramClient:
    """One bot's connection to Telegram."""

    def __init__(self, token: str, poll_timeout: int = 30):
        self._token = token
        self._poll_timeout = poll_timeout
        self._api = f"https://api.telegram.org/bot{token}"

    # -- plumbing ----------------------------------------------------------

    def _call(self, method: str, timeout: int = 60, **payload) -> dict:
        resp = requests.post(f"{self._api}/{method}", json=payload, timeout=timeout)
        body = resp.json()
        if not body.get("ok"):
            raise TelegramError(f"{method} failed: {body.get('description', body)}")
        return body["result"]

    def _upload(self, chat_id: int, method: str, field: str, path: str, caption: str) -> None:
        with open(path, "rb") as fh:
            resp = requests.post(
                f"{self._api}/{method}",
                data={"chat_id": chat_id, "caption": caption[:1024]},
                files={field: fh},
                timeout=180,
            )
        body = resp.json()
        if not body.get("ok"):
            raise TelegramError(f"{method} failed: {body.get('description', body)}")

    # -- receiving ---------------------------------------------------------

    def get_updates(self, offset: int) -> list[dict]:
        """Long-poll: Telegram holds the request open until something arrives.

        Not via `_call` because the HTTP timeout must outlast the poll.
        """
        resp = requests.post(
            f"{self._api}/getUpdates",
            json={
                "offset": offset,
                "timeout": self._poll_timeout,
                "allowed_updates": ["message", "callback_query"],
            },
            timeout=self._poll_timeout + 10,
        )
        body = resp.json()
        if not body.get("ok"):
            raise TelegramError(f"getUpdates failed: {body.get('description', body)}")
        return body["result"]

    def download_file(self, file_id: str, dest_dir: str, filename: str) -> str:
        """Fetch an uploaded document. `getFile` returns a path on the /file/
        host, a different base URL, and bots may download at most 20 MB."""
        remote = self._call("getFile", file_id=file_id)["file_path"]
        url = f"https://api.telegram.org/file/bot{self._token}/{remote}"
        resp = requests.get(url, timeout=180)
        resp.raise_for_status()

        os.makedirs(dest_dir, exist_ok=True)
        local = os.path.join(dest_dir, filename)
        with open(local, "wb") as fh:
            fh.write(resp.content)
        return local

    # -- sending -----------------------------------------------------------

    def send_message(self, chat_id: int, text: str, markdown: bool = True) -> None:
        """Send text, rendering Markdown where Telegram allows it.

        Falls back to plain text if the markup is rejected: an unparseable reply
        must still reach the user.
        """
        for chunk in chunk_text(text or "(empty reply)"):
            if markdown:
                try:
                    self._call(
                        "sendMessage",
                        chat_id=chat_id,
                        text=to_html(chunk),
                        parse_mode="HTML",
                        link_preview_options={"is_disabled": True},
                    )
                    continue
                except TelegramError as exc:
                    print(f"[html] falling back to plain text: {exc}")
            self._call("sendMessage", chat_id=chat_id, text=chunk)

    def send_html(self, chat_id: int, html_text: str, keyboard: list | None = None) -> None:
        """Send text that is already HTML, optionally with inline buttons."""
        payload = {"chat_id": chat_id, "text": html_text, "parse_mode": "HTML"}
        if keyboard is not None:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        try:
            self._call("sendMessage", **payload)
        except TelegramError as exc:              # markup must never block HITL
            print(f"[html] falling back to plain text: {exc}")
            payload["text"] = strip_html(html_text)
            payload.pop("parse_mode")
            self._call("sendMessage", **payload)

    def send_photo(self, chat_id: int, path: str, caption: str | None = None) -> None:
        """sendPhoto is capped at 10 MB, so bigger files go as a document
        instead of failing."""
        caption = caption or ""
        if os.path.getsize(path) > PHOTO_MAX_BYTES:
            self._upload(chat_id, "sendDocument", "document", path, caption)
        else:
            self._upload(chat_id, "sendPhoto", "photo", path, caption)

    def send_document(self, chat_id: int, path: str, caption: str | None = None) -> None:
        self._upload(chat_id, "sendDocument", "document", path, caption or "")

    def send_typing(self, chat_id: int) -> None:
        """Instant feedback that the message landed, which is what lets the
        "Working on it…" notice wait for runs that are actually slow."""
        try:
            self._call("sendChatAction", chat_id=chat_id, action="typing", timeout=10)
        except Exception as exc:                  # cosmetic only, never fatal
            print(f"[typing] {exc}")

    def answer_callback(self, callback_query_id: str, text: str = "") -> None:
        """Clears the spinner on a tapped inline button."""
        self._call("answerCallbackQuery",
                   callback_query_id=callback_query_id, text=text[:200])
