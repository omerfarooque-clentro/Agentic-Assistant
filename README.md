# Agentic Assistant

A Django + LangGraph "personal ops" agent that can read and send email, manage
calendars, read/write Google Docs and Sheets, and read/send Slack messages —
scoped, per user, to only the integrations that user has actually connected.

The core design goal is **token efficiency and tool safety**: instead of
binding every possible tool from every possible integration to the LLM on
every turn, the agent classifies what the user is asking for first, resolves
that to a small, exact set of MCP tool names, and only then lets the LLM see
and call tools — with human approval required before any state‑changing
action (send an email, post to Slack, edit a doc/sheet, create a calendar
event, etc.) actually executes.

---
# snapshot
<img width="1366" height="612" alt="image" src="https://github.com/user-attachments/assets/e7875f9b-f9bb-4de3-8bd5-2f78ec4db2fd" />

---

## Table of contents

- [High-level flow](#high-level-flow)
- [Why two LLM calls per turn](#why-two-llm-calls-per-turn)
- [Architecture](#architecture)
  - [Authentication & account recovery](#authentication--account-recovery)
  - [Integrations layer](#integrations-layer)
  - [MCP clients](#mcp-clients)
  - [Tool discovery & domain grouping](#tool-discovery--domain-grouping)
  - [Intent routing (NLP node)](#intent-routing-nlp-node)
  - [LangGraph graph](#langgraph-graph)
  - [Streaming](#streaming)
  - [Human-in-the-loop approval](#human-in-the-loop-approval)
  - [Conversations & threads](#conversations--threads)
- [Frontend](#frontend)
- [Project layout](#project-layout)
- [Setup](#setup)
- [Environment variables](#environment-variables)
- [API surface](#api-surface)
- [Known limitations / open items](#known-limitations--open-items)

---

## High-level flow

```
User message
   │
   ▼
NLP node ── (LLM call #1: query rewrite/dependency resolution)
   │           classifies whether the current message is a follow-up
   │           ("send it to Arsalan") or standalone, and rewrites it
   │           into a self-contained query before classification.
   ▼
Naive Bayes intent classifier (TF-IDF + MultinomialNB)
   │           predicts a fine-grained intent, e.g. "slack.send",
   │           "email.read", "calendar.create", "docs.update" …
   ▼
Supervisor router
   │           maps the intent's domain (email / calendar / docs /
   │           sheets / slack / research) to a domain-scoped subgraph,
   │           or falls back to a general agent if that domain isn't
   │           enabled for this user.
   ▼
Domain agent ── (LLM call #2: tool-calling, streamed token-by-token)
   │           only the exact MCP tools allow-listed for the predicted
   │           intent are bound to the model — not the full tool
   │           catalog for the domain, and never tools from other
   │           domains.
   ▼
Tool call?
   ├─ no  → thread naming → END
   ├─ yes, read-only        → ToolNode executes → back to domain agent
   └─ yes, write/send action → approval node (interrupt) → human
        approves/rejects → ToolNode executes (or the run is cancelled)
```

The whole run above happens inside one Server-Sent Events response —
`agent/runner.py: run_agent` is an async generator that yields `status`
(which node is active), `token` (streamed model output), `approval_required`,
`completed`, or `error` events as the graph executes, and
`core/views.py: new_chat_view` / `agent_chat_view` stream those straight to
the browser as they're produced instead of waiting for the whole run to
finish and returning one JSON blob.

## Why two LLM calls per turn

A single-shot classifier can't resolve context. Given:

```
human: what's the weather today?
agent: <weather answer>
human: okay send it to arsalan and tell i...
```

a bag-of-words / NB classifier looking only at the *last* message has no way
to know "it" refers to the weather answer, or that "send … to arsalan" means
Slack rather than email. Fed the raw last message, it can just as easily
predict `research.search` as `slack.send`.

To fix this without asking the classifier to also understand conversational
state, `agent/routing/query_generator.py` makes a first LLM call that looks
at the last 3 messages of context plus the current one and:

1. Decides whether the current message is dependent on prior turns or
   independent, and rewrites it into one self-contained instruction
   (e.g. *"Inform Ahmed on Slack about the weather forecast and that I will
   be working remotely."*).
2. Can still flag a message as covering **multiple distinct actions across
   domains** by returning `TYPE: MULTI` — but this signal is no longer acted
   on anywhere. `route_intent` (`intent_router.py`) used to short-circuit on
   `TYPE: MULTI` and return a synthetic `domain: "multi"` result; that branch
   never had a real destination in the graph and has since been **removed
   entirely**, not just deprecated. `route_intent` now ignores `query['type']`
   outright and always classifies `query['query']` as a single intent,
   whether the rewriter tagged it MULTI or SINGLE. A genuinely
   cross-domain request ("check my calendar and email the summary to the
   team") still has to be handled as separate turns — see
   [Known limitations](#known-limitations--open-items).

Only after that rewrite does the TF-IDF/Naive Bayes model (`intent_router.py`)
classify intent. This costs one extra LLM round-trip per turn, but it keeps
the classifier's input clean and — more importantly — keeps the *second*
LLM call (the one that can actually call tools) scoped to a handful of tools
instead of the entire tool catalog across five+ MCP servers, which is the
larger token cost.

## Architecture

### Authentication & account recovery

Login is JWT-based (`djangorestframework_simplejwt`); `accounts/models.py`'s
`User` extends `AbstractUser` with `created_at`/`updated_at` and an
`otp_secret` field. Password recovery does **not** use an emailed or SMS'd
one-time code — it's a single, static, high-entropy **recovery
credential** the user downloads once and must keep safe:

- `accounts/utils.py` — `generate_recovery_otp()` produces a formatted
  credential like `PO-8F2K-M3NP-X94W` (`secrets.choice` over a
  36-character alphabet with visually-confusable characters like `0/O`,
  `1/I/L` removed); `hash_recovery_otp`/`verify_recovery_otp` store and
  check it via Django's password hasher (`make_password`/`check_password`),
  with a normalized-plaintext comparison fallback via
  `secrets.compare_digest`. `generate_secure_password` produces a 16-char
  password (guaranteed upper/lower/digit/symbol) when a user opts to have
  one auto-generated at registration instead of choosing their own.
- **Registration** (`RegisterationSerializer.create`, `core/serializers.py`):
  generates the initial recovery credential, hashes it into `otp_secret`,
  and returns the raw credential (and the auto-generated password, if used)
  to the client exactly once, in the registration response — it is never
  stored or retrievable in plaintext again. `frontend/templates/frontend/register.html`
  shows a two-step flow: the signup form, then a credential screen with a
  "Download Credentials (.txt)" button before the user can enter the app.
- **Forgot password** is a 3-step API sequence, each independently callable:
  `forgot_password_view` (checks the email exists) → `verify_otp_view`
  (checks the recovery credential against the hash) → `reset_password_view`
  (sets the new password **and** immediately rotates to a brand-new recovery
  credential, invalidating the old one, returned once in the response).
  `frontend/templates/frontend/reset_password.html` drives this as an
  Email → Verify OTP → Set Password → Download New OTP wizard.
- **In-app password change** while already signed in
  (`in_app_reset_password_view` / `InAppResetPasswordSerializer`) accepts
  *either* the current password *or* a valid recovery credential as proof —
  if the recovery credential was used to authorize the change, a new one is
  generated and rotated in the same request, same as the forgot-password
  flow. `otp_generate` lets a signed-in user manually rotate their recovery
  credential on demand (re-verifying their current password first),
  invalidating the previous one.
- Every endpoint above is exposed twice — once at its short path
  (`/forgot-password/`, `/verify-otp/`, `/reset-password-api/`,
  `/otp-generate/`, `/change-password/`) and once under `/api/auth/...` —
  see [API surface](#api-surface).

### Integrations layer

`agent/models.py` defines `MCPIntegration`: one row per `(user, service)`,
storing the OAuth `access_token` / `refresh_token`, `expires_at`, enabled
scopes, and an `enabled` flag. This is the source of truth for "what is this
user actually allowed to use right now" — nothing downstream loads a tool
for a service the user hasn't connected and enabled.

`agent/integrations/access.py` keeps those credentials usable:

- `refresh_expired_google_token` refreshes any Google-backed integration
  (`gmail`, `calendar`, `docs`, `sheets`) whose token expires within 2
  minutes, using the stored refresh token, and persists the new token.
- `validate_slack_integration` calls Slack's `auth.test` before use; if the
  token is dead, the integration is disabled in the database rather than
  silently failing mid-conversation.

Both run concurrently across all of a user's integrations
(`agent/tools/service.py: get_user_tools`) before any tools are fetched.

> Tokens are encrypted at rest via `EncryptedTextField`
> (`django-encrypted-model-fields`) — see migration
> `0004_alter_mcpintegration_access_token_and_more`.

### MCP clients

`mcp_clients/common.py` is the single place that builds MCP server configs
and opens `MultiServerMCPClient` connections (via `langchain_mcp_adapters`).
Each Google Workspace product is treated as its **own MCP server** with its
own URL and its own per-request bearer token (`GMAIL_MCP_URL`,
`CALENDAR_MCP_URL`, `DOCS_MCP_URL`, `SHEETS_MCP_URL`), alongside a Slack MCP
server and a Tavily MCP server for web research. `get_tools(name, config)`
opens a connection, fetches that server's tool list, and returns it —
per-integration, not globally cached — so a disabled/disconnected service
never contributes tools to a run.

By default the Google Workspace URLs point at `http://127.0.0.1:8001/mcp` —
that's the vendored `google_workspace_mcp/` directory (a copy of the
open-source `taylorwilsdon/workspace-mcp` FastMCP server, covering Gmail,
Calendar, Docs, Sheets, Drive, Slides, Tasks, Forms, Chat, Contacts, and
search). It ships with its own `Dockerfile` and needs to be run separately
(see [Setup](#setup)) — the Django app talks to it as just another MCP
server over HTTP, it doesn't import or embed it directly.

### Tool discovery & domain grouping

Fetching tools is a two-step reduction, both designed to avoid ever handing
the LLM a large, mixed-domain tool list:

1. **`agent/tools/service.py: get_user_tools`** — loads only the user's
   `enabled=True` `MCPIntegration` rows, refreshes/validates their tokens,
   then fetches tools **concurrently** (`asyncio.gather`) from each
   integration's MCP server.
2. **`agent/tools/grouping.py: build_user_tool_groups`** — buckets the
   returned tools by *domain* (not by MCP server), de-duplicating by tool
   name. This matters because a single MCP server (e.g. Google Workspace)
   can return tools spanning multiple products in one response.

Domain resolution is O(1): `agent/tools/domain_registry.py` maintains an
explicit `tool_name → domain` allow-list per product (Gmail tool names,
Calendar tool names, Docs tool names, Sheets tool names) built once at
import time, plus a `service → domain` fallback for single-purpose servers
like Slack and Tavily. `resolve_tool_domain(service, tool_name)` is a single
dict lookup — no per-request scanning or string matching.

The result, `tools_groups`, is a `dict[domain] -> list[tool]` and is exactly
what's fed into graph construction (`available_domains = set(tools_groups)`),
so a domain the user hasn't connected literally has no node in the graph for
that run.

### Intent routing (NLP node)

`agent/routing/intent_router.py`:

- Trains a `TfidfVectorizer` + `MultinomialNB` pipeline at import time on
  `agent/routing/data/intent_data.CSV` — 21 fine-grained intents across the
  six domains (`email.search/.send/.read/.draft/.forward`,
  `calendar.create/.search/.update/.delete/.availability`,
  `docs.read/.create/.update/.summarize`, `sheets.read/.write/.update`,
  `slack.send/.search/.history`, `research.search`). The dataset previously
  also carried `general`/`out_of_scope` catch-all labels; those have been
  removed — the classifier no longer predicts them, and the routing-side
  special-casing for them in `intent_router.py` (`get_candidate_intents`'s
  domain filter, and `route_intent`'s remap-to-`research.search` step) is
  now dead code left over from that change, not active behavior.
- `get_candidate_intents` restricts predictions to domains the user actually
  has enabled (`available_domains`), so the classifier can never route to a
  domain with no tools behind it.
- `route_intent` takes the top-2 candidates, computes a **confidence** (top
  probability) and **margin** (gap to the second candidate), and returns a
  `status` of `confident`, `ambiguous`, or `unavailable` against fixed
  thresholds (`CONFIDENCE_THRESHOLD = 0.65`, `MARGIN_THRESHOLD = 0.20`).
  Low-confidence, no-candidate, or unavailable-domain predictions fall back
  to `general_agent` (`supervisor_router` sends any `routing_status ==
  "unavailable"` there) — a plain, tool-less LLM call, not a
  research-domain agent specifically.
- `ACTION_MCP_TOOL_NAMES` maps each fine-grained intent to the **exact** set
  of MCP tool names that intent is allowed to call (e.g. `slack.send` maps
  to `slack_send_message`, `slack_schedule_message`,
  `slack_send_message_draft`, `slack_add_reaction`, canvas tools, and the
  Slack search tools it needs to resolve a user/channel — but not
  `slack_read_channel` or other history tools). `get_mcp_tool_names(intent)`
  is intersected against whatever tools the user's integrations actually
  returned, so an intent never grants access to a tool the user hasn't
  connected either.

### LangGraph graph

`agent/graph/builder.py: create_graph(tools_groups)` builds the state graph
per run (state schema in `agent/graph/state.py`, node functions in
`agent/graph/nodes.py`):

- `START → nlp` — runs the NLP node described above.
- `nlp → supervisor_router` — conditional edge to `general_agent` or one of
  `{email, calendar, docs, sheets, slack, research}_agent`, restricted to
  domains present in this user's `tools_groups`.
- Each domain agent (`scoped_agent`) is a closure that, **on every
  invocation**, re-derives the allowed tool names for the current
  `state["intent"]` via `get_mcp_tool_names` and filters the domain's full
  tool list down to just those — so even within an already-scoped domain
  subgraph, the model still only sees the handful of tools relevant to this
  specific intent, not the whole domain (e.g. within `email_agent`,
  `email.send` sees only `send_gmail_message`, not label management or
  filter tools).
- Each domain agent has its own `{domain}_tools` `ToolNode` and a
  conditional edge (`scoped_should_continue`) that goes to `tools` (execute),
  `approval` (interrupt for state-changing actions), or `end`.
- After tool execution, control returns to the same domain agent (tool loop)
  until the model stops calling tools, then flows to `thread_naming → END`.
- The LLM used for both the general agent and each scoped domain agent is
  **Groq** (`openai/gpt-oss-120b`) with an automatic fallback to **Google
  Gemini** (`gemini-2.5-flash`) via LangChain's `.with_fallbacks(...)`
  (`agent/llm/client.py`). Tools are bound to both providers up front
  (`bind_tools_with_fallback`) so a mid-run provider failure doesn't drop
  tool access. Because Gemini and Groq can shape `message.content`
  differently (a plain string vs. a list of content blocks), `core/views.py`
  normalizes it through `extract_text_content()` before it's saved or
  returned to the frontend.
- Conversation state is checkpointed to **Postgres** via
  `langgraph-checkpoint-postgres` (`AsyncPostgresSaver` over an
  `AsyncConnectionPool`), which is what makes the human-approval interrupt
  durable across the request/response boundary — the graph can pause mid-run
  and resume later against the same `thread_id`.

### Streaming

`agent/runner.py: run_agent` doesn't return a single result — it's an async
generator built on `app.astream_events(..., version="v2")` that yields
structured events as the graph executes:

- **`status`** — whenever a node with an entry in `agent/status.py:
  NODE_STATUS_MAP` starts a chat-model call (e.g. `nlp` → "Understanding
  your request…", `email_agent` → "Working with email…"), so the frontend
  can show what the agent is currently doing rather than a generic spinner.
- **`token`** — each streamed chunk of model output from one of the
  `AGENT_NODES` (the general agent and each domain agent), so replies render
  incrementally instead of appearing all at once.
- **`approval_required`** — once the graph hits an interrupt, carrying the
  pending tool call for the frontend to render as an approval card.
- **`completed`** — the final state once the graph finishes with no pending
  interrupt; this is also where the agent's turn is persisted to `Message`.
- **`error`** — any exception raised during the run, including
  `asyncio.CancelledError`/`GeneratorExit` handling so a client disconnecting
  mid-stream doesn't leave the generator running. On the backend, an
  `error` chunk also persists a generic fallback agent message ("An
  unexpected error occurred during processing, please try again.") to
  `Message` rather than leaving the thread's transcript missing a reply —
  the raw exception text is only sent to the frontend in the SSE payload,
  never stored.

`core/views.py: new_chat_view` and `agent_chat_view` wrap this generator in a
`StreamingHttpResponse` with `content_type="text/event-stream"`, serializing
each event as an SSE `data: {...}` frame. The frontend (`app.js: send`)
consumes this with `fetch` + `response.body.getReader()`, reassembling SSE
blocks and rendering `status`/`token`/`completed`/`approval_required`/`error`
as they arrive — each in-flight request is tagged with a `streamRequestId`
so a stale stream (e.g. after switching threads or starting a new message)
is discarded instead of overwriting newer output, and an `AbortController`
lets the client cancel a stream in progress (e.g. on "New thread").

### Human-in-the-loop approval

`agent/graph/approval.py` defines, per domain, exactly which tool calls are
considered "actions" that require explicit approval before they run —
sending or drafting email, creating/modifying calendar events (including
out-of-office and focus time), any Docs mutation, Sheets writes, and any
Slack send/draft/canvas action. Read-only tools (search, get, list) in the
same domains are **not** gated and execute immediately.

When a gated tool call is produced, `scoped_should_continue` routes to a
shared `approval` node instead of the domain's `ToolNode`. That node:

- Uses LangGraph's `interrupt()` to pause the graph and surface the pending
  tool name/args/domain to the caller.
- Tracks already-executed `tool_call_id`s in-process to flag duplicate
  approval requests (e.g. re-approving an action that already ran).
- On resume with `{"approved": true/false}`, either lets execution fall
  through to the correct `{domain}_tools` node (via `approval_result`,
  which reads `state["details"]["domain"]`) or replaces the pending AI
  message with a rejection notice and routes to `thread_naming → END`.

### Conversations & threads

`conversations/models.py` is intentionally simple:

- `Thread` — one per conversation, owned by a user, auto-named ("New
  Thread" until the graph's `thread_naming_node` generates a real name once
  the conversation has more than one exchange). `updated_at` uses Django's
  `auto_now=True`, and `ThreadListView` (`conversations/views.py`) orders
  the sidebar by `-updated_at, -id` — so a thread only actually sorts to
  the top when something explicitly calls `.save()` on it after the
  original creation. `core/views.py`'s `new_chat_view` and
  `agent_chat_view` now call `await thread.asave(update_fields=["updated_at"])`
  after **every** message append (user message, agent reply, and the
  error-fallback message on a failed run) — previously this only happened
  inconsistently, so a thread with new activity could sit stale in the list
  instead of moving to the top.
- `Message` — belongs to a thread, has a `role` (`user`/`assistant`/etc.)
  and `content`.
- `Approval` — records the domain and outcome of a human-in-the-loop
  approval decision against a thread/message.

`accounts/models.py` extends Django's `AbstractUser` with `created_at` /
`updated_at` and `otp_secret`; auth is JWT-based
(`djangorestframework_simplejwt`) — see
[Authentication & account recovery](#authentication--account-recovery).

## Frontend

The dashboard (`frontend/static/frontend/app.js`, server-rendered by
`frontend/templates/frontend/`) is a single vanilla-JS file with no build
step. A few things worth knowing if you're working on it:

- **Streaming consumption.** `send()` reads the SSE response with
  `fetch` + `response.body.getReader()` (no `EventSource`, since that can't
  send an `Authorization` header), reassembling `\n\n`-delimited SSE blocks
  and dispatching on each event's `type`. A live status line ("Understanding
  your request…", "Working with email…", etc.) updates from `status` events
  while tokens stream in, so the pending message bubble reflects what the
  agent is actually doing.
- **Auth.** `fetchWithAuth` decodes the JWT's `exp` claim and refreshes the
  access token proactively (~15s before expiry) instead of waiting for a 401,
  and de-dupes concurrent refresh attempts behind a single in-flight promise
  so simultaneous requests (e.g. loading threads + integrations on page load)
  don't each trigger their own refresh call.
- **Markdown rendering.** `renderMarkdown` is a small hand-rolled renderer
  (headings, bullet/numbered lists, code fences, blockquotes, tables,
  horizontal rules, bold/italic/inline-code/links) — there's no external
  markdown dependency.
- **Weather cards.** `detectWeather`/`parseWeatherReport` recognize a single
  weather reading and render it as a compact stat card instead of raw
  bullets; `parseMultiDayForecast`/`parseMarkdownTableForecast` separately
  detect a multi-day forecast (day headers like "Monday", "Day 3", "Sat 14
  Jun", or a markdown table with day/date/condition/temp/rain columns) and
  render a horizontally-scrollable day strip instead, so a 5/7/10-day
  forecast doesn't get flattened into one oversized card. Cards are
  condition-themed (`data-theme="sunny"|"rain"|"storm"|"cloudy"|"snow"|"fog"`,
  each with its own gradient/border color) and include a hero
  temperature, a metrics tile grid (feels-like, wind, humidity, etc.), and
  an advisory banner line when the source text has one.
- **Approvals.** `addApprovalCard`/`approve()` render domain-specific
  layouts for calendar (date badge, attendee chips), email (To chips,
  subject, quoted body), and Slack (channel chip, message bubble); anything
  else falls back to a generic, length-capped field list. Approving/
  cancelling shows an inline spinner and status line, and the composer is
  disabled while an approval is pending so a new message can't race the
  graph's paused, interrupted run.
- **Threads.** In addition to selecting a thread, each entry in the sidebar
  has a delete (`×`) button wired to `DELETE /api/thread/<id>/delete/`, with
  a confirm prompt before it fires.

---

## Project layout

```
accounts/         Custom Django User model (incl. otp_secret), utils.py for
                  recovery-credential generation/hashing/verification
agent/
  graph/          LangGraph state, node functions, graph builder, approval
  integrations/   OAuth connect/callback/disconnect/status views, token refresh
  llm/            LLM clients (Groq + Gemini fallback), prompt templates
  migrations/
  models.py       MCPIntegration (per-user OAuth credentials per service)
  routing/        NLP query rewriter + TF-IDF/NaiveBayes intent classifier
  status.py       Node → status-message map used by the SSE stream
  tools/          Domain registry, per-user tool discovery/grouping
  runner.py       Async-generator entry point that streams graph events
config/           Django project settings, ASGI/WSGI, root URLconf
conversations/    Thread / Message / Approval models, thread & message APIs
core/             Registration/login views, streaming chat + approval +
                  thread-delete endpoint views, `core/tests.py` test suite
frontend/         Server-rendered dashboard/settings/login/register UI
google_workspace_mcp/  Vendored Google Workspace MCP server (Gmail, Calendar,
                  Docs, Sheets, Drive, Slides, Tasks, Forms, Chat, Contacts) —
                  a separate service, run independently of the Django app
mcp_clients/      Per-service MCP server configs and standalone test clients
scripts/          Ad-hoc NLP router testing script
```

## Setup

The project targets **async execution** (LangGraph + Postgres checkpointer +
SSE streaming), so it should be run under ASGI rather than the plain Django
dev server.

```bash
python -m venv .venv
source .venv/bin/activate        # .venv\Scripts\activate on Windows
pip install -r requirements.txt

# Postgres must be reachable — the checkpointer setup depends on it.
python manage.py migrate

python -m uvicorn config.asgi:application --reload
```

Gmail/Calendar/Docs/Sheets tools also require the vendored MCP server in
`google_workspace_mcp/` to be running (its `Dockerfile`, or its own
`pyproject.toml`/`uv.lock` for a local run) and reachable at whatever
`GMAIL_MCP_URL` / `CALENDAR_MCP_URL` / `DOCS_MCP_URL` / `SHEETS_MCP_URL`
point to (default `http://127.0.0.1:8001/mcp`) — it's a separate process
from the Django app, not something `manage.py` starts for you.

## Environment variables

```text
# Postgres (checkpointer + Django DB)
DB_USER
DB_PASSWORD
DB_HOST=localhost
DB_PORT=5432
DB_NAME

# LLM providers
GROQ_API_KEY               # via langchain-groq
GOOGLE_API_KEY              # via langchain-google-genai (fallback model)

# Google OAuth (Gmail / Calendar / Docs / Sheets integrations)
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
GOOGLE_OAUTH_REDIRECT_URI=http://127.0.0.1:8000/api/integrations/google/callback/

# Slack OAuth
SLACK_CLIENT_ID
SLACK_CLIENT_SECRET
SLACK_OAUTH_REDIRECT_URI=http://127.0.0.1:8000/api/integrations/slack/callback/

# MCP server endpoints (each Google Workspace product is a separate server)
GMAIL_MCP_URL
CALENDAR_MCP_URL
DOCS_MCP_URL
SHEETS_MCP_URL
SLACK_MCP_URL
TAVILY_MCP_URL
TAVILY_API_KEY
```

## API surface

```text
# Auth
POST /login/
POST /registration/
POST /api/token/refresh/

# Password recovery / change (each exposed at its short path AND under /api/auth/...)
POST /forgot-password/          POST /api/auth/forgot-password/    # check email exists
POST /verify-otp/               POST /api/auth/verify-otp/         # check recovery credential
POST /reset-password-api/       POST /api/auth/reset-password/     # set new password, rotate credential
POST /otp-generate/             POST /api/auth/otp-generate/       # manually rotate credential (signed in)
POST /change-password/          POST /api/auth/change-password/    # in-app change via current password OR credential

# Threads & messages
GET    /api/list_thread/
GET    /api/thread/<thread_id>/messages/
DELETE /api/thread/<thread_id>/delete/

# Agent (both stream Server-Sent Events: status / token / approval_required / completed / error)
POST /api/chat/                                   # start a new thread
POST /api/thread/<thread_id>/chat/                # continue a thread
POST /api/thread/<thread_id>/tool-approval/        # resume an interrupted approval

# Connected accounts (per service: gmail | calendar | docs | sheets | slack)
GET  /api/integrations/status/
GET  /api/integrations/<service>/connect/
GET  /api/integrations/<service>/callback/
POST /api/integrations/<service>/disconnect/
```

The server-rendered frontend (`/`, `/signin/`, `/register/`,
`/reset-password/`, `/settings/`) drives these same endpoints for sign-in,
registration credential download, password recovery, thread history, chat,
and the connected-accounts page.

## Known limitations / open items

These are called out directly in the codebase / team notes as things still
to do, not yet-hidden bugs:

- **`route_intent`'s `TYPE: MULTI` handling has been removed, not just
  deprecated.** It never had a working destination in the graph — the
  conditional-edge map (`agent/graph/builder.py: create_graph`) only has
  destinations for `general` and the six fixed domains, and
  `ACTION_MCP_TOOL_NAMES` has no `"multi"` entry either. `route_intent` no
  longer reads `query['type']` at all; a message the rewriter tags `TYPE:
  MULTI` is now classified as a single intent exactly like `TYPE: SINGLE`.
  `agent/routing/query_generator.py`'s prompt still asks the LLM to choose
  MULTI or SINGLE, so that half of the signal is generated but silently
  discarded — worth either removing the MULTI option from the prompt or
  wiring up a real destination for it.
- **One tool call per turn per domain agent, and no cross-domain fan-out.**
  A single domain agent can loop through multiple sequential tool calls, but
  there's no support for invoking tools across genuinely different domains
  in a single turn — a multi-domain request (e.g. "check my calendar and
  email the summary to the team") has to be handled as separate turns, since
  the `TYPE: MULTI` path meant to cover this was removed (see above).
- **Test coverage is improving but still uneven.** `core/tests.py` covers
  auth URL resolution, the login serializer, JWT-authenticated access, and
  now the recovery-OTP utilities, registration, and password-reset
  endpoints. `agent/routing/test.py` covers `ACTION_MCP_TOOL_NAMES`/
  `get_mcp_tool_names` domain-and-tool mappings, `_domain_for_intent`, and
  `get_candidate_intents`/`route_intent` decision logic (confident/
  ambiguous/unavailable, thresholds, remapping) against a mocked classifier.
  Still uncovered: async chat streaming end-to-end, token refresh, thread
  isolation, provider OAuth callbacks, MCP JSON-schema sanitization,
  approval resume, and the in-app password-change/OTP-rotation endpoints.
- **`intent_data.CSV` quality determines routing quality.** The NB
  classifier is only as good as this dataset — expanding coverage per
  intent, and watching for near-duplicate phrasing that gets labeled
  inconsistently across intents (e.g. a `slack.search`-worded row and a
  `slack.history`-worded row for what's actually the same request), is the
  main lever for reducing `ambiguous`/`unavailable` fallbacks. The dataset's
  former `general`/`out_of_scope` catch-all labels have been removed (see
  [Intent routing](#intent-routing-nlp-node)); the corresponding dead
  special-casing in `intent_router.py` still needs cleaning up.
- **In-process duplicate-approval tracking** (`sent_tool_call_ids` in
  `agent/graph/approval.py`) is a plain Python `set`, so it does not persist
  across process restarts or scale across multiple worker processes.
- **`google_workspace_mcp/` is vendored, not pinned as a dependency.** It's
  a full copy of a third-party project living in-tree with its own
  `Dockerfile`/`pyproject.toml`, rather than being pulled in as a package or
  submodule — fine for now, but worth deciding deliberately before it drifts
  from upstream.
