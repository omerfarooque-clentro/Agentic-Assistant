import os

from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from psycopg_pool import AsyncConnectionPool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from agent.llm import llm
from agent.hitl import approval_result, email_approval
from agent.state import AgentState
from agent.nodes import (
    after_tools,
    agent_node,
    should_continue,
    nlp_node,
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
    
def create_graph(tools):
    if memory is None:
        raise RuntimeError("Checkpointer not set up. Call setup_checkpointer() first.")
    
    llm_with_tools = llm.bind_tools(tools)

    def user_agent_node(state):
        return agent_node(state, llm_with_tools)

    graph = StateGraph(AgentState)

    graph.add_node("agent", user_agent_node)
    graph.add_node("tools", ToolNode(tools, handle_tool_errors=True))
    graph.add_node("email_approval", email_approval)
    graph.add_node("nlp", nlp_node)

    graph.add_edge(START, "nlp")
    graph.add_edge("nlp", "agent")

    graph.add_conditional_edges(
        "agent",
        should_continue,
        {
            "tools": "tools",
            "email_approval": "email_approval",
            "end": END,
        },
    )

    graph.add_conditional_edges(
        "email_approval",
        approval_result,
        {
            "send": "tools",
            "cancel": END,
        },
    )

    graph.add_conditional_edges(
        "tools",
        after_tools,
        {
            "agent": "agent",
            "end": END,
        },
    )

    return graph.compile(checkpointer=memory)