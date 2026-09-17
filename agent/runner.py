"""Async runtime orchestration for LangGraph execution and token/latency streaming."""

from __future__ import annotations

import asyncio
import re
import time
from langchain_core.messages import AIMessage, HumanMessage

from agent.tools import get_user_tools
from agent.graph import create_graph, ensure_checkpointer
from agent.status import NODE_STATUS_MAP
from agent.metrics import aggregate_turn_metrics
from agent.llm import StreamTitleFilter, extract_title_from_text, generate_title_from_context, is_substantive_for_title
from conversations.models import Thread


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
    start_time = time.perf_counter()

    try:
        await ensure_checkpointer()
        tools = await get_user_tools(user)
        available_domains = list(tools.keys())

        thread_obj = await Thread.objects.filter(id=thread_id).afirst()
        needs_title = bool(thread_obj and (thread_obj.name in ("New Thread", "New Conversation", "") or not thread_obj.name))

        app = create_graph(tools)

        config = {
            "configurable": {
                "thread_id": str(thread_id),
            },
            "recursion_limit": 10,
        }

        input_message = {
            "messages": [HumanMessage(content=message)],
            "call_metrics": [{"__reset__": True}],
            "available_domains": set(available_domains),
            "thread_id": str(thread_id),
            "user_id": str(user.id),
            "needs_title": needs_title,
        }

        title_filter = StreamTitleFilter()

        async for event in app.astream_events(input_message, config=config, version="v2"):
            event_type = event["event"]
            metadata = event.get("metadata", {})
            node_name = metadata.get("langgraph_node")

            status_info = NODE_STATUS_MAP.get(node_name, {})

            if status_info and event_type == "on_chat_model_start":
                yield {
                    "type": "status",
                    "node": node_name,
                    **status_info,
                }

            if node_name not in AGENT_NODES:
                continue

            if event_type != "on_chat_model_stream":
                continue

            chunk = event["data"]["chunk"]
            chunk_content = getattr(chunk, "content", None)
            if not chunk_content or not isinstance(chunk_content, str):
                continue

            user_token = title_filter.process_chunk(chunk_content)
            if user_token:
                yield {
                    "type": "token",
                    "token": user_token,
                }

        rem_token, extracted_title = title_filter.finalize()
        if rem_token:
            yield {
                "type": "token",
                "token": rem_token,
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
        messages = list(final_state.get("messages", []))

        # Fallback inspection or generation of title if needed
        last_content = getattr(messages[-1], "content", "") if messages else ""
        if isinstance(last_content, list):
            last_content = " ".join(str(b.get("text") or "") if isinstance(b, dict) else str(b) for b in last_content)

        # Fallback if assistant finished with empty text content
        if not str(last_content).strip():
            tool_calls = getattr(messages[-1], "tool_calls", None) if messages else None
            if tool_calls:
                tool_names = ", ".join(t.get("name", "tool") for t in tool_calls)
                last_content = f"Operation completed successfully ({tool_names})."
            else:
                last_content = "I processed your request. Let me know if you need anything else!"
            if messages:
                messages[-1] = AIMessage(content=last_content)
                final_state["messages"] = messages
            yield {
                "type": "token",
                "token": last_content,
            }

        if not extracted_title and needs_title and last_content:
            _, extracted_title = extract_title_from_text(str(last_content))

        if not extracted_title and needs_title and is_substantive_for_title(user_prompt=message, assistant_response=str(last_content)):
            extracted_title = await generate_title_from_context(user_prompt=message, assistant_response=str(last_content))

        thread_name = thread_obj.name if thread_obj else ""
        if extracted_title and thread_obj and thread_obj.name in ("New Thread", "New Conversation", "", None):
            thread_obj.name = extracted_title
            await thread_obj.asave(update_fields=["name", "updated_at"])
            thread_name = extracted_title
            yield {
                "type": "thread_name",
                "thread_id": thread_id,
                "thread_name": extracted_title,
            }

        metrics = aggregate_turn_metrics(
            call_metrics=final_state.get("call_metrics", []),
            start_time=start_time,
            messages=messages,
        )

        yield {
            "type": "completed",
            "thread_id": thread_id,
            "thread_name": thread_name,
            "result": final_state,
            "metrics": metrics,
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
            "type": "error",
            "message": str(e),
        }
        exit_reason = "error yielded"
        return
    finally:
        print(f"run_agent: exiting for thread {thread_id} with reason: {exit_reason}")