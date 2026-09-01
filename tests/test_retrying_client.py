"""The retry decorator around the Telegram client.

Same surface as the client it wraps, so everything above it is unaware. The bot
runs for days on a home connection: a dropped packet should not lose a card.
"""

import pytest
import requests

from retrying_client import RetryingClient


class FlakyClient:
    """Fails the first `failures` calls of each method, then succeeds."""

    def __init__(self, failures: int = 0,
                 error=requests.exceptions.ConnectionError("reset")):
        self.failures = failures
        self.error = error
        self.calls: list[tuple[str, tuple]] = []

    def _maybe_fail(self, name, args):
        self.calls.append((name, args))
        if self.failures > 0:
            self.failures -= 1
            raise self.error

    def send_message(self, chat_id, text, markdown=True):
        self._maybe_fail("send_message", (chat_id, text))
        return "sent"

    def send_photo(self, chat_id, path, caption=None):
        self._maybe_fail("send_photo", (chat_id, path))

    def get_updates(self, offset):
        self._maybe_fail("get_updates", (offset,))
        return [{"update_id": 1}]

    def send_typing(self, chat_id):
        self._maybe_fail("send_typing", (chat_id,))

    poll_timeout = 30          # a plain attribute must pass through


def wrap(client, **kwargs):
    return RetryingClient(client, sleep=lambda seconds: None, **kwargs)


# --------------------------------------------------------------------------
# retrying
# --------------------------------------------------------------------------

def test_a_first_failure_is_retried():
    flaky = FlakyClient(failures=1)
    assert wrap(flaky).send_message(1, "hi") == "sent"
    assert len(flaky.calls) == 2


def test_it_gives_up_after_the_last_attempt():
    flaky = FlakyClient(failures=5)
    with pytest.raises(requests.exceptions.ConnectionError):
        wrap(flaky, attempts=3).send_message(1, "hi")
    assert len(flaky.calls) == 3


def test_a_working_call_is_made_once():
    flaky = FlakyClient()
    wrap(flaky).send_message(1, "hi")
    assert len(flaky.calls) == 1


def test_the_backoff_grows(monkeypatch):
    waits = []
    client = RetryingClient(FlakyClient(failures=2), attempts=3, backoff=0.5,
                            sleep=waits.append)
    client.send_message(1, "hi")
    assert waits == [0.5, 1.0]


@pytest.mark.parametrize("error", [
    requests.exceptions.ConnectionError("reset"),
    requests.exceptions.Timeout("slow"),
    requests.exceptions.ChunkedEncodingError("truncated"),
])
def test_every_transient_error_is_retried(error):
    flaky = FlakyClient(failures=1, error=error)
    wrap(flaky).send_message(1, "hi")
    assert len(flaky.calls) == 2


def test_a_real_error_is_not_retried():
    """A rejected request is not a network problem: retrying just repeats it."""
    from telegram_client import TelegramError

    flaky = FlakyClient(failures=1, error=TelegramError("chat not found"))
    with pytest.raises(TelegramError):
        wrap(flaky).send_message(1, "hi")
    assert len(flaky.calls) == 1


# --------------------------------------------------------------------------
# what is not retried
# --------------------------------------------------------------------------

def test_long_polling_is_not_retried():
    """The polling loop already loops; a retry here would only delay it."""
    flaky = FlakyClient(failures=1)
    with pytest.raises(requests.exceptions.ConnectionError):
        wrap(flaky).get_updates(0)
    assert len(flaky.calls) == 1


def test_the_typing_dot_is_not_retried():
    """Cosmetic, and the client already swallows its own failures."""
    flaky = FlakyClient(failures=1)
    with pytest.raises(requests.exceptions.ConnectionError):
        wrap(flaky).send_typing(1)
    assert len(flaky.calls) == 1


# --------------------------------------------------------------------------
# transparency
# --------------------------------------------------------------------------

def test_plain_attributes_pass_through():
    assert wrap(FlakyClient()).poll_timeout == 30


def test_unknown_methods_pass_through():
    """Anything added to the client later keeps working, undecorated."""
    class Extended(FlakyClient):
        def pin_message(self, chat_id, message_id):
            return "pinned"

    assert wrap(Extended()).pin_message(1, 2) == "pinned"


def test_it_can_stand_in_for_the_real_client(client, graph):
    """The bot must not care which one it was handed."""
    import telegram_bot as bot
    from conftest import ai_result, text_update

    built = bot.build_bot(client=wrap(client), graph=graph)
    graph.results = [ai_result("wrapped and working")]
    built.router.handle_message(text_update("hi"))
    assert client.said("wrapped and working")
