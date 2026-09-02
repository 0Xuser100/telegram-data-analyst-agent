"""Deciding what each incoming Telegram update should do.

Routing only. It runs nothing itself: the runner runs the graph, the delivery
sends the result, the store decides the conversation. All three are passed in,
so a test can hand it doubles.
"""

import os

from analyst.conversation.approvals import (
    DETAILS_ACTION,
    SUPPORTED_DECISIONS,
    TelegramApprover,
    actions_in,
    decisions_for,
)
from analyst.conversation.delivery import final_text
from analyst.plumbing.formatting import safe_filename
from analyst.plumbing.tracing import NullTracer


def _short(text: str, limit: int = 40) -> str:
    """A trace name has to fit in a list column."""
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"

HELP = (
    "**Send me a .csv file** and I'll analyse it and chart it — no instructions "
    "needed. Add a caption to say what you want instead.\n\n"
    "Each file starts a fresh conversation, so ask follow-up questions about "
    "the results before sending the next one.\n\n"
    "Or just describe a task in text.\n\n"
    "Anything that writes a file or runs a command pauses for your approval "
    "first — tap ✅ or ❌, or 🔍 to see the full request.\n\n"
    "`/new` start a fresh conversation\n"
    "`/help` show this message"
)

# Uploads the agent can read with pandas. Anything else is refused rather than
# handed to a shell-capable agent.
ALLOWED_UPLOAD_EXT = (".csv", ".tsv", ".json", ".xlsx", ".xls", ".txt")
MAX_UPLOAD_BYTES = 20 * 1024 * 1024      # bots cannot getFile above 20 MB

BUSY_REPLY = ("There's still an approval waiting — tap a button above first, "
              "or /new to start over.")
EMPTY_REPLY = "Send me a .csv file, or describe a task in text."
STALE_REPLY = "That approval is no longer pending."
FRESH_START_NOTICE = "Working on it… (fresh start for this file)"


