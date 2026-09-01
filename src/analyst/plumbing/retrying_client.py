"""A Telegram client that retries a dropped connection.

Decorator: same surface as `TelegramClient`, wrapped around one. The bot runs
for days on a home connection, and a single dropped packet should not turn into
a lost approval card. Nothing above it changes — it is passed in where the plain
client was.
"""

import time

import requests

TRANSIENT = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)

# Sending is worth retrying; long-polling is not — the polling loop already
# loops, and a retry there would just delay the next poll.
RETRIED_METHODS = (
    "send_message", "send_html", "send_photo", "send_document",
    "answer_callback", "download_file",
)


class RetryingClient:
    """Retries transient network failures, then gives up and re-raises."""

    def __init__(self, client, attempts: int = 3, backoff: float = 1.0, sleep=time.sleep):
        self._client = client
        self._attempts = max(1, attempts)
        self._backoff = backoff
        self._sleep = sleep

    def __getattr__(self, name: str):
        """Everything not retried passes straight through: get_updates,
        send_typing, and anything added to the client later."""
        target = getattr(self._client, name)
        if name not in RETRIED_METHODS or not callable(target):
            return target

        def retrying(*args, **kwargs):
            last: Exception | None = None
            for attempt in range(self._attempts):
                try:
                    return target(*args, **kwargs)
                except TRANSIENT as exc:
                    last = exc
                    print(f"[retry] {name} failed ({exc}); "
                          f"attempt {attempt + 1}/{self._attempts}")
                    if attempt + 1 < self._attempts:
                        self._sleep(self._backoff * (attempt + 1))
            raise last

        return retrying
