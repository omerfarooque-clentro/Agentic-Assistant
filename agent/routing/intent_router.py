"""Production-oriented, tool-agnostic intent routing with adaptive reference detection."""

from __future__ import annotations

import os
from typing import Any, TypedDict

import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import make_pipeline

from agent.metrics import CallMetrics
from agent.routing.reference_detector import (
    extract_message_text,
    has_conversational_reference,
    is_domain_ambiguous,
    detect_explicit_domains,
    clean_conversational_prefix,
    extract_action_directive,
    is_compound_multi_domain,
    EMAIL_ADDRESS_PATTERN,
)
from agent.routing.query_generator import generate_routing_query


CONFIDENCE_THRESHOLD = 0.65
MARGIN_THRESHOLD = 0.20
DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "intent_data.CSV")

# Exact MCP tool names selected for each logical intent. These are intersected
# with the tools actually returned by the user's enabled MCP integrations.
ACTION_MCP_TOOL_NAMES = {
    "email.search": {"search_gmail_messages", "get_gmail_message_content", "get_gmail_thread_content", "send_gmail_message"},
    "email.send": {"send_gmail_message"},
    "email.read": {"get_gmail_message_content", "get_gmail_thread_content", "search_gmail_messages", "send_gmail_message"},
    "email.draft": {"draft_gmail_message"},
    "email.forward": {"send_gmail_message"},
    "calendar.create": {"manage_event"},
    "calendar.search": {"get_events", "manage_event"},
    "calendar.update": {"manage_event", "get_events"},
    "calendar.delete": {"manage_event", "get_events"},
    "calendar.availability": {"query_freebusy", "get_events"},
    "docs.read": {"get_doc_content", "get_doc_as_markdown"},
    "docs.create": {"create_doc"},
    "docs.update": {"modify_doc_text", "find_and_replace_doc", "batch_update_doc"},
    "docs.summarize": {"get_doc_content", "get_doc_as_markdown"},
    "sheets.read": {"read_sheet_values", "get_spreadsheet_info"},
    "sheets.write": {"modify_sheet_values", "append_table_rows"},
    "sheets.update": {"modify_sheet_values", "append_table_rows"},
    "slack.send": {
        "slack_send_message",
        "slack_create_canvas",
        "slack_update_canvas",
        "resolve_slack_id",
    },
    "slack.draft": {"slack_send_message_draft", "resolve_slack_id"},
    "slack.reaction": {"slack_add_reaction", "resolve_slack_id"},
    "slack.schedule": {"slack_schedule_message", "resolve_slack_id"},
    "slack.search": {
        "slack_search_public_and_private",
        "slack_search_channels",
        "slack_search_users",
        "slack_read_user_profile",
        "slack_list_channel_members",
    },
    "slack.history": {
        "slack_read_channel",
        "slack_read_thread",
        "slack_read_canvas",
        "slack_read_file",
        "slack_get_reactions",
        "slack_search_public_and_private",
    },
    "research.search": {"tavily_search"},
}

training_data = pd.read_csv(DATA_FILE).dropna(subset=["text", "intent"])
training_data["intent"] = training_data["intent"].astype(str).str.strip()
model = make_pipeline(TfidfVectorizer(), MultinomialNB())
model.fit(training_data["text"], training_data["intent"])


def _domain_for_intent(intent: str) -> str:
    """Return the availability domain for an intent."""
    return intent.split(".", 1)[0]


class CandidateIntent(TypedDict):
    intent: str
    probability: float


