from langchain_core.messages import HumanMessage
from agent.graph import app


def run_agent(message: str, thread_id: str):
    config = {
        "configurable": {
            "thread_id": thread_id,
        },
        "recursion_limit": 10
    }

    result = app.invoke(
        {
            "messages": [
                HumanMessage(content=message)
            ]
        },
        config=config,
    )

    state = app.get_state(config)

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