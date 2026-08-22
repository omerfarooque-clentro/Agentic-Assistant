from agent.llm import llm
from agent.llm.messages import messages_for_llm
from agent.graph.approval import APPROVAL_TOOL_NAMES
from agent.routing.intent_router import route_intent
from agent.graph.state import AgentState
from conversations.models import Thread
 

# Domains whose write actions are gated behind human-in-the-loop approval.
APPROVAL_DOMAINS = {"email", "calendar", "docs", "sheets", "slack"}

def nlp_node(state: AgentState, available_domains=()):

    messages = state["messages"]
    result = route_intent(
        messages,
        available_domains=available_domains
    )

    return {
        "intent": result["intent"],
        "domain": result["domain"],
        "confidence": result["confidence"],
        "margin": result["margin"],
        "routing_status": result["status"],
    }


def agent_node(state: AgentState, llm_with_tools):
    messages = messages_for_llm(state)
    response = llm_with_tools.invoke(messages)

    return {
        "messages": [response]
    }


def supervisor_router(state: AgentState):
    if state["routing_status"] == "unavailable":
        return "general"

    return state["domain"]
    

def scoped_should_continue(state, domain):
    last_message = state["messages"][-1]

    if not last_message.tool_calls:
        return "end"
    if domain in APPROVAL_DOMAINS:
        for tool_call in last_message.tool_calls:
            if tool_call["name"] in APPROVAL_TOOL_NAMES:
                return "approval"

    return "tools"




def scoped_after_tools(state, domain):
    last_message = state["messages"][-1]

    if (
        domain in APPROVAL_DOMAINS
        and getattr(last_message, "name", None) in APPROVAL_TOOL_NAMES
    ):
        return "end"

    return "agent"



def thread_naming_node(state: AgentState):
    messages = state["messages"]

    if len(messages) <= 2:
        return {}

    thread = Thread.objects.get(id=state["thread_id"],user_id=state["user_id"])

    if thread.name != "New Thread":
        return {}

    conversation = "\n".join(
        f"{m.type}: {m.content}" for m in messages if isinstance(m.content, str)
    )

    response = llm.invoke(
        f"""
        Generate a name for this conversation.

        Rules:
        - 5 words or fewer
        - Return ONLY the name
        - No quotation marks
        - If the conversation covers unrelated topics, use "General Discussion"

        Conversation:
        {conversation}
        """
            )

    thread.name = response.content.strip()[:100]
    thread.save(update_fields=["name"])

    return {}