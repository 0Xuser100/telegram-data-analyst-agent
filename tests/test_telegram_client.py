"""The Telegram API wrapper. `requests` is patched, so nothing leaves here."""

import json

import pytest

from analyst.plumbing import telegram_client as tc
@pytest.fixture
def api(monkeypatch):
    """Records each call and replies OK. Returns a list of (url, payload)."""
    calls: list[tuple[str, dict]] = []

    class Response:
        content = b"a,b\n1,2\n"

        @staticmethod
        def json():
            return {"ok": True, "result": [{"update_id": 1}]}

        @staticmethod
        def raise_for_status():
            return None

    def post(url, json=None, data=None, files=None, timeout=None):
        calls.append((url, json or data or {}))
        return Response()

    monkeypatch.setattr(tc.requests, "post", post)
    monkeypatch.setattr(tc.requests, "get", lambda url, timeout=None: Response())
    return calls


@pytest.fixture
def bot(api):
    return tc.TelegramClient("123:TEST", poll_timeout=5), api


def method_of(url: str) -> str:
    return url.rsplit("/", 1)[-1]


# --------------------------------------------------------------------------
# sending
# --------------------------------------------------------------------------

def test_send_message_renders_html_and_disables_previews(bot):
    client, calls = bot
    client.send_message(1, "**hi**")
    url, payload = calls[0]
    assert method_of(url) == "sendMessage"
    assert payload["text"] == "<b>hi</b>"
    assert payload["link_preview_options"] == {"is_disabled": True}


def test_send_message_never_sends_an_empty_body(bot):
    client, calls = bot
    client.send_message(1, "")
    assert calls[0][1]["text"] == "(empty reply)"


def test_markdown_can_be_disabled(bot):
    client, calls = bot
    client.send_message(1, "**hi**", markdown=False)
    assert calls[0][1]["text"] == "**hi**"
    assert "parse_mode" not in calls[0][1]


def test_long_reply_is_split_into_several_messages(bot):
    client, calls = bot
    client.send_message(1, "\n".join(f"line {i}" for i in range(2000)))
    assert len(calls) > 1


def test_send_message_falls_back_to_plain_text(monkeypatch):
    """An unparseable reply must still reach the user."""
    sent = []

    class Response:
        def __init__(self, ok):
            self._ok = ok

        def json(self):
            return {"ok": self._ok, "result": {}, "description": "can't parse entities"}

    def post(url, json=None, **kwargs):
        sent.append(json)
        return Response("parse_mode" not in json)

    monkeypatch.setattr(tc.requests, "post", post)
    tc.TelegramClient("123:TEST").send_message(1, "**hi**")
    assert sent[-1]["text"] == "**hi**"


def test_send_html_passes_the_keyboard(bot):
    client, calls = bot
    keyboard = [[{"text": "✅", "callback_data": "approve"}]]
    client.send_html(1, "<b>card</b>", keyboard)
    assert calls[0][1]["reply_markup"] == {"inline_keyboard": keyboard}
    assert calls[0][1]["parse_mode"] == "HTML"


def test_send_html_keeps_the_buttons_when_markup_fails(monkeypatch):
    """Markup must never block a human decision."""
    sent = []

    class Response:
        def __init__(self, ok):
            self._ok = ok

        def json(self):
            return {"ok": self._ok, "result": {}, "description": "bad entity"}

    def post(url, json=None, **kwargs):
        sent.append(json)
        return Response("parse_mode" not in json)

    monkeypatch.setattr(tc.requests, "post", post)
    keyboard = [[{"text": "✅", "callback_data": "approve"}]]
    tc.TelegramClient("123:TEST").send_html(1, "<b>card</b>", keyboard)

    assert len(sent) == 2
    assert sent[1]["text"] == "card"                       # tags stripped
    assert sent[1]["reply_markup"] == {"inline_keyboard": keyboard}


# --------------------------------------------------------------------------
# uploads
# --------------------------------------------------------------------------

def test_small_image_goes_as_a_photo(bot, tmp_path):
    client, calls = bot
    path = tmp_path / "p.png"
    path.write_bytes(b"x" * 10)
    client.send_photo(1, str(path))
    assert method_of(calls[0][0]) == "sendPhoto"


def test_oversized_image_goes_as_a_document(bot, tmp_path, monkeypatch):
    """sendPhoto is capped at 10 MB and re-compresses, so a bigger figure would
    simply fail."""
    client, calls = bot
    monkeypatch.setattr(tc, "PHOTO_MAX_BYTES", 10)
    path = tmp_path / "p.png"
    path.write_bytes(b"x" * 100)
    client.send_photo(1, str(path))
    assert method_of(calls[0][0]) == "sendDocument"


def test_caption_is_capped(bot, tmp_path):
    client, calls = bot
    path = tmp_path / "p.png"
    path.write_bytes(b"x")
    client.send_photo(1, str(path), caption="c" * 2000)
    assert len(calls[0][1]["caption"]) == 1024


