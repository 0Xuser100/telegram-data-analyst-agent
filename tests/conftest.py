"""Shared test setup.

Fake credentials are set at import time, before any project module loads, so a
leaked request can never carry the real token. The session runs in a temp
directory, so the real `checkpoints.sqlite`, `data/` and `output/` are untouched.
No test does network I/O and the model is never invoked.
"""

import atexit
import os
import shutil
import tempfile
from contextlib import contextmanager

os.environ.update({
    "LANGSMITH_TRACING": "false",
    "LANGSMITH_ENDPOINT": "https://api.smith.langchain.com",
    "LANGSMITH_API_KEY": "lsv2_pt_test",
    "LANGSMITH_PROJECT": "test-project",
    "OPENAI_API_KEY": "sk-test-not-a-real-key",
    "OPENAI_MODEL": "gpt-4.1-mini-2025-04-14",
    "TELEGRAM_BOT_TOKEN": "123456:TEST-token",
    "TELEGRAM_ALLOWED_CHAT_IDS": "555,777",
    "TELEGRAM_POLL_TIMEOUT": "1",
})

_SANDBOX = tempfile.mkdtemp(prefix="deepagents-tests-")
atexit.register(shutil.rmtree, _SANDBOX, ignore_errors=True)

import pytest  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

ALLOWED_CHAT = 555
OTHER_ALLOWED_CHAT = 777
BLOCKED_CHAT = 999


@pytest.fixture(scope="session", autouse=True)
def sandbox_cwd():
    """Run the whole session inside the sandbox.

    A fixture, not a chdir at import time, which would break pytest's own
    `testpaths` lookup.
    """
    original = os.getcwd()
    os.chdir(_SANDBOX)
    try:
        yield _SANDBOX
    finally:
        os.chdir(original)


# --------------------------------------------------------------------------
# doubles
# --------------------------------------------------------------------------

class FakeInterrupt:
    """Stands in for a LangGraph `Interrupt`: only `.value` is read."""

    def __init__(self, value: dict):
        self.value = value


class FakeTask:
    def __init__(self, interrupts):
        self.interrupts = tuple(interrupts)


class FakeState:
    def __init__(self, tasks):
        self.tasks = tuple(tasks)


class FakeAgent:
    """Scriptable stand-in for the compiled graph.

    `results` is a queue, one per `invoke`. `pending` is what `get_state`
    reports, which is how the bot reads interrupts back.
    """

    def __init__(self):
        self.results: list[dict] = []
        self.invocations: list[tuple] = []
        self.delay: float = 0.0
        self.error: BaseException | None = None
        # Per thread, like the real checkpointer: a new conversation must not
        # inherit the interrupt another one is paused on.
        self._paused: dict[str | None, list] = {}

    @staticmethod
    def _thread_of(config) -> str | None:
        return (config or {}).get("configurable", {}).get("thread_id")

    def invoke(self, payload, config=None):
        self.invocations.append((payload, config))
        if self.delay:
            import time
            time.sleep(self.delay)
        if self.error:
            raise self.error
        result = self.results.pop(0) if self.results else {"messages": []}
        self._paused[self._thread_of(config)] = list(result.get("__interrupt__", []))
        return result

    def get_state(self, config):
        thread = self._thread_of(config)
        interrupts = self._paused[thread] if thread in self._paused             else self._paused.get(None, [])
        return FakeState([FakeTask(interrupts)] if interrupts else [])

    @property
    def pending(self) -> list:
        """Every thread's interrupts, for tests that only count them."""
        return [item for items in self._paused.values() for item in items]

    @pending.setter
    def pending(self, interrupts) -> None:
        """Pause every thread, for tests that skip straight to an approval."""
        self._paused = {None: list(interrupts)}

    @property
    def threads(self) -> list[str]:
        return [config["configurable"]["thread_id"] for _, config in self.invocations]


class FakeClient:
    """Records what would have been sent to Telegram.

    Same surface as TelegramClient, which is what lets every layer above it be
    tested without a network.
    """

    def __init__(self):
        self.messages: list[tuple[int, str]] = []
        self.html: list[tuple[int, str, list | None]] = []
        self.photos: list[tuple[int, str]] = []
        self.documents: list[tuple[int, str]] = []
        self.typing: list[int] = []
        self.answered: list[str] = []
        self.downloads: list[tuple[str, str, str]] = []
        self.download_error: Exception | None = None
        self.photo_error: Exception | None = None

    def send_message(self, chat_id, text, markdown=True):
        self.messages.append((chat_id, text))

    def send_html(self, chat_id, html_text, keyboard=None):
        self.html.append((chat_id, html_text, keyboard))

    def send_photo(self, chat_id, path, caption=None):
        if self.photo_error:
            raise self.photo_error
        self.photos.append((chat_id, path))

    def send_document(self, chat_id, path, caption=None):
        self.documents.append((chat_id, path))

    def send_typing(self, chat_id):
        self.typing.append(chat_id)

    def answer_callback(self, callback_query_id, text=""):
        self.answered.append(callback_query_id)

    def download_file(self, file_id, dest_dir, filename):
        if self.download_error:
            raise self.download_error
        self.downloads.append((file_id, dest_dir, filename))
        os.makedirs(dest_dir, exist_ok=True)
        path = os.path.join(dest_dir, filename)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("Date,Product,Units Sold,Revenue\n2025-08-01,Widget A,10,250\n")
        return path

    # -- views used by tests ----------------------------------------------

    @property
    def texts(self) -> list[str]:
        return [text for _, text in self.messages]

    @property
    def cards(self) -> list[str]:
        return [body for _, body, _ in self.html]

    def said(self, needle: str) -> bool:
        return any(needle.lower() in text.lower() for text in self.texts)


