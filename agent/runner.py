from langchain_core.messages import HumanMessage
from .tools import get_user_tools
from .graph import create_graph, ensure_checkpointer


async def run_agent(message: str, thread_id: str, user):

    await ensure_checkpointer()
    tools = await get_user_tools(user)
    
    app = create_graph(tools)

    config = {
        "configurable": {
            "thread_id": thread_id,
        },
        "recursion_limit": 10,
    }

    result = await app.ainvoke(
        {
            "messages": [
                HumanMessage(content=message)
            ]
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