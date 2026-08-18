from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import status
from rest_framework import generics
from rest_framework.permissions import AllowAny, IsAuthenticated
from django.contrib.auth import get_user_model
from agent.graph import app
from agent.runner import run_agent
from core.serializers import AgentChatSerializer, RegisterationSerializer, LoginSerializer, ApproveEmailSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from conversations.models import Thread, Message
from django.shortcuts import get_object_or_404

User = get_user_model()


class RegistrationView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = RegisterationSerializer
    permission_classes = [AllowAny]

class LoginView(generics.GenericAPIView):
    serializer_class = LoginSerializer
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user =  serializer.validated_data["user"]
        refersh = RefreshToken.for_user(user)


        return Response({
            "access" : str(refersh.access_token),
            "refersh" : str(refersh),
            "user_id": user.id,
            "username": user.username,
            "email": user.email,
        })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def new_chat_view(request):
    serializer = AgentChatSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    message = serializer.validated_data["message"]
    
    thread = Thread.objects.create(
        user=request.user,
        name="New Thread",
    )
    message_obj = Message.objects.create(
        thread=thread,
        role="user",
        content=message,
    )
    
    result = run_agent(
        message=message,
        thread_id=str(thread.id),
    )

    if result["status"] == "approval_required":
        return Response(
            {
                "thread_id": thread.id,
                "status": "approval_required",
                "approval": result["interrupt"],
                "details" :  "go to /approve-email/ endpoint to approve or cancel the email, type:bool, fields: thread_id, approved",
            },
            status=status.HTTP_200_OK,
        )
    
    message_obj = Message.objects.create(
        thread=thread,
        role="agent",
        content=result["result"]["messages"][-1].content,
    )

    return Response(
        {
            "thread_id": thread.id,
            "status": "completed",
            "response": result["result"]["messages"][-1].content,
        },
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def agent_chat_view(request, thread_id):
    serializer = AgentChatSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    message = serializer.validated_data["message"]
 
    thread = get_object_or_404(
        Thread,
        id=thread_id,
        user=request.user,
    )

    message_obj = Message.objects.create(
        thread=thread,
        role="user",
        content=message,
    )

    result = run_agent(
        message=message,
        thread_id=str(thread.id),
    )

    if result["status"] == "approval_required":
        return Response(
            {
                "thread_id": thread_id,
                "status": "approval_required",
                "approval": result["interrupt"],
                "details" :  "go to /approve-email/ endpoint to approve or cancel the email, type:bool, fields: thread_id, approved",
            },
            status=status.HTTP_200_OK,
        )
    
    message_obj = Message.objects.create(
        thread=thread,
        role="agent",
        content=result["result"]["messages"][-1].content,
    )

    return Response(
        {
            "thread_id": thread_id,
            "status": "completed",
            "response": result["result"]["messages"][-1].content,
        },
        status=status.HTTP_200_OK,
    )

 

from langgraph.types import Command


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def approve_email_view(request, thread_id):
    approved = ApproveEmailSerializer(request.data["approved"])

    thread = get_object_or_404(
        Thread,
        id=thread_id,
        user=request.user,
    )

    config = {
        "configurable": {
            "thread_id": str(thread.id)
        }
    }

    result = app.invoke(
        Command(
            resume={
                "approved": approved
            }
        ),
        config=config,
    )

    return Response({
        "result": result["messages"][-1].content
    })