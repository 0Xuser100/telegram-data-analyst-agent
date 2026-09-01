# How deepagents supports an iteration loop

Research note for [issue #5](https://github.com/0Xuser100/telegram-data-analyst-agent/issues/5),
part of the map in [issue #1](https://github.com/0Xuser100/telegram-data-analyst-agent/issues/1).

**Question.** The agent must move from one fixed analysis pass to
hypothesis -> test -> follow-what-it-finds. Does deepagents give primitives for
that, or is it purely a prompt concern?

**Answer in one line.** The *loop itself* is a prompt concern — nothing in
deepagents or LangChain sequences hypotheses for you. But four surrounding
concerns are library concerns with real primitives, and three of them are
currently mis-wired for a multi-step run: the plan the model writes is
invisible after compaction, nothing caps the number of steps, and the durable
place to put findings between steps is the filesystem, not the todo list.

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
  deliberately passes `system_prompt=""` to its own middleware to trim this
  prose (`deepagents/graph.py:630-640`); that trimming does not apply here.
  So the agent is already paying a few hundred tokens of planning instructions
  on every model call.
- `tests/test_agent_wiring.py:115-121` asserts `TodoListMiddleware` is present
  under the docstring *"Passing middleware must not remove the built-ins"* and
  lists it among "the deepagents core stack". That framing is wrong for 0.7.5 —
  it is not a built-in. The assertion still passes (this repo supplies it), but
  the test would not catch its removal for the reason it claims to.

### Is it the right vehicle for "form a hypothesis, test it, revise"?

**Partly. It is a revisable plan tracker, and nothing more.** The tool prompt is
explicit that revision is intended:

> "The plan may need future revisions or updates based on results from the
> first few steps" (`todo.py`, trigger 5 in *When to Use This Tool*)

> "After completing a task — Mark it as completed and add any new follow-up
> tasks discovered during implementation." (`todo.py`, *How to Use This Tool*)

> "Don't be afraid to revise the To-Do list as you go. New information may
> reveal new tasks that need to be done, or old tasks that are irrelevant."
> (`todo.py:130-131`, `WRITE_TODOS_SYSTEM_PROMPT`)

That is genuinely the shape we want: a plan the model rewrites as results
arrive. But three limits matter:

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
3. **It explicitly discourages itself for short work** — "If the user's request
   is trivial and takes less than 3 steps, it is better to NOT use this tool"
   (`todo.py:53`) — which is compatible with the existing SCOPE rule in
   `SYSTEM_RULES` that a greeting gets one sentence.

**Verdict:** keep `write_todos` as the *spine* of the loop (it is the only
primitive that models "plan, then revise the plan"), but do not expect it to
carry state. And treat item 2 as a real wiring bug to decide about, not a
detail.

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
`src/analyst/agent/prompts.py:9`:

```
- Do ALL the work yourself. Do NOT use the task tool or delegate to a sub-agent.
```

`tests/test_prompts.py:63` pins the string. Git history is two commits and
carries no explanation (`git log -S "Do NOT use the task tool"` returns only
the plumbing commit). None of `docs/HIGH_LEVEL_DESIGN.md`,
`docs/LOW_LEVEL_DESIGN.md`, `docs/SOLID_AND_PATTERNS.md`, `docs/TESTING.md`
mention the task tool, sub-agents, or `write_todos` at all.

So the honest answer is: unknown as recorded. **But the source gives a
correctness reason strong enough that the ban should stand regardless of what
motivated it**, and this is the part that matters for the iteration decision:

- The subagent graph is compiled by `_create_subagent_from_spec`
  (`deepagents/middleware/subagents.py:341-385`) via `create_agent(...)` with
  **no `checkpointer`** among `create_agent_kwargs`.
- The `SubAgent` docstring for `interrupt_on` says plainly: **"Requires a
  checkpointer."** (`subagents.py:70`).
- Yet `deepagents/graph.py` propagates the parent's `interrupt_on` into every
  subagent: `subagent_interrupt_on = spec.get("interrupt_on", interrupt_on)`
  (`graph.py:719`), and `_create_subagent_from_spec` duly appends a
  `HumanInTheLoopMiddleware` for it (`subagents.py:370-372`).
- The subagent is invoked **synchronously inside the parent's tool call**:
  `result = subagent.invoke(subagent_state, subagent_config)`
  (`subagents.py:566-567`).

*(Inferred, not executed:)* this repo's entire approval story —
`INTERRUPT_ON = {"execute": True, "write_file": True, ...}`
(`builder.py:34-39`), an approval card that must survive a bot restart
(`builder.py:49-53`), `AgentRunner.waiting_for` reading pauses back out of the
checkpointer (`runner.py:57-63`) — depends on an interrupt being resumable at
the point it was raised. An interrupt raised inside an uncheckpointed subagent
cannot be; resuming the parent re-enters the `task` tool and replays the
subagent from its first step, re-running whatever it already ran. That is a
correctness hazard for a tool whose whole job is executing scripts.

Two further reasons the ban is right for *this* agent shape specifically:

- **Isolation is the wrong default for iteration.** The value proposition of a
  subagent is a clean context window; the cost is that intermediate findings
  never reach the parent ("only sees your final assistant message"). A
  hypothesis loop is precisely the workload where step *n+1* needs the detail
  of step *n*. Delegating an analysis step throws away the thing the next step
  reasons over.
- **`todos` do not cross the boundary.** `_EXCLUDED_STATE_KEYS = {"messages",
  "todos", "structured_response"}` (`subagents.py:253-256`) — the todo list is
  not passed down and not returned up, because it has "no defined reducer and
  no clear meaning for returning them from a subagent to the main agent"
  (`subagents.py:264-266`). So a subagent cannot participate in the plan.

Note the one thing that *does* cross: any other state key, including the
backend's file state, is passed both ways (everything not in
`_EXCLUDED_STATE_KEYS`) — and with this app's `LocalShellBackend` the files are
on real disk anyway, so a subagent would share the workspace.

**Does an iterative analysis change the calculus?** It strengthens the ban for
the analysis loop itself. The only shape where sub-agents would earn their keep
is a *fan-out of genuinely independent, read-only* sub-questions whose answers
compress to a paragraph each — and even that trips the interrupt problem as
long as `execute`/`write_file` are gated. If sub-agents are ever wanted, the
prerequisite is the map's scoped auto-approve landing first, so the subagent
raises no interrupt at all.

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

Verified on the real compiled agent:

```
config: {'recursion_limit': 9999,
         'metadata': {'ls_integration': 'deepagents',
                      'lc_versions': {'deepagents': '0.7.5'}, ...},
         'configurable': {}}
```

Nothing in this repo overrides it — `grep -rn "recursion_limit\|max_iterations" src tests` finds no hits.
`AgentRunner._invoke` builds its config from `thread_id`, `run_name` and
`metadata` only (`src/analyst/agent/runner.py:76-86`), so the bound 9,999
stands.

### What the repo's own cap does and does not do

`run_to_completion(..., max_rounds: int = 12)` (`runner.py:20-35`) raises
`TooManyApprovals` after 12 rounds. Two limits on that:

- It counts **approval rounds**, not tool calls or model calls. A step that
  needs no approval is free.
- **The Telegram bot never calls it.** `run_to_completion` is used only by
  `src/analyst/entrypoints/cli.py:42-43` and `tests_e2e/test_end_to_end.py:81`.
  The bot is event-driven: `Router` calls `self._runner.resume(...)` once per
  button tap (`src/analyst/conversation/router.py:208-218`). So in production
  there is **no bound at all** on how long one analysis runs beyond
  `recursion_limit=9999` and the user's patience.

### The primitives that exist and are not used

LangChain 1.3 ships two middlewares built exactly for this, both present in the
installed tree and neither wired in:

- **`ModelCallLimitMiddleware`** (`langchain/agents/middleware/model_call_limit.py`):
  `thread_limit` / `run_limit` / `exit_behavior="end" | "error"`
  (`model_call_limit.py:126-160`). Raises `ModelCallLimitExceededError` on
  `"error"` (`model_call_limit.py:63-92`); `"end"` stops the loop cleanly.
- **`ToolCallLimitMiddleware`** (`langchain/agents/middleware/tool_call_limit.py`):
  the same plus an optional `tool_name=` so a single tool can be capped, and
  `exit_behavior` defaulting to `"continue"`, which injects
  `"Tool call limit exceeded. Do not call '<tool>' again."` back to the model
  (`tool_call_limit.py:53-71`, `141-208`).

The full list of built-in middleware found under
`langchain/agents/middleware/`: `context_editing`, `file_search`,
`human_in_the_loop`, `model_call_limit`, `model_fallback`, `model_retry`, `pii`,
`provider_tool_search`, `shell_tool`, `summarization`, `todo`,
`tool_call_limit`, `tool_emulator`, `tool_error`, `tool_retry`,
`tool_selection`, plus internals (`_execution`, `_redaction`, `_retry`,
`_trace_policy`, `internal_call_transformer`).

**Verdict:** step-limiting is a library concern with a ready answer.
`ModelCallLimitMiddleware(run_limit=N, exit_behavior="end")` is the natural
place to put the map's "cost guardrails" once the model swap lands, and
`ToolCallLimitMiddleware(tool_name="execute", ...)` is the natural place to put
a bound on the map's "repeated script failure" spin. Both take effect *inside*
one graph run, which is where the bot has no protection today.

---

## 4. State between steps

There are **four** channels, not one, and they behave differently:

**(a) Conversation history.** The default. Subject to compaction:
`SummarizationMiddleware` with `trigger=("tokens", 40_000)`,
`keep=("messages", 6)` (`builder.py:56-69`). The deepagents default for this
model would have been `trigger=("tokens", 170000), keep=("messages", 6)`
(`deepagents/middleware/summarization.py:280-285`) — the repo lowered the
trigger deliberately (`builder.py:26-30`). Evicted messages are not lost: they
are written as markdown to `/conversation_history/{thread_id}.md`, appended per
event (`deepagents/middleware/summarization.py:43-50`), and the agent can
`read_file` them. **Implication for an 8-step loop: earlier script output will
be summarized away, and the summary is an LLM's paraphrase of numbers.** This is
the strongest argument that findings must not live only in the transcript.

**(b) The real filesystem — the durable channel.** This app uses
`LocalShellBackend(root_dir=".")`
(`src/analyst/plumbing/backend.py:27-33, 52`), chosen because
`FilesystemBackend` cannot run commands. So `write_file` / `execute` /
`read_file` / `glob` / `grep` act on actual disk under the repo root, which
means a later script can read what an earlier script *wrote* — a CSV of
intermediate results, a JSON of fitted parameters — with no dependence on the
transcript at all, and it survives compaction, restarts, and even `/new`
(a fresh thread generation via `ThreadStore.start_new`,
`src/analyst/plumbing/thread_store.py:38-56`, changes the checkpoint thread but
not the disk).

This is the answer to "how does a later script learn what an earlier one
found": **it should be told to write its findings to a file in `output/`, and
read them back.** That is a prompt instruction resting on a library guarantee,
and it is the piece that is missing today — `SYSTEM_RULES` never mentions
persisting intermediate results.

(For contrast, the alternative backends: `StateBackend` keeps files in graph
state, so they "persist within a conversation thread but not across threads"
(`deepagents/backends/state.py:37-46`); `StoreBackend` uses the LangGraph store.
Neither offers command execution.)

**(c) Automatic overflow-to-file.** `FilesystemMiddleware` offloads oversized
tool results to disk and replaces the message with a path plus a head/tail
preview: default `tool_token_limit_before_evict=20000`,
`human_message_token_limit_before_evict=50000`
(`deepagents/middleware/filesystem.py:1604-1605`), with the replacement text at
`deepagents/middleware/_message_eviction.py:22-34`:

> "Tool result too large, the result of this tool call {tool_call_id} was saved
> in the filesystem at this path: {file_path} ... You can read the result from
> the filesystem by using the read_file tool"

So a very chatty `execute` step degrades gracefully rather than blowing the
window. This is free and already active.

**(d) The `todos` state key.** Survives compaction as *state* but, as shown in
§1, is not re-injected into the prompt, so surviving in state buys nothing
today.

---

## 5. Is there a documented pattern for this agent shape?

See the doc-survey section below. In short: the deepagents material documents
the *ingredients* (planning tool, subagents, filesystem, middleware) and
positions "deep agents" as the answer to long-horizon work, but there is no
canonical "hypothesis -> test -> revise" recipe to copy. The closest
first-party artefact is the `write_todos` prompt itself, which describes a plan
revised from results — and that is a prompt, which is the point.

---

## What this means for the ticket's question

**Prompt concern:**

- Deciding *what* the next hypothesis is, and when the loop stops. No primitive
  does this; it is `SYSTEM_RULES` work.
- Instructing the agent to persist intermediate findings to `output/` and read
  them back. Library guarantees it; only the prompt can ask for it.
- Instructing the agent to actually use `write_todos` for multi-step analysis
  (it currently has the tool and no instruction about it, and the tool's own
  prompt tells it to skip the tool for short work).

**Library concern, with a primitive:**

- Step/cost bounds -> `ModelCallLimitMiddleware`, `ToolCallLimitMiddleware`.
- Keeping the plan visible late in a long run -> either raise
  `COMPACT_AT_TOKENS`, raise `KEEP_MESSAGES`, or add a small middleware that
  re-injects `state["todos"]` into the prompt each turn. This is a decision the
  map does not currently have.
- Sub-agents: leave off. Correctness, not just cost, and the ban should be kept
  with that reason written down.

---

## Doc survey


Primary sources, all read for this note. Installed versions verified locally.

**Step limits.** [LangGraph graph API](https://docs.langchain.com/oss/python/langgraph/graph-api):
`recursion_limit` caps *super-steps*, defaults to **1000** since 1.0.6, and is a
standalone `config` key, not a member of `configurable`. Exceeding it raises
`GraphRecursionError` (`langgraph.errors`). A `RemainingSteps` managed value
allows graceful degradation before the limit. The often-quoted default of 25 is
the **JavaScript** default (`recursionLimit`), not Python's. No doc anywhere
maps one tool call onto a number of super-steps; that model-node -> tools-node
-> model-node is roughly two super-steps per iteration is an inference from the
[middleware overview](https://docs.langchain.com/oss/python/langchain/middleware)
description of the agent loop, not a documented statement.

**Bounds that are not `recursion_limit`.**
[`ModelCallLimitMiddleware`](https://docs.langchain.com/oss/python/langchain/middleware/built-in#model-call-limit)
takes `thread_limit` / `run_limit` and an `exit_behavior` of `end` (graceful,
the default) or `error`. `ToolCallLimitMiddleware` does the same per tool, with
`exit_behavior` defaulting to `continue`. Thread-scoped limits need a
checkpointer, which this repo has. Custom `before_model` hooks can also exit
early by returning `jump_to`.

**Planning.** [`TodoListMiddleware`](https://reference.langchain.com/python/langchain/agents/middleware/todo/TodoListMiddleware)
supplies the `write_todos` tool plus a system prompt, enforces at most one call
per model turn, and stores the list under the `todos` state key. Its built-in
[system prompt](https://reference.langchain.com/python/langchain/agents/middleware/todo/WRITE_TODOS_SYSTEM_PROMPT)
tells the model to skip the tool for short work and to "revise the To-Do list as
you go. New information may reveal new tasks" — a plan revised from results,
which is the loop this ticket asks about, expressed as a prompt. The
[tool description](https://reference.langchain.com/python/langchain/agents/middleware/todo/WRITE_TODOS_TOOL_DESCRIPTION)
sets the bar at "3 or more distinct steps". Deep Agents made planning
[opt-in at v0.7](https://docs.langchain.com/oss/python/deepagents/overview);
this repo opts in explicitly (`builder.py:80`).

**Compaction.** [`SummarizationMiddleware`](https://reference.langchain.com/python/langchain/agents/middleware/summarization/SummarizationMiddleware)
replaces older messages with a generated summary; its
[prompt](https://reference.langchain.com/python/langchain/agents/middleware/summarization/DEFAULT_SUMMARY_PROMPT)
states outright that "the conversation history below will be replaced", with an
`## ARTIFACTS` section whose stated purpose is to prevent "silent loss of
artifact information". Only the messages inside `keep` retain full fidelity.
Deep Agents softens this with
[filesystem preservation](https://docs.langchain.com/oss/python/deepagents/context-engineering):
the original messages are written to disk as a canonical record, and tool
results over ~20k tokens are offloaded to a file path plus a ten-line preview.
[`ContextEditingMiddleware`](https://docs.langchain.com/oss/python/langchain/middleware/built-in#context-editing)
is the surgical alternative: it clears old tool outputs while preserving the
most recent `keep` of them and any tool named in `exclude_tools`, rather than
paraphrasing them away.

**Human-in-the-loop.** [Docs](https://docs.langchain.com/oss/python/langchain/human-in-the-loop):
`interrupt_on` maps a tool name to `True`, `False` (auto-approve), or an
`InterruptOnConfig`. That config carries `allowed_decisions` and — the finding
that matters most for issue #2 — a **`when` predicate receiving the
`ToolCallRequest`**, returning `True` to interrupt and `False` to auto-approve,
available since `langchain>=1.3.3` (this repo has 1.3.15). Calls the predicate
rejects "are never added to the interrupt batch, so a reviewer only sees the
actions that need a decision". There is no global allowlist key; approval is
per-tool or predicate-scoped. A checkpointer is required, and resumption is
`Command(resume={"decisions": [...]})` on the same `thread_id`.

**Not found.** No `max_iterations` or `max_steps` parameter on `create_agent`
(the only `max_iterations` in the Python docs belongs to `RubricMiddleware`,
which caps self-grading, not the agent loop). No canonical
hypothesis-test-revise recipe in the deepagents material. The
`/oss/python/langgraph/errors` index is a 404; per-error pages live one level
deeper.
