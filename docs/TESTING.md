# Testing

Two parts: **how pytest works**, then **how this project's 554 tests are put
together**. If you have never used pytest, read part 1 first — everything in
part 2 is built out of those few ideas.

[README](../README.md) · [High-level design](HIGH_LEVEL_DESIGN.md) ·
[Low-level design](LOW_LEVEL_DESIGN.md) · [SOLID and patterns](SOLID_AND_PATTERNS.md)

---

# Part 1 — pytest, the whole idea

## 1. A test is a function that asserts

No classes to inherit, no special methods. A file named `test_*.py` holding
functions named `test_*`, using the plain `assert` keyword:

```python
def test_human_size():
    assert human_size(2048) == "2.0 KB"
```

Run `pytest`, and it finds them by that naming alone. That is the whole contract.

When an assert fails, pytest rewrites it to show you the values, not just
"False":

```
E       AssertionError: assert 'pythonexe a.py' == 'python a.py'
E         - python a.py
E         + pythonexe a.py
```

That output is why plain `assert` is enough — no `assertEqual` needed.

## 2. Fixtures: the setup a test asks for

A fixture is a named piece of setup. A test *asks* for it by putting its name in
the parameter list, and pytest builds it and passes it in.

```python
@pytest.fixture
def client():
    return FakeClient()

def test_a_greeting_gets_one_message(client):     # asks for it by name
    ...
```

Three things make fixtures worth understanding:

**They compose.** A fixture can ask for other fixtures, so setup builds up in
layers instead of being repeated:

```python
@pytest.fixture
def delivery(client, collector):        # both are fixtures themselves
    return ResultDelivery(client, collector)
```

**They can clean up.** `yield` splits setup from teardown; everything after the
`yield` runs when the test finishes, pass or fail:

```python
@pytest.fixture
def clean_output(sandbox):
    out = os.path.join(sandbox, "output")
    os.makedirs(out, exist_ok=True)
    yield out                       # ← the test runs here
    shutil.rmtree(out)              # ← always runs afterwards
```

**They have a lifetime.** `scope` decides how often the setup runs: `function`
(the default, once per test), `module`, or `session` (once for the whole run).
Use a wider scope only for setup that is expensive and read-only:

```python
@pytest.fixture(scope="module")     # building the agent takes seconds
def mod():
    import agent
    return agent
```

`autouse=True` applies a fixture to every test without being asked — for
environment setup, not for anything a test needs to see.

## 3. conftest.py: shared setup, found automatically

Fixtures in `conftest.py` are available to every test file beside it and below
it, with no import. That is the file where doubles and shared setup live. There
is exactly one rule to remember: **pytest imports `conftest.py` before it imports
any test module**, which makes it the right place for things that must happen
first (environment variables, in this project).

## 4. Parametrize: one test, many cases

```python
@pytest.mark.parametrize(("raw", "expected"), [
    ("/output/analysis.py",  "output/analysis.py"),
    ("./output/analysis.py", "output/analysis.py"),
    (r"output\analysis.py",  "output/analysis.py"),
])
def test_paths_are_normalised(raw, expected):
    assert tidy_path(raw) == expected
```

Each row is a separate test with its own name in the output, so a failure tells
you *which* case broke. This suite uses it 25 times — it is the cheapest way to
cover a table of inputs.

## 5. monkeypatch: change something, and have it changed back

The built-in fixture for temporary replacement. It undoes itself when the test
ends, which is what makes it safe:

```python
def test_oversized_image_goes_as_a_document(monkeypatch, tmp_path):
    monkeypatch.setattr(tc, "PHOTO_MAX_BYTES", 10)      # restored afterwards
    ...
```

`setattr`, `setitem`, `delenv`, `chdir` — all reversed automatically. Used 47
times here.

## 6. The other built-ins worth knowing

| Fixture | What it gives you | Used here |
|---|---|---|
| `tmp_path` | a fresh empty directory, unique per test | 18 times, for sqlite files and fake uploads |
| `capsys` | whatever the code printed | 4 times, to check terminal output |
| `monkeypatch` | temporary patching | 47 times |
| `request` | information about the running test | twice |

## 7. Expecting failure

```python
with pytest.raises(RuntimeError, match="kaboom"):
    runner.start(thread, "hi")
```

