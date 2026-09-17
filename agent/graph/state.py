from typing import Annotated, TypedDict, Literal
from langgraph.graph.message import add_messages


def _merge_metrics(left: list | None, right: list | None) -> list:
    """Merge per-call metrics within a turn, allowing a clean reset signal for new turns."""
    if right and any(isinstance(m, dict) and m.get("__reset__") for m in right):
        return [m for m in right if not (isinstance(m, dict) and m.get("__reset__"))]
    if left is None:
        left = []
    if right is None:
        right = []
    return left + right


class PlanStep(TypedDict):
    id: int
    domain: str
    intent: str
    description: str
    status: Literal["pending", "in_progress", "completed", "failed"]
    result_summary: str | None


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    call_metrics: Annotated[list, _merge_metrics]

    approved: bool
    user_id: int
    thread_id: int

    intent: str
    domain: str
    confidence: float
    margin: float
    routing_status: str

    available_domains: set[str]
    needs_title: bool

    plan: list[PlanStep]
    current_step_index: int

    details: dict
    is_re_send: bool