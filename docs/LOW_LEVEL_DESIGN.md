# Low-level design

Module by module: what each one exposes, what it does, and the details that are
easy to get wrong. Signatures are current as of the code in this folder.

[README](../README.md) · [High-level design](HIGH_LEVEL_DESIGN.md) ·
[SOLID and patterns](SOLID_AND_PATTERNS.md) · [Testing](TESTING.md)

---

## Map

| Module | Lines | Exposes |
|---|---|---|
| [`config.py`](#configpy) | 55 | `Settings`, `get_settings`, `apply_tracing_env` |
| [`agent/prompts.py`](#agentpromptspy) | 155 | `SYSTEM_RULES`, `UPLOADED_FILE_TASK`, `ANALYSIS_PROMPT` |
| [`plumbing/backend.py`](#plumbingbackendpy) | 56 | `create_backend`, `ensure_sample_data`, `backend` |
| [`agent/builder.py`](#agentbuilderpy) | 100 | `build_model`, `build_checkpointer`, `build_summarizer`, `build_agent`, `thread_config` |
| [`agent/runner.py`](#agentrunnerpy) | 114 | `AgentRunner`, `run_to_completion`, `TooManyApprovals` |
| [`agent/pending.py`](#agentpendingpy) | 62 | `PendingAction`, `pending_actions`, `actions_in`, `decisions_for` |
| [`agent/policy.py`](#agentpolicypy) | 118 | `PERMISSIONS`-free predicates: `is_routine_execute`, `is_routine_write`, `is_sensitive_read` |
| [`agent/plan_visibility.py`](#agentplan_visibilitypy) | 51 | `PlanVisibilityMiddleware` |
| [`conversation/approvals.py`](#conversationapprovalspy) | 188 | `Card`, `describes`, `describe`, `raw_args`, `TelegramApprover`, `ConsoleApprover` |
| [`plumbing/artifacts.py`](#plumbingartifactspy) | 91 | `ArtifactCollector`, `mentioned_paths` |
| [`conversation/delivery.py`](#conversationdeliverypy) | 72 | `ResultDelivery`, `final_text` |
| [`conversation/router.py`](#conversationrouterpy) | 212 | `UpdateRouter` |
| [`plumbing/progress.py`](#plumbingprogresspy) | 36 | `Progress`, `SilentProgress`, `ChatProgress` |
| [`plumbing/formatting.py`](#plumbingformattingpy) | 96 | `to_html`, `chunk_text`, `human_size`, `tidy_path`, `shorten_interpreter`, `safe_filename` |
| [`plumbing/telegram_client.py`](#plumbingtelegram_clientpy) | 147 | `TelegramClient`, `TelegramError` |
| [`plumbing/retrying_client.py`](#plumbingretrying_clientpy) | 56 | `RetryingClient` |
| [`plumbing/thread_store.py`](#plumbingthread_storepy) | 56 | `ThreadStore` |
| [`plumbing/tracing.py`](#plumbingtracingpy) | 168 | `TaskTracer`, `TraceStore`, `NullTracer`, `build_tracer` |
| [`entrypoints/bot.py`](#entrypointsbotpy) | 132 | `Bot`, `build_bot`, `PollingLoop`, `main` |
| [`entrypoints/cli.py`](#entrypointsclipy) | 61 | `run_analysis`, `main` |

---

## config.py

```python
class Settings(BaseSettings)          # env_file=".env"
    LANGSMITH_TRACING: bool           # required
    LANGSMITH_ENDPOINT: str           # required
    LANGSMITH_API_KEY: str            # required
    LANGSMITH_PROJECT: str            # required
    OPENAI_API_KEY: str               # required
    OPENAI_MODEL: str                 # required
    TELEGRAM_BOT_TOKEN: str | None = None
    TELEGRAM_ALLOWED_CHAT_IDS: str = ""
    TELEGRAM_POLL_TIMEOUT: int = 30
    allowed_chat_ids -> set[int]      # property

get_settings() -> Settings            # @lru_cache: one instance per process
apply_tracing_env(settings=None) -> None
```

- `allowed_chat_ids` parses `"555, 777"`, ignores empty parts, and raises on a
  non-numeric id — a loud failure beats a silently narrower allowlist. **Empty
  means nobody**, never everybody.
- `apply_tracing_env` copies the four LangSmith values into `os.environ`.
  pydantic-settings reads `.env` without exporting it, and the tracing client
  only reads the environment. Both entry points call this **before** importing
  the graph.

## agent/prompts.py

Three strings. This is where behaviour lives — changing them changes what the
agent does far more than changing code.

| Constant | Used by | Placeholders |
|---|---|---|
| `SYSTEM_RULES` | both entry points, via `agent.build_agent` | `{python_path}`, `{output_dir}` |
| `UPLOADED_FILE_TASK` | a Telegram upload with no caption | `{file_path}`, `{output_dir}` |
| `ANALYSIS_PROMPT` | `entrypoints/cli.py` (a `ChatPromptTemplate`) | `{data_path}`, `{python_path}`, `{output_dir}`, `{plot_name}` |

`SYSTEM_RULES` sections, in order: execution rules · scope · looking at the data
· output location · choosing the chart · never claim unverified work · answering.

Rules with a scar behind them:

- **`{python_path}` is mandatory** — `python` is often not on `PATH` on Windows.
- **No `read_file` on a data file** — a 60-column CSV forced three compactions in
  five seconds. Write an inspect script instead: shape, dtypes, `head(10)`,
  `describe()` (transposed and capped for wide files), missing counts.
- **No inline `python -c`, no heredoc** — a file on disk stays reviewable after
  the fact, and it is the only shape `is_routine_execute` will let through
  without a card. Inline
  code hides what is about to run.
- **`palette` without `hue`** is deprecated in seaborn and raises.
- **No `plt.show()`** — it blocks forever headless.
- **The reply never names a file** — the chart is attached automatically.

## plumbing/backend.py

```python
create_backend(root_dir=".") -> LocalShellBackend
ensure_directories() -> None                       # data/ and output/
ensure_sample_data(target=None) -> str             # "./data/sales_data.csv"
backend                                            # the app's shared instance
SAMPLE_ROWS                                        # 5 rows, 33 units, $840
```

`LocalShellBackend`, not `FilesystemBackend`: without command execution the agent
can write a script but never run it.

**Gotcha:** the backend resolves `root_dir` when it is constructed, and
`backend` is built at import time. A test that changes the working directory must
build its own (`ensure_sample_data(create_backend())`).

## agent/builder.py

```python
build_model(settings=None) -> ChatOpenAI
build_checkpointer(db_path="checkpoints.sqlite") -> SqliteSaver
build_summarizer(model, target_backend=None) -> SummarizationMiddleware
build_agent(model=None, checkpointer=None, target_backend=None,
            summarizer=None, python_path=sys.executable,
            output_dir="./output") -> CompiledStateGraph
thread_config(thread_id) -> {"configurable": {"thread_id": str}}

CHECKPOINT_DB = "checkpoints.sqlite"
COMPACT_AT_TOKENS = 40_000
KEEP_MESSAGES = 6
INTERRUPT_ON = {"execute": True, "write_file": True,
                "read_file": False, "ls": False}

model, checkpointer, summarizer, agent      # the app's defaults
```

- **The key is passed explicitly.** pydantic-settings does not export `.env`, so
  `ChatOpenAI`'s own `OPENAI_API_KEY` lookup would miss it.
- **`SqliteSaver` with `check_same_thread=False`.** The graph runs on a worker
  thread; `SqliteSaver` serialises access with its own lock
  (`langgraph/checkpoint/sqlite/__init__.py:95`) and sets `journal_mode=WAL`. Not
  `from_conn_string`, which closes the connection when its `with` block exits.
- **The summarizer replaces the deepagents default** because it carries the same
  `.name`; `_apply_custom_middleware` (`deepagents/graph.py:201`) matches by name
  and swaps in place. Two summarizers would compact twice.
- **`truncate_args_settings` is passed explicitly.** The constructor defaults it
  to `None`, which would silently drop the tool-arg clipping the deepagents
  factory normally configures.
- **Read-only tools are not gated**, or every run becomes button-tapping.

## agent/runner.py

```python
ANNOUNCE_AFTER = 4.0

class AgentRunner:
    __init__(agent, progress: Progress | None = None, announce_after=4.0)
    waiting_for(thread_id) -> list[PendingAction]
    start(thread_id, task, **kwargs) -> dict          # task: str | list[message]
    resume(thread_id, decisions, **kwargs) -> dict
    _invoke(thread_id, payload, notice="Working on it…", announce_after=None,
            progress=None, run_name=None, metadata=None, attach=None) -> dict

run_to_completion(runner, thread_id, task, approver, max_rounds=12, **kwargs) -> dict
class TooManyApprovals(RuntimeError)
```

`_invoke` is the only place that touches the graph:

1. resolve the delay (`announce_after` argument, else the instance default) —
   resolved at call time, never as a default argument, which would freeze the
   value at import;
2. `progress.busy()` — the typing dot;
3. start a **daemon** thread running `agent.invoke` (a pool would join at
   interpreter exit, so Ctrl-C mid-analysis would hang);
4. if `delay <= 0`, send the notice immediately — going through `join(0)` would
   race the worker and skip the notice whenever it finished first;
5. otherwise `join(delay)`, and announce only if it is still alive;
6. `join()` to the end, then re-raise anything the worker caught.

`waiting_for` reads state from the checkpointer, not memory, which is what makes
an approval survive a restart.

`run_to_completion` is the loop both front-ends use: start, `approver.ask(...)`,
`resume(approver.decisions)`, repeat, `TooManyApprovals` after `max_rounds`.

## agent/pending.py

What the agent stopped on, as data. No front-end: a `PendingAction` says which
tool wants to run and with which arguments, never how a human is asked.

This is the agent layer because a paused run is agent state. It used to live in
`conversation/approvals.py`, which made `runner` import upward — the one place
the layer rule was broken before `tests/test_layers.py` existed.

```python
SUPPORTED_DECISIONS = ("approve", "reject")     # edit/respond need a conversation
REJECT_MESSAGE = "User rejected this action. Do not retry it."

@dataclass(frozen=True)
class PendingAction:
    name: str
    args: dict = {}
    allowed_decisions: tuple[str, ...] = SUPPORTED_DECISIONS
    offered_decisions -> tuple[str, ...]        # filtered, never empty

pending_actions(interrupts) -> list[PendingAction]   # flatten the interrupt
actions_in(result) -> list[PendingAction]            # what an invoke waits on
decisions_for(choice, count) -> list[dict]           # the resume payload
```

`decisions_for` builds one decision per pending action, in order: LangGraph
resumes with a list, and a mismatched length silently drops an approval.

## agent/policy.py

Decides what runs without asking. Pure functions over the tool call, so the
whole policy is testable without building a graph.

`is_routine_execute(request)` auto-approves exactly one command shape: the
pinned interpreter, one `.py` file, resolving inside `output/`. A whitelist,
because blacklisting shell metacharacters is a game you lose. Anything
unparseable returns False — the failure mode has to be asking, never running.

`is_routine_write(request)` covers `write_file` and `edit_file` alike.
`is_sensitive_read(request)` raises a card for `.env`, the checkpoint database,
`.git` and `.venv`, which `read_file` reached silently before.

Containment resolves paths rather than comparing strings, so `output/../x.py`
is caught. That work would have belonged to `FilesystemPermission`, but
deepagents refuses `permissions` alongside a backend that can execute commands
— the only kind this app can use — so it is ours, and `interrupt_on` can only
ask rather than refuse.

**The honest sentence, repeated here because it matters:** this is a scope
control, not a security control. `execute` is `subprocess.run(shell=True)` with
no sandbox, and the predicate constrains the command, never the contents of the
script it runs.

## agent/plan_visibility.py

`PlanVisibilityMiddleware` appends the agent's current todo list to the system
prompt on every model call.

`TodoListMiddleware` injects only a static prompt; the plan itself arrives as a
tool result, which the summarizer evicts once a run gets long. `state["todos"]`
survives that, but nothing read it back — so a long analysis forgot what it set
out to do and finished something else. Re-attaching costs a few hundred tokens
a turn and scales with run length, where a larger compaction budget only defers
the failure.

## conversation/approvals.py

The card and the question. Imports its data types from `agent/pending.py` and
re-exports them, so a front-end has one import site for anything approval-shaped.

```python
DETAILS_ACTION = "details"                      # 🔍 — not a decision

@dataclass(frozen=True)
class Card:
    title: str
    detail: tuple[str, ...] = ()
    as_html() -> str

DESCRIBERS: dict[str, Callable[[PendingAction], Card]]
describes(*tool_names)                          # decorator, registers a describer
describe(action) -> Card                        # unknown tools -> generic card
raw_args(args, limit=3200) -> str
decisions_for(choice, count) -> list[dict]

class Approver(Protocol):  ask(actions) -> None
class TelegramApprover:    ask(...), show_details(...)
class ConsoleApprover:     ask(...)             # .decisions after each round
```

Registered describers:

| Tools | Card |
|---|---|
| `write_file`, `edit_file` | `📝 Write a file` · path · kind · lines · size |
| `execute`, `shell`, `bash`, `run_command` | `▶️ Run a command` · shortened command · "Runs on this machine." |
| `read_file`, `ls`, `glob`, `grep` | `📂 <tool>` · target |
| anything else | `🔧 <tool>` · up to 6 args, values clipped at 120 chars |

Every value goes through `html.escape`: tool args are model output and must not
be able to inject markup. `ConsoleApprover` resets `decisions` at the start of
each `ask`, so a second round cannot resend the first round's answers.

## plumbing/artifacts.py

```python
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif")
mentioned_paths(text) -> list[str]

class ArtifactCollector:
    __init__(output_dir="./output", clock=time.time)
    start_run(key) -> None            # key is the chat id
    forget(key) -> None
    new_images(key, text="") -> list[str]
    warning_for(text, images_sent) -> str | None
```

- `start_run` records `clock() - 1`; the second of slack absorbs filesystem
  mtime granularity.
- `new_images` finds files two ways — paths named in the reply, and anything in
  `output_dir` modified since the run started — then filters out what this
  conversation has already been sent. Since the reply no longer names files, the
  mtime scan is the primary path.
- `warning_for` returns text rather than sending it, which is why it can be
  tested with a directory and a string. Two cases: a named file that does not
  exist, or a chart described when nothing was produced (both a chart word *and*
  a made word must match, so "no chart was created" does not trip it).

## conversation/delivery.py

```python
final_text(result) -> str                       # the last non-empty AI message
class ResultDelivery:
    __init__(client, artifacts)
    start_run(chat_id), forget(chat_id)
    deliver(chat_id, result) -> None
    send_images(chat_id, text) -> None
```

`deliver` asks `actions_in(result)`: if anything is pending, one card per action
and nothing else; otherwise the answer, then the images. `final_text` flattens
content blocks and skips empty AI messages — after a few tool calls the history
is dozens of messages and only the last answer is relayed. Photos are sent with
**no caption**: the reply never names files, and repeating the filename under the
picture would put that noise back. A failed upload is reported but never loses
the text that already arrived.

## conversation/router.py

```python
class UpdateRouter:
    __init__(client, runner, threads, delivery, allowed_chats,
             upload_task_template, data_dir="./data",
             output_dir="./output", progress_for=None)
    COMMANDS = {"/start": "_command_help", "/help": "_command_help",
                "/new": "_command_new"}
    handle(update)            # message | callback_query, anything else ignored
    handle_message(message)
    handle_document(message)
    handle_callback(callback)
    is_allowed(chat_id) -> bool

ALLOWED_UPLOAD_EXT = (".csv", ".tsv", ".json", ".xlsx", ".xls", ".txt")
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
```

`handle_message`, in order — the order is the design:

1. **allowlist** — an unknown chat is dropped silently and logged locally;
2. **commands** — looked up in `COMMANDS`, so `/help` and `/new` work even while
   an approval is pending;
3. **pending guard** — never start a second run on a paused conversation;
4. **document** — delegate to `handle_document`;
5. **empty** — a hint;
6. **anything else** — the text is the task.

`handle_document`: sanitise the name (`safe_filename`), refuse an unreadable
extension, refuse over 20 MB (the bot download limit), say "Downloading …",
download, build the task (a caption replaces the canned one), **start a new
conversation**, then run with the notice up front. The order matters — a refused
or failed upload must not burn a conversation.

`handle_callback`: clear the button spinner first (Telegram shows it until you
do), reject unknown callback data, report a stale card, handle 🔍 without
resuming, then announce the decision and resume.

## plumbing/progress.py

```python
class Progress(Protocol):  busy(); note(message)
class SilentProgress:      # does nothing — the terminal shows its own prompts
class ChatProgress:        # busy -> send_typing, note -> send_message
```

Three tiny classes so `agent/runner.py` never imports a Telegram client.

## plumbing/formatting.py

```python
MAX_MESSAGE = 3900          # 4096 minus room for our own markers
chunk_text(text, limit=MAX_MESSAGE) -> list[str]
to_html(text) -> str
strip_html(text) -> str
human_size(n) -> str
tidy_path(raw) -> str
shorten_interpreter(command) -> str
safe_filename(name) -> str
```

- `to_html` handles `**bold**`, `*italic*`, headings, `` `code` ``, fenced blocks
  and bullets. Code spans are pulled out *first* so their contents are never
  reinterpreted, then everything is escaped, then the markup is applied. The
  patterns use `[ \t]` rather than `\s` deliberately: `\s` matches newlines and
  would swallow the blank lines that separate sections.
- `chunk_text` splits on line boundaries, and splits a single enormous line by
  force. Nothing is lost — a test asserts `"".join(chunks) == text`.
- `shorten_interpreter` turns a 60-character interpreter path into `python`. The
  version and the `.exe` suffix are matched separately; one `[\d.]*` class
  swallowed the dot and produced `pythonexe`.
- `safe_filename` strips every path component: `../../secret.json` must not
  escape `data/`. Truncated to 80 characters.

## plumbing/telegram_client.py

```python
PHOTO_MAX_BYTES = 10 * 1024 * 1024
class TelegramError(RuntimeError)

class TelegramClient:
    __init__(token, poll_timeout=30)
    get_updates(offset) -> list[dict]
    download_file(file_id, dest_dir, filename) -> str
    send_message(chat_id, text, markdown=True)
    send_html(chat_id, html_text, keyboard=None)
    send_photo(chat_id, path, caption=None)
    send_document(chat_id, path, caption=None)
    send_typing(chat_id)
    answer_callback(callback_query_id, text="")
```

- `get_updates` is built by hand rather than through `_call`, because there are
  two timeouts: Telegram's long-poll and the HTTP read. **The HTTP one must be
  longer** (`poll + 10`) or every poll ends in a client-side timeout.
- `send_message` renders Markdown, and falls back to plain text if Telegram
  rejects the entities — an unparseable reply must still arrive. `send_html` does
  the same and **keeps the keyboard**, so markup can never block a decision.
- `send_photo` falls back to `sendDocument` above 10 MB, which `sendPhoto`
  refuses.
- `send_typing` swallows its own errors: it is cosmetic and must never take a run
  down.
- `download_file` fetches from the `/file/` host, a different base URL from the
  method endpoints. Bots may download at most 20 MB.

## plumbing/retrying_client.py

```python
TRANSIENT = (ConnectionError, Timeout, ChunkedEncodingError)
RETRIED_METHODS = ("send_message", "send_html", "send_photo",
                   "send_document", "answer_callback", "download_file")

class RetryingClient:
    __init__(client, attempts=3, backoff=1.0, sleep=time.sleep)
```

`__getattr__` wraps only the listed methods; everything else — `get_updates`,
`send_typing`, attributes, anything added later — passes straight through.
Backoff grows (`backoff * attempt`). A `TelegramError` is a rejected request, not
a network fault, so it is not retried. `get_updates` is not retried either: the
polling loop already loops.

## plumbing/thread_store.py

```python
class ThreadStore:
    __init__(db_path)                 # CREATE TABLE IF NOT EXISTS chat_threads
    generation(chat_id) -> int        # 0 if never reset
    start_new(chat_id) -> int         # bump, return the new value
    thread_id(chat_id) -> str         # "555" at gen 0, else "555-2"
```

```sql
CREATE TABLE chat_threads (chat_id INTEGER PRIMARY KEY, generation INTEGER NOT NULL)
```

Its own connection on the checkpointer's file: `SqliteSaver` locks the one it
owns, so sharing it would be racing. `check_same_thread=False` with a
`threading.Lock` around every access. Generation 0 keeps the bare chat id, so
threads created before this table existed stay reachable — including one holding
a pending approval.

## plumbing/tracing.py

```python
class NullTracer:                 # every method a no-op; used when tracing is off
class TraceStore:
    save(thread_id, run_id, headers); load(thread_id); drop(thread_id)
class TaskTracer:
    __init__(store, client=None, run_tree_factory=RunTree, project_name=None)
    start(thread_id, name, inputs=None)     # open the parent run
    parent_for(thread_id) -> dict | None    # headers for the next invoke
    attached(thread_id)                     # context manager
    finish(thread_id, outputs=None)         # close it: the task answered
    abandon(thread_id, reason="abandoned")  # close it: /new, or a new upload
build_tracer(db_path, enabled, project_name=None)
```

Without this, every `invoke` is its own root run, so one analysis appears as four
or five unrelated traces. `start` posts a parent run and saves
`RunTree.to_headers()` — the dotted order plus baggage — in a `thread_traces`
table beside the conversation. Every later invoke re-attaches with
`tracing_context(parent=headers)`, so the resumes become children of the same
tree. **On disk, not in memory**, so a resume days later, or after a restart,
still lands in the right trace.

Details that matter:

- **The context must be entered on the worker thread.** `contextvars` are not
  inherited by a new thread, so `runner._invoke` takes an `attach` factory and
  enters it *inside* `work()`. Entering it on the caller thread would leave every
  run parentless.
- **`start` closes any previous parent** for that conversation, or a task nobody
  approved would adopt the next task's runs.
- **Every call is wrapped.** A LangSmith outage prints a line and is ignored:
  tracing must never take a run down. `_close` drops the stored headers even when
  the update call fails, so a dead trace cannot capture future runs.
- **`build_tracer` returns `NullTracer` when `LANGSMITH_TRACING` is false**, so no
  caller needs a conditional.

```sql
CREATE TABLE thread_traces (thread_id TEXT PRIMARY KEY,
                            run_id TEXT NOT NULL, headers TEXT NOT NULL)
```

## entrypoints/bot.py

```python
class PollingLoop:
    __init__(client, router); run_forever()

@dataclass(frozen=True)
class Bot:
    client; router; run_forever()

build_bot(client=None, graph=None, config=None) -> Bot
main() -> None
```

`build_bot` is the only place that knows every part. It wraps the real client in
`RetryingClient`, builds the collector, delivery, store and runner, and hands the
router a `progress_for` factory so progress reaches the chat that asked.

`main()` refuses to start without a token or an allowlist — the allowlist is the
only real access control, so empty must mean nobody.

`PollingLoop.run_forever` advances the offset past each handled update (otherwise
every restart replays the backlog) and catches everything: a poll error is
logged and polling continues; a handler error is logged with a traceback and the
chat is told, unless it is not on the allowlist — even the failure notice must
not confirm the bot exists.

## entrypoints/cli.py

```python
DATA_FILE = "./data/sales_data.csv"
PLOT_NAME = "sales_plot.png"
MAX_APPROVAL_ROUNDS = 12

run_analysis(runner, approver, thread_id) -> str
main() -> None
```

Fills `ANALYSIS_PROMPT`, calls `run_to_completion` with a `ConsoleApprover`, and
prints `final_text`. A fresh `uuid7` thread per run, so the CLI remembers
nothing between runs.

The previous version called `result.interrupts` and `result.value["messages"]` on
a plain dict and raised `AttributeError` at the first approval. That is what
`tests/test_main.py` now guards.

---

## Cross-cutting details

### Threading

Only the graph runs off the main thread, on one daemon thread per invoke.
Everything touching sqlite is either serialised by `SqliteSaver`'s own lock or by
`ThreadStore`'s. Progress messages are sent from the main thread while the worker
runs, which is the whole point of the arrangement.

### Where paths come from

`sys.executable` for the interpreter; `./data` and `./output` relative to the
working directory; `checkpoints.sqlite` likewise. Run the bot from the project
folder — or set the working directory first, as the tests do.

### Errors, by layer

| Layer | Policy |
|---|---|
| `telegram_client` | raises `TelegramError`; falls back on markup failures; swallows typing failures |
| `retrying_client` | retries transient network errors, then re-raises |
| `runner` | re-raises whatever the worker caught, on the caller's thread |
| `router` | reports refusals to the chat; lets real errors out |
| `PollingLoop` | catches everything, logs, tells the chat, keeps going |

### Test seams

Every constructor takes its collaborators, which is what the suite uses:
`FakeClient` (records messages, cards, photos), `FakeAgent` (scripted results,
per-thread pending interrupts like the real checkpointer), `FakeProgress`, a
temp-file `ThreadStore`, and an injectable `clock` on `ArtifactCollector`.
See [TESTING.md](TESTING.md).
