"""The polling loop and the wiring.

The loop has to survive network blips, handler exceptions and update shapes it
does not know: a crash here takes the bot offline with approvals still pending.
"""

import pytest

import telegram_bot as bot
from conftest import ALLOWED_CHAT, BLOCKED_CHAT


class StopLoop(BaseException):
    """Breaks out of `while True` once the test has what it needs.

    BaseException on purpose: the loop catches `Exception`, so anything narrower
    would be swallowed and it would spin forever.
    """


class ScriptedClient:
    """A client whose `get_updates` replays a script, then stops the loop."""

    def __init__(self, batches):
        self._batches = list(batches)
        self.polls = 0
        self.last_offset = None
        self.messages: list[tuple[int, str]] = []

    def get_updates(self, offset):
        self.polls += 1
        self.last_offset = offset
        if not self._batches:
            raise StopLoop
        item = self._batches.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def send_message(self, chat_id, text, markdown=True):
        self.messages.append((chat_id, text))

    def said(self, needle):
        return any(needle.lower() in text.lower() for _, text in self.messages)


class ScriptedRouter:
    def __init__(self, allowed=(ALLOWED_CHAT,), error=None):
        self.seen: list[dict] = []
        self._allowed = set(allowed)
        self._error = error

    def handle(self, update):
        self.seen.append(update)
        if self._error:
            raise self._error

    def is_allowed(self, chat_id):
        return int(chat_id) in self._allowed


def run_loop(client, router):
    with pytest.raises(StopLoop):
        bot.PollingLoop(client, router).run_forever()


def message_update(update_id=1, chat_id=ALLOWED_CHAT):
    return {"update_id": update_id,
            "message": {"chat": {"id": chat_id}, "text": "hi"}}


def callback_update(update_id=1, chat_id=ALLOWED_CHAT):
    return {"update_id": update_id,
            "callback_query": {"id": "CB", "data": "approve",
                               "message": {"chat": {"id": chat_id}}}}


# --------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------

def test_every_update_reaches_the_router():
    client = ScriptedClient([[message_update(1), callback_update(2)]])
    router = ScriptedRouter()
    run_loop(client, router)
    assert len(router.seen) == 2


def test_offset_advances_past_handled_updates():
    """Without this every restart would replay the whole backlog."""
    client = ScriptedClient([[message_update(41)]])
    run_loop(client, ScriptedRouter())
    assert client.last_offset == 42


def test_an_empty_batch_is_fine():
    client = ScriptedClient([[]])
    router = ScriptedRouter()
    run_loop(client, router)
    assert router.seen == []


# --------------------------------------------------------------------------
# resilience
# --------------------------------------------------------------------------

def test_a_network_blip_does_not_stop_the_loop():
    client = ScriptedClient([RuntimeError("connection reset"), []])
    run_loop(client, ScriptedRouter())
    assert client.polls == 3          # blip, empty batch, then StopLoop


def test_a_handler_exception_is_reported_to_the_chat():
    client = ScriptedClient([[message_update()]])
    run_loop(client, ScriptedRouter(error=ValueError("kaboom")))
    assert client.said("something went wrong")


def test_a_handler_exception_does_not_stop_the_loop():
    client = ScriptedClient([[message_update()], []])
    run_loop(client, ScriptedRouter(error=ValueError("kaboom")))
    assert client.polls == 3


def test_an_error_is_not_reported_to_an_unauthorised_chat():
    """Even the failure notice must not confirm the bot exists."""
    client = ScriptedClient([[message_update(chat_id=BLOCKED_CHAT)]])
    run_loop(client, ScriptedRouter(error=ValueError("kaboom")))
    assert client.messages == []


def test_an_error_on_a_callback_is_reported_to_that_chat():
    """The chat id has to be dug out of callback_query.message."""
    client = ScriptedClient([[callback_update()]])
    run_loop(client, ScriptedRouter(error=ValueError("kaboom")))
    assert client.said("something went wrong")


def test_an_update_without_a_chat_does_not_crash_the_handler():
    client = ScriptedClient([[{"update_id": 1, "poll": {"id": "x"}}]])
    run_loop(client, ScriptedRouter(error=ValueError("kaboom")))
    assert client.messages == []


# --------------------------------------------------------------------------
# startup
# --------------------------------------------------------------------------

def test_refuses_to_start_without_a_token(monkeypatch):
    monkeypatch.setattr(bot.settings, "TELEGRAM_BOT_TOKEN", None)
    with pytest.raises(SystemExit, match="TELEGRAM_BOT_TOKEN"):
        bot.main()


def test_refuses_to_start_without_an_allowlist(monkeypatch):
    """The allowlist is the only real access control: empty must mean nobody."""
    monkeypatch.setattr(type(bot.settings), "allowed_chat_ids",
                        property(lambda self: set()))
    with pytest.raises(SystemExit, match="TELEGRAM_ALLOWED_CHAT_IDS"):
        bot.main()


# --------------------------------------------------------------------------
# wiring
# --------------------------------------------------------------------------

def test_build_bot_returns_a_wired_facade(client, graph):
    """One place knows all the parts; everything else takes them as arguments."""
    built = bot.build_bot(client=client, graph=graph)
    assert built.client is client
    assert built.router.is_allowed(ALLOWED_CHAT)
    assert not built.router.is_allowed(BLOCKED_CHAT)


def test_the_facade_can_run_the_loop(client, graph, monkeypatch):
    """`main` only calls run_forever; the loop itself is built inside."""
    started = []
    monkeypatch.setattr(bot.PollingLoop, "run_forever",
                        lambda self: started.append(self))
    bot.build_bot(client=client, graph=graph).run_forever()
    assert len(started) == 1


def test_the_built_router_runs_the_given_graph(client, graph):
    from conftest import ai_result, text_update
    built = bot.build_bot(client=client, graph=graph)
    graph.results = [ai_result("wired up")]
    built.router.handle_message(text_update("hi"))
    assert client.said("wired up")


def test_progress_goes_to_the_chat_that_asked(client, graph):
    from conftest import text_update
    built = bot.build_bot(client=client, graph=graph)
    graph.delay = 0.1
    built.router.handle_message(text_update("analyse this"))
    assert client.typing == [ALLOWED_CHAT]


def test_chat_progress_uses_the_client(client):
    progress = bot.ChatProgress(client, 42)
    progress.busy()
    progress.note("Working on it…")
    assert client.typing == [42]
    assert client.messages == [(42, "Working on it…")]
