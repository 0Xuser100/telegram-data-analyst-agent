"""Keeping the agent's own plan in front of it.

`TodoListMiddleware` gives the agent a `write_todos` tool and a static system
prompt about using it. The plan the agent actually writes comes back only as a
tool result -- an ordinary message, which the summarizer evicts once a run gets
long. `state["todos"]` survives that eviction, but nothing reads it back into
the prompt, so surviving buys nothing.

For a two-step run that never mattered. For a hypothesis loop it is the
difference between finishing the analysis you started and finishing a different
one, so the plan is re-attached to the system prompt on every turn. A few
hundred tokens, and it scales with the length of the run rather than deferring
the problem the way a larger compaction budget would.
"""

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage

HEADING = "\n\nYOUR CURRENT PLAN (you wrote this; revise it as you learn):\n"


def _render(todos) -> str:
    """One line per step, with its status. Anything malformed is skipped.

    The list is written by the model, so it can be any shape at all; a plan
    that fails to render must not take the run down with it.
    """
    lines = []
    for todo in todos or []:
        if not isinstance(todo, dict):
            continue
        content = todo.get("content")
        if not content:
            continue
        lines.append(f"- [{todo.get('status', 'pending')}] {content}")
    return "\n".join(lines)


class PlanVisibilityMiddleware(AgentMiddleware):
    """Appends the current todo list to the system prompt on every model call."""

    name = "PlanVisibilityMiddleware"

    def wrap_model_call(self, request, handler):
        plan = _render((request.state or {}).get("todos"))
        if not plan:
            return handler(request)         # a greeting pays nothing

        existing = request.system_message.content if request.system_message else ""
        return handler(request.override(
            system_message=SystemMessage(existing + HEADING + plan)))
