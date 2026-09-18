"""SSE streaming helpers for agent chat and tool-approval flows."""

import json
import logging
import re
import time

from django.http import StreamingHttpResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from agent.cards import render_cards
from agent.constants import AGENT_NODES
from agent.graph.builder import create_graph, ensure_checkpointer
from agent.llm import StreamTitleFilter
from agent.llm.titles import extract_title_from_text
from agent.metrics import aggregate_turn_metrics
from agent.runner import run_agent
from agent.status import NODE_STATUS_MAP
from agent.tools.service import get_user_tools
from agent.utils import extract_text_content
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
            latest_human_idx = max(
                (i for i, m in enumerate(messages) if isinstance(m, HumanMessage)),
                default=-1,
            )
            turn_ai_messages = [
                m for m in messages[latest_human_idx + 1:]
                if isinstance(m, AIMessage) and getattr(m, "content", None)
            ]
            if len(turn_ai_messages) > 1:
                raw_content = "\n\n".join(
                    extract_text_content(m.content) for m in turn_ai_messages if extract_text_content(m.content).strip()
                )
            elif turn_ai_messages:
                raw_content = extract_text_content(turn_ai_messages[-1].content)
            elif messages:
                raw_content = extract_text_content(messages[-1].content)
            else:
                raw_content = ""
            final_content, suggested_title = extract_title_from_text(raw_content)

            resolved_title = chunk.get("thread_name") or suggested_title

            if resolved_title and thread.name in ("New Thread", "New Conversation", "", None):
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
                completed_actions = [
                    a for a in (chunk.get("result", {}).get("completed_actions") or [])
                    if isinstance(a, dict) and a.get("summary") and not a.get("__reset__")
                ]
                if completed_actions:
                    final_content = "\n\n".join(
                        f"**{a.get('domain', '').capitalize()}:** {a.get('summary', '').strip()}"
                        for a in completed_actions
                    )
                else:
                    final_content = "I processed your request. Let me know if there's anything else you'd like to do!"

            await Message.objects.acreate(
                thread=thread,
                role="agent",
                content=final_content,
                metrics=chunk.get("metrics") or {},
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

        async for event in app.astream_events(resume_input, config=config, version="v2"):
            event_type = event["event"]
            metadata = event.get("metadata", {})
            node_name = metadata.get("langgraph_node")

            # Emit status events for known nodes (both LLM calls and pure Python steps like advance_plan)
            status_info = NODE_STATUS_MAP.get(node_name)
            if status_info:
                is_llm_start = (event_type == "on_chat_model_start")
                is_node_start = (event_type == "on_chain_start" and event.get("name") == node_name)

                if is_llm_start or is_node_start:
                    yield f"data: {json.dumps({'type': 'status', 'status': status_info.get('status'), 'message': status_info.get('message'), 'node': node_name})}\n\n"

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
                yield f"data: {json.dumps({'type': 'token', 'token': user_token})}\n\n"

        # Flush remaining title filter buffer
        rem_token, extracted_title = title_filter.finalize()
        if rem_token:
            yield f"data: {json.dumps({'type': 'token', 'token': rem_token})}\n\n"

        # Get final state after graph execution
        state = await app.aget_state(config)
        final_values = state.values
        messages = list(final_values.get("messages", []))

        latest_human_idx = max(
            (i for i, m in enumerate(messages) if isinstance(m, HumanMessage)),
            default=-1,
        )
        turn_ai_messages = [
            m for m in messages[latest_human_idx + 1:]
            if isinstance(m, AIMessage) and getattr(m, "content", None)
        ]
        if len(turn_ai_messages) > 1:
            raw_message = "\n\n".join(
                extract_text_content(m.content) for m in turn_ai_messages if extract_text_content(m.content).strip()
            )
        elif turn_ai_messages:
            raw_message = extract_text_content(turn_ai_messages[-1].content)
        elif messages:
            raw_message = extract_text_content(messages[-1].content)
        else:
            raw_message = ""

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
            interrupt_content = final_text.strip() or (f"{card_domain.title()} action completed." if approved else "Action cancelled.")
            await Message.objects.acreate(
                thread=thread,
                role="agent",
                content=interrupt_content,
                metrics=approval_metrics,
                cards=[card_record],
            )

            yield f"data: {json.dumps({'type': 'approval_required', 'approval': state.interrupts[0].value, 'result': final_text if final_text.strip() else None, 'thread_id': int(thread.id), 'thread_name': thread.name, 'metrics': approval_metrics, 'card_record': card_record})}\n\n"
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
                completed_actions = [
                    a for a in (final_values.get("completed_actions") or [])
                    if isinstance(a, dict) and a.get("summary") and not a.get("__reset__")
                ]
                if completed_actions:
                    final_text = "\n\n".join(
                        f"**{a.get('domain', '').capitalize()}:** {a.get('summary', '').strip()}"
                        for a in completed_actions
                    )
                else:
                    final_text = "Action executed successfully." if approved else "Action cancelled."

        await Message.objects.acreate(
            thread=thread,
            role="agent",
            content=final_text,
            metrics=approval_metrics,
            cards=[card_record],
        )

        yield f"data: {json.dumps({'type': 'completed', 'result': final_text, 'thread_id': int(thread.id), 'thread_name': thread.name, 'metrics': approval_metrics, 'card_record': card_record})}\n\n"

    except Exception as e:
        logger.exception("approval_event_stream error for thread %s", thread.id)
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"