# 🤖 Agentic Assistant

[#agentic-assistant](#agentic-assistant)

A **Django + LangGraph personal ops agent** that reads and sends email, manages calendars, reads/writes Google Docs and Sheets, and reads/sends Slack messages — scoped per user to only the integrations that user has actually connected.

> **Core idea:** classify what the user wants *before* the LLM ever sees a tool. Route to a small, exact set of MCP tools instead of binding the entire tool catalog on every turn — and require human approval before any state-changing action actually executes.

---

## Snapshot

<img width="1362" height="642" alt="image" src="https://github.com/user-attachments/assets/61b21cd8-4027-4e7f-8073-6bf9112f3f72" />

---

## 📑 Table of Contents

[#table-of-contents](#table-of-contents)

- [Features](#-features)
- [Why Two LLM Calls Per Turn](#-why-two-llm-calls-per-turn)
- [⚡ Latest Update: Metrics, Auto-Naming & Routing Efficiency](#-latest-update-metrics-auto-naming--routing-efficiency)
- [Architecture](#️-architecture)
- [Authentication & Account Recovery](#-authentication--account-recovery)
- [Integrations & MCP Clients](#-integrations--mcp-clients)
- [Intent Routing](#-intent-routing)
- [Slack ID Resolution](#-slack-id-resolution)
- [LangGraph Flow](#-langgraph-flow)
- [Streaming](#-streaming)
- [Human-in-the-Loop Approval](#-human-in-the-loop-approval)
- [Project Layout](#-project-layout)
- [Setup](#-setup)
- [Environment Variables](#-environment-variables)
- [API Surface](#-api-surface)
- [Known Limitations / Open Items](#️-known-limitations--open-items)
- [What This Project Demonstrates](#-what-this-project-demonstrates)

---

## ✨ Features

[#-features](#-features)

### 🧭 Intent-Scoped Tool Routing

[#-intent-scoped-tool-routing](#-intent-scoped-tool-routing)

- Classifies the user's request into one of 21 fine-grained intents across six domains (email, calendar, docs, sheets, slack, research) before any tool is bound to the model.
- Only the exact MCP tools needed for that intent are exposed — never the full domain's tool list, and never another domain's tools.
- A user only ever sees tools for services they've actually connected and enabled.

### ✅ Human-in-the-Loop Approval

[#-human-in-the-loop-approval-1](#-human-in-the-loop-approval-1)

- Every state-changing action (send email, post to Slack, edit a doc/sheet, create a calendar event) pauses the graph and waits for explicit human approval before executing.
- Read-only actions (search, get, list) execute immediately, no approval needed.
- Approval state is checkpointed to Postgres, so a pause survives across the request/response boundary.

### 🔁 Multi-Provider LLM with Fallback

[#-multi-provider-llm-with-fallback](#-multi-provider-llm-with-fallback)

- Primary model: Groq (`gpt-oss-120b`). Automatic fallback to Google Gemini (`gemini-2.5-flash`) if the primary call fails mid-run.
- Tools are bound to both providers up front, so a fallback doesn't mean losing tool access.

### 🔐 Credential-Based Account Recovery

[#-credential-based-account-recovery](#-credential-based-account-recovery)

- No emailed/SMS'd OTP for password recovery — a single high-entropy recovery credential, downloaded once at registration, rotates every time it's used.
- JWT-based auth throughout, with proactive token refresh on the frontend.

### 📡 Real-Time Streaming

[#-real-time-streaming](#-real-time-streaming)

- The entire agent run streams over Server-Sent Events: live status updates ("Working with email…"), token-by-token model output, approval prompts, and the final result — all in one connection.

---

## 🧠 Why Two LLM Calls Per Turn

[#-why-two-llm-calls-per-turn](#-why-two-llm-calls-per-turn)

A single-shot classifier can't resolve conversational context. Given:

```
human: what's the weather today?
agent: <weather answer>
human: okay send it to arsalan and tell i...
```

A bag-of-words classifier looking only at the last message has no way to know "it" refers to the weather answer, or that "send to arsalan" means Slack rather than email.

So the first LLM call rewrites the message into a self-contained instruction before classification ever happens:

```
Raw:      "okay send it to arsalan and tell i..."
Rewritten: "Inform Arsalan on Slack about the weather
            forecast and that I will be working remotely."
```

Only *then* does the TF-IDF/Naive Bayes classifier predict intent — keeping its input clean, and keeping the tool-calling LLM call scoped to a handful of tools instead of the full catalog across five+ MCP servers.

---

## ⚡ Latest Update: Metrics, Auto-Naming & Routing Efficiency

[#-latest-update-metrics-auto-naming--routing-efficiency](#-latest-update-metrics-auto-naming--routing-efficiency)

> Merged via PR #2, `feat/agent-metrics-and-ui-improvements` — a comprehensive upgrade covering token metrics, thread auto-naming, routing efficiency, and UI/DB stability fixes.

### 🧠 Zero-Cost Reference Detection

[#-zero-cost-reference-detection](#-zero-cost-reference-detection)

`agent/routing/reference_detector.py` is a new rule-based (no-LLM) gate in front of the query-rewrite call. It decides whether a message depends on prior conversational context — and therefore needs the rewrite LLM call — using two checks:

- **Pronoun/anaphora regex** — `it, that, this, them, those, these, him, her, his, their, the same, previous, prior, earlier, above, again, instead, also, too, latter, former`.
- **Short action-trigger check** — a message of 4 words or fewer containing a bare trigger word (`yes, send, email, share, post, cancel, confirm, reply, forward`) is treated as context-dependent even without an explicit pronoun (e.g. "yes, send it").

```python
def has_conversational_reference(text: str) -> bool:
    cleaned = text.strip().lower()
    words = cleaned.split()
    if len(words) <= 4 and any(t in words for t in SHORT_ACTION_TRIGGERS):
        return True
    return bool(REFERENCE_PATTERN.search(cleaned))
```

If this returns `False`, the query-rewrite LLM call is skipped entirely and the message goes straight to intent classification — on *any* turn, not just the first message of a thread.

### 📛 Multi-Tier Thread Auto-Naming

[#-multi-tier-thread-auto-naming](#-multi-tier-thread-auto-naming)

The previous dedicated "thread naming" graph node has been **removed** — `agent/graph/nodes.py` now routes straight to `END` instead. In its place:

- `agent/llm/titles.py` appends a `THREAD_NAMING_RULE` instruction to a new thread's first prompt, asking the model to end its own reply with an inline `<suggested_title>...</suggested_title>` tag.
- `StreamTitleFilter` (also in `titles.py`) strips that tag out of the token stream in real time — the user never sees the raw markup — while capturing the extracted title for the frontend.
- If the model doesn't produce a usable tag, naming falls back through a fast, cheap LLM call, and finally to a heuristic (non-LLM) title derivation — so a thread always gets named without ever *requiring* an extra full-cost LLM round-trip.
- A new `PATCH /api/thread/<id>/rename/` endpoint lets a user manually rename a thread, with inline-edit UI in the sidebar.

Net effect: no separate LLM call dedicated purely to naming in the common case — the title rides along on the same response that's already being generated and streamed.

### 📊 Modular Metrics Architecture

[#-modular-metrics-architecture](#-modular-metrics-architecture)

Token/latency tracking was extracted into its own package, `agent/metrics/`:

- **`agent/metrics/collector.py`** — `extract_call_metrics()` reads each provider's real `usage_metadata` when available (input/output/cached tokens, model name), falling back to a character-based heuristic (`len(text) // 4`) when a provider doesn't return usage data. `aggregate_turn_metrics()` rolls per-call metrics up into one turn-level summary.
- **`agent/metrics/types.py`** — typed `CallMetrics` / `TurnMetrics` structures plus context-window constants.
- Metrics are persisted to the database per message (`conversations/migrations/0004_message_metrics.py` adds a `metrics` field to `Message`), not just held in memory for the current SSE stream — so historical turns retain their own token/latency data.

### 🖥️ Session Intelligence HUD

[#-session-intelligence-hud](#-session-intelligence-hud)

The header's token pill was rebuilt into an interactive HUD rather than a static badge:

- Compact and expanded view modes.
- Per-turn navigation and a step-by-step breakdown (see each call in a turn individually, not just the turn total).
- Theme-aware styling and dynamic viewport placement so the popover doesn't get clipped or overflow on smaller screens.

### 🎯 Routing & Dataset Improvements

[#-routing--dataset-improvements](#-routing--dataset-improvements)

- `intent_data.CSV` diversity expanded, with **domain-aware candidate re-normalization** added to `intent_router.py` — candidate intent probabilities are re-weighted relative to the domains actually available to the user, rather than only using the raw top-2 confidence/margin check.
- Duplicate/near-duplicate rows in the general-intent dataset were cleaned up.

### 🧪 Test Coverage Added This Update

[#-test-coverage-added-this-update](#-test-coverage-added-this-update)

This update shipped alongside real new test coverage, not just features:

| File | New tests |
|---|---|
| `agent/tests.py` *(new)* | Reference-detector envelope stripping, standalone-vs-reference routing behavior, metrics extraction structure, turn-metrics aggregation |
| `conversations/tests.py` | Message creation with/without metrics, metrics field in serializer output, cross-user thread isolation, unauthenticated-request rejection, thread creation/listing API |
| `accounts/tests.py` | Recovery-OTP formatting/normalization, hash/verify round-trip, legacy-fallback verification, secure password generation |

This directly closes part of the coverage gap called out below — cross-user thread isolation and metrics-on-serializer behavior are now explicitly tested, which they weren't before.

---

## 🏗️ Architecture

[#️-architecture](#️-architecture)

```
User message
   │
   ▼
NLP node ── LLM call: query rewrite / reference resolution
   │           (skipped on standalone turns — see optimization above)
   ▼
Naive Bayes intent classifier (TF-IDF + MultinomialNB)
   │           21 fine-grained intents across 6 domains
   ▼
Supervisor router
   │           maps intent → domain-scoped subgraph, or falls back
   │           to a general agent if the domain isn't enabled
   ▼
Domain agent ── LLM call: tool-calling, streamed token-by-token
   │           only the exact MCP tools for this intent are bound
   ▼
Tool call?
   ├─ no  → thread naming (inline) → END
   ├─ yes, read-only        → execute → back to domain agent
   └─ yes, write/send action → approval (interrupt) → human
        approves/rejects → execute (or run is cancelled)
```

The entire run streams over one Server-Sent Events connection — `agent/runner.py: run_agent` is an async generator yielding `status`, `token`, `approval_required`, `completed`, and `error` events as the graph executes.

---

## 🔐 Authentication & Account Recovery

[#-authentication--account-recovery](#-authentication--account-recovery)

Login is JWT-based. Password recovery does **not** use an emailed/SMS'd code — it's a single, static, high-entropy recovery credential (e.g. `PO-8F2K-M3NP-X94W`) the user downloads once and keeps safe.

| Flow | What happens |
|---|---|
| **Registration** | Generates + hashes a recovery credential; returns it once, in the response, never stored in plaintext again |
| **Forgot password** | 3-step wizard: verify email → verify credential → set new password + rotate to a new credential |
| **In-app password change** | Accepts current password *or* a valid recovery credential; rotates the credential if the credential was used |
| **Manual rotation** | Signed-in users can rotate their credential on demand, invalidating the old one |

Tokens are encrypted at rest via `EncryptedTextField`.

---

## 🔌 Integrations & MCP Clients

[#-integrations--mcp-clients](#-integrations--mcp-clients)

`MCPIntegration` is the source of truth for what a user can use — one row per `(user, service)`, storing OAuth tokens, scopes, and an `enabled` flag. Nothing downstream loads a tool for a service the user hasn't connected.

- Google-backed tokens (Gmail, Calendar, Docs, Sheets) auto-refresh 2 minutes before expiry.
- Slack tokens are validated via `auth.test` before use; a dead token disables the integration rather than failing mid-conversation.
- Each Google Workspace product is its own MCP server; a vendored `google_workspace_mcp/` server runs separately and is reached over HTTP, not imported directly.

**Tool discovery** is a two-step reduction: fetch only the user's enabled integrations' tools (concurrently), then bucket them by *domain* — so a user is never handed a large, mixed-domain tool list.

---

## 🎯 Intent Routing

[#-intent-routing](#-intent-routing)

A `TfidfVectorizer` + `MultinomialNB` pipeline predicts one of 21 intents across 6 domains, restricted to domains the user has actually enabled. Each prediction carries a **confidence** and **margin** score against fixed thresholds — low-confidence or ambiguous predictions fall back to a plain, tool-less general agent rather than guessing.

Each intent maps to an **exact** allow-list of MCP tool names — e.g. `slack.send` can call the send/schedule/draft/reaction tools plus the Slack ID resolver, but never message-history tools.

---

## 💬 Slack ID Resolution

[#-slack-id-resolution](#-slack-id-resolution)

Slack's write tools need a channel/user **ID**, not a name — but people say "#dev-learning" or "@alice." Resolution is cached:

```
"#dev-learning"
     │
     ▼
Check SlackResource cache (per-user)
     │
     ├─ hit  → return cached ID
     │
     └─ miss → query Slack Web API → cache result → return ID
```

A given name is looked up against Slack at most once per user; every repeat send after that skips the API entirely.

---

## 🕸️ LangGraph Flow

[#-langgraph-flow](#-langgraph-flow)

- Each domain agent re-derives its allowed tool names on every invocation from the current intent — so even inside an already-scoped subgraph, the model only sees tools relevant to *this* specific intent.
- Conversation state is checkpointed to **Postgres**, which is what lets a human-approval interrupt pause mid-run and resume later against the same thread.
- LLM calls use Groq with automatic Gemini fallback; tool bindings are set up for both providers up front.

---

## 📡 Streaming

[#-streaming](#-streaming)

`run_agent` is an async generator streaming five event types over SSE:

| Event | Purpose |
|---|---|
| `status` | Which node is active ("Understanding your request…") |
| `token` | Streamed model output, chunk by chunk |
| `approval_required` | A pending tool call, rendered as an approval card |
| `completed` | Final state + usage metrics (see optimization section above) |
| `error` | Any exception, including clean handling of client disconnects |

---

## ✅ Human-in-the-Loop Approval

[#-human-in-the-loop-approval-2](#-human-in-the-loop-approval-2)

Gated actions (send/draft email, calendar mutations, Docs/Sheets writes, Slack send/draft/canvas) route to a shared approval node that interrupts the graph and surfaces the pending action. On resume, the action either executes or is replaced with a rejection notice — read-only actions never gate.

---

## 📁 Project Layout

[#-project-layout](#-project-layout)

```
accounts/         Custom User model + recovery-credential utilities
agent/
  graph/          LangGraph state, nodes, builder, approval logic
  integrations/   OAuth connect/callback/disconnect, token refresh
  llm/            LLM clients (Groq + Gemini fallback), prompts
  models.py       MCPIntegration (per-user OAuth per service)
  routing/        Query rewriter + TF-IDF/NaiveBayes classifier
  status.py       Node → status-message map for the SSE stream
  tools/          Domain registry, tool discovery/grouping
  runner.py       Async-generator entry point for streaming
config/           Django settings, ASGI/WSGI, URLconf
conversations/    Thread / Message / Approval models + APIs
core/             Auth views, chat + approval endpoints, tests
frontend/         Server-rendered dashboard/settings/login UI
google_workspace_mcp/  Vendored Google Workspace MCP server
mcp_clients/      Per-service MCP server configs
scripts/          Ad-hoc NLP router testing
```

---

## 🚀 Setup

[#-setup](#-setup)

The project targets **async execution** (LangGraph + Postgres checkpointer + SSE), so it runs under ASGI rather than the plain Django dev server.

```bash
python -m venv .venv
source .venv/bin/activate        # .venv\Scripts\activate on Windows
pip install -r requirements.txt

# Postgres must be reachable — the checkpointer depends on it.
python manage.py migrate

python -m uvicorn config.asgi:application --reload
```

Gmail/Calendar/Docs/Sheets tools also require the vendored MCP server in `google_workspace_mcp/` running separately and reachable at the URLs below (default `http://127.0.0.1:8001/mcp`).

---

## 🔧 Environment Variables

[#-environment-variables](#-environment-variables)

```env
# Postgres (checkpointer + Django DB)
DB_USER
DB_PASSWORD
DB_HOST=localhost
DB_PORT=5432
DB_NAME

# LLM providers
GROQ_API_KEY
GOOGLE_API_KEY

# Google OAuth
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
GOOGLE_OAUTH_REDIRECT_URI=http://127.0.0.1:8000/api/integrations/google/callback/

# Slack OAuth
SLACK_CLIENT_ID
SLACK_CLIENT_SECRET
SLACK_OAUTH_REDIRECT_URI=http://127.0.0.1:8000/api/integrations/slack/callback/

# MCP server endpoints
GMAIL_MCP_URL
CALENDAR_MCP_URL
DOCS_MCP_URL
SHEETS_MCP_URL
SLACK_MCP_URL
TAVILY_MCP_URL
TAVILY_API_KEY
```

---

## 🔌 API Surface

[#-api-surface](#-api-surface)

```
# Auth
POST /login/
POST /registration/
POST /api/token/refresh/

# Password recovery / change
POST /forgot-password/          # check email exists
POST /verify-otp/               # check recovery credential
POST /reset-password-api/       # set new password, rotate credential
POST /otp-generate/             # manually rotate credential
POST /change-password/          # in-app change

# Threads & messages
GET    /api/list_thread/
GET    /api/thread/<thread_id>/messages/
DELETE /api/thread/<thread_id>/delete/

# Agent (SSE: status / token / approval_required / completed / error)
POST /api/chat/
POST /api/thread/<thread_id>/chat/
POST /api/thread/<thread_id>/tool-approval/

# Connected accounts
GET  /api/integrations/status/
GET  /api/integrations/<service>/connect/
GET  /api/integrations/<service>/callback/
POST /api/integrations/<service>/disconnect/
```

---

## ⚠️ Known Limitations / Open Items

[#️-known-limitations--open-items](#️-known-limitations--open-items)

- **No cross-domain fan-out.** A single turn can only route to one domain — a request like "check my calendar and email the summary" still has to be handled as separate turns.
- **Test coverage is improving but still uneven.** Auth, JWT, routing-logic, reference-detector behavior, metrics extraction/aggregation, and thread/message isolation are now covered (see [Test Coverage Added This Update](#-test-coverage-added-this-update)). Still uncovered: async chat streaming end-to-end, token refresh, OAuth provider callbacks, approval resume, and the in-app password-change/OTP-rotation endpoints.
- **Heuristic token estimation is a fallback, not the primary source.** `agent/metrics/collector.py` reads real provider `usage_metadata` when available, but falls back to a `len(text) // 4` character-based estimate for providers/responses that don't return usage data — so metrics accuracy varies by provider.
- **`SlackResource` cache has no invalidation.** A renamed/deleted Slack channel or departed user still returns a stale cached ID until a lookup fails.
- **Duplicate-approval tracking is in-process.** It's a plain Python set — doesn't persist across restarts or scale across multiple workers.
- **`google_workspace_mcp/` is vendored, not pinned.** It's a full in-tree copy of a third-party project rather than a package dependency.
- **Routing quality is dataset-bound.** The intent classifier is only as good as `intent_data.CSV` — expanding coverage and resolving near-duplicate phrasing across intents is the main lever for reducing ambiguous/unavailable fallbacks.

---

## 📌 What This Project Demonstrates

[#-what-this-project-demonstrates](#-what-this-project-demonstrates)

```
Django + LangGraph
        │
        ├── Intent classification (TF-IDF + NaiveBayes)
        ├── Domain-scoped tool binding
        ├── Multi-provider LLM w/ fallback
        ├── Postgres-backed durable checkpointing
        ├── Human-in-the-loop approval gates
        ├── Real-time SSE streaming
        └── Token/latency-aware optimization + observability
```

This project focuses on more than wiring up an LLM API call — it's an end-to-end agent system where **routing, tool safety, approval workflows, streaming, and cost-awareness work together**, not as separate bolted-on features.
