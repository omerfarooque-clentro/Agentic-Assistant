import asyncio

from langchain_core.messages import HumanMessage
from agent.tools import get_user_tools
from agent.graph import create_graph, ensure_checkpointer
from agent.status import NODE_STATUS_MAP


AGENT_NODES = {
    "general_agent",
    "email_agent",
    "calendar_agent",
    "docs_agent",
    "sheets_agent",
    "slack_agent",
    "research_agent",
}




async def run_agent(message: str, thread_id: int, user):
    thread_id = int(thread_id)
    exit_reason = "running"

    try:
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

        input_message = {
            "messages": [HumanMessage(content=message)],
            "available_domains": set(available_domains),
            "thread_id": str(thread_id),
            "user_id": str(user.id),
        }

        async for event in app.astream_events(input_message, config=config, version="v2"):
            event_type = event["event"]

            metadata = event.get("metadata", {})
            node_name = metadata.get("langgraph_node")
            
            status_info = NODE_STATUS_MAP.get(node_name, {})

            if status_info and event_type == "on_chat_model_start":
                # print(f"run_agent: yielding chunk type=status for thread {thread_id} from node {node_name}: status={status}, message={message}")
                yield {
                    "type": "status",
                    **status_info,
                }

            if node_name not in AGENT_NODES:
                        continue
        
            if event_type != "on_chat_model_stream":
                                continue
                
            chunk = event["data"]["chunk"]

            if not chunk.content:
                continue
 
            yield {
                "type" : "token",   
                "token" : chunk.content,
            }

       
        state = await app.aget_state(config)

        if state.interrupts:

            yield {
                "type": "approval_required",
                "thread_id": thread_id,
                "interrupt": state.interrupts[0].value,
            }
            exit_reason = "returned after approval_required"
            return

        final_state = state.values

        yield {
            "type": "completed",
            "thread_id": thread_id,
            "result": final_state,
        }
       
    except asyncio.CancelledError:
        exit_reason = "cancelled"
       
        raise
    except GeneratorExit:
        exit_reason = "closed early"
       
        raise
    except Exception as e:
        exit_reason = f"exception: {type(e).__name__}"
        
        yield {
            "type" : "error",
            "message" : str(e),
        }
        exit_reason = "error yielded"
        return
    finally:
    