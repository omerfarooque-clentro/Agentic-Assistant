from langchain_core.messages import SystemMessage
from agent.nlp_router import nlp_decider
from agent.state import AgentState
from .prompts import SYSTEM_PROMPT


def nlp_node(state: AgentState):
    result = nlp_decider(state["messages"][-1].content)
    if result["confidence"] > 0.5:
        routing_status = "high_confidence"
    else:
        routing_status = "low_confidence"
         
    return {
        "intent": result["intent"],
        "confidence": result["confidence"],
        "routing_status": routing_status,
    }


def agent_node(state: AgentState, llm_with_tools):

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        *state["messages"],
    ]
    response = llm_with_tools.invoke(messages)
    return {
        "messages": [response]
    }


def should_continue(state: AgentState):
    last_message = state["messages"][-1]

    if not last_message.tool_calls:
        return "end"

    for tool_call in last_message.tool_calls:
        if tool_call["name"] == "send_email":
            return "email_approval"

    return "tools"

def after_tools(state):
    last_message = state["messages"][-1]

    if getattr(last_message, "name", None) == "send_email":
        return "end"

    return "agent"
