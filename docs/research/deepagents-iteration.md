# How deepagents supports an iteration loop

Research note for [issue #5](https://github.com/0Xuser100/telegram-data-analyst-agent/issues/5),
part of the map in [issue #1](https://github.com/0Xuser100/telegram-data-analyst-agent/issues/1).

**Question.** The agent must move from one fixed analysis pass to
hypothesis -> test -> follow-what-it-finds. Does deepagents give primitives for
that, or is it purely a prompt concern?

**Answer in one line.** Sequencing the hypotheses is a prompt concern — no
primitive decides what to test next. But four surrounding concerns are library
concerns with real primitives, three of them currently mis-wired for a
multi-step run: the plan the model writes goes invisible after compaction,
nothing bounds the number of steps in the production path, the durable place to
put findings between steps is the filesystem rather than the todo list, and
there is exactly one framework-level iterate-until-done loop shipped in the
installed tree (`RubricMiddleware`) that this agent does not use.

Versions verified in this repo: `deepagents 0.7.5`, `langchain 1.3.15`,
`langgraph 1.2.11`, `langchain-core 1.6.0`
(`.venv/Lib/site-packages/deepagents-0.7.5.dist-info/METADATA` and siblings).

---

## 1. The planning / todo tooling

### What it is

`write_todos` comes from **LangChain**, not deepagents:
`langchain/agents/middleware/todo.py`. The tool takes a whole list of
`{content, status}` items where status is
`"pending" | "in_progress" | "completed"` (`todo.py:25-32`) and replaces the
list wholesale (`todo.py:152-166`). State lives on a `todos` key
(`todo.py:34-43`).

### Is it enabled here?

Yes — but **only because this repo turns it on explicitly**.
`create_deep_agent` in deepagents 0.7.5 does *not* include
`TodoListMiddleware` in its default stack: the documented middleware ordering in
`deepagents/graph.py:362-392` lists Skills / Filesystem / SubAgent /
Summarization / PatchToolCalls / AsyncSubAgent / *user middleware* / profile
extras / ToolExclusion / prompt caching / Memory / HumanInTheLoop, with no todo
entry, and grepping the package finds `TodoListMiddleware` instantiated only in
one harness profile (`deepagents/profiles/harness/_openai_codex.py:77`).

It reaches this agent from `src/analyst/agent/builder.py:85`:

```python
middleware=[TodoListMiddleware(),
            summarizer or build_summarizer(model, target_backend)],
```

Verified empirically — building the real agent and reading its tool node gives:

```
['delete', 'edit_file', 'execute', 'glob', 'grep', 'ls', 'read_file',
 'task', 'write_file', 'write_todos']
```

Two consequences worth naming:

- Because it is *user* middleware rather than deepagents-owned middleware, it
  carries LangChain's **full** `WRITE_TODOS_SYSTEM_PROMPT` (`todo.py:119-137`)
  plus the long `WRITE_TODOS_TOOL_DESCRIPTION` (`todo.py:51-117`). deepagents
  deliberately passes `system_prompt=""` to its own middleware to trim this kind
  of prose (`deepagents/graph.py:630-640`); that trimming does not apply here.
  So the agent already pays a few hundred tokens of planning instructions on
  every model call, for a tool nothing tells it to use.
- `tests/test_agent_wiring.py:115-121` asserts `TodoListMiddleware` is present
  under the docstring *"Passing middleware must not remove the built-ins"* and
  lists it among "the deepagents core stack". That framing is wrong for 0.7.5 —
  it is not a built-in. The assertion still passes (this repo supplies it), but
  the test does not guard what it claims to guard.

### Is it the right vehicle for "form a hypothesis, test it, revise"?

**Partly. It is a revisable plan tracker, and nothing more.** The tool prompt is
explicit that revision is intended:

> "The plan may need future revisions or updates based on results from the
> first few steps" (`todo.py`, trigger 5 under *When to Use This Tool*)

> "After completing a task - Mark it as completed and add any new follow-up
> tasks discovered during implementation." (`todo.py`, *How to Use This Tool*)

> "Don't be afraid to revise the To-Do list as you go. New information may
> reveal new tasks that need to be done, or old tasks that are irrelevant."
> (`todo.py:130-131`, `WRITE_TODOS_SYSTEM_PROMPT`)

That is genuinely the shape we want: a plan the model rewrites as results
arrive. Three limits matter:

1. **A `Todo` has no room for a finding.** The schema is `content` + `status`
   only (`todo.py:25-32`). There is no field for "what the test showed", so a
   hypothesis's *result* cannot live in the todo list. It has to live somewhere
   else (see §4).
2. **The middleware never re-injects the current list into the prompt.**
   `TodoListMiddleware.wrap_model_call` (`todo.py:230-254`) appends only the
   static system prompt. The only channel by which the model sees its own plan
   is the `ToolMessage(f"Updated todo list to {todos}")` returned by the tool
   (`todo.py:158-163`). **This is the important gotcha:** with
   `COMPACT_AT_TOKENS = 40_000` and `KEEP_MESSAGES = 6`
   (`src/analyst/agent/builder.py:29-30`), once summarization fires that
   ToolMessage is evicted and the model is flying on the summary alone, even
   though `state["todos"]` still holds the plan. An 8-step analysis is exactly
   the run length where this bites.
3. **It talks itself out of short work** — "If the user's request is trivial and
   takes less than 3 steps, it is better to NOT use this tool"
   (`todo.py:53`) — which is compatible with the SCOPE rule in `SYSTEM_RULES`
   that a greeting gets one sentence, and is a reason not to fight it.

**Verdict:** keep `write_todos` as the *spine* of the loop — it is the only
primitive that models "plan, then revise the plan" — but do not expect it to
carry state, and treat item 2 as a wiring decision the map does not yet have.

---

## 2. Sub-agents, and why `SYSTEM_RULES` forbids the task tool

### What is there

`SubAgentMiddleware` is **required** middleware in deepagents — it cannot be
excluded (`deepagents/graph.py:238-262`), and a default `general-purpose`
subagent is auto-added unless the caller supplies their own
(`deepagents/graph.py:748-756`). Hence `task` in the verified tool list above.

The `task` tool description (`deepagents/middleware/subagents.py:285-296`):

> "Launch an ephemeral subagent to handle a complex, multi-step task in an
> isolated context window."
>
> "Each invocation is stateless: the agent sees only the prompt you give it and
> returns a single final report."

And the default subagent prompt (`subagents.py:248-252`):

> "The calling agent only sees your final assistant message, not your
> intermediate work, tool results, or status tracking."

### Was the ban a cost decision or a correctness one?

**There is no recorded rationale.** The rule is
`src/analyst/agent/prompts.py:9` (and again in `ANALYSIS_PROMPT`,
`prompts.py:116`):

```
- Do ALL the work yourself. Do NOT use the task tool or delegate to a sub-agent.
```

`tests/test_prompts.py:63` pins the string. Git history is two commits and
carries no explanation (`git log -S "Do NOT use the task tool"` returns only the
plumbing commit). None of `docs/HIGH_LEVEL_DESIGN.md`,
`docs/LOW_LEVEL_DESIGN.md`, `docs/SOLID_AND_PATTERNS.md` or `docs/TESTING.md`
mentions the task tool, sub-agents, or `write_todos` at all.

So the honest answer is: unknown as recorded. **But the source supplies a
correctness reason strong enough that the ban should stand regardless of what
originally motivated it**, and that is the part that matters for the iteration
decision:

- The subagent graph is compiled by `_create_subagent_from_spec`
  (`deepagents/middleware/subagents.py:341-385`) via `create_agent(...)` with
  **no `checkpointer`** among `create_agent_kwargs`.
- The `SubAgent` docstring for `interrupt_on` says plainly: **"Requires a
  checkpointer."** (`subagents.py:70`).
- Yet `deepagents/graph.py` propagates the parent's `interrupt_on` into every
  subagent — `subagent_interrupt_on = spec.get("interrupt_on", interrupt_on)`
  (`graph.py:719`) — and `_create_subagent_from_spec` duly appends a
  `HumanInTheLoopMiddleware` for it (`subagents.py:370-372`).
- The subagent is invoked **synchronously inside the parent's tool call**:
  `result = subagent.invoke(subagent_state, subagent_config)`
  (`subagents.py:566-567`).

*(Inferred, not reproduced:)* this repo's entire approval story —
`INTERRUPT_ON = {"execute": True, "write_file": True, ...}`
(`builder.py:34-39`), an approval card that must still work after a restart
(`builder.py:49-53`), `AgentRunner.waiting_for` reading pauses back out of the
checkpointer (`runner.py:57-63`) — depends on an interrupt being resumable at
the point it was raised. An interrupt raised inside an uncheckpointed subagent
is not; resuming the parent re-enters the `task` tool and replays the subagent
from its first step, re-running whatever it already ran. For a tool whose job is
executing scripts, that is a correctness hazard, not a cost one.

Two further reasons the ban is right for *this* agent shape:

- **Isolation is the wrong default for iteration.** A subagent buys a clean
  context window and pays for it by never surfacing intermediate findings
  ("only sees your final assistant message"). A hypothesis loop is exactly the
  workload where step *n+1* needs the detail of step *n*.
- **`todos` do not cross the boundary.** `_EXCLUDED_STATE_KEYS = {"messages",
  "todos", "structured_response"}` (`subagents.py:253-256`) — the todo list is
  neither passed down nor returned up, because it has "no defined reducer and no
  clear meaning for returning them from a subagent to the main agent"
  (`subagents.py:264-266`). A subagent cannot participate in the plan.

One thing *does* cross: every state key not in `_EXCLUDED_STATE_KEYS`, including
file state — and with this app's `LocalShellBackend` the files are on real disk
anyway, so a subagent shares the workspace regardless.

**Does an iterative analysis change the calculus?** It strengthens the ban for
the analysis loop itself. The only shape where sub-agents would earn their keep
is a fan-out of genuinely independent, read-only sub-questions whose answers
compress to a paragraph each — and even that trips the interrupt problem while
`execute`/`write_file` are gated. If sub-agents are ever wanted, the
prerequisite is the map's scoped auto-approve landing first, so no interrupt is
raised inside them at all.

---

## 3. Recursion and step limits

### What actually caps a run today

**Nothing meaningful.** deepagents pins the recursion limit to a number chosen
to be out of the way — `deepagents/graph.py:934-943`:

```python
).with_config(
    {
        "recursion_limit": 9_999,
        ...
    }
)
```

Verified by reading it back off the real compiled agent:

```
config: {'recursion_limit': 9999,
         'metadata': {'ls_integration': 'deepagents',
                      'lc_versions': {'deepagents': '0.7.5'}, ...},
         'configurable': {}}
```

Nothing in this repo overrides it: `grep -rn "recursion_limit\|max_iterations" src tests`
finds no hits, and `AgentRunner._invoke` builds its config from `thread_id`,
`run_name` and `metadata` only (`src/analyst/agent/runner.py:76-86`), so the
bound 9,999 stands.

### What the repo's own cap does and does not do

`run_to_completion(..., max_rounds: int = 12)` (`runner.py:20-35`) raises
`TooManyApprovals` after 12 rounds. Two limits on that:

- It counts **approval rounds**, not tool calls or model calls. A step that
  needs no approval is free.
- **The Telegram bot never calls it.** `run_to_completion` is used only by
  `src/analyst/entrypoints/cli.py:42-43` and `tests_e2e/test_end_to_end.py:81`.
  The bot is event-driven: `Router` calls `self._runner.resume(...)` once per
  button tap (`src/analyst/conversation/router.py:208-218`). So in production
  there is **no bound at all** on how long one analysis runs, beyond
  `recursion_limit=9999` and the user's patience.

This is the single most concrete gap the iteration work opens up. Today a run is
2-4 approvals long, so the absence of a bound never shows. An 8-step loop on a
frontier model with auto-approved writes removes both the natural brake and the
human in the loop at the same time.

### The primitives that exist and are not used

LangChain 1.3 ships two middlewares built exactly for this, both in the
installed tree, neither wired in:

- **`ModelCallLimitMiddleware`** (`langchain/agents/middleware/model_call_limit.py`):
  `thread_limit` / `run_limit` / `exit_behavior="end" | "error"`
  (`model_call_limit.py:126-160`). Raises `ModelCallLimitExceededError` on
  `"error"` (`model_call_limit.py:63-92`); `"end"` stops the loop cleanly.
- **`ToolCallLimitMiddleware`** (`langchain/agents/middleware/tool_call_limit.py`):
  the same, plus an optional `tool_name=` so a single tool can be capped, and
  `exit_behavior` defaulting to `"continue"`, which injects
  `"Tool call limit exceeded. Do not call '<tool>' again."` back to the model
  (`tool_call_limit.py:53-71`, `141-208`).

The full set of built-in middleware modules under
`langchain/agents/middleware/`: `context_editing`, `file_search`,
`human_in_the_loop`, `model_call_limit`, `model_fallback`, `model_retry`, `pii`,
`provider_tool_search`, `shell_tool`, `summarization`, `todo`,
`tool_call_limit`, `tool_emulator`, `tool_error`, `tool_retry`,
`tool_selection`, plus internals (`_execution`, `_redaction`, `_retry`,
`_trace_policy`, `internal_call_transformer`). deepagents adds its own under
`deepagents/middleware/`, including `rubric` (see §5) and `skills`, `memory`,
`permissions`, `patch_tool_calls`, `subagents`, `async_subagents`,
`filesystem`, `summarization`.

**Verdict:** step-limiting is a library concern with a ready answer.
`ModelCallLimitMiddleware(run_limit=N, exit_behavior="end")` is the natural home
for the map's "cost guardrails" once the model swap lands, and
`ToolCallLimitMiddleware(tool_name="execute", ...)` is the natural home for a
bound on the map's "repeated script failure" spin. Both act *inside* one graph
run, which is precisely where the bot has no protection today.

---

## 4. State between steps

There are **four** channels, not one, and they behave differently.

**(a) Conversation history.** The default, and the one that leaks. Compaction is
`SummarizationMiddleware` with `trigger=("tokens", 40_000)`,
`keep=("messages", 6)` (`builder.py:56-69`). The deepagents default for this
model would have been `trigger=("tokens", 170000), keep=("messages", 6)`
(`deepagents/middleware/summarization.py:280-285`); the repo lowered the trigger
deliberately because the default never fired (`builder.py:26-30`). Evicted
messages are not destroyed — they are appended as markdown to
`/conversation_history/{thread_id}.md`, one section per summarization event
(`deepagents/middleware/summarization.py:43-50`), and the agent can `read_file`
them. **Implication for an 8-step loop: earlier script output will be
summarized away, and the summary is an LLM's paraphrase of numbers.** That is
the strongest argument that findings must not live only in the transcript.

**(b) The real filesystem — the durable channel, and the answer to the
ticket's question.** This app uses `LocalShellBackend(root_dir=".")`
(`src/analyst/plumbing/backend.py:27-33, 52`), chosen because `FilesystemBackend`
cannot run commands. So `write_file` / `execute` / `read_file` / `glob` / `grep`
act on actual disk under the repo root. A later script can therefore read what
an earlier script *wrote* — a CSV of intermediate results, a JSON of fitted
parameters, a notes file — with no dependence on the transcript at all, and it
survives compaction, process restarts, and even `/new` (a fresh thread
generation via `ThreadStore.start_new`,
`src/analyst/plumbing/thread_store.py:38-56`, changes the checkpoint thread but
not the disk).

So: how does a later script learn what an earlier one found? **It should be told
to write its findings to a file in `output/` and read them back.** That is a
prompt instruction resting on a library guarantee, and it is the piece missing
today — `SYSTEM_RULES` never mentions persisting intermediate results, and its
one instruction about multi-step inspection is the opposite: "One inspect step
is enough" (`prompts.py:40`).

(For contrast: `StateBackend` keeps files in graph state, so they persist within
a thread but not across threads (`deepagents/backends/state.py:37-46`);
`StoreBackend` uses the LangGraph store. Neither can execute commands, which is
why this repo cannot use them as-is.)

**(c) Automatic overflow-to-file.** `FilesystemMiddleware` offloads oversized
tool results to disk and replaces the message with a path plus a preview:
defaults `tool_token_limit_before_evict=20000`,
`human_message_token_limit_before_evict=50000`
(`deepagents/middleware/filesystem.py:1604-1605`), replacement text at
`deepagents/middleware/_message_eviction.py:22-34`:

> "Tool result too large, the result of this tool call {tool_call_id} was saved
> in the filesystem at this path: {file_path} ... You can read the result from
> the filesystem by using the read_file tool, but make sure to only read part of
> the result at a time."

A chatty `execute` step therefore degrades gracefully rather than blowing the
window. This is free and already active.

**(d) The `todos` state key.** Survives compaction as *state*, but as shown in
§1 it is never re-injected into the prompt, so surviving buys nothing today.

---

## 5. Is there a documented pattern for this agent shape?

Summarised here, evidenced in the doc survey below.

- **No.** "hypothesis" appears nowhere in the deepagents documentation, and
  there is no canonical hypothesis -> test -> refine recipe.
- The **official data-analysis tutorial is a single linear pass** and its agent
  is, allowing for the delivery surface, essentially this repo's `build_agent`.
  The official example for this exact agent shape is the report generator the
  map wants to leave behind.
- The **deep-research tutorial's prompt** is the closest transferable artefact,
  and it is a prompt: an assess-after-each-step beat, a numeric tool-call
  budget, and explicit stop conditions including diminishing returns.
- **`RubricMiddleware`** is the one framework-level iterate-until-done loop, it
  ships in the installed tree, it is beta, and it is unused here. It is a
  serious candidate for the map's "required floor" decision.

---

## What this means for the ticket's question

**Prompt concern (no primitive will do it):**

- Deciding what the next hypothesis is, and when to stop. `SYSTEM_RULES` work.
- Instructing the agent to persist intermediate findings into `output/` and read
  them back next step. The library guarantees the channel; only the prompt can
  ask for it. Note this collides with the current "One inspect step is enough"
  line, which must go or be rewritten.
- Instructing the agent to actually use `write_todos` for multi-step analysis.
  It has the tool today and no instruction about it, and the tool's own prompt
  tells it to skip planning for short work.

**Library concern, with a primitive already installed:**

- Step and cost bounds -> `ModelCallLimitMiddleware`, `ToolCallLimitMiddleware`.
  Note the production path has no bound at all today.
- Keeping the plan visible late in a long run -> raise `COMPACT_AT_TOKENS`,
  raise `KEEP_MESSAGES`, or add a small middleware that re-injects
  `state["todos"]` each turn. An open decision the map does not have.
- A checkable "done" for the required floor -> `RubricMiddleware`, with the
  caveats in §5.
- Progress feedback during a long run -> `config["metadata"]["langgraph_step"]`
  is a ready-made step counter.

**Leave alone:**

- Sub-agents. The ban is right, for correctness rather than cost, and the reason
  should be written down. If it is to be enforced properly, the supported
  mechanism is not shipping the `task` tool rather than asking the model not to
  call it.

---

## Doc survey

Every quote below was fetched directly from the page cited, not from a secondary
summary.

### On planning

<https://docs.langchain.com/oss/python/deepagents/overview>:

> "Starting in v0.7 task planning is opt-in only. In earlier versions, task
> planning middleware was included by default."

Planning is documented as useful for:

> "Long or complicated multi-step tasks" / "Less capable models that benefit
> from an explicit accountability tool" / "UIs that stream progress from agent
> state"

<https://docs.langchain.com/oss/python/langchain/middleware/built-in>, "To-do list":

> "Equip agents with task planning and tracking capabilities for complex
> multi-step tasks."

This corroborates the source reading, including that v0.7 made it opt-in —
which is why `builder.py:85` has to supply it at all.

**Worth stating plainly: nowhere in the docs is there a claim that
`write_todos` improves iterative or exploratory *reasoning*.** The documented
benefits are tracking, accountability for weaker models, and UI progress
streaming. Treating it as a reasoning aid is our inference. The strongest
first-party support for the iterative reading is the tool's own prompt text
quoted in §1 ("Don't be afraid to revise the To-Do list as you go"), which is a
prompt, not a mechanism.

### On subagents

<https://docs.langchain.com/oss/python/deepagents/subagents>:

> "Subagents solve the **context bloat problem**. When agents use tools with
> large outputs (web search, file reads, database queries), the context window
> fills up quickly with intermediate results. Subagents isolate this detailed
> work—the main agent receives only the final result, not the dozens of tool
> calls that produced it."

The practice is named "context quarantine". And, verbatim, the "When NOT to use
subagents" list:

> ❌ Simple, single-step tasks
> ❌ **When you need to maintain intermediate context**
> ❌ When the overhead outweighs benefits

**That middle bullet is the documented answer to the ticket's question.** An
iterative hypothesis loop is precisely the case where you need to maintain
intermediate context, so the docs themselves say not to delegate it.

Note also what is *absent*: **cost is not a documented reason to use
subagents.** The page frames the benefit as context, not spend; the overview
page's phrasing is "context and token efficiency". So if the `SYSTEM_RULES` ban
was made as a cost decision, it was not made from the docs.

Overview page, on the `task` tool:

> "a built-in `task` tool that lets the main agent create ephemeral subagents
> for isolated, long-running, multi-step, or parallel tasks"

...returning "one final report to the main agent", with "stateless messaging".

If the ban is ever to be enforced properly, the docs give a mechanism that is
not a prompt line:

> "1. Set `general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)`
> on the active harness profile. 2. Pass no synchronous subagents via
> `subagents=` parameter to `create_deep_agent`."
>
> "Don't reach for `excluded_middleware` here—`SubAgentMiddleware` is required
> scaffolding and listing it raises `ValueError`. The
> `general_purpose_subagent.enabled = False` knob is the supported path."

**This is a real, decidable option: stop shipping the `task` tool instead of
asking the model not to use it.** It would also recover the tool's fairly long
description from every request, and would make `tests/test_prompts.py:63`'s
string assertion unnecessary. That is a decision the map does not currently
have.

On sharing, the backends page confirms the source reading:

> "This backend is shared between the supervisor agent and subagents, and any
> files a subagent writes will remain in the LangGraph agent state even after
> that subagent's execution is complete."

...while skills are "fully isolated", and a subagent's own `permissions`, if
given, "replaces the parent agent's rules entirely"
(`deepagents/middleware/subagents.py:75-80`).

### On recursion limits

`recursion_limit` is **not documented anywhere in the deepagents section**. It
is documented only in LangGraph
(<https://docs.langchain.com/oss/python/langgraph/graph-api>):

> "The recursion limit sets the maximum number of super-steps the graph can
> execute during a single execution."
>
> "Starting in version 1.0.6, the default recursion limit is set to 1000 steps."
>
> "Once the limit is reached, LangGraph will raise `GraphRecursionError`."
>
> "The recursion limit can be set on any graph at runtime, and is passed to
> `invoke`/`stream` via the config dictionary. Importantly, `recursion_limit`
> is a standalone `config` key and should not be passed inside the
> `configurable` key as all other user-defined configuration."
>
> "The current step counter is accessible in
> `config["metadata"]["langgraph_step"]` within any node, allowing for proactive
> recursion handling before hitting the recursion limit."

So: LangGraph's own default is 1,000; deepagents deliberately raises it to
9,999 (`graph.py:937`). Either way it is a runaway guard, not a budget.

Two practical notes. First, it is a *standalone* config key —
`AgentRunner._config` builds `{"configurable": {...}}` plus top-level
`run_name`/`metadata` (`runner.py:76-86`), so adding `recursion_limit` there
would work and nesting it under `configurable` would silently do nothing.
Second, `config["metadata"]["langgraph_step"]` is a ready-made step counter,
which is the obvious hook for the map's open "progress feedback during a long
multi-step run" question.

The built-in step-limiting middleware, from
<https://docs.langchain.com/oss/python/langchain/middleware/built-in>:

> **Model call limit**: "Limit the number of model calls to prevent infinite
> loops or excessive costs." — `thread_limit` (across all runs in a thread),
> `run_limit` (per single invocation), `exit_behavior` `'end'` or `'error'`.
>
> **Tool call limit**: "Control agent execution by limiting the number of tool
> calls, either globally across all tools or for specific tools." —
> `tool_name`, `thread_limit`, `run_limit`, `exit_behavior` `'continue'`
> (default), `'error'`, or `'end'`.

Both are present in the installed tree
(`langchain/agents/middleware/model_call_limit.py`, `tool_call_limit.py`) and
neither is wired into this agent.

### On state and backends

<https://docs.langchain.com/oss/python/deepagents/backends>, on the default
`StateBackend`:

> "Thread-scoped. The default filesystem backend for an agent is stored in
> langgraph state. Files persist across turns within a thread (via your
> checkpointer) and are not shared across threads."
>
> "**Best for:** A scratch pad for the agent to write intermediate results.
> Automatic eviction of large tool outputs which the agent can then read back
> in piece by piece."

That "scratch pad for intermediate results" line is the documented blessing for
the pattern in §4 — and this repo already has a stronger version of it, on real
disk, via `LocalShellBackend`.

<https://docs.langchain.com/oss/python/deepagents/context-engineering>:

> "Content offloading happens when tool call inputs or results exceed a token
> threshold (default 20,000)."
>
> ...the system "substitutes it with a file path reference and a preview of the
> first 10 lines."
>
> "**Use the filesystem for large data**: Subagents can write results to files;
> the main agent reads what it needs."

(Minor doc/source drift, noted for whoever implements: the docs say "the first
10 lines"; the source builds a head+tail preview of 5 lines each with a
truncation marker in between — `deepagents/middleware/_message_eviction.py:37-65`.
Trust the source.)

**One flag adjacent to this ticket rather than inside it.** The backends page
carries an explicit warning about direct-filesystem backends:

> "This backend grants agents direct filesystem read/write access. Use with
> caution and only in appropriate environments."
>
> Appropriate: "Local development CLIs (coding assistants, development tools)";
> "CI/CD pipelines".
>
> Inappropriate: "**Web servers or HTTP APIs** - use StateBackend, StoreBackend,
> or a sandbox backend instead"; "Production environments (such as web servers,
> APIs, multi-tenant systems)".

This agent is a long-poll Telegram bot running `LocalShellBackend(root_dir=".")`
(`src/analyst/plumbing/backend.py:33`) — i.e. rooted at the whole repo,
including `.env` and `checkpoints.sqlite`. Today the approval cards are the
mitigation, plus an allow-list of chat ids
(`src/analyst/conversation/router.py:78-82`). The map's standing decision to
replace approval friction with a scoped auto-approve confined to `output/`
removes the first of those, so *how* the scoping is enforced matters more than
the map currently implies. deepagents has a first-party primitive for it —
`permissions` / `FilesystemPermission` on `create_deep_agent`, enforced by
`FilesystemMiddleware` at the tool layer and converted into interrupt config by
`_build_interrupt_on_from_permissions` (`deepagents/graph.py:45`,
`graph.py:470-478`). Not my ticket; flagging so the auto-approve work does not
rediscover it late.

### On a documented pattern for this agent shape

There is no hypothesis -> test -> refine pattern in the docs. Three near
misses, in ascending order of usefulness.

**1. The official data-analysis tutorial is one linear pass — i.e. it is the
thing the map wants to move away from.**
<https://docs.langchain.com/oss/python/deepagents/data-analysis> builds an agent
that "analyzes data files, generates visualizations, and shares results", which:

> "1. Accept a CSV file for analysis 2. Plan and track analysis steps with an
> opt-in todo list 3. Perform exploratory data analysis and generate
> visualizations 4. Share results to a Slack channel"
>
> "Data analysis often involves long, multi-step work, so pass
> `TodoListMiddleware` when you create the agent. That gives the agent a
> `write_todos` tool for tracking exploratory analysis, visualization, and
> sharing steps."

Its agent is:

```python
agent = create_deep_agent(
    model="google_genai:gemini-3.6-flash",
    tools=[slack_send_message],
    backend=backend,
    checkpointer=checkpointer,
    middleware=[TodoListMiddleware()],
)
```

Allowing for the delivery surface, **that is essentially this repo's
`build_agent`** (`src/analyst/agent/builder.py:78-87`). And the tutorial's
workflow runs once through to completion with no language about iterating,
refining, or revisiting an analysis. So the official example for exactly this
agent shape is a report generator, and there is no official next step to copy.

**2. The deep-research tutorial's *prompt* is the closest documented pattern,
and it is a prompt.**
<https://docs.langchain.com/oss/python/deepagents/deep-research>, from
`RESEARCHER_INSTRUCTIONS`:

> "After each search, pause and assess — Do I have enough to answer? What's
> still missing?"
>
> **Tool Call Budgets**: "Simple queries: Use 2-3 search tool calls maximum" /
> "Complex queries: Use up to 5 search tool calls maximum" / "Always stop: After
> 5 search tool calls if you cannot find the right sources"
>
> **Stop Immediately When**: "You can answer the user's question
> comprehensively" / "You have 3+ relevant examples/sources for the question" /
> "Your last 2 searches returned similar information"

This is the single most transferable artefact in the docs. Note its three
moving parts, none of which `SYSTEM_RULES` has today: **an explicit
after-each-step assessment beat, a numeric budget stated in the prompt, and
stop conditions — including a diminishing-returns one** ("your last 2 searches
returned similar information"). Translating that triple into analysis terms —
inspect broadly, assess, narrow, stop when the last two scripts told you the
same thing — is close to a drop-in for the depth rules.

(Its orchestrator prompt does the opposite of what this agent should do: it
delegates research to sub-agents and never researches directly. That shape suits
a report *compiler* over independent sub-questions, not an analyst following one
dataset.)

**3. `RubricMiddleware` is the one genuine framework-level iteration loop, and
it is unused here.**
<https://docs.langchain.com/oss/python/langchain/middleware/built-in>, "Rubric
grading": it lets agents "self-evaluate work against defined rubrics and iterate
until requirements are met or iteration limits are reached", and is "designed
for tasks with clear success criteria that agents cannot reliably complete on
first attempts". Status: "RubricMiddleware requires deepagents>=0.6.5. It is in
beta; the API may change in the future." We are on 0.7.5, and it ships in the
installed tree at `deepagents/middleware/rubric.py`.

From the module docstring (`rubric.py:1-11`):

> "Each time the agent would otherwise finish — i.e. the model returns a
> response with no further tool calls — the middleware invokes a separate grader
> sub-agent against the transcript. If the grader returns `needs_revision`, its
> feedback is injected as a `HumanMessage` and the agent loop resumes. Grading
> repeats until the grader returns `satisfied` or `failed`, or `max_iterations`
> is reached."

Signature (`rubric.py:492-499`): `RubricMiddleware(*, model, system_prompt=None,
tools=None, max_iterations=3, on_evaluation=None)`. Terminal statuses:
`satisfied`, `failed`, `max_iterations_reached`, `grader_error`
(`rubric.py:73-92`).

Three properties make it a real candidate rather than a curiosity:

- **It activates per invocation, not per build.** "The middleware activates only
  when a caller passes a `rubric` on invocation state. With no rubric, both
  `before_agent` and `after_agent` return without modifying state, so the
  middleware is safe to include unconditionally in a `create_deep_agent` stack"
  (`rubric.py:441-446`). So a greeting stays a greeting, and an uploaded-file
  analysis can carry a rubric. It needs `AgentRunner.start` to pass a `rubric`
  key alongside `messages` (`runner.py:67-69`) — a small, well-shaped change.
- **The map's "required floor" is exactly a rubric.** The map already decided
  the report is "agent-composed per dataset with a required floor, not a fixed
  template". A rubric is the machine-checkable form of that sentence, checked by
  a grader that reads the transcript rather than by hoping the prompt held.
- **It is bounded by construction** (`max_iterations`, default 3), which is the
  opposite of today's unbounded bot run.

Costs to weigh honestly: it is beta with a changing API; it adds a grader model
call every time the agent would have finished; on a non-`satisfied` termination
it does not touch the response, so `delivery.py` would ship whatever the model
said before the grader gave up unless the caller inspects `_rubric_status`
(`rubric.py:449-460`); and the grader's feedback lands as a `HumanMessage` in a
thread whose history is also the user's chat history.

---

## Verified vs inferred

**Verified by reading installed source, or by running the code:**

- `recursion_limit` is 9,999, bound at compile time, and unoverridden here
  (read back off the real compiled agent).
- The agent's tool list is exactly `delete, edit_file, execute, glob, grep, ls,
  read_file, task, write_file, write_todos` — both `task` and `write_todos` are
  live.
- `TodoListMiddleware` is not in deepagents 0.7.5's default stack; `builder.py`
  supplies it.
- `TodoListMiddleware` injects only a static system prompt; the todo list itself
  reaches the model only as a `ToolMessage`.
- `Todo` has fields `content` and `status` only — no room for a result.
- Subagent graphs are compiled with no checkpointer, while the parent's
  `interrupt_on` is propagated into them, and `interrupt_on` is documented to
  require a checkpointer.
- `todos` are excluded from subagent input and output.
- `ModelCallLimitMiddleware`, `ToolCallLimitMiddleware` and `RubricMiddleware`
  all exist in the installed tree and none is used.
- This app's backend is `LocalShellBackend(root_dir=".")`.
- Compaction is at 40k tokens keeping 6 messages; evicted text is appended to
  `conversation_history/{thread_id}.md`.
- The Telegram path never calls `run_to_completion`, so `max_rounds=12` does not
  apply in production.
- Neither the four `docs/*.md` files nor the git history records any rationale
  for the task-tool ban, and none of them mentions `write_todos`.

**Verified by fetching the cited documentation page directly** (not a secondary
summary): every doc quote in this note.

**Inferred, not executed:**

- That an interrupt raised inside a subagent would replay that subagent on
  resume, re-running its side effects. Every component of the argument is
  verified; the failure itself was not reproduced.
- That the todo `ToolMessage` being evicted by compaction actually degrades an
  8-step run. The eviction follows from the config with certainty; the
  behavioural cost is a prediction.
- That translating the deep-research prompt's assess/budget/stop triple into
  analysis terms will work here. It is the best-supported option available, not
  a tested result.
