from agent.llm import llm
from agent.messages_for_llm import messages_for_llm
from agent.hitl import APPROVAL_TOOL_NAMES
from agent.nlp_router import nlp_decider
from agent.state import AgentState
from conversations.models import Thread
 

# Domains whose write actions are gated behind human-in-the-loop approval.
APPROVAL_DOMAINS = {"email", "calendar", "docs", "sheets", "slack"}

def nlp_node(state: AgentState, available_domains=()):
    result = nlp_decider(
        state["messages"][-1].content,
        available_domains=state.get("available_domains", available_domains)
    )

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
    messages = messages_for_llm(state)
    response = llm_with_tools.invoke(messages)

    return {
        "messages": [response]
    }


def supervisor_router(state: AgentState):
    domain = state.get("intent")
    allowed_domains = state.get("available_domains", set())

    if domain in allowed_domains:
        return domain

    return "general"
    

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