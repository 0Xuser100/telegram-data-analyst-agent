"""How the agent is assembled: builders, model, checkpointer, compaction.

Marked `slow` because building the graph is the expensive part. No model call is
made.
"""

import pytest

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def mod():
    from analyst.agent import builder as agent
    return agent


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------

def test_build_model_uses_the_configured_model(mod):
    from langchain_openai import ChatOpenAI
    model = mod.build_model()
    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "gpt-5.6-luna"


def test_the_model_is_reached_through_the_responses_api(mod):
    """gpt-5.x rejects function tools on /v1/chat/completions while a
    reasoning effort is in play, and langchain-openai sends that key whatever
    we do. The Responses API is the fix that keeps reasoning switched on;
    reasoning_effort="none" also clears the error but by disabling the
    reasoning we changed model for."""
    assert mod.build_model().use_responses_api is True


def test_the_key_is_passed_explicitly(mod):
    """pydantic-settings reads .env without exporting to os.environ, so
    ChatOpenAI's own OPENAI_API_KEY lookup would miss it."""
    assert mod.build_model().openai_api_key.get_secret_value() == "sk-test-not-a-real-key"


def test_build_checkpointer_is_durable(mod, tmp_path):
    """An in-memory saver would orphan every pending approval on restart."""
    from langgraph.checkpoint.sqlite import SqliteSaver
    assert isinstance(mod.build_checkpointer(str(tmp_path / "c.sqlite")), SqliteSaver)


def test_the_checkpointer_writes_where_it_is_told(mod, tmp_path):
    path = tmp_path / "own.sqlite"
    mod.build_checkpointer(str(path))
    assert path.exists()


def test_build_summarizer_compacts_at_40k(mod):
    """The deepagents default resolves to 170k for this model, which these
    ~40k-per-turn runs never reach: the conversation grew until it was
    expensive, not until it was compacted."""
    summarizer = mod.build_summarizer(mod.model)
    assert summarizer._lc_helper.trigger == ("tokens", 40_000)
    assert summarizer._lc_helper.keep == ("messages", 16)


def test_tool_arg_clipping_is_preserved(mod):
    """The constructor defaults truncate_args_settings to None, which would drop
    the cheap step that often avoids compaction entirely. Compared against the
    factory's own defaults, which differ by model."""
    from deepagents.middleware.summarization import compute_summarization_defaults

    expected = compute_summarization_defaults(mod.model)["truncate_args_settings"]
    assert mod.summarizer._truncate_args_trigger == expected["trigger"]
    assert mod.summarizer._truncate_args_keep == expected["keep"]


def test_evicted_history_is_offloaded_not_dropped(mod):
    assert mod.summarizer._history_path_prefix == "/conversation_history"


def test_the_summarizer_keeps_the_default_name(mod):
    """The name is what makes create_deep_agent replace the default instead of
    stacking a second summarizer."""
    assert mod.summarizer.name == "SummarizationMiddleware"


def test_thread_config_shape(mod):
    assert mod.thread_config(123) == {"configurable": {"thread_id": "123"}}


# --------------------------------------------------------------------------
# the assembled stack
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def stack(mod):
    """Build an agent through a spy to capture the final middleware list."""
    import deepagents.graph as graph

    captured = {}
    original = graph.create_agent

    def spy(*args, **kwargs):
        captured["middleware"] = list(kwargs.get("middleware", []))
        return original(*args, **kwargs)

    graph.create_agent = spy
    try:
        mod.build_agent(model=mod.model, checkpointer=mod.checkpointer,
                        summarizer=mod.summarizer)
    finally:
        graph.create_agent = original
    return captured["middleware"]


def test_exactly_one_summarizer_is_registered(stack):
    assert [m.name for m in stack].count("SummarizationMiddleware") == 1


def test_the_registered_summarizer_is_the_one_we_passed(stack, mod):
    summarizer = next(m for m in stack if m.name == "SummarizationMiddleware")
    assert summarizer is mod.summarizer


def test_the_deepagents_core_stack_is_still_there(stack):
    """Passing middleware must not remove the built-ins."""
    names = [m.name for m in stack]
    for required in ("FilesystemMiddleware", "SubAgentMiddleware",
                     "PatchToolCallsMiddleware", "TodoListMiddleware",
                     "HumanInTheLoopMiddleware"):
        assert required in names


def test_human_in_the_loop_is_last(stack):
    """Approval gates the tool call after everything else has shaped it."""
    assert stack[-1].name == "HumanInTheLoopMiddleware"


# --------------------------------------------------------------------------
# approval rules
# --------------------------------------------------------------------------

def test_writes_and_commands_are_gated(mod):
    assert mod.INTERRUPT_ON["execute"] is True
    assert mod.INTERRUPT_ON["write_file"] is True


def test_reads_are_not_gated(mod):
    """Read-only tools must not ask, or every run turns into button-tapping."""
    assert mod.INTERRUPT_ON["read_file"] is False
    assert mod.INTERRUPT_ON["ls"] is False


# --------------------------------------------------------------------------
# run bounds
# --------------------------------------------------------------------------

def test_the_plan_is_kept_visible(stack):
    """Compaction evicts the tool result the plan arrives in, so it has to be
    re-attached to the prompt or a long run loses its way."""
    assert "PlanVisibilityMiddleware" in [m.name for m in stack]


def test_a_run_is_bounded_by_model_calls(stack):
    """Nothing bounded a run before: deepagents sets recursion_limit to 9_999,
    and the CLI's own cap is not on the bot's path at all."""
    limiter = next(m for m in stack if m.name == "ModelCallLimitMiddleware")
    assert limiter.run_limit == 40
    assert limiter.thread_limit is None         # follow-ups share a thread


def test_repeated_execution_is_bounded_separately(stack):
    """A script that keeps failing must not eat the whole model-call budget."""
    limiter = next(m for m in stack
                   if m.name.startswith("ToolCallLimitMiddleware"))
    assert limiter.run_limit == 15
    assert limiter.thread_limit is None


def test_a_bounded_run_answers_instead_of_raising(stack):
    """The measured run that would have been killed by the old cap produced
    the correct analysis, so a bound must return what it has."""
    limiter = next(m for m in stack if m.name == "ModelCallLimitMiddleware")
    assert limiter.exit_behavior == "end"


# --------------------------------------------------------------------------
# the app defaults
# --------------------------------------------------------------------------

def test_the_module_exposes_a_ready_agent(mod):
    assert mod.agent is not None
    assert mod.model is not None
    assert mod.checkpointer is not None


def test_destructive_and_delegating_tools_always_ask(mod):
    """The backend exposes `delete` and `task` as well as the write tools.
    Neither is ever routine: an analysis has no reason to remove a file, and
    losing the user's data is not fixed by re-running."""
    rules = mod.build_interrupt_on(auto_approve=True)
    assert rules["delete"] is True
    assert rules["task"] is True
