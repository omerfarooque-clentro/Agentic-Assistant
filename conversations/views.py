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
        return Thread.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        return serializer.save(user=self.request.user)
    
class MessageView(generics.CreateAPIView):
    serializer_class = MessageSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        thread = get_object_or_404(
            Thread,
            id=self.kwargs["thread_id"],
            user=self.request.user,
        )

        serializer.save(thread=thread, role="user")