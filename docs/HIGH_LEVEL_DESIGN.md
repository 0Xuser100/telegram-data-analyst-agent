# High-level design

What the system is, what the pieces are, and why the shape is what it is.

For the file-by-file detail see [LOW_LEVEL_DESIGN.md](LOW_LEVEL_DESIGN.md).
For the principles and patterns see [SOLID_AND_PATTERNS.md](SOLID_AND_PATTERNS.md).
For running it see [README.md](../README.md).
For the test suite see [TESTING.md](TESTING.md).

---

## 1. What it does

You give it a data file. It works out what the file holds, computes the numbers
that matter, draws the chart that fits, and answers in a few lines. Before it
writes a file or runs a command, it stops and asks you.

There is no "analysis engine". The agent has a shell and a filesystem, so it
does what a person would: write a script, run it, read the output.

```
   ┌────────────┐    task    ┌───────────────┐   write / run   ┌──────────────┐
   │  Telegram  │ ─────────► │  the model    │ ──────────────► │ your machine │
   │     or     │            │  decides the  │                 │  python +    │
   │  terminal  │ ◄───────── │  next step    │ ◄────────────── │  files       │
   └────────────┘  answer +  └───────────────┘   numbers        └──────────────┘
                    chart           │
                                    ▼
                          you approve each step
```

## 2. The four boundaries

Everything in the project sits in one of four layers. Dependencies point
downward only; nothing lower ever imports something higher.

```
   ┌─────────────────────────────────────────────────────────────┐
   │  ENTRY POINTS        main.py            telegram_bot.py     │  wiring
   ├─────────────────────────────────────────────────────────────┤
   │  CONVERSATION        router.py   delivery.py   approvals.py │  decisions
   ├─────────────────────────────────────────────────────────────┤
   │  AGENT               agent.py    runner.py    prompts.py    │  the work
   ├─────────────────────────────────────────────────────────────┤
   │  PLUMBING   telegram_client  formatting  artifacts  tracing │  mechanics
   │             thread_store     progress    backend    config  │
   └─────────────────────────────────────────────────────────────┘
```

| Layer | Answers | Knows nothing about |
|---|---|---|
| Entry points | which parts exist and how they connect | how any one of them works |
| Conversation | what should happen next, and what the user sees | HTTP, sqlite, the model |
| Agent | how a task is run and what rules it follows | Telegram, chats, buttons |
| Plumbing | one mechanical job each | each other |

The rule that keeps this honest: **a component receives what it needs, it does
not import it.** `UpdateRouter` takes a client, a runner, a store and a delivery.
That is why the test suite can run the real router against a fake client with no
network and no model.

## 3. The components

| Component | Job | Depends on |
|---|---|---|
| `TelegramClient` | Bot API calls: send, upload, long-poll, download | requests |
| `RetryingClient` | the same surface, retrying dropped connections | a client |
| `UpdateRouter` | what each message, file or button means | client, runner, store, delivery |
| `ResultDelivery` | what the user sees: answer, chart, or approval card | client, collector |
| `AgentRunner` | start a run, resume it, report what it is waiting for | the graph, progress |
| `ArtifactCollector` | which charts this run produced; catch invented ones | the filesystem |
| `ThreadStore` | which conversation each chat is on | sqlite |
| `TaskTracer` | one LangSmith trace per task, re-attached after a restart | sqlite, langsmith |
| `Approver` | ask a human — buttons, or a terminal prompt | client (Telegram only) |
| `agent.py` | build the model, memory, compaction, approval rules | deepagents, LangChain |
| `prompts.py` | the behaviour rules, in words | nothing |

## 4. How a run flows

### Telegram, with two gates

```
  message ──► UpdateRouter
                 │  allowed chat?  command?  already paused?
                 ▼
              AgentRunner.start ──► the graph ──► pauses on write_file
                 │
                 ▼
              ResultDelivery ──► TelegramApprover ──► card + buttons
                 │
              [ the run ends here — the process is free ]

  ✅ tap  ──► UpdateRouter ──► AgentRunner.resume ──► pauses on execute
                                        │
  ✅ tap  ──► ... ──► finishes ──► ResultDelivery ──► answer + chart
```

