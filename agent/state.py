from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    email_approved: bool
    user_id: int
    thread_id: int
    intent: str
    confidence: float
    available_domains: list[str]