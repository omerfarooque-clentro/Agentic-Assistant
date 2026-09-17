from datetime import datetime
import json
import re
import time
import zoneinfo

from agent.streaming import event_stream, stream_agent_response
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from rest_framework import generics
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.utils import (
    generate_recovery_otp,
    hash_recovery_otp,
    verify_recovery_otp,
)
from agent.graph import create_graph, ensure_checkpointer
from agent.graph.approval import DOMAIN_BY_TOOL_NAME
from agent.llm import extract_title_from_text
from agent.metrics import aggregate_turn_metrics
from agent.models import MCPIntegration
from agent.runner import run_agent
from agent.tools import get_user_tools
from conversations.models import Approval, Message, Thread
from core.serializers import (
    AgentChatSerializer,
    ApproveEmailSerializer,
    ForgotPasswordSerializer,
    InAppResetPasswordSerializer,
    LoginSerializer,
    OTPGenerateSerializer,
    RegisterationSerializer,
    ResetPasswordSerializer,
    VerifyOTPSerializer,
)

User = get_user_model()


def format_user_agent_message(message: str, username: str, timezone_str: str | None = None) -> str:
    """Format user message with dynamic real-time timestamp and resolved timezone context."""
    tz = None
    tz_name = (timezone_str or "").strip()
    if tz_name:
        try:
            tz = zoneinfo.ZoneInfo(tz_name)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError, KeyError):
            tz = None
    if tz is None:
        tz_name = "UTC"
        tz = zoneinfo.ZoneInfo("UTC")

    now = datetime.now(tz)
    return f"Date: {now.strftime('%Y-%m-%d %H:%M:%S')} (Timezone: {tz_name}), {username}: {message}"

def extract_text_content(message_content):
    """Extract string content regardless of provider format."""
    if isinstance(message_content, str):
        return message_content
    elif isinstance(message_content, list):
        # Extract text from block lists returned by Gemini/Claude/LangChain
        text_parts = []
        for block in message_content:
            if isinstance(block, dict):
                text_parts.append(str(block.get("text") or block.get("content") or ""))
            else:
                text_parts.append(str(block))
        return " ".join(text_parts)


def render_cards(approval, messages, modified_args=None, approved=True, instruction=None):
    card_domain = getattr(approval, "domain", "") or ""
    if not card_domain or card_domain == "general":
        for m in reversed(messages):
            if isinstance(m, ToolMessage):
                card_domain = DOMAIN_BY_TOOL_NAME.get(getattr(m, "name", ""), "")
                if card_domain:
                    break
            if hasattr(m, "tool_calls") and m.tool_calls:
                for tc in reversed(m.tool_calls):
                    tc_name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                    card_domain = DOMAIN_BY_TOOL_NAME.get(tc_name, "")
                    if card_domain:
                        break
                if card_domain:
                    break
    if not card_domain:
        card_domain = "general"

    tool_args = dict(modified_args) if modified_args else {}
    if not tool_args:
        for m in reversed(messages):
            if hasattr(m, "tool_calls") and m.tool_calls:
                for tc in reversed(m.tool_calls):
                    tc_name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                    if DOMAIN_BY_TOOL_NAME.get(tc_name) == card_domain:
                        tool_args = tc.get("args") or {} if isinstance(tc, dict) else getattr(tc, "args", {})
                        break
                if tool_args:
                    break

    return {
        "domain": card_domain,
        "approved": approved,
        "status": "completed" if approved else ("revised" if instruction else "cancelled"),
        "heading": f"{card_domain.title()} action",
        "args": tool_args,
    }


@sync_to_async
def authenticate_api_request(request):
    result = JWTAuthentication().authenticate(Request(request))
    return result[0] if result else None


def request_json(request):
    try:
        return json.loads(request.body or b"{}")
    except (TypeError, ValueError):
        return None


async def authenticated_user(request):
    user = await authenticate_api_request(request)
    if user is None:
        return None, JsonResponse({"detail": "Authentication credentials were not provided."}, status=401)
    return user, None


class RegistrationView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = RegisterationSerializer
    permission_classes = [AllowAny]


