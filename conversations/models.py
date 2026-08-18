from django.db import models

# Create your models here.

class Thread(models.Model):
    name = models.CharField(max_length=255, default="New Thread")
    user = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='threads')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} — {self.user.username}"

class Message(models.Model):
    thread = models.ForeignKey(Thread, on_delete=models.CASCADE, related_name='messages')
    content = models.TextField()
    role = models.CharField(max_length=50)  # e.g., 'user', 'assistant', etc.
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.role}: {self.content[:50]}..."