from datetime import datetime
import json
import logging
import re
import time
import zoneinfo

from agent.streaming import stream_agent_response, stream_approval_response
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
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
from agent.models import MCPIntegration
from conversations.models import Approval, Message, Thread
from core.serializers import (
    AgentChatSerializer,
    ApproveEmailSerializer,
    ForgotPasswordSerializer,
    InAppResetPasswordSerializer,
    LoginSerializer,
    OTPGenerateSerializer,
    RegistrationSerializer,
    ResetPasswordSerializer,
    VerifyOTPSerializer,
)

User = get_user_model()
logger = logging.getLogger(__name__)


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
    serializer_class = RegistrationSerializer
    permission_classes = [AllowAny]


class LoginView(generics.GenericAPIView):
    serializer_class = LoginSerializer
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        return redirect('/signin/')

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        refresh = RefreshToken.for_user(user)

        MCPIntegration.objects.get_or_create(user=user, service="tavily", defaults={"enabled": True})

        return Response({
            "access": str(refresh.access_token),
            "refresh": str(refresh),
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
        },
        "recursion_limit": 25,
    }

    logger.debug(
        "tool_approval_view: resuming thread %s for user %s with approved=%s",
        thread.id, user.id, approved,
    )

    return stream_approval_response(approval, thread, user, config, approved, modified_args, instruction)

  


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