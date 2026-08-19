from django.db import models
 

# Create your models here.
class MCPIntegration(models.Model):
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="mcp_integrations",
    )

    service = models.CharField(max_length=50)

    access_token = models.TextField()
    refresh_token = models.TextField(
        blank=True,
        null=True,
    )
    expires_at = models.DateTimeField(blank=True, null=True)

    enabled = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "service"],
                name="unique_user_mcp_service",
            )
        ]