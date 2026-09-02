"""The plan has to outlive compaction.

TodoListMiddleware injects a static system prompt and nothing else; the todo
list itself reaches the model only as a tool result, which the compactor
evicts part-way through a long run. The agent then forgets what it set out to
do and finishes something else -- the one failure that makes an iterative loop
worse than a single pass, because a single pass cannot lose its way.
"""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from analyst.agent.plan_visibility import PlanVisibilityMiddleware


class Request:
    """Stands in for ModelRequest: records what an override was asked for."""

    def __init__(self, state, system_message=None):
        self.state = state
        self.system_message = system_message
        self.overrides = {}

    def override(self, **kwargs):
        self.overrides = kwargs
        return Request({**self.state}, kwargs.get("system_message",
                                                  self.system_message))


def call(state, system_message=None):
    """Run the middleware and return the request the model would have seen."""
    seen = {}

    def handler(request):
        seen["request"] = request
        return AIMessage("ok")

    request = Request(state, system_message)
    PlanVisibilityMiddleware().wrap_model_call(request, handler)
    return seen["request"]


TODOS = [
    {"content": "inspect the columns", "status": "completed"},
    {"content": "check whether State mixes totals with detail", "status": "in_progress"},
    {"content": "chart deaths by cause", "status": "pending"},
]


def test_the_plan_reaches_the_model_on_every_turn():
    seen = call({"todos": TODOS}, SystemMessage("base rules"))
    text = seen.system_message.content
    assert "check whether State mixes totals with detail" in text
    assert "chart deaths by cause" in text


def test_the_original_instructions_survive():
    """Appended, never replaced: the analysis rules live in the same message."""
    seen = call({"todos": TODOS}, SystemMessage("base rules"))
    assert seen.system_message.content.startswith("base rules")


def test_each_step_carries_its_status():
    """A plan without statuses reads as a fresh plan every turn, so the agent
    redoes work it has already done."""
    text = call({"todos": TODOS}, SystemMessage("x")).system_message.content
    assert "completed" in text
    assert "in_progress" in text


def test_nothing_is_added_before_a_plan_exists():
    """A greeting must not pay for this, and an empty heading would read as an
    instruction to invent a plan."""
    seen = call({"todos": []}, SystemMessage("base rules"))
    assert seen.system_message.content == "base rules"


def test_a_missing_todos_key_is_not_an_error():
    seen = call({}, SystemMessage("base rules"))
    assert seen.system_message.content == "base rules"


def test_a_plan_with_no_system_message_still_gets_one():
    seen = call({"todos": TODOS}, None)
    assert "chart deaths by cause" in seen.system_message.content


def test_malformed_entries_do_not_break_the_run():
    """The list comes from the model, so it can be any shape at all."""
    seen = call({"todos": [{"content": "real step"}, "not a dict", {}]},
                SystemMessage("base"))
    assert "real step" in seen.system_message.content


def test_the_handler_result_is_returned_untouched():
    def handler(request):
        return AIMessage("the answer")

    result = PlanVisibilityMiddleware().wrap_model_call(
        Request({"todos": TODOS}, SystemMessage("x")), handler)
    assert result.content == "the answer"


def test_messages_are_left_alone():
    """Only the system prompt is touched; history is the compactor's business."""
    history = [HumanMessage("analyse this")]
    seen = call({"todos": TODOS, "messages": history}, SystemMessage("x"))
    assert seen.state.get("messages") == history
