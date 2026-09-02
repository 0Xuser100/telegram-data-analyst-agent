"""The status line: one message, edited, then gone.

After routine steps stopped raising approval cards, this is the only sign the
bot is alive during a run that now takes minutes.
"""

from analyst.plumbing.progress import ChatProgress, EDIT_INTERVAL, SilentProgress


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def tick(self, seconds):
        self.now += seconds


class Client:
    def __init__(self):
        self.sent: list[str] = []
        self.edits: list[tuple[int, str]] = []
        self.deleted: list[int] = []
        self.fail = None

    def send_message(self, chat_id, text, markdown=True):
        if self.fail:
            raise self.fail
        self.sent.append(text)
        return 77

    def edit_message(self, chat_id, message_id, text):
        if self.fail:
            raise self.fail
        self.edits.append((message_id, text))

    def delete_message(self, chat_id, message_id):
        if self.fail:
            raise self.fail
        self.deleted.append(message_id)

    def send_typing(self, chat_id):
        pass


def progress():
    client, clock = Client(), Clock()
    return ChatProgress(client, 555, clock=clock), client, clock


def test_the_first_step_sends_one_message():
    reporter, client, _ = progress()
    reporter.step("checking whether State mixes totals with detail")
    assert client.sent == ["checking whether State mixes totals with detail"]


def test_later_steps_edit_the_same_message():
    """A run makes dozens of tool calls; dozens of messages would bury the
    conversation the answer belongs to."""
    reporter, client, clock = progress()
    reporter.step("inspecting the columns")
    clock.tick(EDIT_INTERVAL + 1)
    reporter.step("charting deaths by cause")

    assert len(client.sent) == 1
    assert client.edits == [(77, "charting deaths by cause")]


def test_updates_are_rate_limited():
    """Telegram rate-limits edits, so a fast run must skip rather than queue."""
    reporter, client, clock = progress()
    reporter.step("first")
    clock.tick(EDIT_INTERVAL / 2)
    reporter.step("second")
    assert client.edits == []


def test_an_unchanged_step_is_not_resent():
    reporter, client, clock = progress()
    reporter.step("same step")
    clock.tick(EDIT_INTERVAL * 2)
    reporter.step("same step")
    assert client.edits == []


def test_an_empty_step_is_ignored():
    reporter, client, _ = progress()
    reporter.step("")
    assert client.sent == []


def test_the_status_line_is_cleared_when_the_answer_lands():
    reporter, client, _ = progress()
    reporter.step("working")
    reporter.done()
    assert client.deleted == [77]


def test_clearing_without_a_status_line_does_nothing():
    reporter, client, _ = progress()
    reporter.done()
    assert client.deleted == []


def test_a_failed_update_never_takes_the_run_down():
    """A status line is cosmetic. A run dying because of one is not."""
    reporter, client, _ = progress()
    client.fail = RuntimeError("telegram is unhappy")
    reporter.step("working")          # must not raise


def test_a_failed_delete_never_takes_the_run_down():
    reporter, client, _ = progress()
    reporter.step("working")
    client.fail = RuntimeError("message already gone")
    reporter.done()                   # must not raise


def test_a_failed_send_does_not_pretend_it_worked():
    """If the first send fails the next step must try again, not silently edit
    a message that was never created."""
    reporter, client, clock = progress()
    client.fail = RuntimeError("nope")
    reporter.step("first")
    client.fail = None
    clock.tick(EDIT_INTERVAL * 2)
    reporter.step("second")
    assert client.sent == ["second"]


def test_the_terminal_reporter_satisfies_the_wider_protocol():
    """The CLI shows its own output; these must stay no-ops rather than
    missing, or the runner would need to know which front-end it has."""
    silent = SilentProgress()
    silent.step("anything")
    silent.done()
