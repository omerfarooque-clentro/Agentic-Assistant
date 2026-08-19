from langchain_core.messages import HumanMessage
from .tools import get_user_tools
from .graph import create_graph, ensure_checkpointer


async def run_agent(message: str, thread_id: str, user):

    print(f"\n=== [RUNNER] START thread={thread_id} user={getattr(user, 'id', user)} ===")
    print(f"[RUNNER] Incoming message: {message!r}")

    await ensure_checkpointer()
    print("[RUNNER] Checkpointer ready")

    tools = await get_user_tools(user)
    available_domains = list(tools.keys())
    print(f"[RUNNER] Available domains for user: {available_domains}")

    app = create_graph(tools)
    print("[RUNNER] Graph compiled, invoking...")

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
            ],
            "available_domains": available_domains
        },
        config=config,
    )

    state = await app.aget_state(config)

    if state.interrupts:
        print(f"[RUNNER] Interrupt raised: {state.interrupts[0].value}")
        print(f"=== [RUNNER] END thread={thread_id} status=approval_required ===\n")
        return {
            "status": "approval_required",
            "thread_id": thread_id,
            "interrupt": state.interrupts[0].value,
        }

    print(f"=== [RUNNER] END thread={thread_id} status=completed ===\n")

    return {
        "status": "completed",
        "result": result,
    }