def get_candidate_intents(message: str, available_domains: set[str], top_k: int = 3) -> list[CandidateIntent]:
    """Return the highest-probability intents allowed by the available domains."""
    if top_k <= 0 or not message:
        return []

    cleaned = clean_conversational_prefix(message)
    explicit_domains = detect_explicit_domains(cleaned) & available_domains
    target_domains = explicit_domains if explicit_domains else available_domains

    probabilities = model.predict_proba([cleaned])[0]
    candidates = [
        {
            "intent": intent,
            "probability": float(probability),
        }
        for intent, probability in zip(model.classes_, probabilities)
        if (
            _domain_for_intent(intent) in target_domains
            or _domain_for_intent(intent) == "general"
            or intent in {"general", "general.conversation", "out_of_scope"}
        )
    ]

    # If an explicit domain was identified, prioritize its candidates over generic conversation
    if explicit_domains:
        domain_candidates = [
            c for c in candidates if _domain_for_intent(c["intent"]) in explicit_domains
        ]
        if domain_candidates:
            candidates = domain_candidates

    sorted_candidates = sorted(candidates, key=lambda candidate: candidate["probability"], reverse=True)[:top_k]

    if explicit_domains and sorted_candidates:
        domain_total = sum(c["probability"] for c in sorted_candidates)
        if domain_total > 0:
            for c in sorted_candidates:
                c["probability"] = float(c["probability"] / domain_total)

    return sorted_candidates


def get_mcp_tool_names(intent: str) -> set[str]:
    """Return exact MCP names allowed for an intent."""
    return set(ACTION_MCP_TOOL_NAMES.get(intent, set()))


class RoutingResult(TypedDict, total=False):
    intent: str
    domain: str
    confidence: float
    margin: float
    status: str
    call_metrics: CallMetrics | None
    plan: list | None


def resolve_active_plan(plan: list | None) -> RoutingResult | None:
    """Check if an active multi-step plan has pending or in-progress steps."""
    if not plan:
        return None
    pending_step = next(
        (s for s in plan if isinstance(s, dict) and s.get("status") in ("pending", "in_progress")),
        None,
    )
    if not pending_step:
        return None

    pending_step["status"] = "in_progress"
    intent = pending_step.get("intent", "general")
    domain = pending_step.get("domain", _domain_for_intent(intent))
    return {
        "intent": intent,
        "domain": domain,
        "confidence": 1.0,
        "margin": 1.0,
        "status": "confident",
        "call_metrics": None,
        "plan": plan,
    }


def preprocess_query(message: Any) -> tuple[list[Any], str, str]:
    """Normalize input messages and extract plain text and action directives."""
    if isinstance(message, (list, tuple)):
        message_list = list(message)
    elif message:
        message_list = [message]
    else:
        message_list = []

    latest_text = extract_message_text(message_list[-1], strip_envelope=True) if message_list else ""
    routed_query = latest_text

    directive = extract_action_directive(latest_text)
    if directive and directive != latest_text:
        routed_query = directive

    return message_list, latest_text, routed_query


def determine_routing_strategy(
    latest_text: str,
    routed_query: str,
    message_list: list[Any],
    available_domains: set[str],
) -> bool:
    """Determine whether the query requires LLM query rewriting or multi-agent planning."""
    has_ref = has_conversational_reference(latest_text)
    is_domain_ambig = is_domain_ambiguous(routed_query)
    is_multi_turn = len(message_list) > 1
    is_compound = is_compound_multi_domain(latest_text, available_domains)

    return (is_multi_turn and has_ref) or is_domain_ambig or is_compound


def rewrite_or_plan(
    message: Any,
    available_domains: set[str],
    default_query: str,
) -> tuple[str, CallMetrics | None, list[dict] | None]:
    """Call LLM query generator to rewrite contextual queries or generate a multi-step workflow plan."""
    rewrite_result = generate_routing_query(message, available_domains=available_domains)
    call_metrics = rewrite_result.get("metrics")

    # Multi-step workflow plan decomposition
    if rewrite_result.get("type") == "MULTI" and rewrite_result.get("steps"):
        generated_plan = []
        for idx, step in enumerate(rewrite_result["steps"]):
            s_domain = step["domain"]
            s_query = step["query"]
            s_candidates = get_candidate_intents(s_query, available_domains={s_domain})
            s_intent = s_candidates[0]["intent"] if s_candidates else f"{s_domain}.default"
            generated_plan.append({
                "id": idx + 1,
                "domain": s_domain,
                "intent": s_intent,
                "description": s_query,
                "status": "in_progress" if idx == 0 else "pending",
                "result_summary": None,
            })
        print(f"Generated multi-agent plan: {generated_plan}")
        return rewrite_result["steps"][0]["query"], call_metrics, generated_plan

    routed_query = rewrite_result.get("query", default_query)
    return routed_query, call_metrics, None


