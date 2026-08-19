import os

from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from psycopg_pool import AsyncConnectionPool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from agent.llm import llm
from agent.hitl import approval_result, email_approval, EMAIL_APPROVAL_TOOL_NAMES
from agent.state import AgentState
from agent.nodes import (
    after_tools,
    agent_node,
    should_continue,
    nlp_node,
    supervisor_router,
)


DB_URI = (
    f"postgresql://{os.getenv('DB_USER')}:"
    f"{os.getenv('DB_PASSWORD')}@"
    f"{os.getenv('DB_HOST', 'localhost')}:"
    f"{os.getenv('DB_PORT', '5432')}/"
    f"{os.getenv('DB_NAME')}"
)

pool = None
memory = None

async def setup_checkpointer():
    global pool, memory
    if memory is not None:
        return

    if pool is None:
        pool = AsyncConnectionPool(DB_URI, max_size=10, kwargs={"autocommit": True}, open=False)

    try:
        await pool.open()
        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()
        memory = checkpointer
    except Exception:
        await pool.close()
        pool = None
        memory = None
        raise


async def ensure_checkpointer():
    """Initialize the checkpointer for non-ASGI entry points and tests."""
    if memory is None:
        await setup_checkpointer()

async def close_checkpointer():
    global pool, memory
    if pool is not None:
        await pool.close()
    pool = None
    memory = None


def scoped_should_continue(state, domain):
    last_message = state["messages"][-1]

    if not last_message.tool_calls:
        print(f"[ROUTER:{domain}] scoped_should_continue -> 'end' (no tool calls)")
        return "end"
    print(f"tool call name is {last_message.tool_calls[0]['name']}")
    if domain == "email":
        for tool_call in last_message.tool_calls:
            if tool_call["name"] in EMAIL_APPROVAL_TOOL_NAMES:
                print(f"[ROUTER:{domain}] scoped_should_continue -> 'email_approval'")
                return "email_approval"

    print(f"[ROUTER:{domain}] scoped_should_continue -> 'tools'")
    return "tools"


def scoped_after_tools(state, domain):
    print("\n========== AFTER TOOLS ==========")

    for i, message in enumerate(state["messages"]):
        print(
            i,
            type(message).__name__,
            "name=", getattr(message, "name", None),
            "tool_calls=", getattr(message, "tool_calls", None),
            "content=", getattr(message, "content", None),
        )

    print("=================================\n")

    last_message = state["messages"][-1]

    if (
        domain == "email"
        and getattr(last_message, "name", None) in EMAIL_APPROVAL_TOOL_NAMES
    ):
        print(f"[ROUTER:{domain}] scoped_after_tools -> 'end'")
        return "end"

    print(f"[ROUTER:{domain}] scoped_after_tools -> 'agent'")
    return "agent"


DOMAINS = ["email", "calendar", "docs", "sheets", "slack", "research"]
    
def create_graph(tools_groups):
    if memory is None:
        raise RuntimeError("Checkpointer not set up. Call setup_checkpointer() first.")

    print(f"[GRAPH] Building graph for domains: {list(tools_groups.keys())}")

    graph = StateGraph(AgentState)

    available_domains = set(tools_groups)

    def user_nlp_node(state):
        return nlp_node(state, available_domains)

    graph.add_node("nlp", user_nlp_node)

    graph.add_edge(START, "nlp")

    graph.add_conditional_edges(
        "nlp",
        supervisor_router,
                {
                        "general": END,
                        **{
                                domain: f"{domain}_agent"
                                for domain in DOMAINS
                                if domain in available_domains
                        },
                },
    )

    for domain in DOMAINS:
        domain_tools = tools_groups.get(domain, [])
        if not domain_tools:
            continue
        print(f"[GRAPH] Wiring '{domain}' agent+tools nodes with tools: {[t.name for t in domain_tools]}")
        llm_with_tools = llm.bind_tools(domain_tools)

        def make_agent(llm_with_tools):
            def scoped_agent(state):
                return agent_node(state, llm_with_tools)
            return scoped_agent
        
        agent_name = f"{domain}_agent"
        tools_name = f"{domain}_tools"

        graph.add_node(agent_name, make_agent(llm_with_tools))
        graph.add_node(tools_name, ToolNode(domain_tools, handle_tool_errors=True))

        graph.add_conditional_edges(
            agent_name,
            lambda state, domain=domain: scoped_should_continue(state, domain),
            {
                "tools": tools_name,
                "email_approval": "email_approval",
                "end": END,
            },
        )

        graph.add_conditional_edges(
            tools_name,
            lambda state, domain=domain: scoped_after_tools(state, domain),
            {
                "agent": agent_name,
                "end": END,
            },

        )


    if tools_groups.get("email"):
        print("[GRAPH] Wiring 'email_approval' HITL node")

        graph.add_node("email_approval", email_approval)

        graph.add_conditional_edges(
            "email_approval",
            approval_result,
            {
                "send": "email_tools",
                "cancel": END,
            },
        )

    print("[GRAPH] Graph build complete, compiling with checkpointer")
    return graph.compile(checkpointer=memory)