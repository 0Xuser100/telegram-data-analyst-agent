"""What a paused run is waiting for.

A LangGraph interrupt is a nested structure; this module flattens it into
`PendingAction` values and builds the decision payload that resumes the graph.

This is agent-layer data, deliberately free of any front-end: it says *what*
the agent stopped on, never how a human is asked about it. Rendering a card and
collecting the answer belong to the conversation layer
(`analyst.conversation.approvals`), which imports downward into this module.
"""

from dataclasses import dataclass, field

# edit/respond need a follow-up conversation the bot does not have.
SUPPORTED_DECISIONS = ("approve", "reject")

REJECT_MESSAGE = "User rejected this action. Do not retry it."


@dataclass(frozen=True)
class PendingAction:
    """One tool call waiting for a decision."""

    name: str
    args: dict = field(default_factory=dict)
    allowed_decisions: tuple[str, ...] = SUPPORTED_DECISIONS

    @property
    def offered_decisions(self) -> tuple[str, ...]:
        offered = tuple(d for d in self.allowed_decisions if d in SUPPORTED_DECISIONS)
        return offered or SUPPORTED_DECISIONS


def pending_actions(interrupts) -> list[PendingAction]:
    """Flatten LangGraph interrupts into the actions waiting for a decision."""
    actions: list[PendingAction] = []
    for interrupt in interrupts or ():
        value = getattr(interrupt, "value", {}) or {}
        allowed = {
            config.get("action_name"): tuple(config.get("allowed_decisions", ()))
            for config in value.get("review_configs", [])
        }
        for request in value.get("action_requests", []):
            name = request.get("name", "?")
            actions.append(PendingAction(
                name=name,
                args=request.get("args", {}) or {},
                allowed_decisions=allowed.get(name) or SUPPORTED_DECISIONS,
            ))
    return actions


def actions_in(result: dict) -> list[PendingAction]:
    """What a finished invoke is now waiting for, if anything."""
    return pending_actions((result or {}).get("__interrupt__"))


def decisions_for(choice: str, count: int) -> list[dict]:
    """One decision per pending action, in order."""
    if choice == "approve":
        return [{"type": "approve"} for _ in range(count)]
    return [{"type": "reject", "message": REJECT_MESSAGE} for _ in range(count)]