class LoginView(generics.GenericAPIView):
    serializer_class = LoginSerializer
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        return redirect('/signin/')

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user =  serializer.validated_data["user"]
        refersh = RefreshToken.for_user(user)

        MCPIntegration.objects.get_or_create(user=user, service="tavily", defaults={"enabled": True})
        
        return Response({
            "access" : str(refersh.access_token),
            "refresh" : str(refersh),
            "refersh" : str(refersh),
            "user_id": user.id,
            "username": user.username,
            "email": user.email,
        })


@csrf_exempt
async def otp_generate(request):
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed."}, status=405)

    user, error = await authenticated_user(request)
    if error:
        return error

    payload = request_json(request) or {}
    serializer = OTPGenerateSerializer(data=payload, context={"user": user})
    if not serializer.is_valid():
        return JsonResponse(serializer.errors, status=400)

    # Invalidate old OTP and generate a NEW recovery OTP
    new_otp = generate_recovery_otp()
    user.otp_secret = hash_recovery_otp(new_otp)
    await user.asave(update_fields=["otp_secret"])

    return JsonResponse({
        "detail": "New recovery code generated successfully.",
        "recovery_code": new_otp,
        "username": user.username,
        "email": user.email,
        "message": "Old recovery code is now invalid. Your new recovery code is active.",
    }, status=200)


@csrf_exempt
async def in_app_reset_password_view(request):
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed."}, status=405)

    user, error = await authenticated_user(request)
    if error:
        return error

    payload = request_json(request) or {}
    serializer = InAppResetPasswordSerializer(data=payload, context={"user": user})
    if not serializer.is_valid():
        return JsonResponse(serializer.errors, status=400)

    new_password = serializer.validated_data["new_password"]
    used_otp = serializer.validated_data.get("used_otp", False)

    user.set_password(new_password)

    if used_otp:
        # If user reset password using their recovery OTP, rotate to a new recovery OTP
        new_otp = generate_recovery_otp()
        user.otp_secret = hash_recovery_otp(new_otp)
        await user.asave(update_fields=["password", "otp_secret"])
        return JsonResponse({
            "detail": "Password updated successfully. A new recovery code has been generated.",
            "recovery_code": new_otp,
            "rotated_otp": True,
        }, status=200)
    else:
        await user.asave(update_fields=["password"])
        return JsonResponse({
            "detail": "Password updated successfully.",
            "rotated_otp": False,
        }, status=200)



@csrf_exempt
def forgot_password_view(request):
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed."}, status=405)

    payload = request_json(request) if request.body else request.POST.dict()
    serializer = ForgotPasswordSerializer(data=payload)
    if not serializer.is_valid():
        return JsonResponse(serializer.errors, status=400)

    email = serializer.validated_data["email"]
    return JsonResponse({
        "detail": "Email verified. Please enter your recovery OTP/credential.",
        "email": email,
    }, status=200)


@csrf_exempt
def verify_otp_view(request):
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed."}, status=405)

    payload = request_json(request) if request.body else request.POST.dict()
    serializer = VerifyOTPSerializer(data=payload)
    if not serializer.is_valid():
        return JsonResponse(serializer.errors, status=400)

    email = serializer.validated_data["email"]
    return JsonResponse({
        "detail": "Recovery OTP verified successfully.",
        "email": email,
    }, status=200)


@csrf_exempt
def reset_password_view(request):
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed."}, status=405)

    payload = request_json(request) if request.body else request.POST.dict()
    serializer = ResetPasswordSerializer(data=payload)
    if not serializer.is_valid():
        return JsonResponse(serializer.errors, status=400)

    user = serializer.validated_data["user"]
    new_password = serializer.validated_data["new_password"]

    # 1. Update password
    user.set_password(new_password)

    # 2. Force generation of a NEW recovery OTP and invalidate the previous one
    new_recovery_otp = generate_recovery_otp()
    user.otp_secret = hash_recovery_otp(new_recovery_otp)
    user.save(update_fields=["password", "otp_secret"])

    return JsonResponse({
        "detail": "Password reset successfully. A new recovery OTP has been generated.",
        "recovery_code": new_recovery_otp,
        "email": user.email,
        "username": user.username,
    }, status=200)



