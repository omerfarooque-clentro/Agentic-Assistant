# 🤖 Agentic Assistant

A **Django + LangGraph personal operations agent** that orchestrates email, calendars, Google Docs & Sheets, Slack, and web research — dynamically scoped per user to only the integrations and tools that user has connected and authorized.

> **Core Philosophy:** Classify intent and decompose workflows *before* the model ever binds a tool. Route to a minimized, exact set of MCP tools rather than dumping an entire catalog into the context window — and mandate explicit human approval before any state-changing action executes.

---

## 📸 Snapshot

<img width="1362" height="642" alt="image" src="https://github.com/user-attachments/assets/61b21cd8-4027-4e7f-8073-6bf9112f3f72" />

---

## 📑 Table of Contents

- [✨ Key Capabilities](#-key-capabilities)
- [🏗️ System Architecture](#️-system-architecture)
- [🧠 Adaptive Intent Routing & Modular Pipeline](#-adaptive-intent-routing--modular-pipeline)
- [🔄 Multi-Agent Sequential Workflows](#-multi-agent-sequential-workflows)
- [🛡️ Human-in-the-Loop (HITL) Safety & Approval](#️-human-in-the-loop-hitl-safety--approval)
- [🔁 Resilient Multi-Provider LLM Orchestration](#-resilient-multi-provider-llm-orchestration)
- [📊 Observability & Token Intelligence HUD](#-observability--token-intelligence-hud)
- [🔐 Authentication & Zero-Knowledge Credential Recovery](#-authentication--zero-knowledge-credential-recovery)
- [🔌 Integrations & MCP Client Service](#-integrations--mcp-client-service)
- [💬 Slack Identity Resolution Cache](#-slack-identity-resolution-cache)
- [📡 Real-Time SSE Streaming](#-real-time-sse-streaming)
- [📁 Project Layout](#-project-layout)
- [🚀 Quickstart & Setup](#-quickstart--setup)
- [🔧 Environment Configuration](#-environment-configuration)
- [🔌 API Surface](#-api-surface)
- [⚠️ Known Considerations](#️-known-considerations)

---

## ✨ Key Capabilities

### 🧭 Intent-Scoped Tool Routing
- Classifies requests into one of 21 granular intents across 6 domains (`email`, `calendar`, `docs`, `sheets`, `slack`, `research`).
- Dynamically derives exact MCP tool allowlists per turn. If a user only needs to search emails, send/delete tools are completely excluded from the model context.
- Users only ever access tools for integrations they have actively connected and authenticated.

### 🔄 Multi-Agent Workflow Decomposition
- Detects compound multi-domain requests (e.g. *"Check my calendar for tomorrow and email my availability to omer@example.com"*).
- Decomposes requests into a structured multi-step execution plan across distinct domain agents (`calendar` ➔ `email`), maintaining intermediate context and advancing state seamlessly.

### 🛡️ Interactive Human-in-the-Loop Approval
- State-altering operations (sending emails, scheduling meetings, updating spreadsheets, posting Slack messages) trigger a durable graph interrupt.
- Renders rich, interactive approval cards in the UI allowing users to:
  - **Approve & Execute** with verified parameters.
  - **Edit parameters in-place** before executing (e.g. modifying meeting times or email recipients).
  - **Provide natural language revision instructions** to steer the agent.
  - **Cancel** safely with zero state mutations.

### ⚡ Zero-Cost Reference Resolution
- Rule-based anaphora detector identifies whether a query relies on prior conversational turns (`"it"`, `"that"`, `"send it to him"`).
- Standalone queries bypass the query-rewriting LLM call entirely, conserving tokens and cutting latency in half.

### 📊 Comprehensive Observability HUD
- Real-time token tracking (prompt tokens, output tokens, cached tokens, latency per call) persisted per turn in PostgreSQL.
- Interactive Session Intelligence HUD with compact/expanded modes, step breakdowns, and turn navigation.

---

## 🏗️ System Architecture

```
                               ┌───────────────────────────┐
                               │       User Request        │
                               └─────────────┬─────────────┘
                                             │
                                             ▼
                             ┌───────────────────────────────┐
                             │     Adaptive Preprocessing    │
                             │  (Reference & Typo Detection) │
                             └───────────────┬───────────────┘
                                             │
                       ┌─────────────────────┴─────────────────────┐
                       │                                           │
         [Compound / Contextual]                             [Standalone]
                       ▼                                           ▼
         ┌───────────────────────────┐               ┌───────────────────────────┐
         │     Call #1: Planner      │               │     TF-IDF + Naive Bayes  │
         │ (Sequential Workflow Plan)│               │    (Direct Fast Classifier)│
         └─────────────┬─────────────┘               └─────────────┬─────────────┘
                       │                                           │
                       └─────────────────────┬─────────────────────┘
                                             │
                                             ▼
                             ┌───────────────────────────────┐
                             │       Supervisor Router       │
                             │   (Domain & Tool Scoping)     │
                             └───────────────┬───────────────┘
                                             │
                                             ▼
                             ┌───────────────────────────────┐
                             │   Domain Agent (Call #2)      │
                             │ (Scoped strictly to exact MCP)│
                             └───────────────┬───────────────┘
                                             │
                    ┌────────────────────────┴────────────────────────┐
                    │                                                 │
          [Read-Only Action]                                [Mutating Action]
                    ▼                                                 ▼
      ┌───────────────────────────┐                     ┌───────────────────────────┐
      │   Execute Tool Directly   │                     │   HITL Approval Interrupt │
      └─────────────┬─────────────┘                     │    (Durable PG Checkpoint)│
                    │                                   └─────────────┬─────────────┘
                    │                                                 │
                    │                                          [User Decision]
                    │                                                 │
                    │                           ┌─────────────────────┴─────────────────────┐
                    │                           │                                           │
                    │                      [Approved]                                  [Cancelled]
                    │                           ▼                                           ▼
                    │               ┌───────────────────────┐                   ┌───────────────────────┐
                    │               │  Execute Gated Tool   │                   │  Abort / Revert Plan  │
                    │               └───────────┬───────────┘                   └───────────┬───────────┘
                    │                           │                                           │
                    └───────────────────────────┼───────────────────────────────────────────┘
                                                │
                                                ▼
                                    ┌───────────────────────┐
                                    │  More steps in plan?  │
                                    └───────────┬───────────┘
                                                │
                                    ┌───────────┴───────────┐
                                    │                       │
                                  [Yes]                    [No]
                                    ▼                       ▼
                         ┌─────────────────────┐    ┌───────────────┐
                         │ advance_plan node   │    │  Stream END   │
                         │ (Next Domain Agent) │    └───────────────┘
                         └─────────────────────┘
```

The execution loop runs asynchronously under ASGI and streams real-time updates over Server-Sent Events (SSE) via `agent/runner.py:run_agent`.

---

## 🧠 Adaptive Intent Routing & Modular Pipeline

`route_intent()` in [`agent/routing/intent_router.py`](agent/routing/intent_router.py) is architected as a clean, single-responsibility pipeline:

```
route_intent()
│
├── resolve_active_plan()            ── Check if an active workflow has pending steps
├── preprocess_query()               ── Normalize messages, clean envelopes, isolate directives
├── determine_routing_strategy()     ── Evaluate multi-turn references, ambiguity & compound indicators
├── rewrite_or_plan()                ── Optional Call #1: Decompose compound tasks or resolve pronouns
├── classify_intent()                ── Scikit-Learn TF-IDF + MultinomialNB vectorizer
└── validate_prediction()            ── Confidence & margin thresholds against connected integrations
```

### Intent to MCP Tool Matrix

| Domain | Intent | Selected MCP Tools |
|---|---|---|
| **`email`** | `email.search` | `search_gmail_messages`, `get_gmail_message_content`, `get_gmail_thread_content`, `send_gmail_message` |
| | `email.read` | `get_gmail_message_content`, `get_gmail_thread_content`, `search_gmail_messages`, `send_gmail_message` |
| | `email.send` | `send_gmail_message` |
| | `email.draft` | `draft_gmail_message` |
| | `email.forward` | `send_gmail_message` |
| **`calendar`**| `calendar.create` | `manage_event` |
| | `calendar.search` | `get_events`, `manage_event` |
| | `calendar.update` | `manage_event`, `get_events` |
| | `calendar.delete` | `manage_event`, `get_events` |
| | `calendar.availability`| `query_freebusy`, `get_events` |
| **`docs`** | `docs.read` / `docs.summarize` | `get_doc_content`, `get_doc_as_markdown` |
| | `docs.create` | `create_doc` |
| | `docs.update` | `modify_doc_text`, `find_and_replace_doc`, `batch_update_doc` |
| **`sheets`** | `sheets.read` | `read_sheet_values`, `get_spreadsheet_info` |
| | `sheets.write` / `sheets.update` | `modify_sheet_values`, `append_table_rows` |
| **`slack`** | `slack.send` | `slack_send_message`, `slack_create_canvas`, `slack_update_canvas`, `resolve_slack_id` |
| | `slack.draft` | `slack_send_message_draft`, `resolve_slack_id` |
| | `slack.search` | `slack_search_public_and_private`, `slack_search_channels`, `slack_search_users`, `slack_read_user_profile` |
| | `slack.history` | `slack_read_channel`, `slack_read_thread`, `slack_read_canvas`, `slack_read_file`, `slack_get_reactions` |
| **`research`**| `research.search` | `tavily_search` |

---

## 🔄 Multi-Agent Sequential Workflows

When a user provides a complex or compound instruction, the agent automatically organizes it into a sequential plan.

**Example Turn:**
> *"Check my calendar for tomorrow and email my availability to omer@example.com"*

1. **Detection:** `is_compound_multi_domain()` identifies references to both `calendar` and `email`.
2. **Decomposition:** Planner generates a structured execution queue:
   - **Step 1 (`calendar`):** Inspect tomorrow's events and determine open slots via `get_events` / `query_freebusy`.
   - **Step 2 (`email`):** Format availability and dispatch the email to `omer@example.com`.
3. **Execution:** The `calendar_agent` runs first. Upon completing its retrieval, the graph transitions to `advance_plan`, records the availability summary, and transitions to the `email_agent`.
4. **Safety:** The second step (sending the email) routes to human approval before dispatching.

---

## 🛡️ Human-in-the-Loop (HITL) Safety & Approval

Every state-mutating action pauses execution via a LangGraph interrupt and checkpoints conversation state to PostgreSQL.

### Interactive Approval Features
- **Zero Default Spinners:** Clean button states with immediate visual feedback (`Approving…`, `Cancelling…`, `Revising…`) and animated progress track bars.
- **In-Place Field Editing:** Expand the edit drawer on any approval card to alter action parameters directly (e.g. adjust start times, change subject line, edit message text) before approval.
- **Revision Steering:** Type a conversational revision instruction (e.g. *"Make it 30 minutes earlier"* or *"Add Omer as an attendee"*), and the agent updates the proposed action while maintaining context.

---

## 🔁 Resilient Multi-Provider LLM Orchestration

Configured in [`agent/llm/client.py`](agent/llm/client.py) with cross-provider fallbacks:
- **Primary Frontier Model:** Groq (`openai/gpt-oss-120b`) for rapid tool orchestration and agent reasoning.
- **Resilient Fallback:** Google Gemini (`gemini-2.5-flash`) seamlessly catches mid-run failures, rate limits, or network disconnections.
- **Pre-bound Tools:** MCP tools are bound to both provider interfaces upfront, ensuring zero capability degradation during failover.

---

## 📊 Observability & Token Intelligence HUD

Full observability without performance overhead:
- **Real-Time Token Tracking:** Computes prompt, completion, cached token counts, and millisecond latency across both Call #1 and Call #2.
- **Database Persistence:** Metrics are serialized and saved with each message record, enabling historical analysis across threads.
- **Interactive UI HUD:** Real-time token counter in the navigation bar with expandable turn-by-turn breakdowns.

---

## 🔐 Authentication & Zero-Knowledge Credential Recovery

- **JWT Authentication:** Secure stateless access with automated proactive token refresh.
- **Zero-Knowledge Recovery Key:** Avoids vulnerable SMS/Email OTPs. Users receive a high-entropy recovery credential (`PO-XXXX-XXXX-XXXX`) at registration.
- **Cryptographic Rotation:** Using the recovery key automatically invalidates the old credential and issues a freshly hashed key.
- **Encrypted Token Store:** OAuth access and refresh tokens are encrypted at rest using `django-cryptography` (`EncryptedTextField`).

---

## 🔌 Integrations & MCP Client Service

User integrations are isolated in the `MCPIntegration` model:
- **Google Workspace (Gmail, Calendar, Docs, Sheets):** Connects to the vendored `google_workspace_mcp/` server via Model Context Protocol (MCP) HTTP transport. Tokens automatically refresh 2 minutes prior to expiration.
- **Slack:** Validates tokens on startup using `auth.test`.
- **Tavily:** Built-in web research capability for current events and knowledge retrieval.

---

## 💬 Slack Identity Resolution Cache

Slack APIs require opaque channel and user IDs (`C01234567`, `U01234567`) rather than human handles (`#announcements`, `@omer`).
The `SlackResource` cache provides per-user identity mapping:
- Checks local database cache for names and handles.
- Resolves cache misses through Slack Web API and stores the mapping.
- Subsequent references execute with zero external API calls.

---

## 📡 Real-Time SSE Streaming

The `POST /api/chat/` and `POST /api/thread/<id>/chat/` endpoints stream Server-Sent Events:

| Event Type | Payload Data | Description |
|---|---|---|
| `status` | `{ "step": "...", "label": "..." }` | Current processing state ("Consulting Calendar…") |
| `token` | `{ "token": "..." }` | Real-time token-by-token response streaming |
| `approval_required` | `{ "thread_id": 96, "interrupt": { ... } }` | Render interactive approval card for mutating action |
| `thread_name` | `{ "thread_name": "Project Discussion" }` | Real-time thread title derivation |
| `completed` | `{ "result": { ... }, "metrics": { ... } }` | Final response state and turn metrics summary |
| `error` | `{ "message": "..." }` | Structured error messages with graceful disconnect handling |

---

## 📁 Project Layout

```
Agentic-Assistant/
├── accounts/                  # Custom User model & recovery credential security
├── agent/
│   ├── graph/                 # LangGraph state, nodes, approval logic, and builders
│   ├── integrations/          # OAuth flows, token validation & refresh
│   ├── llm/                   # Multi-provider clients, fallbacks, prompts & titles
│   ├── metrics/               # Turn & call metric aggregators and token calculators
│   ├── routing/               # Intent router, reference detector & query generator
│   ├── tools/                 # MCP grouping, Slack resolver & tool discovery
│   ├── models.py              # Per-user MCPIntegration schema
│   ├── runner.py              # Streaming execution orchestrator (ASGI SSE)
│   └── status.py              # Node-to-status messaging map
├── config/                    # Django core, ASGI application & URL routing
├── conversations/             # Thread, Message, and Approval models & REST APIs
├── core/                      # Authentication endpoints, chat serializers, views
├── frontend/                  # Modern server-rendered dashboard, templates & assets
│   ├── static/frontend/       # Vanilla CSS design system & client-side app logic
│   └── templates/frontend/    # Dashboard, auth, settings & integration UI
└── google_workspace_mcp/      # Vendored Google Workspace MCP server
```

---

## 🚀 Quickstart & Setup

### Prerequisites
- Python 3.10+
- PostgreSQL database instance
- Google Cloud Console & Slack App credentials

### 1. Environment Setup
```bash
git clone https://github.com/omerfarooque-clentro/Agentic-Assistant.git
cd Agentic-Assistant

python -m venv my_env
source my_env/bin/activate  # On Windows: my_env\Scripts\activate

pip install -r requirements.txt
```

### 2. Database Migration
```bash
# Ensure PostgreSQL is running and credentials match .env
python manage.py migrate
```

### 3. Run Development Server
```bash
python -m uvicorn config.asgi:application --reload --port 8000
```

---

## 🔧 Environment Configuration

Create a `.env` file in the project root:

```env
# Database Configuration
DB_NAME=agentic_assistant
DB_USER=postgres
DB_PASSWORD=your_postgres_password
DB_HOST=localhost
DB_PORT=5432

# Security & Secrets
DJANGO_SECRET_KEY=your_django_secret_key
DJANGO_DEBUG=True
FIELD_ENCRYPTION_KEY=your_32_byte_base64_encryption_key

# LLM Providers
GROQ_API_KEY=gsk_...
GOOGLE_API_KEY=AIzaSy...

# Google OAuth (Gmail, Calendar, Docs, Sheets)
GOOGLE_CLIENT_ID=your_google_client_id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your_google_client_secret

# Slack App Integration
SLACK_CLIENT_ID=your_slack_client_id
SLACK_CLIENT_SECRET=your_slack_client_secret

# Research & External APIs
TAVILY_API_KEY=tvly-...

# MCP Endpoints (Local or Remote)
GMAIL_MCP_URL=http://127.0.0.1:8001/mcp
CALENDAR_MCP_URL=http://127.0.0.1:8001/mcp
DOCS_MCP_URL=http://127.0.0.1:8001/mcp
SHEETS_MCP_URL=http://127.0.0.1:8001/mcp
```

---

## 🔌 API Surface

### Authentication & Account Security
- `POST /login/` — Authenticate and issue JWT tokens.
- `POST /registration/` — Create account and issue one-time recovery credential.
- `POST /api/token/refresh/` — Proactively rotate expired access tokens.
- `POST /forgot-password/` — Validate user identity for password reset.
- `POST /verify-otp/` — Verify recovery credential.
- `POST /reset-password-api/` — Set new password and rotate recovery credential.
- `POST /otp-generate/` — Manually generate a fresh recovery key.

### Conversations & Messages
- `GET /api/list_thread/` — Retrieve user threads with last message preview.
- `GET /api/thread/<id>/messages/` — Fetch message history with turn metrics.
- `PATCH /api/thread/<id>/rename/` — Inline thread title rename.
- `DELETE /api/thread/<id>/delete/` — Delete thread and associated checkpoints.

### Agent Streaming & Tool Approvals
- `POST /api/chat/` — Initialize new conversation with live SSE stream.
- `POST /api/thread/<id>/chat/` — Stream turn execution on existing thread.
- `POST /api/thread/<id>/tool-approval/` — Submit approval, parameter edits, or rejection.

### Integrations
- `GET /api/integrations/status/` — List connection status of all services.
- `GET /api/integrations/<service>/connect/` — Begin OAuth handshake.
- `GET /api/integrations/<service>/callback/` — Finalize OAuth credentials.
- `POST /api/integrations/<service>/disconnect/` — Revoke and remove service credentials.

---

## ⚠️ Known Considerations

- **MCP Server Separation:** Google Workspace tools run through the vendored `google_workspace_mcp/` server reachable via HTTP MCP transport.
- **In-Memory Duplicate Approval Cache:** Action duplicate tracking uses in-memory sets during a single runtime lifecycle.
- **Slack ID Resolution:** Names mapped to IDs are cached in PostgreSQL; channel renames should be manually cleared if IDs become invalid.
