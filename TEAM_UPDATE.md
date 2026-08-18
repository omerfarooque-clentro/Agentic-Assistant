# Personal Ops Agent Team Update

Date: 2026-08-18

## Delivered

- Added a dedicated `frontend/` Django surface with sign-in, registration, and workspace pages.
- Added responsive workspace UI with thread navigation, per-thread message history, chat composer, approval controls, and independent thread/chat scrolling.
- Added JWT session handling in the browser:
  - Stores access and refresh tokens after login.
  - Refreshes expired access tokens through `/api/token/refresh/`.
  - Clears stale tokens before a new login.
  - Keeps public auth requests free of stale authorization headers.
- Mapped frontend routes:
  - `/` workspace
  - `/signin/` browser login
  - `/register/` registration
  - `POST /login/` JSON login API
  - `GET /login/` redirects to `/signin/`
- Added explicit development static serving so `frontend/static/frontend/app.css` and `app.js` load under `runserver`.
- Added canonical thread history endpoint:
  - `GET /api/thread/<thread_id>/messages/`
  - History is scoped to the authenticated user and ordered chronologically.
  - Thread list is ordered newest-first by `updated_at`.
- Converted chat and approval endpoints to native async Django handlers using async ORM and direct LangGraph awaits.
- Changed LangGraph state inspection to `await app.aget_state(config)` for the async Postgres checkpointer.
- Added ASGI lifespan setup and shutdown for the shared Postgres checkpointer.
- Added Windows selector event-loop policy for Django entry points to support psycopg async mode.
- Added MCP client modules for Google Workspace, Slack, and Tavily integrations, with shared configuration helpers.

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

## Run Locally

Preferred for async LangGraph and Postgres checkpointer:

```powershell
python -m uvicorn config.asgi:application --reload
```

The Django development server can still serve the frontend, but ASGI is the supported runtime for async agent execution.

## Known Follow-ups

- Ensure the active virtual environment contains every dependency from `requirements.txt`, including `langchain_mcp_adapters`.
- Verify PostgreSQL connectivity and credentials before starting the ASGI lifespan; checkpointer startup depends on the database.
- Add automated endpoint tests for native async chat, token refresh, thread isolation, and approval resume.
- Replace the current pending-response UI with true streamed agent events when the backend streaming contract is finalized.
- Review the in-progress `agent/` and MCP changes together before merging; the current worktree contains uncommitted changes across those areas.

## Validation Status

- VS Code diagnostics report no errors in the recently touched Python/frontend files.
- Full terminal validation should be run from the project virtual environment after dependencies are installed:

```powershell
python manage.py check
python -m compileall -q agent core config frontend
```
