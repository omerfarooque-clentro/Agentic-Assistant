from langchain_core.messages import SystemMessage
from agent.nlp_router import nlp_decider
from agent.state import AgentState
from .prompts import SYSTEM_PROMPT


def nlp_node(state: AgentState, available_domains=()):
    print("[NODE:nlp] Classifying intent...")
    result = nlp_decider(
        state["messages"][-1].content,
        available_domains=state.get("available_domains", available_domains)
    )

    if result["confidence"] > 0.5:
        routing_status = "high_confidence"
    else:
        routing_status = "low_confidence"

    print(f"[NODE:nlp] intent={result['intent']} confidence={result['confidence']:.2f} status={routing_status}")

    return {
        "intent": result["intent"],
        "confidence": result["confidence"],
        "routing_status": routing_status,
    }


def agent_node(state: AgentState, llm_with_tools):
    print("[NODE:agent] Invoking LLM with bound tools...")

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        *state["messages"],
    ]
    response = llm_with_tools.invoke(messages)

    tool_calls = getattr(response, "tool_calls", None) or []
    if tool_calls:
        print(f"[NODE:agent] LLM requested tool calls: {[call['name'] for call in tool_calls]}")
    else:
        print("[NODE:agent] LLM returned a final response (no tool calls)")

    return {
        "messages": [response]
    }


def supervisor_router(state: AgentState):
    print(state)
    domain = state.get("intent")
    allowed_domains = state.get("available_domains", set())

    if domain in allowed_domains:
        print(f"[ROUTER] supervisor_router -> '{domain}_agent'")
        return domain
    
    print(f"[ROUTER] supervisor_router -> 'general' (domain '{domain}' not allowed)")
    return "general"
    

def should_continue(state: AgentState):
    last_message = state["messages"][-1]

    if not last_message.tool_calls:
        print("[ROUTER] should_continue -> 'end' (no tool calls)")
        return "end"

    for tool_call in last_message.tool_calls:
        if tool_call["name"] == "send_email":
            print("[ROUTER] should_continue -> 'email_approval' (send_email requested)")
            return "email_approval"

    print("[ROUTER] should_continue -> 'tools'")
    return "tools"

def after_tools(state):
    last_message = state["messages"][-1]

    if getattr(last_message, "name", None) == "send_email":
        print("[ROUTER] after_tools -> 'end' (email already sent)")
        return "end"

    print("[ROUTER] after_tools -> 'agent'")
    return "agent"
