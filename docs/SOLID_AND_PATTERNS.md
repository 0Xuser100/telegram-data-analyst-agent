# SOLID and design patterns

Every principle and pattern below is in the code, with the file, the reason, and
the test that keeps it honest. Nothing here was added for the sake of having it.

[README](../README.md) · [High-level design](HIGH_LEVEL_DESIGN.md) ·
[Low-level design](LOW_LEVEL_DESIGN.md) · [Testing](TESTING.md)

---

## Part 1 — SOLID

### S · Single responsibility

Each module has one job, and one reason to change.

| Module | Its one job | Changes when |
|---|---|---|
| `plumbing/telegram_client.py` | talk to the Bot API | Telegram changes its API |
| `plumbing/formatting.py` | turn text into what a chat accepts | the markup rules change |
| `conversation/approvals.py` | present a pending call and collect a decision | a new tool appears |
| `plumbing/artifacts.py` | find the charts a run produced | output conventions change |
| `agent/runner.py` | run and resume the graph | the LangGraph API changes |
| `conversation/delivery.py` | decide what the user sees | the reply format changes |
| `conversation/router.py` | decide what an update means | a new command or upload rule |
| `plumbing/thread_store.py` | remember which conversation a chat is on | the reset policy changes |
| `plumbing/tracing.py` | keep one trace per task | the tracing backend changes |

**Before:** `entrypoints/bot.py` was 470 lines doing all of it — HTTP, HTML,
approval rendering, image discovery, routing, and the loop.
**After:** 132 lines of wiring plus a polling loop.

The test that proves the split is real: `tests/test_artifacts.py` exercises chart
discovery with a directory and a string — no Telegram, no agent, no mocks.

### O · Open for extension, closed for modification

Two places where new behaviour means adding, not editing.

**A new tool to describe** — `conversation/approvals.py`:

```python
@describes("deploy")
def _describe_deploy(action: PendingAction) -> Card:
    return Card("🚀 Deploy", (f"<code>{action.args['target']}</code>",))
```

`describe()` never changes; it is a dict lookup with a generic fallback.
Guarded by `test_a_new_tool_needs_only_a_registry_entry`, which registers a
describer at runtime and removes it again.

**A new bot command** — `conversation/router.py`:

```python
COMMANDS = {"/start": "_command_help", "/help": "_command_help",
            "/new": "_command_new"}
```

Guarded by `test_a_new_command_needs_only_a_table_entry` (adds `/status` at
runtime) and `test_every_command_maps_to_a_method` (a typo in the table fails
immediately rather than when a user types it).

### L · Substitution

Anything shaped like an `Approver` can stand in for any other, and the run loop
cannot tell:

```python
run_to_completion(runner, thread_id, task, ConsoleApprover())   # entrypoints/cli.py
run_to_completion(runner, thread_id, task, LoudApprover())      # tests_e2e
```

Same for `Progress`: `ChatProgress` sends to Telegram, `SilentProgress` does
nothing, `FakeProgress` records. And for the client: `RetryingClient` wraps
`TelegramClient`, `FakeClient` replaces both.

The tests that pin it: `test_silent_progress_is_a_valid_reporter`,
`test_it_can_stand_in_for_the_real_client`, and the whole of
`tests/test_router.py`, which runs the real router against a fake client.

### I · Small interfaces

Nothing is forced to implement more than it uses.

```python
class Progress(Protocol):          # two methods
    def busy(self) -> None: ...
    def note(self, message: str) -> None: ...

class Approver(Protocol):          # one method
    def ask(self, actions: list[PendingAction]) -> None: ...
```

`Protocol` rather than a base class: the test doubles satisfy these by shape, and
inherit nothing. `SilentProgress` is 6 lines.

### D · Depend on what is passed in

No component reaches for its collaborators.

```python
UpdateRouter(client=..., runner=..., threads=..., delivery=...,
             allowed_chats=..., upload_task_template=..., progress_for=...)
AgentRunner(agent, progress=None, announce_after=4.0)
ResultDelivery(client, artifacts)
ArtifactCollector(output_dir="./output", clock=time.time)
```

`build_bot()` in `entrypoints/bot.py` is the single composition root — the only
function that knows every concrete class. That inversion is what makes 442 tests
run with no network, no API key, and no model: the tests do the wiring instead.

Even the clock is injected, so chart-discovery tests are deterministic
(`test_the_clock_is_injectable`).

