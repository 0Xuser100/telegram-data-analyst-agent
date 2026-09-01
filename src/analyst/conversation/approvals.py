"""Turning a pending tool call into something a human can decide on.

Describing a tool is a registry, not an if/elif chain: a new tool needs a new
entry here, not a change to the code that renders the card.

The data types live one layer down in `analyst.agent.pending`, because *what*
the agent is paused on is agent state, while *how* a human is asked is a
conversation concern. They are re-exported here so this module stays the single
import site for anything approval-related in a front-end.
"""

import html
import json
import os
from dataclasses import dataclass
from typing import Callable, Protocol

from analyst.agent.pending import (  # re-exported: see the module docstring
    REJECT_MESSAGE,
    SUPPORTED_DECISIONS,
    PendingAction,
    actions_in,
    decisions_for,
    pending_actions,
)
from analyst.plumbing.formatting import human_size, shorten_interpreter, strip_html, tidy_path

# Not a decision: it reprints the request without resuming the graph.
DETAILS_ACTION = "details"

LABELS = {
    "approve": "✅ Approve",
    "reject": "❌ Reject",
    DETAILS_ACTION: "🔍 Show details",
}

_FILE_KINDS = {
    ".py": "Python script", ".sh": "Shell script", ".sql": "SQL file",
    ".json": "JSON file", ".csv": "CSV file", ".md": "Note",
    ".txt": "Text file", ".yml": "YAML file", ".yaml": "YAML file",
}


@dataclass(frozen=True)
class Card:
    """What a human sees before deciding: a title and a few detail lines."""

    title: str
    detail: tuple[str, ...] = ()

    def as_html(self) -> str:
        return "\n".join(["🔐 <b>Approval needed</b>", "",
                          f"<b>{self.title}</b>", *self.detail])


# --------------------------------------------------------------------------
# describers — a registry keyed by tool name (Strategy, looked up not branched)
# --------------------------------------------------------------------------

DESCRIBERS: dict[str, Callable[[PendingAction], Card]] = {}


def describes(*tool_names: str):
    """Register a describer for these tools.

    A new tool needs a decorated function and nothing else: no edit to the
    renderer, no branch to extend.
    """
    def register(describer: Callable[[PendingAction], Card]):
        for name in tool_names:
            DESCRIBERS[name] = describer
        return describer
    return register


@describes("write_file", "edit_file")
def _describe_write(action: PendingAction) -> Card:
    path = tidy_path(action.args.get("file_path") or action.args.get("path"))
    content = action.args.get("content") or action.args.get("new_string") or ""
    detail = [f"<code>{html.escape(path)}</code>"]
    if content:
        kind = _FILE_KINDS.get(os.path.splitext(path)[1].lower(), "File")
        size = human_size(len(content.encode("utf-8", "replace")))
        detail.append(f"{kind} · {content.count(chr(10)) + 1} lines · {size}")
    return Card("📝 Write a file", tuple(detail))


@describes("execute", "shell", "bash", "run_command")
def _describe_command(action: PendingAction) -> Card:
    command = str(action.args.get("command") or action.args.get("cmd") or "").strip()
    return Card("▶️ Run a command", (
        f"<code>{html.escape(shorten_interpreter(command))}</code>",
        "<i>Runs on this machine.</i>",
    ))


@describes("read_file", "ls", "glob", "grep")
def _describe_read(action: PendingAction) -> Card:
    target = (action.args.get("file_path") or action.args.get("path")
              or action.args.get("pattern") or ".")
    return Card(f"📂 {html.escape(action.name)}",
                (f"<code>{html.escape(tidy_path(target))}</code>",))


def _describe_unknown(action: PendingAction) -> Card:
    detail = []
    for key, value in list(action.args.items())[:6]:
        text = " ".join(str(value).split())
        if len(text) > 120:
            text = text[:120] + "…"
        detail.append(f"{html.escape(key)}: <code>{html.escape(text)}</code>")
    return Card(f"🔧 {html.escape(action.name)}", tuple(detail))


def describe(action: PendingAction) -> Card:
    """The card for one pending call. Unknown tools get a generic one."""
    return DESCRIBERS.get(action.name, _describe_unknown)(action)


def raw_args(args: dict, limit: int = 3200) -> str:
    """Tool args can hold a whole generated script, which alone blows past
    Telegram's message limit."""
    text = json.dumps(args or {}, indent=2, ensure_ascii=False, default=str)
    if len(text) > limit:
        text = text[:limit] + f"\n… (+{len(text) - limit} more characters)"
    return text


# --------------------------------------------------------------------------
# asking
# --------------------------------------------------------------------------

class Approver(Protocol):
    """Anything that can ask a human about a pending action.

    The Telegram and terminal front-ends implement this, so the run loop does
    not care which one it is talking to.
    """

    def ask(self, actions: list[PendingAction]) -> None: ...


class TelegramApprover:
    """Sends one approval card per pending action, with buttons."""

    def __init__(self, client, chat_id: int):
        self._client = client
        self._chat_id = chat_id

    def ask(self, actions: list[PendingAction]) -> None:
        for action in actions:
            keyboard = [
                [{"text": LABELS[d], "callback_data": d}
                 for d in action.offered_decisions],
                [{"text": LABELS[DETAILS_ACTION], "callback_data": DETAILS_ACTION}],
            ]
            self._client.send_html(self._chat_id, describe(action).as_html(), keyboard)

    def show_details(self, actions: list[PendingAction]) -> None:
        """The full request behind a card. Does not resume the graph."""
        for action in actions:
            body = (f"<b>{html.escape(action.name)}</b>"
                    f"\n<pre>{html.escape(raw_args(action.args))}</pre>")
            self._client.send_html(self._chat_id, body)


class ConsoleApprover:
    """Terminal version: prints the card and blocks on input().

    Used by entrypoints/cli.py. Unlike Telegram, the process can simply wait.
    """

    def __init__(self, prompt=input, out=print):
        self._prompt = prompt
        self._out = out
        self.decisions: list[dict] = []

    def ask(self, actions: list[PendingAction]) -> None:
        self.decisions = []
        for action in actions:
            card = describe(action)
            self._out("\n" + "=" * 60)
            self._out(strip_html(card.title))
            for line in card.detail:
                self._out("  " + strip_html(line))
            self._out("=" * 60)
            choice = self._prompt("Approve? (y = approve / n = reject): ").strip().lower()
            self.decisions += decisions_for("approve" if choice == "y" else "reject", 1)
