"""Production-oriented, tool-agnostic intent routing for the agent."""

from __future__ import annotations

import os
from typing import Any, TypedDict

import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import make_pipeline
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
    "calendar.availability": {"query_freebusy"},
    "docs.read": {"get_doc_content", "get_doc_as_markdown"},
    "docs.create": {"create_doc"},
    "docs.update": {"modify_doc_text", "find_and_replace_doc", "batch_update_doc"},
    "docs.summarize": {"get_doc_content", "get_doc_as_markdown"},
    "sheets.read": {"read_sheet_values", "get_spreadsheet_info", },
    "sheets.write": {"modify_sheet_values", "append_table_rows"},
    "sheets.update": {"modify_sheet_values", "append_table_rows"},
    "slack.send": {
        "slack_send_message",
        "slack_schedule_message",
        "slack_send_message_draft",
        "slack_add_reaction",
        "slack_create_canvas",
        "slack_update_canvas",
        "slack_search_public_and_private",
        "slack_search_users"
    },
    "slack.search": {
        "slack_search_public",
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
    },
    # Tavily exposes tavily_search; search_custom is retained for the bundled
    # Google Custom Search MCP when that provider is configured as research.
    "research.search": {"tavily_search", "search_custom"},
    "general": {"tavily_search", "search_custom"},
    "out_of_scope": set(),
    "multi": {
         "tavily_search",
            "search_gmail_messages",
            "get_gmail_message_content",
            "get_gmail_thread_content",
            "get_doc_content",
            "get_doc_as_markdown",
            "read_sheet_values",
            "get_spreadsheet_info",
            "get_events",
            "tavily_search",
            "search_custom",
            "draft_gmail_message",
    },
}

training_data = pd.read_csv(DATA_FILE).dropna(subset=["text", "intent"])
model = make_pipeline(TfidfVectorizer(), MultinomialNB())
model.fit(training_data["text"], training_data["intent"])


def _domain_for_intent(intent: str) -> str:
    """Return the availability domain for an intent."""
    return intent.split(".", 1)[0]

class CandidateIntent(TypedDict):
    intent: str
    probability: float


def get_candidate_intents(message: str,available_domains: set[str],top_k: int = 3,) -> list[CandidateIntent]:
    
    """Return the highest-probability intents allowed by the available domains."""
    if top_k <= 0:
        return []

    probabilities = model.predict_proba([message])[0]
    candidates = [
        {
            "intent": intent, 
            "probability": float(probability)
        }

        for intent, probability in zip(model.classes_, probabilities)

        if (_domain_for_intent(intent) in available_domains or intent in {"general", "out_of_scope"})
    ]

    return sorted(candidates, key=lambda candidate: candidate["probability"], reverse=True)[:top_k]


def get_mcp_tool_names(intent: str) -> set[str]:
    """Return exact MCP names allowed for an intent."""
    return set(ACTION_MCP_TOOL_NAMES.get(intent, set()))


class RoutingResult(TypedDict):
    intent: str
    domain: str
    confidence: float
    margin: float
    status: str
    

def route_intent(message: str, available_domains: set[str]) -> RoutingResult:
    """Classify a message and report confidence, ambiguity, and availability."""

    query = generate_routing_query(message)

    if query['type'] == 'MULTI':
        return {
            "intent": "multi",
            "domain": "multi",
            "confidence": 1.0,
            "margin": 1.0,
            "status": "confident",
        }

    message = query['query']
    print(f"Routing message: {message}")

    candidates = get_candidate_intents(message, available_domains=available_domains, top_k=2)

    if not candidates:
        return {
            "intent": "general",
            "domain": "general",
            "confidence": 0.0,
            "margin": 0.0,
            "status": "unavailable",
        }
    
    
    prediction = candidates[0]["intent"]
    confidence = candidates[0]["probability"]
    second_probability = candidates[1]["probability"] if len(candidates) > 1 else 0.0
    margin = confidence - second_probability
   
    if prediction in {"general", "out_of_scope"}:
        prediction = "research.search"
   
    domain = _domain_for_intent(prediction)

    if domain not in available_domains:
        status = "unavailable"
    elif confidence >= CONFIDENCE_THRESHOLD and margin >= MARGIN_THRESHOLD:
        status = "confident"
    else:
        status = "ambiguous"

    print(f"Routing result for message: {message}")
    print(f"Prediction: {prediction}, Domain: {domain}, Confidence: {confidence:.2f}, Margin: {margin:.2f}, Status: {status}")

    return {
        "intent": prediction,
        "domain": domain,
        "confidence": confidence,
        "margin": margin,
        "status": status,
    }

 

 