The important part is the gap. Telegram cannot block, so a run *ends* after
sending the buttons and a later tap continues it. That is only possible because
the paused graph is on disk (`checkpoints.sqlite`), which is why the checkpointer
is not optional here.

### Terminal, same loop

```
  uv run main.py ──► run_to_completion(runner, thread, task, ConsoleApprover)
                          │
                          └─ ask at the prompt, resume, repeat
```

Same loop object, different `Approver`. The terminal one blocks on `input()`
because it can.

## 5. Decisions worth knowing

| Decision | Why | Alternative rejected |
|---|---|---|
| The agent gets a shell, not custom tools | any file shape works, and the code is inspectable | a fixed set of analysis tools: brittle, and every new question needs new code |
| Human approval on write and run | model-generated code runs on this machine | trusting the model, or a sandbox (worth doing later) |
| Checkpointer on disk | a button tapped tomorrow must still work | in-memory: a restart orphans every pending card |
| A new file starts a new conversation | one chat reached 565k input tokens over three days | one endless thread, re-sent on every message |
| Compaction at 40k tokens | the deepagents default (170k here) never fired | leaving it: cost grew until the context window did |
| Inspect data with a script, never `read_file` | a 60-column CSV filled the context and forced three compactions in five seconds | reading raw rows into the conversation |
| Code always in a `.py` file | the approval card shows a file; `python -c` hides the code | inline commands |
| The reply never names files | the chart is attached automatically | printing paths the user cannot click |
| One process, one bot, sqlite | zero infrastructure for a single user | Postgres and a server |
| One trace per task, not per chat | a task ends, so the trace closes, and cost is attributable per analysis | per invoke (five unreadable traces per analysis) or per chat (traces open for days) |

## 6. State: what survives what

| State | Where | Survives a restart | Survives `/new` |
|---|---|---|---|
| Conversation history | `checkpoints.sqlite` (LangGraph) | yes | no — a new thread id |
| Pending approval | same, in the same thread | yes | no |
| Which conversation a chat is on | `chat_threads` table, same file | yes | it is what `/new` changes |
| Evicted history after compaction | `conversation_history/<thread>.md` | yes | yes, as a file |
| The open trace for a task | `thread_traces` table, same file | yes — that is the point | no, it is closed on the way out |
| Scripts and charts | `output/` | yes | yes |
| Uploaded data | `data/` | yes | yes |
| Which charts were already sent | memory (`ArtifactCollector`) | no — and it does not need to | reset |

Everything durable lives in one sqlite file and two folders. `rm
checkpoints.sqlite*` is the full reset.

## 7. Failure behaviour

| Failure | What happens |
|---|---|
| Network blip while polling | logged, the loop keeps polling |
| Dropped connection while sending | retried up to 3 times with growing backoff |
| Telegram rejects the markup | resent as plain text, buttons intact |
| A handler raises | logged with a traceback, the chat is told, the loop continues |
| The model errors | the exception reaches the loop, which reports it to the chat |
| The agent keeps asking for approval | capped at 12 rounds, then it stops |
| A reply claims a chart that does not exist | the bot says so instead of going quiet |
| Context window exceeded | compaction fires and retries |
| An unknown chat messages | dropped silently, logged locally |

## 8. Scale, and where it stops

Fine as is: one user, one bot process, files up to 20 MB (a Telegram limit),
conversations of any length thanks to compaction.

Where it would need changing:

- **Two bot instances** — sqlite is single-writer. Move to `PostgresSaver`; the
  interface is the same one line in `agent.py`.
- **Concurrent runs in one chat** — the router refuses a second run while one is
  paused. Lifting that needs per-run keys instead of per-chat ones.
- **Untrusted users** — approval is a safeguard, not a sandbox. Swap
  `LocalShellBackend` for a sandbox backend.
- **Long analyses** — everything is synchronous inside one update. A queue would
  be the next step.

## 9. What was verified, and how

- 418 offline tests, ~12 seconds, 99% coverage. No network, no model calls, a
  temp working directory. See [TESTING.md](TESTING.md).
- One end-to-end test against the real API on `gpt-4.1-mini-2025-04-14`,
  opt-in with `RUN_E2E=1`. It approves every gate, then checks that a script was
  written, a real chart was saved, the totals are right, and the reply follows
  the format rules.
