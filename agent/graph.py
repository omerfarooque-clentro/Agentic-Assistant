import os
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from psycopg_pool import ConnectionPool
from agent.hitl import approval_result, email_approval
from agent.state import AgentState
from agent.nodes import after_tools, agent_node, should_continue, tools, nlp_node
from agent.tools import search_gmail
from langgraph.checkpoint.postgres import PostgresSaver


graph = StateGraph(AgentState)

graph.add_node("agent", agent_node)
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

DB_URI = (
    f"postgresql://{os.getenv('DB_USER')}:"
    f"{os.getenv('DB_PASSWORD')}@"
    f"{os.getenv('DB_HOST', 'localhost')}:"
    f"{os.getenv('DB_PORT', '5432')}/"
    f"{os.getenv('DB_NAME')}"
)

pool = ConnectionPool(
    conninfo=DB_URI,
    max_size=10,
    kwargs={"autocommit": True},
)

memory = PostgresSaver(pool)

memory.setup()

app = graph.compile(checkpointer=memory)