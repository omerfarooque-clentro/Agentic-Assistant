import os
import re
from typing import Any, Optional
import requests
from langchain_core.tools import tool

from agent.models import MCPIntegration, SlackResource


SLACK_ID_PATTERN = re.compile(r"^[CGBUDW][A-Z0-9]{8,14}$")


def normalize_resource_name(name: str) -> str:
    """Normalize resource name by stripping whitespace, leading # or @, and lowercasing."""
    if not name:
        return ""
    cleaned = name.strip()
    if cleaned.startswith("#") or cleaned.startswith("@"):
        cleaned = cleaned[1:]
    return cleaned.lower()


def normalize_resource_type(resource_type: str) -> str:
    """Normalize resource type aliases (channels -> channel, users/member -> user, team -> team)."""
    if not resource_type:
        return "channel"
    cleaned = resource_type.strip().lower()
    if cleaned in {"channel", "channels", "c"}:
        return "channel"
    if cleaned in {"user", "users", "u", "member", "members", "person", "im", "dm"}:
        return "user"
    if cleaned in {"team", "teams", "workspace"}:
        return "team"
    return cleaned


def is_slack_id(value: str) -> bool:
    """Check if the provided value is already a Slack ID format."""
    if not value or not isinstance(value, str):
        return False
    return bool(SLACK_ID_PATTERN.match(value.strip()))


def get_slack_id(user: Any, name: str, resource_type: str = "channel") -> Optional[str]:
    """Retrieve cached Slack ID from the database for a user and resource."""
    norm_name = normalize_resource_name(name)
    norm_type = normalize_resource_type(resource_type)
    if not norm_name:
        return None

    resource = SlackResource.objects.filter(
        user=user,
        resource_type=norm_type,
        name__iexact=norm_name,
    ).first()

    if resource:
        return resource.slack_id
    return None


def save_slack_id(user: Any, name: str, resource_type: str, slack_id: str) -> SlackResource:
    """Save or update a resolved Slack ID in the database cache."""
    norm_name = normalize_resource_name(name)
    norm_type = normalize_resource_type(resource_type)

    resource, _ = SlackResource.objects.update_or_create(
        user=user,
        resource_type=norm_type,
        name=norm_name,
        defaults={"slack_id": slack_id.strip()},
    )
    return resource


def _get_slack_access_token(user: Any) -> Optional[str]:
    """Get Slack access token from user's MCPIntegration or environment."""
    if user and hasattr(user, "is_authenticated") and user.is_authenticated:
        integration = MCPIntegration.objects.filter(
            user=user,
            service="slack",
            enabled=True,
        ).first()
        if integration and integration.access_token:
            return integration.access_token
    return None


def search_slack(user: Any, name: str, resource_type: str = "channel") -> Optional[str]:
    """Search Slack API for a channel or user ID by name."""
    norm_name = normalize_resource_name(name)
    norm_type = normalize_resource_type(resource_type)
    if not norm_name:
        return None

    token = _get_slack_access_token(user)
    if not token:
        return None

    headers = {"Authorization": f"Bearer {token}"}

    try:
        if norm_type == "channel":
            cursor = None
            for _ in range(5):  # Limit pagination to max 5 pages (1000 channels)
                params = {
                    "types": "public_channel,private_channel",
                    "limit": 200,
                    "exclude_archived": True,
                }
                if cursor:
                    params["cursor"] = cursor

                resp = requests.get(
                    "https://slack.com/api/conversations.list",
                    headers=headers,
                    params=params,
                    timeout=10,
                )
                if not resp.ok:
                    break
                data = resp.json()
                if not data.get("ok"):
                    break

                for channel in data.get("channels", []):
                    ch_name = channel.get("name", "").lower()
                    ch_norm = channel.get("name_normalized", "").lower()
                    if norm_name in (ch_name, ch_norm):
                        return channel.get("id")

                cursor = data.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break

        elif norm_type == "user":
            cursor = None
            for _ in range(5):  # Limit pagination to max 5 pages (1000 users)
                params = {"limit": 200}
                if cursor:
                    params["cursor"] = cursor

                resp = requests.get(
                    "https://slack.com/api/users.list",
                    headers=headers,
                    params=params,
                    timeout=10,
                )
                if not resp.ok:
                    break
                data = resp.json()
                if not data.get("ok"):
                    break

                for member in data.get("members", []):
                    if member.get("deleted"):
                        continue
                    m_name = member.get("name", "").lower()
                    m_real = member.get("real_name", "").lower()
                    profile = member.get("profile", {})
                    p_disp = profile.get("display_name", "").lower()
                    p_disp_norm = profile.get("display_name_normalized", "").lower()
                    p_real = profile.get("real_name", "").lower()
                    p_real_norm = profile.get("real_name_normalized", "").lower()

                    if norm_name in (m_name, m_real, p_disp, p_disp_norm, p_real, p_real_norm):
                        return member.get("id")

                cursor = data.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break

    except requests.RequestException:
        return None

    return None


def resolve_slack_resource(user: Any, name: str, resource_type: str = "channel") -> Optional[str]:
    """
    Resolve Slack name to Slack ID with automatic caching:
    1. If input is already a valid Slack ID, return it.
    2. Check database cache (SlackResource).
    3. On miss, search Slack API.
    4. On successful search, persist in database cache and return ID.
    """
    if not name:
        return None

    cleaned_name = name.strip()
    if is_slack_id(cleaned_name):
        return cleaned_name

    norm_name = normalize_resource_name(cleaned_name)
    norm_type = normalize_resource_type(resource_type)

    # 1. DB cache hit
    cached_id = get_slack_id(user, norm_name, norm_type)
    if cached_id:
        return cached_id

    # 2. DB cache miss -> Slack lookup
    slack_id = search_slack(user, norm_name, norm_type)

    # 3. Automatically save to DB cache
    if slack_id:
        save_slack_id(user, norm_name, norm_type, slack_id)
        return slack_id

    return None


def create_slack_resolver_tool(user: Any):
    """Create a LangChain tool bound to the specific user for Slack ID resolution."""
    @tool("resolve_slack_id")
    def resolve_slack_id(name: str, resource_type: str = "channel") -> str:
        """Resolve a Slack channel name (e.g. '#dev-learning', 'general') or user name (e.g. '@alice', 'bob') to its Slack ID (e.g. 'C0123456', 'U0123456').
        Automatically checks the local cache and queries Slack API if not cached.

        Args:
            name: The channel or user name to resolve (e.g. '#dev-learning', 'general', '@alice').
            resource_type: Type of Slack resource ('channel' or 'user'). Defaults to 'channel'.

        Returns:
            The resolved Slack ID (e.g. 'C12345678') or an error message if not found.
        """
        print(f"slack resolver called with name {name}, resource: {resource_type}")
        slack_id = resolve_slack_resource(user, name, resource_type)
        if slack_id:
            return slack_id
        return f"Could not find Slack {resource_type} '{name}'. Please check the name or invite the bot to the channel."

    return resolve_slack_id