@csrf_exempt
async def new_chat_view(request):
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed."}, status=405)

    user, error = await authenticated_user(request)
    if error:
        return error

    serializer = AgentChatSerializer(data=request_json(request))
    if not serializer.is_valid():
        return JsonResponse(serializer.errors, status=400)

    message = serializer.validated_data["message"]
    user_tz = serializer.validated_data.get("timezone")
    formatted_message = format_user_agent_message(message=message, username=user.username, timezone_str=user_tz)
    thread = await Thread.objects.acreate(user=user, name="New Thread")
    await Message.objects.acreate(thread=thread, role="user", content=message)
    await thread.asave(update_fields=["updated_at"]) 
    
    return stream_agent_response(formatted_message, thread, user)


@csrf_exempt
async def agent_chat_view(request, thread_id):
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed."}, status=405)

    user, error = await authenticated_user(request)
    if error:
        return error

    serializer = AgentChatSerializer(data=request_json(request))
    if not serializer.is_valid():
        return JsonResponse(serializer.errors, status=400)

    message = serializer.validated_data["message"]
    user_tz = serializer.validated_data.get("timezone")
    formatted_message = format_user_agent_message(message=message, username=user.username, timezone_str=user_tz)
    try:
        thread = await Thread.objects.aget(id=thread_id, user=user)
    except Thread.DoesNotExist:
        return JsonResponse({"detail": "Thread not found."}, status=404)

    await Message.objects.acreate(thread=thread, role="user", content=message)
    await thread.asave(update_fields=["updated_at"]) 
    
    return stream_agent_response(formatted_message, thread, user)


@csrf_exempt
async def tool_approval_view(request, thread_id):
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed."}, status=405)

    user, error = await authenticated_user(request)
    if error:
        return error

    serializer = ApproveEmailSerializer(data=request_json(request))
    if not serializer.is_valid():
        return JsonResponse(serializer.errors, status=400)
    approved = serializer.validated_data["approved"]
    modified_args = serializer.validated_data.get("modified_args") or {}
    instruction = serializer.validated_data.get("instruction") or ""

    try:
        thread = await Thread.objects.aget(id=thread_id, user=user)
    except Thread.DoesNotExist:
        return JsonResponse({"detail": "Thread not found."}, status=404)

    message = await Message.objects.filter(thread=thread, role="user").order_by("-created_at").afirst()
    if not message:
        return JsonResponse({"detail": "No user message found for this thread."}, status=404)

    if instruction:
        message = await Message.objects.acreate(thread=thread, role="user", content=instruction)
        approval = await Approval.objects.acreate(message=message, thread=thread, approved=False)
    else:
        approval, created = await Approval.objects.aget_or_create(message=message, thread=thread, defaults={"approved": approved})
        if not created:
            approval.approved = approved
            await approval.asave(update_fields=["approved"])

    config = {
        "configurable": {
            "thread_id": str(thread.id)
        }
    }

    print(f"tool_approval_view: resuming thread {thread.id} for user {user.id} with approved={approved}")

    await ensure_checkpointer()
    tools = await get_user_tools(user)
    app = create_graph(tools)

    from agent.status import NODE_STATUS_MAP
    from agent.llm import StreamTitleFilter

    AGENT_NODES = {
        "general_agent", "email_agent", "calendar_agent",
        "docs_agent", "sheets_agent", "slack_agent", "research_agent",
    }

    async def approval_event_stream():
        start_time = time.perf_counter()
        title_filter = StreamTitleFilter()

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

                # Emit status events for known nodes (thinking bubbles)
                status_info = NODE_STATUS_MAP.get(node_name, {})
                if status_info and event_type == "on_chat_model_start":
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

            raw_message = extract_text_content(messages[-1].content) if messages else ""
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
            print(f"tool_approval_view error for thread {thread.id}: {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    response = StreamingHttpResponse(approval_event_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


@csrf_exempt
async def delete_thread_view(request, thread_id):
    if request.method != "DELETE":
        return JsonResponse({"detail": "Method not allowed."}, status=405)

    user, error = await authenticated_user(request)
    if error:
        return error

    try:
        thread = await Thread.objects.aget(id=thread_id, user=user)
    except Thread.DoesNotExist:
        return JsonResponse({"detail": "Thread not found."}, status=404)

    await thread.adelete()
    return JsonResponse({"detail": "Thread deleted successfully."}, status=200)