class UpdateRouter:
    """Sends each update to the right place."""

    def __init__(self, client, runner, threads, delivery, allowed_chats,
                 upload_task_template, data_dir="./data", output_dir="./output",
                 progress_for=None, tracer=None):
        self._client = client
        self._runner = runner
        self._threads = threads
        self._delivery = delivery
        self._allowed = {int(chat) for chat in allowed_chats}
        self._upload_task = upload_task_template
        self._data_dir = data_dir
        self._output_dir = output_dir
        # chat_id -> Progress. The router is the layer that knows the chat.
        self._progress_for = progress_for or (lambda chat_id: None)
        # One trace per task: opened here, re-attached on every approval.
        self._tracer = tracer or NullTracer()

    # -- entry point -------------------------------------------------------

    def handle(self, update: dict) -> None:
        if "message" in update:
            self.handle_message(update["message"])
        elif "callback_query" in update:
            self.handle_callback(update["callback_query"])

    def is_allowed(self, chat_id) -> bool:
        """Unknown chats are dropped silently: the agent runs shell commands,
        and anyone can find a public bot by username."""
        return int(chat_id) in self._allowed

    # -- commands ----------------------------------------------------------

    def _command_help(self, chat_id: int) -> None:
        self._client.send_message(chat_id, HELP)

    def _command_new(self, chat_id: int) -> None:
        self._start_new_conversation(chat_id)
        self._client.send_message(chat_id, "Started a fresh conversation.")

    # Slash command -> method name. Adding a command means adding a line here
    # and a method, not another branch in handle_message.
    COMMANDS = {
        "/start": "_command_help",
        "/help": "_command_help",
        "/new": "_command_new",
    }

    # -- messages ----------------------------------------------------------

    def handle_message(self, message: dict) -> None:
        chat_id = message["chat"]["id"]
        if not self.is_allowed(chat_id):
            print(f"[drop] message from unauthorised chat {chat_id}")
            return

        text = (message.get("text") or "").strip()

        # Commands answer even mid-approval, so none of them are ever blocked.
        command = self.COMMANDS.get(text)
        if command:
            getattr(self, command)(chat_id)
            return

        # Applies to uploads too: never start a second run on a paused thread.
        if self._runner.waiting_for(self._thread_id(chat_id)):
            self._client.send_message(chat_id, BUSY_REPLY)
            return

        if message.get("document"):
            self.handle_document(message)
            return

        if not text:
            self._client.send_message(chat_id, EMPTY_REPLY)
            return

        self._run(chat_id, text, run_name=f"ask: {_short(text)}",
                  metadata={"source": "text", "chat_id": chat_id})

    def handle_document(self, message: dict) -> None:
        """A file upload is a complete request: download it, then analyse it.
        Any caption becomes the instruction instead."""
        chat_id = message["chat"]["id"]
        document = message["document"]
        name = safe_filename(document.get("file_name"))
        extension = os.path.splitext(name)[1].lower()

        if extension not in ALLOWED_UPLOAD_EXT:
            self._client.send_message(
                chat_id,
                f"I can't read {extension or 'that'} files. Send one of: "
                + ", ".join(ALLOWED_UPLOAD_EXT))
            return

        if (document.get("file_size") or 0) > MAX_UPLOAD_BYTES:
            self._client.send_message(
                chat_id, "That file is over the 20 MB bot download limit.")
            return

        self._client.send_message(chat_id, f"Downloading {name}…")
        try:
            path = self._client.download_file(document["file_id"], self._data_dir, name)
        except Exception as exc:
            print(f"[upload] {exc}")
            self._client.send_message(chat_id, "Couldn't download that file.")
            return

        relative = f"./{os.path.relpath(path).replace(os.sep, '/')}"
        caption = (message.get("caption") or "").strip()
        if caption:
            task = f"A data file has just been uploaded to {relative}.\n\n{caption}"
        else:
            task = self._upload_task.format(file_path=relative,
                                            output_dir=self._output_dir)

        # A new file is a new task, and carrying the old history forward is what
        # grew one chat to hundreds of thousands of input tokens. Follow-up
        # questions still work: they land in this new conversation.
        self._start_new_conversation(chat_id)

        # An upload is always a real run, so say so straight away.
        self._run(chat_id, task, notice=FRESH_START_NOTICE, announce_after=0,
                  run_name=f"upload: {name}",
                  metadata={"source": "upload", "chat_id": chat_id,
                            "file": name, "caption": bool(caption)})

    # -- buttons -----------------------------------------------------------

    def handle_callback(self, callback: dict) -> None:
        chat_id = callback["message"]["chat"]["id"]
        choice = callback.get("data", "")

        if not self.is_allowed(chat_id):
            return

        self._client.answer_callback(callback["id"])

        if choice not in SUPPORTED_DECISIONS + (DETAILS_ACTION,):
            self._client.send_message(chat_id, f"Unknown action: {choice!r}")
            return

        actions = self._runner.waiting_for(self._thread_id(chat_id))
        if not actions:
            self._client.send_message(chat_id, STALE_REPLY)
            return

        # 🔍 is not a decision: print the request, leave the graph paused.
        if choice == DETAILS_ACTION:
            TelegramApprover(self._client, chat_id).show_details(actions)
            return

        self._client.send_message(
            chat_id, "Approved ✅" if choice == "approve" else "Rejected ❌")
        gated = ", ".join(action.name for action in actions)
        thread_id = self._thread_id(chat_id)
        # One reporter for the whole turn: it owns the status message, so the
        # object that wrote it has to be the object that clears it.
        reporter = self._progress_for(chat_id)
        try:
            result = self._runner.resume(
                thread_id,
                decisions_for(choice, len(actions)),
                notice="Still working…",
                progress=reporter,
                run_name=f"{choice}: {gated}",
                metadata={"source": "button", "chat_id": chat_id,
                          "decision": choice, "tools": gated},
                attach=lambda: self._tracer.attached(thread_id),
            )
        except BaseException:
            # The bot reports the failure. Without this the status line sits
            # above it for good, since the next turn builds a fresh reporter.
            self._clear(reporter)
            raise
        self._deliver(chat_id, thread_id, result, reporter)

    # -- internals ---------------------------------------------------------

    def _thread_id(self, chat_id: int) -> str:
        return self._threads.thread_id(chat_id)

    def _start_new_conversation(self, chat_id: int) -> None:
        # Close the old task's trace before leaving it behind.
        self._tracer.abandon(self._thread_id(chat_id), reason="conversation restarted")
        self._threads.start_new(chat_id)
        self._delivery.forget(chat_id)

    def _run(self, chat_id: int, task: str, run_name: str | None = None,
             metadata: dict | None = None, **kwargs) -> None:
        thread_id = self._thread_id(chat_id)
        self._delivery.start_run(chat_id)
        self._tracer.start(thread_id, run_name or "task", {"task": task})
        reporter = self._progress_for(chat_id)
        try:
            result = self._runner.start(
                thread_id, task,
                progress=reporter,
                run_name=run_name, metadata=metadata,
                attach=lambda: self._tracer.attached(thread_id),
                **kwargs)
        except BaseException:
            self._clear(reporter)
            raise
        self._deliver(chat_id, thread_id, result, reporter)

    @staticmethod
    def _clear(reporter) -> None:
        if reporter is not None:
            reporter.done()

    def _deliver(self, chat_id: int, thread_id: str, result: dict,
                 reporter=None) -> None:
        """Send the result, and close the trace once nothing is pending."""
        if not actions_in(result):
            self._tracer.finish(thread_id, {"reply": final_text(result)})
        # Before the answer, so the chat ends as answer plus charts with no
        # stale "computing shares…" line hanging above them.
        self._clear(reporter)
        self._delivery.deliver(chat_id, result)