def classify_intent(query: str, available_domains: set[str]) -> list[CandidateIntent]:
    """Classify the query using Naive Bayes vectorizer constrained to available domains."""
    return get_candidate_intents(query, available_domains=available_domains, top_k=2)


def validate_prediction(
    candidates: list[CandidateIntent],
    available_domains: set[str],
    call_metrics: CallMetrics | None,
    routed_query: str,
) -> RoutingResult:
    """Evaluate candidate intents against thresholds and compute prediction metadata."""
    if not candidates:
        return {
            "intent": "general",
            "domain": "general",
            "confidence": 0.0,
            "margin": 0.0,
            "status": "unavailable",
            "call_metrics": call_metrics,
        }

    prediction = candidates[0]["intent"]
    confidence = candidates[0]["probability"]
    second_probability = candidates[1]["probability"] if len(candidates) > 1 else 0.0
    margin = confidence - second_probability

    if prediction in {"out_of_scope"}:
        prediction = "research.search"

    domain = _domain_for_intent(prediction)

    if domain == "general":
        status = "confident" if confidence >= CONFIDENCE_THRESHOLD else "ambiguous"
    elif domain not in available_domains:
        status = "unavailable"
    elif confidence >= CONFIDENCE_THRESHOLD and margin >= MARGIN_THRESHOLD:
        status = "confident"
    else:
        status = "ambiguous"

    print(f"Routed query: {routed_query}, Candidates: {candidates}")
    return {
        "intent": prediction,
        "domain": domain,
        "confidence": confidence,
        "margin": margin,
        "status": status,
        "call_metrics": call_metrics,
    }


def route_intent(message: Any, available_domains: set[str], plan: list | None = None) -> RoutingResult:
    """Classify intent adaptively using rule-based reference detection and modular routing stages."""
    # 1. Resolve active plan queue if pending steps exist
    plan_result = resolve_active_plan(plan)
    if plan_result:
        return plan_result

    # 2. Preprocess message and extract query text
    message_list, latest_text, routed_query = preprocess_query(message)
    call_metrics: CallMetrics | None = None

    # 3. Determine whether rewriting / multi-step planning is required
    should_rewrite = determine_routing_strategy(latest_text, routed_query, message_list, available_domains)
    if should_rewrite:
        routed_query, call_metrics, generated_plan = rewrite_or_plan(message, available_domains, routed_query)
        if generated_plan:
            first_step = generated_plan[0]
            return {
                "intent": first_step["intent"],
                "domain": first_step["domain"],
                "confidence": 1.0,
                "margin": 1.0,
                "status": "confident",
                "call_metrics": call_metrics,
                "plan": generated_plan,
            }

    # 4. Classify intent
    candidates = classify_intent(routed_query, available_domains=available_domains)

    # 5. Disambiguation fallback: only if no candidate matched in available domains for multi-turn
    is_multi_turn = len(message_list) > 1
    has_ref = has_conversational_reference(latest_text)
    if is_multi_turn and not has_ref and call_metrics is None and not candidates:
        rewritten_query, call_metrics, _ = rewrite_or_plan(message, available_domains, routed_query)
        if rewritten_query and rewritten_query != routed_query:
            routed_query = rewritten_query
            candidates = classify_intent(routed_query, available_domains=available_domains)

    # 6. Validate prediction and return result
    return validate_prediction(candidates, available_domains, call_metrics, routed_query)
