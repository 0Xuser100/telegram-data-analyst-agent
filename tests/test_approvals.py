"""Approval cards, the describer registry, and the two approval front-ends."""

import pytest

import approvals as ap
from conftest import EXECUTE_ACTION, WRITE_FILE_ACTION, FakeInterrupt, interrupt_result


def action(name, **args) -> ap.PendingAction:
    return ap.PendingAction(name=name, args=args)


# --------------------------------------------------------------------------
# PendingAction
# --------------------------------------------------------------------------

def test_unsupported_decisions_are_dropped():
    """The middleware also advertises edit/respond, which need a follow-up
    conversation the bot does not have."""
    pending = ap.PendingAction("execute", {}, ("approve", "edit", "reject", "respond"))
    assert pending.offered_decisions == ("approve", "reject")


def test_no_decisions_falls_back_to_both():
    assert ap.PendingAction("execute", {}, ()).offered_decisions == ap.SUPPORTED_DECISIONS


def test_only_reject_is_honoured():
    assert ap.PendingAction("execute", {}, ("reject",)).offered_decisions == ("reject",)


# --------------------------------------------------------------------------
# describers
# --------------------------------------------------------------------------

def test_write_file_shows_path_kind_lines_and_size():
    card = ap.describe(action("write_file", file_path="/output/analysis.py",
                              content="a\nb\nc"))
    assert card.title == "📝 Write a file"
    assert card.detail[0] == "<code>output/analysis.py</code>"
    assert card.detail[1] == "Python script · 3 lines · 5 B"


def test_write_file_without_content_shows_only_the_path():
    card = ap.describe(action("write_file", file_path="a.txt"))
    assert card.detail == ("<code>a.txt</code>",)


def test_edit_file_is_described_like_a_write():
    card = ap.describe(action("edit_file", file_path="notes.md", new_string="hello"))
    assert card.title == "📝 Write a file"
    assert "Note · 1 lines · 5 B" in card.detail[1]


@pytest.mark.parametrize("name", ["execute", "shell", "bash", "run_command"])
def test_command_tools_show_a_shortened_command(name):
    card = ap.describe(action(name, command=r"D:\p\.venv\Scripts\python.exe ./output/a.py"))
    assert card.title == "▶️ Run a command"
    assert card.detail[0] == "<code>python ./output/a.py</code>"
    assert card.detail[1] == "<i>Runs on this machine.</i>"


@pytest.mark.parametrize("name", ["read_file", "ls", "glob", "grep"])
def test_read_only_tools_show_their_target(name):
    card = ap.describe(action(name, file_path="./data/x.csv"))
    assert card.title == f"📂 {name}"
    assert card.detail == ("<code>data/x.csv</code>",)


def test_unknown_tool_lists_its_arguments_compactly():
    card = ap.describe(action("mystery", blob="x" * 500, n=2))
    assert card.title == "🔧 mystery"
    assert card.detail[0].endswith("…</code>")
    assert "n: <code>2</code>" in card.detail[1]


def test_unknown_tool_caps_the_number_of_arguments():
    card = ap.describe(ap.PendingAction("mystery", {f"k{i}": i for i in range(20)}))
    assert len(card.detail) == 6


def test_unknown_tool_collapses_newlines():
    assert ap.describe(action("mystery", k="a\nb\n c")).detail == ("k: <code>a b c</code>",)


def test_markup_in_arguments_is_escaped():
    """Tool args are model output: they must not be able to inject HTML."""
    card = ap.describe(action("write_file", file_path="<b>evil</b>.py", content="x"))
    assert "&lt;b&gt;" in card.detail[0]


def test_a_new_tool_needs_only_a_registry_entry():
    """Open for extension: adding a describer must not touch the renderer."""
    ap.DESCRIBERS["deploy"] = lambda pending: ap.Card("🚀 Deploy", ("prod",))
    try:
        assert ap.describe(action("deploy")).title == "🚀 Deploy"
    finally:
        del ap.DESCRIBERS["deploy"]


def test_missing_args_do_not_raise():
    assert ap.describe(ap.PendingAction("write_file")).title == "📝 Write a file"


# --------------------------------------------------------------------------
# Card
# --------------------------------------------------------------------------

def test_card_renders_a_heading_and_the_detail():
    body = ap.Card("📝 Write a file", ("<code>a.py</code>",)).as_html()
    assert body.startswith("🔐 <b>Approval needed</b>")
    assert "<b>📝 Write a file</b>" in body
    assert body.endswith("<code>a.py</code>")


# --------------------------------------------------------------------------
# interrupts -> actions
# --------------------------------------------------------------------------

def test_interrupts_become_pending_actions():
    actions = ap.actions_in(interrupt_result(WRITE_FILE_ACTION, EXECUTE_ACTION))
    assert [a.name for a in actions] == ["write_file", "execute"]
    assert actions[0].args["file_path"] == "/output/analysis.py"