class FakeTracer:
    """Records the task-trace calls the router makes."""

    def __init__(self):
        self.started: list[tuple[str, str]] = []
        self.finished: list[str] = []
        self.abandoned: list[tuple[str, str]] = []
        self.attached_to: list[str] = []

    def start(self, thread_id, name, inputs=None):
        self.started.append((thread_id, name))

    def parent_for(self, thread_id):
        return {"langsmith-trace": f"dotted-{thread_id}"}

    def finish(self, thread_id, outputs=None):
        self.finished.append(thread_id)

    def abandon(self, thread_id, reason="abandoned"):
        self.abandoned.append((thread_id, reason))

    @contextmanager
    def attached(self, thread_id):
        self.attached_to.append(thread_id)
        yield


class FakeProgress:
    def __init__(self):
        self.busy_calls = 0
        self.notes: list[str] = []

    def busy(self) -> None:
        self.busy_calls += 1

    def note(self, message: str) -> None:
        self.notes.append(message)


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

@pytest.fixture
def sandbox(sandbox_cwd) -> str:
    return sandbox_cwd


@pytest.fixture
def clean_output(sandbox):
    """An empty ./output for tests that assert which figures were produced."""
    out = os.path.join(sandbox, "output")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out, exist_ok=True)
    yield out
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out, exist_ok=True)


@pytest.fixture
def client():
    return FakeClient()


@pytest.fixture
def graph():
    return FakeAgent()


@pytest.fixture
def threads(tmp_path):
    from analyst.plumbing.thread_store import ThreadStore
    return ThreadStore(str(tmp_path / "threads.sqlite"))


@pytest.fixture
def collector(clean_output):
    from analyst.plumbing.artifacts import ArtifactCollector
    return ArtifactCollector(clean_output)


@pytest.fixture
def delivery(client, collector):
    from analyst.conversation.delivery import ResultDelivery
    return ResultDelivery(client, collector)


@pytest.fixture
def progress():
    return FakeProgress()


@pytest.fixture
def runner(graph, progress):
    from analyst.agent.runner import AgentRunner
    # 50ms rather than 4s: the tests assert on the announcement, not on waiting.
    return AgentRunner(graph, progress=progress, announce_after=0.05)


@pytest.fixture
def tracer():
    return FakeTracer()


@pytest.fixture
def router(client, runner, threads, delivery, tracer):
    """A real router with fake edges: fake client, fake graph, temp database."""
    from analyst.plumbing.progress import ChatProgress
    from analyst.agent.prompts import UPLOADED_FILE_TASK
    from analyst.conversation.router import UpdateRouter

    return UpdateRouter(
        client=client,
        runner=runner,
        threads=threads,
        delivery=delivery,
        allowed_chats={ALLOWED_CHAT, OTHER_ALLOWED_CHAT},
        upload_task_template=UPLOADED_FILE_TASK,
        data_dir="./data",
        output_dir="./output",
        progress_for=lambda chat_id: ChatProgress(client, chat_id),
        tracer=tracer,
    )


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------

def text_update(text: str, chat_id: int = ALLOWED_CHAT) -> dict:
    return {"chat": {"id": chat_id}, "text": text}


def document_update(file_name: str = "sales.csv", chat_id: int = ALLOWED_CHAT,
                    size: int = 1024, caption: str | None = None) -> dict:
    message = {
        "chat": {"id": chat_id},
        "document": {"file_id": "FILE-1", "file_name": file_name, "file_size": size},
    }
    if caption:
        message["caption"] = caption
    return message


def callback_update(data: str, chat_id: int = ALLOWED_CHAT, cb_id: str = "CB-1") -> dict:
    return {"id": cb_id, "data": data, "message": {"chat": {"id": chat_id}}}


def ai_result(text: str) -> dict:
    return {"messages": [HumanMessage("do it"), AIMessage(text)]}


def interrupt_result(*action_requests: dict, allowed=("approve", "reject")) -> dict:
    """A graph result that is paused on one interrupt."""
    value = {
        "action_requests": list(action_requests),
        "review_configs": [
            {"action_name": action.get("name"), "allowed_decisions": list(allowed)}
            for action in action_requests
        ],
    }
    return {"messages": [], "__interrupt__": [FakeInterrupt(value)]}


WRITE_FILE_ACTION = {
    "name": "write_file",
    "args": {"file_path": "/output/analysis.py", "content": "import pandas as pd\n" * 20},
}
EXECUTE_ACTION = {
    "name": "execute",
    "args": {"command": r"D:\proj\.venv\Scripts\python.exe ./output/analysis.py"},
}
