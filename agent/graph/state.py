from typing import Annotated, TypedDict
from langgraph.graph.message import add_messages


def _merge_metrics(left: list | None, right: list | None) -> list:
    if left is None:
        left = []
    if right is None:
        right = []
    return left + right


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