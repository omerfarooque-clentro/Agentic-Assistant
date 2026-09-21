"""Async runtime orchestration for LangGraph execution and token/latency streaming."""

from __future__ import annotations

import logging

import asyncio
import re
import time
from langchain_core.messages import AIMessage, HumanMessage

from agent.constants import AGENT_NODES
from agent.tools import get_user_tools
from agent.graph import create_graph, ensure_checkpointer
from agent.status import NODE_STATUS_MAP
from agent.metrics import aggregate_turn_metrics
from agent.llm import StreamTitleFilter, extract_title_from_text, generate_title_from_context, is_substantive_for_title
from agent.utils import (
    extract_stream_chunk_token,
    extract_turn_response,
    is_intermediate_plan_step,
    synthesize_response_from_actions,
)
from conversations.models import Message, Thread

logger = logging.getLogger(__name__)


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
            "recursion_limit": 25,
        }

        input_message = {
            "messages": [HumanMessage(content=message)],
            "call_metrics": [{"__reset__": True}],
            "completed_actions": [{"__reset__": True}],
            "available_domains": set(available_domains),
            "thread_id": str(thread_id),
            "user_id": str(user.id),
            "needs_title": needs_title,
        }

        title_filter = StreamTitleFilter()
        current_plan = None

        async for event in app.astream_events(input_message, config=config, version="v2"):
            event_type = event["event"]
            metadata = event.get("metadata", {})
            node_name = metadata.get("langgraph_node")

            if event_type == "on_chain_end":
                out_data = event.get("data", {}).get("output")
                if isinstance(out_data, dict) and "plan" in out_data:
                    current_plan = out_data["plan"]

            status_info = NODE_STATUS_MAP.get(node_name, {})
            if status_info and event_type == "on_chat_model_start":
                yield {
                    "type": "status",
                    "node": node_name,
                    **status_info,
                }

            if event_type == "on_tool_start":
                yield {
                    "type": "tool_start",
                    "tool": event.get("name"),
                }

            if event_type == "on_tool_end":
                yield {
                    "type": "tool_end",
                    "tool": event.get("name"),
                    "output": event.get("data", {}).get("output"),
                }

            if node_name not in AGENT_NODES or event_type != "on_chat_model_stream":
                continue

            # Suppress conversational tokens during intermediate steps of multi-step plans
            if is_intermediate_plan_step(current_plan):
                continue

            user_token = extract_stream_chunk_token(event["data"]["chunk"], title_filter)
            if user_token:
                yield {
                    "type": "token",
                    "token": user_token,
                }

        rem_token, extracted_title = title_filter.finalize()
        if rem_token and not is_intermediate_plan_step(current_plan):
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
        last_content = extract_turn_response(messages)

        # Fallback if assistant finished with empty text content or completed intermediate steps
        if not str(last_content).strip():
            last_content = synthesize_response_from_actions(
                final_state,
                messages=messages,
                default="I processed your request. Let me know if you need anything else!",
            )
            if messages:
                messages[-1] = AIMessage(content=last_content)
                final_state["messages"] = messages
            yield {
                "type": "token",
                "token": last_content,
            }

        msg_count = await Message.objects.filter(thread_id=thread_id).acount()
        retitled = bool((final_state.get("details") or {}).get("retitled", False))
        allow_retitling = needs_title or (
            msg_count >= 10
            and not retitled
            and is_substantive_for_title(user_prompt=message, assistant_response=str(last_content))
        )

        if not extracted_title and allow_retitling and last_content:
            _, extracted_title = extract_title_from_text(str(last_content))

        if not extracted_title and allow_retitling and is_substantive_for_title(user_prompt=message, assistant_response=str(last_content)):
            extracted_title = await generate_title_from_context(user_prompt=message, assistant_response=str(last_content))

        thread_name = thread_obj.name if thread_obj else ""
        if extracted_title and thread_obj and (thread_obj.name in ("New Thread", "New Conversation", "", None) or allow_retitling):
            thread_obj.name = extracted_title
            await thread_obj.asave(update_fields=["name", "updated_at"])
            thread_name = extracted_title

            if not needs_title:
                updated_details = dict(final_state.get("details") or {})
                updated_details["retitled"] = True
                try:
                    await app.aupdate_state(config, {"details": updated_details})
                except Exception as e:
                    logger.debug("Failed to persist retitled state: %s", e)

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
        logger.debug("run_agent: exiting for thread %s with reason: %s", thread_id, exit_reason)