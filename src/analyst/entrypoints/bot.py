"""Telegram front-end. Builds the parts and polls for updates.

The Telegram message *is* the task. Execution rules live in the system prompt
(agent/prompts.py), so messages carry no boilerplate.

Run with:  uv run analyst-bot  (from the repository root)
"""

import traceback
from dataclasses import dataclass

from analyst.config import apply_tracing_env, get_settings

settings = get_settings()
apply_tracing_env(settings)          # before the graph is imported

from analyst.agent.builder import CHECKPOINT_DB, agent  # noqa: E402
from analyst.plumbing.artifacts import ArtifactCollector  # noqa: E402
from analyst.conversation.delivery import ResultDelivery  # noqa: E402
from analyst.plumbing.progress import ChatProgress  # noqa: E402
from analyst.plumbing.retrying_client import RetryingClient  # noqa: E402
from analyst.agent.prompts import UPLOADED_FILE_TASK  # noqa: E402
from analyst.conversation.router import UpdateRouter  # noqa: E402
from analyst.agent.runner import AgentRunner  # noqa: E402
from analyst.plumbing.telegram_client import TelegramClient  # noqa: E402
from analyst.plumbing.thread_store import ThreadStore  # noqa: E402
from analyst.plumbing.tracing import build_tracer  # noqa: E402

DATA_DIR = "./data"
OUTPUT_DIR = "./output"


class PollingLoop:
    """Long-polls Telegram and hands each update to the router.

    Nothing here may raise: a crash takes the bot offline and leaves every
    pending approval unanswerable.
    """

    def __init__(self, client, router):
        self._client = client
        self._router = router

    def run_forever(self) -> None:
        offset = 0
        while True:
            try:
                updates = self._client.get_updates(offset)
            except Exception as exc:              # network blip, keep polling
                print(f"[poll error] {exc}")
                continue

            for update in updates:
                offset = update["update_id"] + 1
                self._handle(update)

    def _handle(self, update: dict) -> None:
        try:
            self._router.handle(update)
        except Exception:
            traceback.print_exc()
            chat_id = _chat_of(update)
            if chat_id and self._router.is_allowed(chat_id):
                self._client.send_message(
                    chat_id, "Something went wrong — check the bot logs.")


def _chat_of(update: dict) -> int | None:
    message = update.get("message") or update.get("callback_query", {}).get("message", {})
    return (message or {}).get("chat", {}).get("id")


@dataclass(frozen=True)
class Bot:
    """The whole front-end behind one method.

    Facade: `main` builds it and calls `run_forever`. Tests reach past it for
    the router when they want to drive one update at a time.
    """

    client: object
    router: object

    def run_forever(self) -> None:
        PollingLoop(self.client, self.router).run_forever()


def build_bot(client=None, graph=None, config=None) -> Bot:
    """Wire the parts together. This is the only place that knows all of them."""
    config = config or settings
    if client is None:
        # Decorated with retries: a dropped packet on a home connection should
        # not lose an approval card.
        client = RetryingClient(
            TelegramClient(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_POLL_TIMEOUT))
    graph = graph if graph is not None else agent

    artifacts = ArtifactCollector(OUTPUT_DIR)
    delivery = ResultDelivery(client, artifacts)
    threads = ThreadStore(CHECKPOINT_DB)
    tracer = build_tracer(CHECKPOINT_DB, enabled=config.LANGSMITH_TRACING,
                          project_name=config.LANGSMITH_PROJECT)

    runner = AgentRunner(graph)
    router = UpdateRouter(
        client=client,
        runner=runner,
        threads=threads,
        delivery=delivery,
        allowed_chats=config.allowed_chat_ids,
        upload_task_template=UPLOADED_FILE_TASK,
        data_dir=DATA_DIR,
        output_dir=OUTPUT_DIR,
        progress_for=lambda chat_id: ChatProgress(client, chat_id),
        tracer=tracer,
    )
    return Bot(client=client, router=router)


def main() -> None:
    if not settings.TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set in .env")
    if not settings.allowed_chat_ids:
        raise SystemExit(
            "TELEGRAM_ALLOWED_CHAT_IDS is empty. The agent runs shell commands "
            "on this machine, so the bot refuses to serve anyone until you list "
            "your own chat id."
        )

    bot = build_bot()
    print(f"Bot running. Authorised chats: {sorted(settings.allowed_chat_ids)}")
    bot.run_forever()


if __name__ == "__main__":
    main()