The test passes only if that exception is raised, and `match` is a regex against
its message. `pytest.fail("…")` fails on purpose; `pytest.skip("…")` skips.

## 8. Markers and selecting what to run

A marker is a label:

```python
pytestmark = pytest.mark.slow        # applies to the whole file
```

```bash
pytest -m "not slow"       # by marker
pytest -k approval         # by name substring
pytest tests/test_router.py::test_a_greeting_is_answered_in_one_message
```

Markers must be declared in the config, because this project runs with
`--strict-markers` — a typo in a marker name is then an error, not a silently
skipped label.

## 9. Configuration

All of it lives in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]           # where a bare `pytest` looks
pythonpath = [".", "tests"]     # so project modules and conftest import cleanly
addopts = "-q --strict-markers" # applied to every run
markers = ["slow: builds the whole deep agent graph (a few seconds, still offline)"]
```

`tests_e2e/` is deliberately **not** in `testpaths`: it calls the real API and
costs money, so a bare `pytest` never touches it.

---

# Part 2 — this project's suite

## How to run it

```bash
cd telegram-data-analyst-agent

uv run pytest                                    # everything, ~12 seconds
uv run pytest -m "not slow"                      # skip the graph-building files
uv run pytest tests/test_router.py -v            # one file, verbose
uv run pytest -k approval                        # only names containing "approval"
uv run pytest -x                                 # stop at the first failure
uv run pytest --cov=. --cov-report=term-missing  # with coverage

RUN_E2E=1 uv run pytest tests_e2e -v -s          # the real API (costs a few cents)
```

## Two guarantees

**Nothing leaves the machine.** Every Telegram call goes to a `FakeClient`, and
the model is never invoked. There is no way for the offline suite to spend money
or send a message.

**Nothing touches your real state.** `conftest.py` sets fake credentials before
any project module is imported, then moves the whole session into a temp
directory — so `checkpoints.sqlite`, `data/` and `output/` are never the real
ones, and your real bot token is never even loaded.

```python
# conftest.py, at import time
os.environ.update({"OPENAI_API_KEY": "sk-test-not-a-real-key",
                   "TELEGRAM_BOT_TOKEN": "123456:TEST-token", ...})

@pytest.fixture(scope="session", autouse=True)
def sandbox_cwd():
    os.chdir(_SANDBOX)          # a temp dir, removed at exit