# --------------------------------------------------------------------------
# receiving
# --------------------------------------------------------------------------

def test_get_updates_outlasts_the_long_poll(monkeypatch):
    """The HTTP read timeout must be longer than Telegram's poll duration, or
    every poll ends in a client-side timeout."""
    seen = {}

    def post(url, json=None, timeout=None):
        seen.update(json=json, timeout=timeout)

        class Response:
            @staticmethod
            def json():
                return {"ok": True, "result": [{"update_id": 7}]}
        return Response()

    monkeypatch.setattr(tc.requests, "post", post)
    client = tc.TelegramClient("123:TEST", poll_timeout=5)
    assert client.get_updates(0) == [{"update_id": 7}]
    assert seen["timeout"] > seen["json"]["timeout"]
    assert seen["json"]["allowed_updates"] == ["message", "callback_query"]


def test_download_writes_the_file_and_returns_its_path(bot, tmp_path, monkeypatch):
    client, _ = bot
    monkeypatch.setattr(tc.TelegramClient, "_call",
                        lambda self, method, **kw: {"file_path": "documents/f.csv"})
    dest = client.download_file("FILE-1", str(tmp_path / "data"), "sales.csv")
    assert dest.endswith("sales.csv")
    with open(dest, "rb") as fh:
        assert fh.read() == b"a,b\n1,2\n"


# --------------------------------------------------------------------------
# errors
# --------------------------------------------------------------------------

def test_api_errors_become_telegram_errors(monkeypatch):
    class Response:
        @staticmethod
        def json():
            return {"ok": False, "description": "Unauthorized"}

    monkeypatch.setattr(tc.requests, "post", lambda *a, **k: Response())
    with pytest.raises(tc.TelegramError, match="Unauthorized"):
        tc.TelegramClient("123:TEST")._call("sendMessage", chat_id=1, text="x")


def test_get_updates_errors_are_raised(monkeypatch):
    class Response:
        @staticmethod
        def json():
            return {"ok": False, "description": "Conflict"}

    monkeypatch.setattr(tc.requests, "post", lambda *a, **k: Response())
    with pytest.raises(tc.TelegramError, match="Conflict"):
        tc.TelegramClient("123:TEST").get_updates(0)


def test_typing_failure_is_swallowed(monkeypatch):
    """Purely cosmetic: it must never take a run down."""
    monkeypatch.setattr(tc.requests, "post",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    tc.TelegramClient("123:TEST").send_typing(1)


def test_upload_failure_is_reported(monkeypatch, tmp_path):
    class Response:
        @staticmethod
        def json():
            return {"ok": False, "description": "PHOTO_INVALID_DIMENSIONS"}

    monkeypatch.setattr(tc.requests, "post", lambda *a, **k: Response())
    path = tmp_path / "p.png"
    path.write_bytes(b"x")
    with pytest.raises(tc.TelegramError, match="PHOTO_INVALID"):
        tc.TelegramClient("123:TEST").send_photo(1, str(path))


def test_the_token_is_not_in_the_error_message(monkeypatch):
    class Response:
        @staticmethod
        def json():
            return {"ok": False, "description": "nope"}

    monkeypatch.setattr(tc.requests, "post", lambda *a, **k: Response())
    try:
        tc.TelegramClient("SECRET-TOKEN")._call("sendMessage")
    except tc.TelegramError as exc:
        assert "SECRET-TOKEN" not in str(exc)


def test_send_media_group_posts_one_request_with_every_figure(api, tmp_path):
    """One call, one notification: the point of grouping."""
    paths = []
    for name in ("01_a.png", "02_b.png", "03_c.png"):
        path = tmp_path / name
        path.write_bytes(b"x")
        paths.append(str(path))

    tc.TelegramClient("T").send_media_group(1, paths)

    url, payload = api[-1]
    assert url.endswith("/sendMediaGroup")
    assert len(json.loads(payload["media"])) == 3


def test_send_media_group_attaches_each_file_by_reference(api, tmp_path):
    path = tmp_path / "01_a.png"
    path.write_bytes(b"x")
    tc.TelegramClient("T").send_media_group(1, [str(path)])

    media = json.loads(api[-1][1]["media"])
    assert media[0] == {"type": "photo", "media": "attach://file0"}


def test_send_media_group_sends_nothing_for_an_empty_list(api):
    tc.TelegramClient("T").send_media_group(1, [])
    assert api == []


def test_send_media_group_caps_at_the_telegram_limit(api, tmp_path):
    paths = []
    for index in range(tc.MEDIA_GROUP_MAX + 3):
        path = tmp_path / f"{index:02d}.png"
        path.write_bytes(b"x")
        paths.append(str(path))

    tc.TelegramClient("T").send_media_group(1, paths)
    assert len(json.loads(api[-1][1]["media"])) == tc.MEDIA_GROUP_MAX