def test_allowed_decisions_are_matched_per_action():
    actions = ap.actions_in(interrupt_result(EXECUTE_ACTION, allowed=("approve",)))
    assert actions[0].allowed_decisions == ("approve",)


def test_a_finished_result_has_no_actions():
    assert ap.actions_in({"messages": []}) == []
    assert ap.actions_in(None) == []


def test_an_interrupt_without_review_configs_still_works():
    interrupt = FakeInterrupt({"action_requests": [{"name": "execute", "args": {}}]})
    actions = ap.pending_actions([interrupt])
    assert actions[0].offered_decisions == ap.SUPPORTED_DECISIONS


# --------------------------------------------------------------------------
# decisions
# --------------------------------------------------------------------------

def test_approve_is_one_decision_per_action():
    assert ap.decisions_for("approve", 3) == [{"type": "approve"}] * 3


def test_reject_tells_the_agent_not_to_retry():
    decisions = ap.decisions_for("reject", 1)
    assert decisions[0]["type"] == "reject"
    assert "Do not retry" in decisions[0]["message"]


# --------------------------------------------------------------------------
# raw_args
# --------------------------------------------------------------------------

def test_raw_args_pretty_prints_json():
    assert ap.raw_args({"a": 1}) == '{\n  "a": 1\n}'


def test_raw_args_truncates_and_says_so():
    out = ap.raw_args({"content": "x" * 5000}, limit=200)
    assert len(out) < 400
    assert "more characters" in out


def test_raw_args_survives_unserialisable_values():
    assert "object" in ap.raw_args({"x": object()})


def test_raw_args_keeps_non_ascii_readable():
    assert "café" in ap.raw_args({"name": "café"})


# --------------------------------------------------------------------------
# TelegramApprover
# --------------------------------------------------------------------------

def test_the_card_is_a_summary_not_json(client):
    ap.TelegramApprover(client, 1).ask(ap.actions_in(interrupt_result(WRITE_FILE_ACTION)))
    body = client.cards[0]
    assert "import pandas" not in body          # the script body stays out
    assert len(body) < 400


def test_keyboard_has_decisions_then_the_details_button(client):
    ap.TelegramApprover(client, 1).ask(ap.actions_in(interrupt_result(EXECUTE_ACTION)))
    keyboard = client.html[0][2]
    assert [b["callback_data"] for b in keyboard[0]] == ["approve", "reject"]
    assert [b["callback_data"] for b in keyboard[1]] == ["details"]


def test_one_card_per_pending_action(client):
    ap.TelegramApprover(client, 1).ask(
        ap.actions_in(interrupt_result(WRITE_FILE_ACTION, EXECUTE_ACTION)))
    assert len(client.html) == 2


def test_details_shows_the_full_args(client):
    actions = ap.actions_in(interrupt_result(
        {"name": "write_file", "args": {"content": "SECRET-MARKER"}}))
    ap.TelegramApprover(client, 1).show_details(actions)
    assert "SECRET-MARKER" in client.cards[0]
    assert client.html[0][2] is None            # no buttons: nothing to decide


# --------------------------------------------------------------------------
# ConsoleApprover
# --------------------------------------------------------------------------

def test_console_approver_reads_y_as_approve():
    approver = ap.ConsoleApprover(prompt=lambda _: "y", out=lambda *a: None)
    approver.ask(ap.actions_in(interrupt_result(WRITE_FILE_ACTION)))
    assert approver.decisions == [{"type": "approve"}]


@pytest.mark.parametrize("answer", ["n", "no", "", "anything"])
def test_anything_but_y_rejects(answer):
    """The safe default: an unclear answer must not run a command."""
    approver = ap.ConsoleApprover(prompt=lambda _: answer, out=lambda *a: None)
    approver.ask(ap.actions_in(interrupt_result(EXECUTE_ACTION)))
    assert approver.decisions[0]["type"] == "reject"


def test_console_approver_prints_a_readable_card():
    printed = []
    approver = ap.ConsoleApprover(prompt=lambda _: "y", out=printed.append)
    approver.ask(ap.actions_in(interrupt_result(EXECUTE_ACTION)))
    text = "\n".join(printed)
    assert "Run a command" in text
    assert "python ./output/analysis.py" in text
    assert "<code>" not in text                 # tags stripped for the terminal


def test_console_approver_asks_once_per_action():
    asked = []
    approver = ap.ConsoleApprover(prompt=lambda p: asked.append(p) or "y",
                                  out=lambda *a: None)
    approver.ask(ap.actions_in(interrupt_result(WRITE_FILE_ACTION, EXECUTE_ACTION)))
    assert len(asked) == 2
    assert len(approver.decisions) == 2


def test_decisions_are_reset_between_rounds():
    """A second round must not resend the first round's answers."""
    approver = ap.ConsoleApprover(prompt=lambda _: "y", out=lambda *a: None)
    approver.ask(ap.actions_in(interrupt_result(WRITE_FILE_ACTION)))
    approver.ask(ap.actions_in(interrupt_result(EXECUTE_ACTION)))
    assert len(approver.decisions) == 1
