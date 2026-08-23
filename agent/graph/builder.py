import os

import asyncio
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from psycopg_pool import AsyncConnectionPool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from agent.llm import bind_tools_with_fallback, llm
from agent.graph.approval import approval_result, approval_node, TOOL_NAMES_BY_DOMAIN
from agent.graph.state import AgentState
from agent.graph.nodes import (
    agent_node,
    nlp_node,
    supervisor_router,
    scoped_should_continue,
    thread_naming_node,
)
from agent.routing.intent_router import get_mcp_tool_names


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

    graph.add_node("nlp", lambda state: nlp_node(state, available_domains))
    
    graph.add_edge(START, "nlp")

    graph.add_conditional_edges(
        "nlp",
        supervisor_router,
                {
                        "general": "general_agent",
                        **{
                                domain: f"{domain}_agent"
                                for domain in DOMAINS
                                if domain in available_domains
                        },
                },
    )
    
    def general_agent(state):
        return agent_node(state, llm)

    graph.add_node("general_agent", general_agent)
    graph.add_edge("general_agent", "thread_naming")

    graph.add_node("thread_naming", thread_naming_node)
    graph.add_edge("thread_naming", END)


    approval_domains = [
        domain for domain in TOOL_NAMES_BY_DOMAIN if tools_groups.get(domain)
    ]
   
    for domain in DOMAINS:
        domain_tools = tools_groups.get(domain, [])
        if not domain_tools:
            continue

        def make_agent(domain_tools):
            def scoped_agent(state):
                allowed_names = get_mcp_tool_names(state.get("intent", ""))
                selected_tools = [
                    tool for tool in domain_tools
                    if tool.name in allowed_names
                ]
                if not selected_tools:
                    raise RuntimeError(
                        f"No tools selected for intent={state.get('intent')!r} "
                        f"in domain={domain!r}"
                    )
    
                return agent_node(
                    state,
                    bind_tools_with_fallback(selected_tools),
                )
            return scoped_agent
        
        agent_name = f"{domain}_agent"
        tools_name = f"{domain}_tools"

        graph.add_node(agent_name, make_agent(domain_tools))
        graph.add_node(tools_name, ToolNode(domain_tools, handle_tool_errors=True))

        route_map = {"tools": tools_name, "end": "thread_naming"}
        
        if domain in approval_domains:
            route_map["approval"] = "approval"

        graph.add_conditional_edges(
            agent_name,
            lambda state, domain=domain: scoped_should_continue(state, domain),
            route_map,
        )
        
        graph.add_edge(tools_name, agent_name)

    print("approval_domains:", approval_domains)
    print(
        "approval route map:",
        {
            **{domain: f"{domain}_tools" for domain in approval_domains},
            "cancel": "thread_naming",
        },
    ) 
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