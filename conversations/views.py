from django.shortcuts import render
from rest_framework import generics
from .models import Thread
from rest_framework.permissions import IsAuthenticated
from .serializer import ThreadSerializer, MessageSerializer
from django.shortcuts import get_object_or_404

class ThreadListView(generics.ListCreateAPIView):
    serializer_class = ThreadSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Thread.objects.filter(user=self.request.user).order_by("-updated_at", "-id")

    def perform_create(self, serializer):
        return serializer.save(user=self.request.user)
    
class MessageListView(generics.ListCreateAPIView):
    serializer_class = MessageSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        thread = get_object_or_404(
            Thread,
            id=self.kwargs["thread_id"],
            user=self.request.user,
        )

        serializer.save(thread=thread, role="user")

    def get_queryset(self):
        thread_id = self.kwargs.get("thread_id") or self.request.query_params.get("thread_id")
        thread = get_object_or_404(
            Thread,
            id=thread_id,
            user=self.request.user,
        )
        return thread.messages.order_by("created_at", "id")


from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status


class ThreadRenameView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, thread_id):
        thread = get_object_or_404(Thread, id=thread_id, user=request.user)
        name = (request.data.get("name") or "").strip()
        if not name:
            return Response({"detail": "Thread name cannot be empty."}, status=status.HTTP_400_BAD_REQUEST)
        thread.name = name[:255]
        thread.save(update_fields=["name", "updated_at"])
        return Response(ThreadSerializer(thread).data)