```

Why the environment is set at import time and the chdir is a fixture: `Settings`
has required fields, so a missing key would break *collection* rather than fail a
test — but changing the working directory that early would break pytest's own
`testpaths` lookup.

## What is tested

One file per module, plus one for the layer rule: 18 files.

| File | Tests | What it covers |
|---|---|---|
| `test_formatting.py` | 58 | Markdown → HTML, splitting, sizes, paths, safe filenames |
| `test_layers.py` | 24 | Every import in every module: nothing may point at a higher layer |
| `test_prompts.py` | 53 | The rules: inspect-don't-read, reply format, chart menu, no unverified claims |
| `test_router.py` | 57 | What each message, file and button does; the command table; the trace lifecycle |
| `test_approvals.py` | 43 | A card per tool, the button rows, both approvers, the registry |
| `test_runner.py` | 22 | Start and resume, the "Working on it…" timing, thread ids, the shared loop |
| `test_tracing.py` | 22 | One trace per task: the store, re-attaching after a restart, closing, failing safely |
| `test_artifacts.py` | 22 | Finding charts, and catching a reply that invents one |
| `test_config.py` | 21 | Required settings, the chat-id parser, the tracing env copy |
| `test_telegram_client.py` | 17 | Sending, uploads, long-poll timeouts, error handling |
| `test_bot.py` | 16 | The polling loop and the wiring in `build_bot` |
| `test_agent_wiring.py` | 16 | The builders: model, checkpointer, one summarizer at 40k |
| `test_thread_store.py` | 15 | The conversation counter: restart, isolation, concurrency |
| `test_delivery.py` | 15 | What the user sees: answer, chart, next card |
| `test_retrying_client.py` | 13 | What is retried, what is not, what passes through |
| `test_main.py` | 12 | The terminal approval loop |
| `test_integration_flow.py` | 10 | Whole conversations: upload → approve → approve → chart |
| `test_backend.py` | 6 | The shell workspace and the sample CSV |

554 in total, from 419 test functions — `parametrize` accounts for the rest.

Coverage: 99% of the project's lines.

## The doubles

Hand-written, in `conftest.py` — not mock objects, so a change in a real class
that breaks its double shows up as a failing test rather than a passing lie.

| Double | Stands in for | Worth knowing |
|---|---|---|
| `FakeClient` | `TelegramClient` | records messages, cards with their keyboards, photos, downloads; `photo_error` and `download_error` make it fail on demand |
| `FakeAgent` | the compiled graph | `results` is a queue of answers; pending interrupts are tracked **per thread**, like the real checkpointer; `delay` and `error` are injectable |
| `FakeProgress` | `Progress` | counts `busy()`, collects notices |
| `FakeTracer` | `TaskTracer` | records which tasks were opened, attached, finished, abandoned |
| `ScriptedClient` / `ScriptedRouter` | the polling loop's neighbours | replay a script of update batches, then stop the loop |

`FakeAgent` keeping interrupts per thread matters: an earlier version kept one
global list and a test passed that should have failed — a new conversation
appeared to inherit the previous one's approval.

## The fixtures you will use

| Fixture | Gives you |
|---|---|
| `router` | a **real** `UpdateRouter` wired to fakes — the main way to test behaviour |
| `client` | the `FakeClient` that router is wired to |
| `graph` | the `FakeAgent`; set `graph.results = [...]` to script answers |
| `runner` | an `AgentRunner` with a 50 ms announce threshold instead of 4 s |
| `delivery`, `collector`, `threads`, `progress` | the individual parts |
| `clean_output` | an empty `output/`, cleared again afterwards |
| `sandbox` | the temp working directory |

And the builders, imported from `conftest`: `text_update`, `document_update`,
`callback_update`, `ai_result`, `interrupt_result`, plus the ready-made
`WRITE_FILE_ACTION` and `EXECUTE_ACTION`.

## Adding a test

```python
from conftest import ai_result, text_update

def test_a_greeting_is_answered_in_one_message(router, client, graph):
    """The reported regression: "hi" produced "Working on it…" and then the
    actual reply."""
    graph.results = [ai_result("Hi! What would you like to do?")]

    router.handle_message(text_update("hi"))

    assert client.texts == ["Hi! What would you like to do?"]
```

Three habits this suite keeps:

1. **Arrange, act, assert** — script the graph, send one update, check what
   reached the chat.
2. **The docstring says *why*, not what.** The name says what. Where a test
   guards a bug that actually happened, the docstring names the symptom, so
   nobody deletes it later thinking it is redundant.
3. **Assert on the boundary, not the internals.** `client.texts` and
   `client.cards` are what a user would see; `client.said("some words")` is the
   loose version when the exact wording is not the point.

## The real run

`tests_e2e/` is a separate suite: it reads your real key from `.env`, calls the
real API on `gpt-5.6-luna`, and runs one full analysis in a temp
directory, approving every gate automatically.

```bash
RUN_E2E=1 uv run pytest tests_e2e -v -s
```

Without `RUN_E2E=1` every test in it skips, so it can never run by accident. It
asserts the model is the configured one, a script file was written (the prompt
forbids inline `python -c`), a real PNG was saved, the reply contains the true
total of 840, the reply opens with what the data is, and there are no file paths
in it. Ten tests, about 40 seconds, a few cents.

It drives `runner.run_to_completion` — the same loop `entrypoints/cli.py` uses — with an
approver that always says yes. That is the one thing it does which a real user
would not.

## When something looks wrong

| Symptom | Cause |
|---|---|
| `ModuleNotFoundError: config` | run pytest from `telegram-data-analyst-agent/`; `pythonpath` is relative to it |
| `Field required … OPENAI_API_KEY` during collection | a project module was imported before `conftest.py` set the fake keys — import it inside a fixture or a test |
| A test writes into the real `output/` | it built something at import time; build it inside the test, after the sandbox chdir |
| `'slow' not found in markers` | `--strict-markers` is on: declare the marker in `pyproject.toml` |
| The suite is suddenly slow | a test is importing `agent` outside a `slow`-marked file |
| A test passes alone but fails in the suite | shared state — usually a module-level object built with the wrong working directory |
