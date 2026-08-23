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
  - [Integrations layer](#integrations-layer)
  - [MCP clients](#mcp-clients)
  - [Tool discovery & domain grouping](#tool-discovery--domain-grouping)
  - [Intent routing (NLP node)](#intent-routing-nlp-node)
  - [LangGraph graph](#langgraph-graph)
  - [Human-in-the-loop approval](#human-in-the-loop-approval)
  - [Conversations & threads](#conversations--threads)
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
Domain agent ── (LLM call #2: tool-calling)
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
2. Flags whether the message actually contains **multiple distinct actions
   across domains** (`TYPE: MULTI`) — in which case intent classification is
   skipped and the message is routed straight to a general multi-domain
   agent with a broader (but still curated) tool set, rather than forcing it
   into one of the fine-grained single-intent buckets.

Only after that rewrite does the TF-IDF/Naive Bayes model (`intent_router.py`)
classify intent. This costs one extra LLM round-trip per turn, but it keeps
the classifier's input clean and — more importantly — keeps the *second*
LLM call (the one that can actually call tools) scoped to a handful of tools
instead of the entire tool catalog across five+ MCP servers, which is the
larger token cost.

## Architecture

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

> ⚠️ **Tokens are currently stored in plaintext** (`access_token` /
> `refresh_token` are plain `TextField`s). Encrypting these at rest is called
> out as a pre-production requirement — see
> [Known limitations](#known-limitations--open-items).

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
  `agent/routing/data/intent_data.CSV` (fine-grained intents such as
  `email.send`, `email.read`, `email.draft`, `calendar.create`,
  `calendar.availability`, `docs.update`, `sheets.write`, `slack.send`,
  `slack.search`, `slack.history`, `research.search`, plus `general` /
  `out_of_scope`).
- `get_candidate_intents` restricts predictions to domains the user actually
  has enabled (`available_domains`), so the classifier can never route to a
  domain with no tools behind it.
- `route_intent` takes the top-2 candidates, computes a **confidence** (top
  probability) and **margin** (gap to the second candidate), and returns a
  `status` of `confident`, `ambiguous`, or `unavailable` against fixed
  thresholds (`CONFIDENCE_THRESHOLD = 0.65`, `MARGIN_THRESHOLD = 0.20`).
  Low-confidence or unavailable-domain predictions fall back to the general
  research agent rather than guessing.
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
  Gemini** (`gemini-2.0-flash`) via LangChain's `.with_fallbacks(...)`
  (`agent/llm/client.py`). Tools are bound to both providers up front
  (`bind_tools_with_fallback`) so a mid-run provider failure doesn't drop
  tool access.
- Conversation state is checkpointed to **Postgres** via
  `langgraph-checkpoint-postgres` (`AsyncPostgresSaver` over an
  `AsyncConnectionPool`), which is what makes the human-approval interrupt
  durable across the request/response boundary — the graph can pause mid-run
  and resume later against the same `thread_id`.

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
  the conversation has more than one exchange).
- `Message` — belongs to a thread, has a `role` (`user`/`assistant`/etc.)
  and `content`.
- `Approval` — records the domain and outcome of a human-in-the-loop
  approval decision against a thread/message.

`accounts/models.py` extends Django's `AbstractUser` with `created_at` /
`updated_at`; auth is JWT-based (`djangorestframework_simplejwt`).

---

## Project layout

```
accounts/         Custom Django User model, registration/login
agent/
  graph/          LangGraph state, node functions, graph builder, approval
  integrations/   OAuth connect/callback/disconnect/status views, token refresh
  llm/            LLM clients (Groq + Gemini fallback), prompt templates
  migrations/
  models.py       MCPIntegration (per-user OAuth credentials per service)
  routing/        NLP query rewriter + TF-IDF/NaiveBayes intent classifier
  tools/          Domain registry, per-user tool discovery/grouping
  runner.py       Entry point that assembles tools + graph and invokes it
config/           Django project settings, ASGI/WSGI, root URLconf
conversations/    Thread / Message / Approval models, thread & message APIs
core/             Registration/login views, chat + approval endpoint views
frontend/         Server-rendered dashboard/settings/login/register UI
mcp_clients/      Per-service MCP server configs and standalone test clients
scripts/          Ad-hoc NLP router testing script
```

## Setup

The project targets **async execution** (LangGraph + Postgres checkpointer),
so it should be run under ASGI rather than the plain Django dev server.

```bash
python -m venv .venv
source .venv/bin/activate        # .venv\Scripts\activate on Windows
pip install -r requirements.txt

# Postgres must be reachable — the checkpointer setup depends on it.
python manage.py migrate

python -m uvicorn config.asgi:application --reload
```

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

# Threads & messages
GET  /api/list_thread/
GET  /api/thread/<thread_id>/messages/
POST /api/thread/<thread_id>/messages/

# Agent
POST /api/thread/<thread_id>/chat/
POST /api/thread/<thread_id>/action-email/     # resume an interrupted approval

# Connected accounts (per service: gmail | calendar | docs | sheets | slack)
GET  /api/integrations/status/
GET  /api/integrations/<service>/connect/
GET  /api/integrations/<service>/callback/
POST /api/integrations/<service>/disconnect/
```

The server-rendered frontend (`/`, `/signin/`, `/register/`, `/settings/`)
drives these same endpoints for sign-in, thread history, chat, and the
connected-accounts page.

## Known limitations / open items

These are called out directly in the codebase / team notes as things still
to do, not yet-hidden bugs:

- **OAuth tokens are stored in plaintext** in `MCPIntegration.access_token`
  / `refresh_token`. Encrypting these at rest (and adding provider token
  revocation on disconnect) is required before production use.
- **No automated tests yet** for async chat, token refresh, thread
  isolation, provider OAuth callbacks, MCP JSON-schema sanitization, or
  approval resume — these are currently only manually/CI-checked via
  `python manage.py check` and `compileall`.
- **One tool call per turn per domain agent.** A single domain agent can
  loop through multiple sequential tool calls, but there's no support for
  invoking tools across genuinely different domains in a single model
  turn — cross-domain requests are handled instead by the `MULTI` path in
  the query generator, which routes to a broader (not domain-scoped)
  tool set rather than true parallel multi-domain tool calls.
- **Streaming is not yet implemented.** The frontend currently polls for a
  pending response rather than consuming streamed agent events; this is a
  planned change once the backend streaming contract is finalized.
- **`intent_data.CSV` quality determines routing quality.** The NB
  classifier is only as good as this dataset — expanding coverage per
  intent (especially near the `general`/`out_of_scope`/domain-specific
  boundary) is the main lever for reducing `ambiguous`/`unavailable`
  fallbacks.
- **In-process duplicate-approval tracking** (`sent_tool_call_ids` in
  `agent/graph/approval.py`) is a plain Python `set`, so it does not persist
  across process restarts or scale across multiple worker processes.
