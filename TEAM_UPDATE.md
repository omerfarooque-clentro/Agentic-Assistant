# Personal Ops Agent Team Update

Date: 2026-08-20

## Delivered

- Added the authenticated Personal Ops frontend for sign-in, registration, workspace threads, message history, chat, approval controls, and independent navigation/conversation scrolling.
- Added browser JWT session handling, including access-token refresh, stale-token cleanup, and public auth requests without inherited authorization headers.
- Added the `/settings/` Connected Accounts page for Gmail, Google Calendar, Google Docs, Google Sheets, and Slack.
- Added integration status, connect, callback, and disconnect API flows with per-user OAuth credentials and token expiry tracking.
- Added Google token refresh before MCP tool loading and shared provider configuration for user-scoped access tokens.
- Split Google Workspace MCP configuration into dedicated Gmail, Calendar, Docs, and Sheets endpoints, alongside Slack and Tavily support.
- Added MCP JSON Schema sanitization that resolves references, simplifies union schemas, and removes unsupported provider schema keywords before binding tools to the LLM.
- Added dynamic MCP tool loading based on each user's enabled integrations, with unavailable domains excluded from routing.
- Improved the LangGraph supervisor and agent routing with explicit domain checks, confidence status, tool-call logging, and safe fallback to the general agent.
- Expanded human-in-the-loop approval coverage to email and Slack send/draft actions, including duplicate tool-call detection and approval metadata.
- Corrected approval request parsing to use a boolean `approved` API field.
- Updated intent routing to train on the complete cleaned intent dataset instead of a train/test split at import time, and added the expanded routing dataset.
- Added the `expires_at` database field and migration for `MCPIntegration` OAuth credentials.
- Added integration-aware dashboard connection controls and connected/not-connected status indicators.
- Added provider-specific environment configuration for MCP URLs and OAuth client credentials.

## Current API Workflow

```text
Browser login
  -> POST /login/
  -> store access + refresh tokens
  -> GET /api/list_thread/
  -> GET /api/thread/<id>/messages/
  -> POST /api/thread/<id>/chat/
  -> optional POST /api/thread/<id>/approve-email/
```

Connected account endpoints:

```text
GET  /api/integrations/status/
GET  /api/integrations/<gmail|calendar|docs|sheets|slack>/connect/
GET  /api/integrations/<google|slack>/callback/
POST /api/integrations/<service>/disconnect/
```

Thread and approval endpoints:

```text
GET  /api/list_thread/
GET  /api/thread/<thread_id>/messages/
POST /api/thread/<thread_id>/chat/
POST /api/thread/<thread_id>/approve-email/
```

Provider environment variables:

```text
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
GOOGLE_OAUTH_REDIRECT_URI=http://127.0.0.1:8000/api/integrations/google/callback/
SLACK_CLIENT_ID
SLACK_CLIENT_SECRET
SLACK_OAUTH_REDIRECT_URI=http://127.0.0.1:8000/api/integrations/slack/callback/
```

After pulling the changes, apply the integration expiry migration:

```powershell
python manage.py migrate
```

## Run Locally

Preferred for async LangGraph and Postgres checkpointer:

```powershell
python -m uvicorn config.asgi:application --reload
```

The Django development server can still serve the frontend, but ASGI is the supported runtime for async agent execution.

## Known Follow-ups

- Ensure the active virtual environment contains every dependency from `requirements.txt`, including `langchain_mcp_adapters`.
- Verify PostgreSQL connectivity and credentials before starting the ASGI lifespan; checkpointer startup depends on the database.
- Add automated tests for native async chat, token refresh, thread isolation, provider callbacks, MCP schema sanitization, and approval resume.
- Replace the current pending-response UI with true streamed agent events when the backend streaming contract is finalized.
- Add provider token revocation calls and encrypt OAuth tokens at rest before production deployment.
- Review the in-progress agent, MCP, OAuth, and frontend changes together before merging; the current worktree contains uncommitted changes across those areas.

## Validation Status

- VS Code diagnostics report no errors in the recently touched Python/frontend files.
- Terminal validation for this update:

```powershell
python manage.py check
python -m compileall -q agent core config frontend
```