---

## Part 2 — Design patterns

| Pattern | Where | Problem it solves |
|---|---|---|
| [Registry](#registry) | `approvals.DESCRIBERS` + `@describes` | an if/elif chain grew with every tool |
| [Strategy](#strategy) | `Approver`, `Progress` | two front-ends, one run loop |
| [Template method](#template-method) | `runner.run_to_completion` | three copies of the same approval loop |
| [Adapter](#adapter) | `progress.ChatProgress` | the runner must not know about chats |
| [Decorator](#decorator) | `retrying_client.RetryingClient` | flaky home connection, no change anywhere else |
| [Null object](#null-object) | `progress.SilentProgress`, `tracing.NullTracer` | the terminal has nothing to report to; tracing may be off |
| [Facade](#facade) | `telegram_bot.Bot` | `main` should not assemble a loop |
| [Value object](#value-object) | `PendingAction`, `Card` | a pending call must not change while it waits |
| [Repository](#repository) | `thread_store.ThreadStore`, `tracing.TraceStore` | "which conversation?" and "which open trace?" behind a few methods |
| [Composition root](#composition-root) | `entrypoints/bot.py` → `build_bot` | one place that knows the wiring |
| [Test double](#test-double) | `tests/conftest.py` | prove behaviour without a network |

### Registry

```python
DESCRIBERS: dict[str, Callable[[PendingAction], Card]] = {}

def describes(*tool_names: str):
    def register(describer):
        for name in tool_names:
            DESCRIBERS[name] = describer
        return describer
    return register

@describes("write_file", "edit_file")
def _describe_write(action): ...

def describe(action) -> Card:
    return DESCRIBERS.get(action.name, _describe_unknown)(action)
```

Ten tools, one lookup, no branching. The generic fallback means an unknown tool
still produces a usable card instead of an error.

### Strategy

The decision-asker and the progress reporter are both interchangeable
implementations chosen by the caller:

| Interface | Implementations |
|---|---|
| `Approver` | `TelegramApprover` (buttons) · `ConsoleApprover` (prompt) · `LoudApprover` (e2e) · `AlwaysApproves` (unit) |
| `Progress` | `ChatProgress` · `SilentProgress` · `FakeProgress` |

### Template method

```python
def run_to_completion(runner, thread_id, task, approver, max_rounds=12, **kwargs):
    result = runner.start(thread_id, task, **kwargs)
    for _ in range(max_rounds):
        actions = pending_actions(result.get("__interrupt__"))
        if not actions:
            return result
        approver.ask(actions)                       # ← the varying step
        result = runner.resume(thread_id, approver.decisions, **kwargs)
    raise TooManyApprovals(...)
```

The skeleton is fixed; the one varying step is delegated. This replaced three
hand-written copies of the loop (`entrypoints/cli.py`, the e2e test, and an earlier version
in the bot). The round cap is the reason a stuck agent cannot spend money
forever.

### Adapter

```python
class ChatProgress:
    def busy(self):  self._client.send_typing(self._chat_id)
    def note(self, message): self._client.send_message(self._chat_id, message)
```

A Telegram client has no `busy()`. This gives it one, so `agent/runner.py` never
imports a client and never learns what a chat is.

### Decorator

```python
class RetryingClient:
    def __getattr__(self, name):
        target = getattr(self._client, name)
        if name not in RETRIED_METHODS or not callable(target):
            return target                    # pass straight through
        def retrying(*args, **kwargs): ...   # attempts with growing backoff
        return retrying
```

Same surface, wrapped around a client. `build_bot` wraps the real one; tests pass
a bare fake. Deliberately selective:

- **retried** — sending, uploading, answering a callback, downloading;
- **not retried** — `get_updates` (the polling loop already loops) and
  `send_typing` (cosmetic);
- **not retried** — `TelegramError`: a rejected request is not a network fault,
  so repeating it just repeats the rejection.

Nothing above the client changed to gain this.

### Null object

```python
class SilentProgress:
    def busy(self): pass
    def note(self, message): pass
```

`AgentRunner` never checks whether it has a reporter. The terminal passes this
one because it prints its own prompts.

`tracing.NullTracer` is the same idea: when `LANGSMITH_TRACING` is false,
`build_tracer` returns one whose `start`, `finish`, `abandon` and `attached` all
do nothing, so the router needs no `if tracing_enabled` anywhere.

### Facade

```python
@dataclass(frozen=True)
class Bot:
    client: object
    router: object
    def run_forever(self):
        PollingLoop(self.client, self.router).run_forever()
```

`main()` becomes: check the settings, `build_bot()`, `run_forever()`. Tests reach
past it for `bot.router` when they want to drive one update at a time.

### Value object

```python
@dataclass(frozen=True)
class PendingAction:
    name: str
    args: dict = field(default_factory=dict)
    allowed_decisions: tuple[str, ...] = SUPPORTED_DECISIONS

    @property
    def offered_decisions(self) -> tuple[str, ...]:
        offered = tuple(d for d in self.allowed_decisions if d in SUPPORTED_DECISIONS)
        return offered or SUPPORTED_DECISIONS
```

A pending call waits on disk until someone taps a button, sometimes for days. It
being immutable means nothing can quietly edit what the card promised. `Card` is
frozen for the same reason. `offered_decisions` puts the "which buttons?" rule on
the object that knows the answer.

### Repository

```python
class ThreadStore:
    def generation(self, chat_id) -> int
    def start_new(self, chat_id) -> int
    def thread_id(self, chat_id) -> str
```

Storage is an implementation detail behind three methods. The router just asks
"which conversation is this chat on?"

### Composition root

`build_bot()` is the one function that imports the concrete classes and connects
them. Everything else receives what it needs. That is why swapping the client for
a retrying one, or the graph for a fake, is a one-line change in one place.

### Test double

`tests/conftest.py` holds hand-written doubles rather than mock objects:

| Double | Stands in for | Notable behaviour |
|---|---|---|
| `FakeClient` | `TelegramClient` | records messages, cards with keyboards, photos, downloads; can be told to fail |
| `FakeAgent` | the compiled graph | scripted results, **per-thread** pending interrupts, injectable delay and error |
| `FakeProgress` | `Progress` | counts `busy()`, collects notes |
| `ScriptedClient` / `ScriptedRouter` | the loop's collaborators | replay a script, then stop the loop |

`FakeAgent` keeping pending interrupts per thread matters: an earlier version
kept one global list, and a test passed that should have failed — a new
conversation appeared to inherit the old one's approval.

---

## Part 3 — What was considered and left out

| Not used | Why not |
|---|---|
| Chain of responsibility for `handle_message` | five ordered checks read better as a sequence than as five handler classes |
| Observer / composite progress | nothing needs two listeners; `CompositeProgress` would be speculative |
| Abstract base classes | `Protocol` gives substitutability without forcing inheritance, and the doubles satisfy it by shape |
| Factory class for the agent | four module-level `build_*` functions do the job; a class would add a receiver with no state |
| Command objects for decisions | LangGraph's `Command(resume=...)` already is one; wrapping it again buys nothing |
| Repository over the checkpointer | LangGraph owns that storage; a second abstraction over it would only hide behaviour |
| State machine for the conversation | there are two states (idle, paused) and one transition each way |

The rule applied throughout: a pattern goes in when it removes a real duplication
or a real coupling. Otherwise it is a layer to read past.

---

## Part 4 — How this is enforced

| Claim | Test |
|---|---|
| A new tool needs no renderer change | `test_a_new_tool_needs_only_a_registry_entry` |
| A new command needs no new branch | `test_a_new_command_needs_only_a_table_entry` |
| Every command in the table exists | `test_every_command_maps_to_a_method` |
| The run loop works for any approver | `test_each_gate_is_put_to_the_approver` |
| A stuck run stops | `test_a_stuck_run_gives_up` |
| The retry wrapper is invisible upward | `test_it_can_stand_in_for_the_real_client` |
| Only the right calls are retried | `test_long_polling_is_not_retried`, `test_a_real_error_is_not_retried` |
| Progress is optional | `test_silent_progress_is_a_valid_reporter` |
| The facade runs the loop | `test_the_facade_can_run_the_loop` |
| One summarizer, not two | `test_exactly_one_summarizer_is_registered` |
| The clock is injectable | `test_the_clock_is_injectable` |
| A restart re-attaches to the same trace | `test_a_resume_after_a_restart_finds_the_same_parent` |
| Tracing never breaks a run | `test_a_failure_to_open_is_swallowed` |

442 offline tests, ~12 seconds, 99% line coverage, no network and no model calls.
