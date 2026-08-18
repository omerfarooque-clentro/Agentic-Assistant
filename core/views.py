import json

from asgiref.sync import sync_to_async
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from rest_framework.response import Response
from rest_framework import status
from rest_framework import generics
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework_simplejwt.authentication import JWTAuthentication
from django.contrib.auth import get_user_model
from agent.runner import run_agent
from core.serializers import AgentChatSerializer, RegisterationSerializer, LoginSerializer, ApproveEmailSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from conversations.models import Thread, Message
from agent.tools import get_user_tools
from agent.graph import create_graph, ensure_checkpointer

User = get_user_model()


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

from django.shortcuts import redirect


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


        return Response({
            "access" : str(refersh.access_token),
            "refresh" : str(refersh),
            "refersh" : str(refersh),
            "user_id": user.id,
            "username": user.username,
            "email": user.email,
        })


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

    thread = await Thread.objects.acreate(
        user=user,
        name="New Thread",
    )
    await Message.objects.acreate(
        thread=thread,
        role="user",
        content=message,
    )

    result = await run_agent(
        message=message,
        thread_id=str(thread.id),
        user=user,
    )

    if result["status"] == "approval_required":
        return JsonResponse({
                "thread_id": thread.id,
                "status": "approval_required",
                "approval": result["interrupt"],
                "details" :  "go to /approve-email/ endpoint to approve or cancel the email, type:bool, fields: thread_id, approved",
            })

    await Message.objects.acreate(
        thread=thread,
        role="agent",
        content=result["result"]["messages"][-1].content,
    )

    return JsonResponse({
            "thread_id": thread.id,
            "status": "completed",
            "response": result["result"]["messages"][-1].content,
        })


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

    try:
        thread = await Thread.objects.aget(id=thread_id, user=user)
    except Thread.DoesNotExist:
        return JsonResponse({"detail": "Thread not found."}, status=404)

    await Message.objects.acreate(
        thread=thread,
        role="user",
        content=message,
    )

    result = await run_agent(
        message=message,
        thread_id=str(thread.id),
        user=user,
    )

    if result["status"] == "approval_required":
        return JsonResponse({
                "thread_id": thread_id,
                "status": "approval_required",
                "approval": result["interrupt"],
                "details" :  "go to /approve-email/ endpoint to approve or cancel the email, type:bool, fields: thread_id, approved",
            })

    await Message.objects.acreate(
        thread=thread,
        role="agent",
        content=result["result"]["messages"][-1].content,
    )

    return JsonResponse({
            "thread_id": thread_id,
            "status": "completed",
            "response": result["result"]["messages"][-1].content,
        })

 

from langgraph.types import Command


@csrf_exempt
async def approve_email_view(request, thread_id):
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed."}, status=405)

    user, error = await authenticated_user(request)
    if error:
        return error

    serializer = ApproveEmailSerializer(data=request_json(request))
    if not serializer.is_valid():
        return JsonResponse(serializer.errors, status=400)
    approved = serializer.validated_data["approved"]

    try:
        thread = await Thread.objects.aget(id=thread_id, user=user)
    except Thread.DoesNotExist:
        return JsonResponse({"detail": "Thread not found."}, status=404)

    config = {
        "configurable": {
            "thread_id": str(thread.id)
        }
    }

    await ensure_checkpointer()
    tools = await get_user_tools(user)
    app = create_graph(tools)

    result = await app.ainvoke(
        Command(
            resume={
                "approved": approved
            }
        ),
        config=config,
    )

    return JsonResponse({
        "result": result["messages"][-1].content
    })