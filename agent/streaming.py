"""SSE streaming helpers for agent chat and tool-approval flows."""

import json
import logging
import re
import time

from django.http import StreamingHttpResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from agent.cards import build_result_card, render_cards
from agent.constants import AGENT_NODES
from agent.graph.builder import create_graph, ensure_checkpointer
from agent.llm import StreamTitleFilter
from agent.llm.titles import extract_title_from_text
from agent.metrics import aggregate_turn_metrics
from agent.runner import run_agent
from agent.status import NODE_STATUS_MAP
from agent.tools.service import get_user_tools
from agent.utils import (
    extract_stream_chunk_token,
    extract_text_content,
    extract_turn_response,
    is_intermediate_plan_step,
    synthesize_response_from_actions,
)
from conversations.models import Message

logger = logging.getLogger(__name__)


def stream_agent_response(formatted_message, thread, user):
    """Wrap the async event generator in an SSE StreamingHttpResponse."""
    response = StreamingHttpResponse(
        event_stream(formatted_message, thread, user),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


def stream_approval_response(approval, thread, user, config, approved, modified_args, instruction):
    """Wrap the async approval generator in an SSE StreamingHttpResponse."""
    response = StreamingHttpResponse(
        approval_event_stream(approval, thread, user, config, approved, modified_args, instruction),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


async def event_stream(formatted_message, thread, user):
    """Async generator that streams agent tokens, status updates, and results as SSE events."""
    local_save = []

    try:
        async for chunk in run_agent(message=formatted_message, thread_id=thread.id, user=user):
            chunk_type = chunk["type"]

            if chunk_type == "status":
                yield f"data: {json.dumps({'type': 'status', 'status': chunk.get('status'), 'message': chunk.get('message')})}\n\n"
                continue
            if chunk_type == "token":
                local_save.append(chunk['token'])
                yield f"data: {json.dumps({'type': 'token', 'token': chunk['token']})}\n\n"
                continue
            if chunk_type == "approval_required":
                yield f"data: {json.dumps({'type': 'approval_required', 'approval': chunk['interrupt'], 'thread_id': thread.id})}\n\n"
                return
            if chunk_type == "error":
                err_msg = chunk.get("message") or "Unknown error occurred"
                accumulated = "".join(local_save).strip()
                db_content = f"{accumulated}\n\n[Error: {err_msg}]" if accumulated else f"[Error: {err_msg}]"
                await Message.objects.acreate(thread=thread, role="agent", content=db_content)
                await thread.asave(update_fields=["updated_at"])
                yield f"data: {json.dumps({'type': 'error', 'message': err_msg})}\n\n"
                return
            if chunk_type == "thread_name":
                yield f"data: {json.dumps({'type': 'thread_name', 'thread_id': chunk['thread_id'], 'thread_name': chunk['thread_name']})}\n\n"
                continue
            if chunk_type != "completed":
                continue

            messages = chunk["result"].get("messages", [])
            raw_content = extract_turn_response(messages)
            final_content, suggested_title = extract_title_from_text(raw_content)

            resolved_title = chunk.get("thread_name") or suggested_title

            if resolved_title and (thread.name in ("New Thread", "New Conversation", "", None) or chunk.get("thread_name")):
                thread.name = resolved_title
                await thread.asave(update_fields=["name", "updated_at"])
            else:
                await thread.asave(update_fields=["updated_at"])

            if resolved_title:
                final_content = re.sub(
                    rf"^(?:#*\s*)?{re.escape(resolved_title)}[:\s]*\n+",
                    "",
                    final_content,
                    flags=re.IGNORECASE,
                ).strip()

            if not final_content.strip():
                final_content = synthesize_response_from_actions(chunk.get("result", {}))

            if final_content:
                final_content = re.sub(r"【[^】]*】", "", final_content).strip()

            rc = build_result_card(messages)
            if rc:
                yield f"data: {json.dumps({'type': 'result_card', 'card': rc})}\n\n"

            await Message.objects.acreate(
                thread=thread,
                role="agent",
                content=final_content,
                metrics=chunk.get("metrics") or {},
                cards=[rc] if rc else None,
            )
            yield f"data: {json.dumps({'type': 'completed', 'response': final_content, 'thread_id': thread.id, 'thread_name': thread.name, 'metrics': chunk.get('metrics', {})})}\n\n"
            return
    except Exception as e:
        logger.exception("event_stream error for thread %s", thread.id)
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

async def approval_event_stream(approval, thread, user, config, approved, modified_args, instruction):
    """Async generator that resumes a paused graph after user approval/rejection and streams results."""
    start_time = time.perf_counter()
    title_filter = StreamTitleFilter()

    await ensure_checkpointer()
    tools = await get_user_tools(user)
    app = create_graph(tools)

    try:
        resume_input = Command(
            resume={
                "approved": approved,
                "modified_args": modified_args,
                "instruction": instruction,
            }
        )

        current_plan = approval.get("plan") if isinstance(approval, dict) else None

        async for event in app.astream_events(resume_input, config=config, version="v2"):
            event_type = event["event"]
            metadata = event.get("metadata", {})
            node_name = metadata.get("langgraph_node")

            if event_type == "on_chain_end":
                out_data = event.get("data", {}).get("output")
                if isinstance(out_data, dict) and "plan" in out_data:
                    current_plan = out_data["plan"]

            # Emit status events for known nodes (both LLM calls and pure Python steps like advance_plan)
            status_info = NODE_STATUS_MAP.get(node_name)
            if status_info:
                is_llm_start = (event_type == "on_chat_model_start")
                is_node_start = (event_type == "on_chain_start" and event.get("name") == node_name)

                if is_llm_start or is_node_start:
                    yield f"data: {json.dumps({'type': 'status', 'status': status_info.get('status'), 'message': status_info.get('message'), 'node': node_name})}\n\n"

            if node_name not in AGENT_NODES or event_type != "on_chat_model_stream":
                continue

            # Suppress conversational tokens during intermediate steps of multi-step plans
            if is_intermediate_plan_step(current_plan):
                continue

            user_token = extract_stream_chunk_token(event["data"]["chunk"], title_filter)
            if user_token:
                yield f"data: {json.dumps({'type': 'token', 'token': user_token})}\n\n"

        # Flush remaining title filter buffer
        rem_token, extracted_title = title_filter.finalize()
        if rem_token and not is_intermediate_plan_step(current_plan):
            yield f"data: {json.dumps({'type': 'token', 'token': rem_token})}\n\n"

        # Get final state after graph execution
        state = await app.aget_state(config)
        final_values = state.values
        messages = list(final_values.get("messages", []))
        raw_message = extract_turn_response(messages)
        final_text, suggested_title = extract_title_from_text(raw_message)

        if not extracted_title and suggested_title:
            extracted_title = suggested_title

        approval_metrics = aggregate_turn_metrics(
            call_metrics=final_values.get("call_metrics", []),
            start_time=start_time,
            messages=messages,
        )

        card_record = render_cards(
            approval=approval,
            messages=messages,
            modified_args=modified_args,
            approved=approved,
            instruction=instruction,
        )
        card_domain = card_record["domain"]

        # Handle thread naming
        if extracted_title and thread.name in ("New Thread", "New Conversation", "", None):
            thread.name = extracted_title
            await thread.asave(update_fields=["name", "updated_at"])
            yield f"data: {json.dumps({'type': 'thread_name', 'thread_id': thread.id, 'thread_name': extracted_title})}\n\n"
        else:
            await thread.asave(update_fields=["updated_at"])

        # Check if graph hit another interrupt (next approval step)
        if state.interrupts:
            interrupt_content = (
                (final_text.strip() if not is_intermediate_plan_step(final_values.get("plan")) else "")
                or (f"{card_domain.title()} action completed." if approved else "Action cancelled.")
            )
            await Message.objects.acreate(
                thread=thread,
                role="agent",
                content=interrupt_content,
                metrics=approval_metrics,
                cards=[card_record],
            )

            yield f"data: {json.dumps({'type': 'approval_required', 'approval': state.interrupts[0].value, 'result': final_text if (final_text.strip() and not is_intermediate_plan_step(final_values.get('plan'))) else None, 'thread_id': int(thread.id), 'thread_name': thread.name, 'metrics': approval_metrics, 'card_record': card_record})}\n\n"
            return

        # No more interrupts — graph completed
        if not final_text.strip():
            tool_err = None
            for msg in reversed(messages):
                if isinstance(msg, ToolMessage):
                    if getattr(msg, "status", None) == "error" or "Error calling tool" in str(msg.content):
                        tool_err = str(msg.content)
                    break
            if tool_err:
                final_text = f"Action failed: {tool_err}"
            else:
                final_text = synthesize_response_from_actions(
                    final_values,
                    default="Action executed successfully." if approved else "Action cancelled.",
                )

        if final_text:
            final_text = re.sub(r"【[^】]*】", "", final_text).strip()

        rc = build_result_card(messages)
        if rc:
            yield f"data: {json.dumps({'type': 'result_card', 'card': rc})}\n\n"

        await Message.objects.acreate(
            thread=thread,
            role="agent",
            content=final_text,
            metrics=approval_metrics,
            cards=[card_record, *([rc] if rc else [])],
        )

        yield f"data: {json.dumps({'type': 'completed', 'result': final_text, 'thread_id': int(thread.id), 'thread_name': thread.name, 'metrics': approval_metrics, 'card_record': card_record})}\n\n"

    except Exception as e:
        logger.exception("approval_event_stream error for thread %s", thread.id)
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"