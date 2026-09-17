from concurrent.futures import thread
from agent.runner import run_agent
from streamlit import json, user
from agent.models import Message, Thread
from django.http import StreamingHttpResponse
import re
from agent.utils import extract_text_content, extract_title_from_text


def stream_agent_response(formatted_message, thread, user):
    response = StreamingHttpResponse(
        event_stream(formatted_message, thread, user),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response

async def event_stream(formatted_message, thread, user):
       
    local_save  = []

    try:
        async for chunk in run_agent(message=formatted_message, thread_id=thread.id, user=user):
            chunk_type = chunk["type"]

            if chunk_type == "status":
                # Pass through status events from the backend with their message
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
            raw_content = extract_text_content(messages[-1].content) if messages else ""
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
                final_content = "I processed your request. Let me know if there’s anything else you’d like to do!"
                
            await Message.objects.acreate(
                thread=thread,
                role="agent",
                content=final_content,
                metrics=chunk.get("metrics") or {},
            )
            yield f"data: {json.dumps({'type': 'completed', 'response': final_content, 'thread_id': thread.id, 'thread_name': thread.name, 'metrics': chunk.get('metrics', {})})}\n\n"
            return
    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"


