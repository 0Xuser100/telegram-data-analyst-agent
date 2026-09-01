# Telegram Data Analyst Agent

Send this agent a CSV file. It writes its own Python script, runs it, makes a
chart, and sends you a short summary. It asks you before it writes any file or
runs any command.

You can use it from the terminal or from a Telegram chat.

Built with [Deep Agents](https://docs.langchain.com/oss/python/deepagents) and
LangChain, based on the
[data analysis tutorial](https://docs.langchain.com/oss/python/deepagents/data-analysis).
Runs on OpenAI `gpt-4.1-mini-2025-04-14`.

## Documentation

| File | What is in it |
|---|---|
| **README.md** (this file) | What it does, how to set it up, how to run it |
| [HIGH_LEVEL_DESIGN.md](docs/HIGH_LEVEL_DESIGN.md) | The architecture: layers, components, flows, decisions, failure behaviour |
| [LOW_LEVEL_DESIGN.md](docs/LOW_LEVEL_DESIGN.md) | Module by module: every signature, and the details that are easy to get wrong |
| [SOLID_AND_PATTERNS.md](docs/SOLID_AND_PATTERNS.md) | The principles and patterns used, with the test that keeps each one honest |
| [TESTING.md](docs/TESTING.md) | How pytest works, then how the 418 tests here are built |

---

## The big picture

```
        YOU                        THE AGENT                    YOUR MACHINE
  ┌──────────────┐            ┌────────────────┐            ┌────────────────┐
  │  Telegram    │  message   │                │  write     │  output/*.py   │
  │  chat        │ ─────────► │   the model    │ ─────────► │  (its script)  │
  │              │            │   picks the    │            │                │
  │      or      │            │   next step    │  run       │  python.exe    │
  │              │            │                │ ─────────► │  (runs it)     │
  │  terminal    │ ◄───────── │                │ ◄───────── │                │
  └──────────────┘  summary   └────────────────┘  numbers   │  output/*.png  │
                    + chart          │                      └────────────────┘
                                     │
                              you approve first
```

The agent has no special data tools. It has a shell and a filesystem, so it
solves the task the way a person would: write a script, run it, read the output.

---

## What one run looks like

```
  you: send sales.csv
        │
        ▼
  agent thinks
        │
        ▼
  ┌────────────────────────────┐
  │ 📝 write output/analysis.py │  ──►  you tap ✅ or ❌
  └────────────────────────────┘
        │ approved
        ▼
  ┌────────────────────────────┐
  │ ▶️ run the script           │  ──►  you tap ✅ or ❌
  └────────────────────────────┘
        │ approved
        ▼
  it reads the real numbers
        │
        ▼
  you get:  a short summary  +  the chart image
```

If the script fails, the agent reads the error, fixes the script, and asks to run
it again. A real run is usually two to four approvals.

---

## What is inside

```
telegram-data-analyst-agent/
│
│  the agent
├── agent.py            builds it: model, memory, compaction, approval rules
├── prompts.py          the rules it follows (behaviour lives here)
├── backend.py          its workspace: a shell + the sample CSV
├── runner.py           runs it: start, resume, what is it waiting for
├── approvals.py        turns a pending tool call into a card a human can judge
├── artifacts.py        which charts a run produced
│
│  the two front-ends
├── main.py             terminal
├── telegram_bot.py     Telegram: wiring + the polling loop
├── router.py           what each incoming message should do
├── delivery.py         what the user sees: the answer, the chart, the card
├── telegram_client.py  the Telegram API, and nothing else
├── retrying_client.py  wraps the client and retries a dropped connection
├── formatting.py       text: Markdown to Telegram HTML, sizes, paths
├── progress.py         where "still working" news goes
├── tracing.py          one LangSmith trace per task, across the approvals
├── thread_store.py     which conversation each chat is on
├── config.py           reads your .env
│
├── tests/              418 offline tests (see docs/TESTING.md)
├── tests_e2e/          one real run against the real API
│
├── docs/               the design docs (see the table at the top)
├── data/               your input files
└── output/             what the agent writes (scripts + charts)
```

Each file has one job, and the parts are passed in rather than imported, so
every piece can be tested on its own. `telegram_bot.py` is the only file that
knows about all of them.

| File | What it does |
|---|---|
| `agent.py` | Builds the agent. One function per part, so a test can swap one. |
| `prompts.py` | The rules: how to look at data, which chart to draw, how to answer, never to claim work it did not do. |
| `backend.py` | The local shell the agent works in, plus the sample CSV. |
| `runner.py` | Starts and resumes a run, and reads what it is paused on. |
| `approvals.py` | Describes a pending tool call, and asks — by buttons or at the prompt. |
| `artifacts.py` | Finds the charts a run produced, and catches a reply that invents one. |
| `main.py` | Terminal front-end: one analysis, approvals typed in. |
| `telegram_bot.py` | Telegram front-end: builds the parts, then polls. |
| `router.py` | Decides what each message, file or button does. |
| `delivery.py` | Sends the answer and the chart, or the next approval card. |
| `telegram_client.py` | The Telegram API calls. Knows nothing about agents. |
| `retrying_client.py` | The same client, plus retries for a dropped connection. |
| `formatting.py` | Markdown to Telegram HTML, message splitting, tidy paths. |
| `progress.py` | The "Working on it…" adapter. |
| `tracing.py` | Keeps one LangSmith trace per task, even across restarts. |
| `thread_store.py` | Each chat's conversation number, so `/new` survives a restart. |
| `config.py` | Loads `.env` and checks nothing is missing. |

---

## Setup

You need Python 3.12+, [uv](https://docs.astral.sh/uv/), and an
[OpenAI API key](https://platform.openai.com/api-keys).

**1. Install**

```bash
git clone <this-repo-url>
cd telegram-data-analyst-agent
uv sync
```

`uv sync` creates `.venv/` and installs the exact versions pinned in `uv.lock`.

**2. Add your keys**

```bash
cp .env.example .env
```

Then open `.env` and fill it in:

| Setting | Needed? | What it is |
|---|---|---|
| `OPENAI_API_KEY` | yes | Your OpenAI key. |
| `OPENAI_MODEL` | yes | The model to use. This project uses `gpt-4.1-mini-2025-04-14`. |
| `LANGSMITH_*` | yes | Tracing. Set `LANGSMITH_TRACING=false` if you do not want it. |
| `TELEGRAM_BOT_TOKEN` | only for Telegram | From [@BotFather](https://t.me/BotFather). |
| `TELEGRAM_ALLOWED_CHAT_IDS` | only for Telegram | Chat ids allowed to use the bot. Empty means nobody. |
| `TELEGRAM_POLL_TIMEOUT` | no | Seconds each long-poll waits. Defaults to 30. |

`.env` is git-ignored. Never commit it.

**3. That is all**

You do not set any Python path. The project finds your interpreter by itself, so
it works on Windows, macOS and Linux.

---

## How to run it

### In the terminal

```bash
uv run main.py
```

It analyses the sample CSV. When it asks, type `y` to approve or `n` to reject.
Results go to `output/`.

### As a Telegram bot

```bash
uv run telegram_bot.py
```

Then message your bot. Send it a CSV, or just write what you want. Approvals
arrive as buttons: ✅ approve, ❌ reject, 🔍 show the full request.

First time only:

1. Message [@BotFather](https://t.me/BotFather), send `/newbot`, copy the token.
2. Send your bot any message, then ask Telegram who wrote it:
   `curl "https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates"` and copy
   `result[0].message.chat.id` into `TELEGRAM_ALLOWED_CHAT_IDS`.
3. Start the bot. Leave it running; stop it with Ctrl-C.

Bot commands: `/help`, and `/new` to start a fresh conversation.

### All commands

| Command | What it does |
|---|---|
| `uv sync` | Install everything (first time only). |
| `uv run main.py` | One analysis in the terminal. |
| `uv run telegram_bot.py` | Start the bot and keep it running. |
| `uv run backend.py` | Recreate the sample CSV. |
| `uv run pytest` | Run the 418 offline tests (about 12 seconds). |
| `RUN_E2E=1 uv run pytest tests_e2e -v -s` | One real run against the API (costs a few cents). |

---

## Two things that are easy to get wrong

### 1. Buttons must still work after a restart

In the terminal the program waits for your answer. Telegram cannot wait: the run
has to end, and your tap arrives later.

```
   your message ──► run ──► needs approval ──► sends buttons ──► run ENDS
                                                                    │
                     ... minutes, hours, or a restart later ...     │
                                                                    ▼
   you tap ✅ ─────────────────────────────────► run continues where it stopped
```

This works because the paused run is saved in `checkpoints.sqlite`. If it were
kept in memory only, a restart would leave dead buttons behind.

### 2. A conversation must not grow forever

Every message sends the whole conversation to the model again. One chat here grew
to 565,000 input tokens over three days, so even "hi" became expensive.

```
   BEFORE                               NOW
   one endless conversation             each file gets its own conversation

   file A ─┐                            file A ──► [ conversation 1 ]
   file B ─┼─► [ one thread ]           file B ──► [ conversation 2 ]
   "hi"   ─┘     grows forever          "hi"   ──► stays in conversation 2
```

Two guards:

- **A new file starts a new conversation.** Follow-up questions still work. They
  stay in the same conversation as the results, so you can keep asking about the
  numbers the agent just printed.
- **Long conversations get summarised** at 40,000 tokens. The old messages are
  saved to a file first, so nothing is lost.

`/new` clears the conversation by hand. Deleting `checkpoints.sqlite` clears
everything.

---

## Changing what it does

**The answer format and the charts** — edit `prompts.py`. `SYSTEM_RULES` holds the
rules for both entry points. The agent picks the chart from a menu (line, bar,
pie, scatter, histogram, box, heatmap and more) based on what the columns look
like, so the picture follows the data.

**What needs approval** — edit `interrupt_on` in `agent.py`:

```python
interrupt_on={
    "execute": True,       # ask before running commands
    "write_file": True,    # ask before writing files
    "read_file": False,    # never ask
    "ls": False,           # never ask
}
```

**The data to analyse** (terminal version) — edit the paths in `main.py`.

**The model** — change `OPENAI_MODEL` in `.env`.

---

## Tests

```bash
uv run pytest                              # 418 tests, ~12s, no network
uv run pytest -m "not slow"                # skip the slowest module
RUN_E2E=1 uv run pytest tests_e2e -v -s    # real API, real chart
```

The offline tests never touch the network, never call the model, and never touch
your real `checkpoints.sqlite`, `data/` or `output/`. Details in [TESTING.md](docs/TESTING.md).

---

## How the pieces fit

Each file has one job, the parts are passed in rather than imported, and
`telegram_bot.build_bot()` is the only place that knows all of them. That is why
418 tests run with no network and no API key.

The shapes used — registry, strategy, adapter, decorator, null object, facade,
value object, repository — are listed with their reasons and their tests in
[SOLID_AND_PATTERNS.md](docs/SOLID_AND_PATTERNS.md). The layer boundaries are in
[HIGH_LEVEL_DESIGN.md](docs/HIGH_LEVEL_DESIGN.md).

---

## Safety

The agent runs real commands on your computer with your permissions. There is no
sandbox. Two rules follow from that:

- **Read each approval before you tap it.** That is the real protection.
- **`TELEGRAM_ALLOWED_CHAT_IDS` is required.** Anyone can find a bot by its
  username, so the bot ignores every chat that is not on the list, and refuses to
  start when the list is empty.

This is fine for your own data on your own machine. It is not for production or
untrusted input. For that, use a
[sandbox backend](https://docs.langchain.com/oss/python/deepagents/sandboxes).

---

## If something goes wrong

| Message | What it means |
|---|---|
| `Field required ... OPENAI_API_KEY` | `.env` is missing a value. Compare it with `.env.example`. |
| `model_not_found` | `OPENAI_MODEL` is not a model your key can use. |
| `TELEGRAM_ALLOWED_CHAT_IDS is empty` | Add your chat id (see the Telegram setup steps). |
| `'python' is not recognized` | Something replaced the automatic interpreter path. Put `sys.executable` back. |
| `ModuleNotFoundError` | A library is missing: `uv add <package>`. The agent cannot install packages itself. |
| The reply mentions a chart but no image arrives | The script was never run. Ask again and approve the run step. |
| Empty reply from the agent | Some middleware upsets some models. Try removing `TodoListMiddleware` in `agent.py`. |

---

## Read more

- [Deep Agents](https://docs.langchain.com/oss/python/deepagents)
- [Backends](https://docs.langchain.com/oss/python/deepagents/backends)
- [Human in the loop](https://docs.langchain.com/oss/python/deepagents/human-in-the-loop)
- [Sandboxes](https://docs.langchain.com/oss/python/deepagents/sandboxes)
- [Memory](https://docs.langchain.com/oss/python/concepts/memory)
- [ChatOpenAI](https://docs.langchain.com/oss/python/integrations/chat/openai)
