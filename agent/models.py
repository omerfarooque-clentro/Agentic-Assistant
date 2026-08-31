from django.db import models
from encrypted_model_fields.fields import EncryptedTextField


# Create your models here.
class MCPIntegration(models.Model):
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="mcp_integrations",
    )

    service = models.CharField(max_length=50)

    access_token = EncryptedTextField()
    refresh_token = EncryptedTextField(
        blank=True,
        null=True,
    )
    expires_at = models.DateTimeField(blank=True, null=True)

    enabled = models.BooleanField(default=False)
    scopes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "service"],
                name="unique_user_mcp_service",
            )
        ]


class SlackResource(models.Model):
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="slack_resources",
    )
    resource_type = models.CharField(max_length=20)  # e.g., "channel", "user", "team"
    name = models.CharField(max_length=255)
    slack_id = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "resource_type", "name"],
                name="unique_slack_resource",
            )
        ]

    def __str__(self):
        return f"{self.user} - {self.resource_type}:{self.name} -> {self.slack_id}"
        