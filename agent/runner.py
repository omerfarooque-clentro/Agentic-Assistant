from langchain_core.messages import HumanMessage
from agent.tools import get_user_tools
from agent.graph import create_graph, ensure_checkpointer

async def run_agent(message: str, thread_id: int, user):

    thread_id = int(thread_id)
    await ensure_checkpointer()
    tools = await get_user_tools(user)
    available_domains = list(tools.keys())
   
    app = create_graph(tools)

    config = {
        "configurable": {
            "thread_id": str(thread_id),
        },
        "recursion_limit": 10,
    }

    result = await app.ainvoke(
        {
            "messages": [
                HumanMessage(content=message)
            ],
            "available_domains": available_domains,
            "thread_id": thread_id,
            "user_id": int(user.id),
        },
        config=config,
    )

    state = await app.aget_state(config)

    if state.interrupts:
        return {
            "status": "approval_required",
            "thread_id": thread_id,
            "interrupt": state.interrupts[0].value,
        }
    return {
        "status": "completed",
        "result": result,
    }