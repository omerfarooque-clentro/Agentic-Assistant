import os

import asyncio
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from psycopg_pool import AsyncConnectionPool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from agent.llm import llm
from agent.hitl import approval_result, approval_node, TOOL_NAMES_BY_DOMAIN
from agent.state import AgentState
from agent.nodes import (
    agent_node,
    nlp_node,
    supervisor_router,
    scoped_after_tools,
    scoped_should_continue,
    thread_naming_node,
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
_init_lock = asyncio.Lock()

async def setup_checkpointer():
    global pool, memory
    async with _init_lock:
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
    async with _init_lock:
        if pool is not None:
            await pool.close()
        pool = None
        memory = None


DOMAINS = ["email", "calendar", "docs", "sheets", "slack", "research"]
    
def create_graph(tools_groups):
    if memory is None:
        raise RuntimeError("Checkpointer not set up. Call setup_checkpointer() first.")

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
                        # Chit-chat/unrouted intent falls back to the research agent (web
                        # search) when available, since that's a direct/no-approval domain.
                        "general": "research_agent" if "research" in available_domains else "thread_naming",
                        **{
                                domain: f"{domain}_agent"
                                for domain in DOMAINS
                                if domain in available_domains
                        },
                },
    )

    graph.add_node("thread_naming", thread_naming_node)
    graph.add_edge("thread_naming", END)
   
    for domain in DOMAINS:
        domain_tools = tools_groups.get(domain, [])
        if not domain_tools:
            continue
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
                "approval": "approval",
                "end": "thread_naming",
            },
        )
        
        graph.add_conditional_edges(
            tools_name,
            lambda state, domain=domain: scoped_after_tools(state, domain),
            {
                "agent": agent_name,
                "end": "thread_naming",
            },

        )
 
    approval_domains = [
        domain for domain in TOOL_NAMES_BY_DOMAIN if tools_groups.get(domain)
    ]
    if approval_domains:
        graph.add_node("approval", approval_node)

        graph.add_conditional_edges(
            "approval",
            approval_result,
            {
                **{domain: f"{domain}_tools" for domain in approval_domains},
                "cancel": "thread_naming",
            },
        )

    return graph.compile(checkpointer=memory)