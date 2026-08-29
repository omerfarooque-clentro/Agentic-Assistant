"""
tests.py

Full unittest coverage for the intent-routing NLP node
(agent.routing.intent_router). No LLM calls are made anywhere in this file —
`generate_routing_query` and `get_candidate_intents` are always mocked with
hardcoded outputs, so tests are fast, deterministic, and free.

Note on MULTI: the "MULTI" query-type branch in `route_intent` is deprecated
and intentionally NOT tested here. Every mocked `generate_routing_query`
return value below uses `"type": "SINGLE"`.

Run:
    python -m unittest tests.py -v
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

# Ensure workspace root is in sys.path for direct execution
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

from agent.routing.intent_router import (
    ACTION_MCP_TOOL_NAMES,
    CONFIDENCE_THRESHOLD,
    MARGIN_THRESHOLD,
    _domain_for_intent,
    get_candidate_intents,
    get_mcp_tool_names,
    route_intent,
    model,
)
from agent.tools.domain_registry import (
    GMAIL_TOOL_NAMES,
    CALENDAR_TOOL_NAMES,
    DOCS_TOOL_NAMES,
    SHEETS_TOOL_NAMES,
    DOMAIN_TOOL_NAMES,
)

# ==============================================================================
# Shared fixtures: the full, exhaustive intent list per domain.
# If you add/remove an intent in the router, update this dict — every test
# below that claims "all intents" is driven off it.
# ==============================================================================
DOMAIN_INTENTS = {
    "email": ["email.search", "email.send", "email.read", "email.draft", "email.forward"],
    "calendar": ["calendar.create", "calendar.search", "calendar.update", "calendar.delete", "calendar.availability"],
    "docs": ["docs.read", "docs.create", "docs.update", "docs.summarize"],
    "sheets": ["sheets.read", "sheets.write", "sheets.update"],
    "slack": ["slack.send", "slack.search", "slack.history"],
    "research": ["research.search"],
}
ALL_ACTIVE_DOMAINS = set(DOMAIN_INTENTS.keys())
ALL_INTENTS = [intent for intents in DOMAIN_INTENTS.values() for intent in intents]


def _query(text: str) -> dict:
    """Build a well-formed SINGLE-type generate_routing_query() mock return value."""
    return {"type": "SINGLE", "query": text}


# ==============================================================================
# 1. Domain & Tool Definition Tests (All Domains, All Tools)
# ==============================================================================
class TestDomainAndToolDefinitions(unittest.TestCase):
    """Verifies that all expected domains and tools are registered and mapped properly."""

    ALL_DOMAINS = {"email", "calendar", "docs", "sheets", "slack", "research"}

    def test_all_domains_present_in_action_mcp_tool_names(self):
        registered_domains = {_domain_for_intent(intent) for intent in ACTION_MCP_TOOL_NAMES}
        self.assertEqual(registered_domains, self.ALL_DOMAINS)

    def test_all_domain_intents_registered(self):
        """Every intent in DOMAIN_INTENTS (our exhaustive fixture) must be a real, mapped intent."""
        for domain, intents in DOMAIN_INTENTS.items():
            for intent in intents:
                with self.subTest(domain=domain, intent=intent):
                    self.assertIn(intent, ACTION_MCP_TOOL_NAMES)
                    self.assertEqual(_domain_for_intent(intent), domain)
                    self.assertTrue(len(get_mcp_tool_names(intent)) > 0)

    def test_email_domain_intents_and_tools(self):
        email_intents = {
            "email.search": {"search_gmail_messages", "get_gmail_message_content", "get_gmail_thread_content", "send_gmail_message"},
            "email.send": {"send_gmail_message"},
            "email.read": {"get_gmail_message_content", "get_gmail_thread_content", "search_gmail_messages", "send_gmail_message"},
            "email.draft": {"draft_gmail_message"},
            "email.forward": {"send_gmail_message"},
        }
        for intent, expected_tools in email_intents.items():
            with self.subTest(intent=intent):
                self.assertIn(intent, ACTION_MCP_TOOL_NAMES)
                self.assertEqual(get_mcp_tool_names(intent), expected_tools)

    def test_calendar_domain_intents_and_tools(self):
        calendar_intents = {
            "calendar.create": {"manage_event"},
            "calendar.search": {"get_events", "manage_event"},
            "calendar.update": {"manage_event", "get_events"},
            "calendar.delete": {"manage_event", "get_events"},
            "calendar.availability": {"query_freebusy"},
        }
        for intent, expected_tools in calendar_intents.items():
            with self.subTest(intent=intent):
                self.assertIn(intent, ACTION_MCP_TOOL_NAMES)
                self.assertEqual(get_mcp_tool_names(intent), expected_tools)

    def test_docs_domain_intents_and_tools(self):
        docs_intents = {
            "docs.read": {"get_doc_content", "get_doc_as_markdown"},
            "docs.create": {"create_doc"},
            "docs.update": {"modify_doc_text", "find_and_replace_doc", "batch_update_doc"},
            "docs.summarize": {"get_doc_content", "get_doc_as_markdown"},
        }
        for intent, expected_tools in docs_intents.items():
            with self.subTest(intent=intent):
                self.assertIn(intent, ACTION_MCP_TOOL_NAMES)
                self.assertEqual(get_mcp_tool_names(intent), expected_tools)

    def test_sheets_domain_intents_and_tools(self):
        sheets_intents = {
            "sheets.read": {"read_sheet_values", "get_spreadsheet_info"},
            "sheets.write": {"modify_sheet_values", "append_table_rows"},
            "sheets.update": {"modify_sheet_values", "append_table_rows"},
        }
        for intent, expected_tools in sheets_intents.items():
            with self.subTest(intent=intent):
                self.assertIn(intent, ACTION_MCP_TOOL_NAMES)
                self.assertEqual(get_mcp_tool_names(intent), expected_tools)

    def test_slack_domain_intents_and_tools(self):
        slack_intents = {
            "slack.send": {
                "slack_send_message", "slack_schedule_message", "slack_send_message_draft",
                "slack_add_reaction", "slack_create_canvas", "slack_update_canvas",
                "slack_search_public_and_private", "slack_search_users",
            },
            "slack.search": {
                "slack_search_public", "slack_search_public_and_private", "slack_search_channels",
                "slack_search_users", "slack_read_user_profile", "slack_list_channel_members",
            },
            "slack.history": {
                "slack_read_channel", "slack_read_thread", "slack_read_canvas", "slack_read_file",
                "slack_get_reactions", "slack_search_public", "slack_search_public_and_private",
            },
        }
        for intent, expected_tools in slack_intents.items():
            with self.subTest(intent=intent):
                self.assertIn(intent, ACTION_MCP_TOOL_NAMES)
                self.assertEqual(get_mcp_tool_names(intent), expected_tools)

    def test_research_domain_intents_and_tools(self):
        expected_tools = {"tavily_search", "search_custom"}
        self.assertIn("research.search", ACTION_MCP_TOOL_NAMES)
        self.assertEqual(get_mcp_tool_names("research.search"), expected_tools)

    #def test_general_and_out_of_scope_tools(self):
        #self.assertEqual(get_mcp_tool_names("general"), {"tavily_search", "search_custom"})
        #self.assertEqual(get_mcp_tool_names("out_of_scope"), set())

    def test_unknown_or_empty_intent_returns_empty_set(self):
        self.assertEqual(get_mcp_tool_names("unknown_intent"), set())
        self.assertEqual(get_mcp_tool_names("email.unknown"), set())
        self.assertEqual(get_mcp_tool_names(""), set())
        self.assertEqual(get_mcp_tool_names("   "), set())

    def test_get_mcp_tool_names_returns_defensive_copy(self):
        tools = get_mcp_tool_names("email.send")
        tools.add("fake_tool_name")
        self.assertNotIn("fake_tool_name", get_mcp_tool_names("email.send"))
        self.assertNotIn("fake_tool_name", ACTION_MCP_TOOL_NAMES["email.send"])

    def test_all_mapped_tools_are_non_empty_strings(self):
        for intent, tools in ACTION_MCP_TOOL_NAMES.items():
            self.assertIsInstance(tools, set)
            for tool in tools:
                self.assertIsInstance(tool, str)
                self.assertTrue(len(tool) > 0)
                self.assertEqual(tool, tool.strip())

    def test_domain_registry_consistency(self):
        for intent, tools in ACTION_MCP_TOOL_NAMES.items():
            domain = _domain_for_intent(intent)
            if domain in DOMAIN_TOOL_NAMES:
                for tool in tools:
                    self.assertIn(
                        tool, DOMAIN_TOOL_NAMES[domain],
                        f"Tool '{tool}' in intent '{intent}' not found in domain_registry for '{domain}'",
                    )


# ==============================================================================
# 2. Helper Function Tests (_domain_for_intent)
# ==============================================================================
class TestHelperFunctions(unittest.TestCase):

    def test_domain_for_intent_all_fixture_intents(self):
        for domain, intents in DOMAIN_INTENTS.items():
            for intent in intents:
                with self.subTest(intent=intent):
                    self.assertEqual(_domain_for_intent(intent), domain)

    def test_domain_for_intent_general_and_out_of_scope(self):
        self.assertEqual(_domain_for_intent("general"), "general")
        self.assertEqual(_domain_for_intent("out_of_scope"), "out_of_scope")

    def test_domain_for_intent_edge_cases(self):
        self.assertEqual(_domain_for_intent("email.messages.search"), "email")
        self.assertEqual(_domain_for_intent("slack."), "slack")
        self.assertEqual(_domain_for_intent(".docs"), "")
        self.assertEqual(_domain_for_intent("custom_single_token"), "custom_single_token")
        self.assertEqual(_domain_for_intent(""), "")


# ==============================================================================
# 3. Candidate Extraction Tests (Filtering & Ranking)
# ==============================================================================
class TestCandidateExtraction(unittest.TestCase):

    @patch("agent.routing.intent_router.model")
    def test_get_candidate_intents_filters_unavailable_domains(self, mock_model):
        mock_model.classes_ = np.array([
            "email.send", "slack.send", "docs.create", "sheets.write", "calendar.create", "general", "out_of_scope"
        ])
        mock_model.predict_proba.return_value = np.array([[0.35, 0.25, 0.15, 0.10, 0.08, 0.05, 0.02]])

        candidates = get_candidate_intents("Test query", available_domains={"email", "docs"}, top_k=5)
        intents = [c["intent"] for c in candidates]

        self.assertIn("email.send", intents)
        self.assertIn("docs.create", intents)
        self.assertIn("general", intents)
        self.assertIn("out_of_scope", intents)
        self.assertNotIn("slack.send", intents)
        self.assertNotIn("sheets.write", intents)
        self.assertNotIn("calendar.create", intents)

    @patch("agent.routing.intent_router.model")
    def test_get_candidate_intents_each_domain_isolation(self, mock_model):
        domains = list(DOMAIN_INTENTS.keys())
        top_intent_per_domain = [DOMAIN_INTENTS[d][0] for d in domains]
        mock_model.classes_ = np.array(top_intent_per_domain)
        probs = np.linspace(0.30, 0.05, num=len(top_intent_per_domain))
        mock_model.predict_proba.return_value = np.array([probs])

        for target_domain in domains:
            with self.subTest(domain=target_domain):
                candidates = get_candidate_intents("Query", available_domains={target_domain}, top_k=10)
                for cand in candidates:
                    intent_domain = _domain_for_intent(cand["intent"])
                    self.assertIn(intent_domain, {target_domain, "general", "out_of_scope"})

    @patch("agent.routing.intent_router.model")
    def test_get_candidate_intents_empty_available_domains(self, mock_model):
        mock_model.classes_ = np.array(["email.send", "slack.send", "general", "out_of_scope"])
        mock_model.predict_proba.return_value = np.array([[0.60, 0.20, 0.15, 0.05]])

        candidates = get_candidate_intents("Query", available_domains=set(), top_k=5)
        intents = [c["intent"] for c in candidates]

        self.assertNotIn("email.send", intents)
        self.assertNotIn("slack.send", intents)
        self.assertIn("general", intents)
        self.assertIn("out_of_scope", intents)

    @patch("agent.routing.intent_router.model")
    def test_get_candidate_intents_top_k_edge_cases(self, mock_model):
        mock_model.classes_ = np.array(["email.send", "email.read", "general"])
        mock_model.predict_proba.return_value = np.array([[0.50, 0.30, 0.20]])

        self.assertEqual(get_candidate_intents("Query", available_domains={"email"}, top_k=0), [])
        self.assertEqual(get_candidate_intents("Query", available_domains={"email"}, top_k=-1), [])

        one_cand = get_candidate_intents("Query", available_domains={"email"}, top_k=1)
        self.assertEqual(len(one_cand), 1)
        self.assertEqual(one_cand[0]["intent"], "email.send")

        all_cands = get_candidate_intents("Query", available_domains={"email"}, top_k=10)
        self.assertEqual(len(all_cands), 3)

    @patch("agent.routing.intent_router.model")
    def test_get_candidate_intents_sorting_order_and_types(self, mock_model):
        mock_model.classes_ = np.array(["sheets.read", "sheets.write", "sheets.update"])
        mock_model.predict_proba.return_value = np.array([[0.15, 0.60, 0.25]])

        candidates = get_candidate_intents("Query", available_domains={"sheets"}, top_k=3)
        self.assertEqual(len(candidates), 3)
        self.assertEqual(candidates[0]["intent"], "sheets.write")
        self.assertEqual(candidates[1]["intent"], "sheets.update")
        self.assertEqual(candidates[2]["intent"], "sheets.read")

        for i in range(len(candidates) - 1):
            self.assertGreaterEqual(candidates[i]["probability"], candidates[i + 1]["probability"])
            self.assertIsInstance(candidates[i]["probability"], float)


# ==============================================================================
# 4. Router Decision Logic — EVERY intent in EVERY domain
# ==============================================================================
class TestRouterDecisionLogicAllIntents(unittest.TestCase):
    """
    Exhaustively drives route_intent() across every intent listed in
    DOMAIN_INTENTS for the confident, ambiguous, and unavailable branches —
    not just one representative intent per domain.
    """

    # --------------------------------------------------------------------------
    # 4.1 Confident: every intent, in its own domain, with strong signal
    # --------------------------------------------------------------------------
    @patch("agent.routing.intent_router.get_candidate_intents")
    @patch("agent.routing.intent_router.generate_routing_query")
    def test_confident_for_every_intent_in_every_domain(self, mock_query_gen, mock_get_candidates):
        for domain, intents in DOMAIN_INTENTS.items():
            for intent in intents:
                with self.subTest(domain=domain, intent=intent):
                    mock_query_gen.return_value = _query(f"do the {intent} thing")
                    runner_up = next((i for i in intents if i != intent), f"{domain}.other")
                    mock_get_candidates.return_value = [
                        {"intent": intent, "probability": 0.85},
                        {"intent": runner_up, "probability": 0.10},
                    ]

                    result = route_intent(f"do the {intent} thing", available_domains={domain})

                    self.assertEqual(result["intent"], intent)
                    self.assertEqual(result["domain"], domain)
                    self.assertAlmostEqual(result["confidence"], 0.85)
                    self.assertAlmostEqual(result["margin"], 0.75)
                    self.assertEqual(result["status"], "confident")

    # --------------------------------------------------------------------------
    # 4.2 Unavailable: every intent, when its domain is disabled
    # --------------------------------------------------------------------------
    @patch("agent.routing.intent_router.get_candidate_intents")
    @patch("agent.routing.intent_router.generate_routing_query")
    def test_unavailable_for_every_intent_when_domain_disabled(self, mock_query_gen, mock_get_candidates):
        for domain, intents in DOMAIN_INTENTS.items():
            for intent in intents:
                with self.subTest(domain=domain, intent=intent):
                    mock_query_gen.return_value = _query(f"Action for {intent}")
                    mock_get_candidates.return_value = [
                        {"intent": intent, "probability": 0.90},
                    ]

                    # every domain EXCEPT the predicted one is enabled
                    active_domains = ALL_ACTIVE_DOMAINS - {domain}
                    result = route_intent(f"Action for {intent}", available_domains=active_domains)

                    self.assertEqual(result["intent"], intent)
                    self.assertEqual(result["domain"], domain)
                    self.assertEqual(result["status"], "unavailable")

    # --------------------------------------------------------------------------
    # 4.3 Ambiguous: one representative intent per domain, low-confidence
    #     and low-margin variants (the arithmetic doesn't depend on which
    #     specific intent within a domain won, only on the probabilities)
    # --------------------------------------------------------------------------
    @patch("agent.routing.intent_router.get_candidate_intents")
    @patch("agent.routing.intent_router.generate_routing_query")
    def test_ambiguous_due_to_low_confidence_per_domain(self, mock_query_gen, mock_get_candidates):
        for domain, intents in DOMAIN_INTENTS.items():
            intent = intents[0]
            runner_up = intents[1] if len(intents) > 1 else "general"
            with self.subTest(domain=domain, intent=intent, reason="low_confidence"):
                mock_query_gen.return_value = _query("vague request")
                mock_get_candidates.return_value = [
                    {"intent": intent, "probability": 0.60},   # below CONFIDENCE_THRESHOLD (0.65)
                    {"intent": runner_up, "probability": 0.10},  # margin is wide (0.50) but doesn't matter
                ]
                result = route_intent("vague request", available_domains={domain})
                self.assertEqual(result["intent"], intent)
                self.assertEqual(result["status"], "ambiguous")

    @patch("agent.routing.intent_router.get_candidate_intents")
    @patch("agent.routing.intent_router.generate_routing_query")
    def test_ambiguous_due_to_low_margin_per_domain(self, mock_query_gen, mock_get_candidates):
        for domain, intents in DOMAIN_INTENTS.items():
            intent = intents[0]
            runner_up = intents[1] if len(intents) > 1 else "general"
            with self.subTest(domain=domain, intent=intent, reason="low_margin"):
                mock_query_gen.return_value = _query("close call request")
                mock_get_candidates.return_value = [
                    {"intent": intent, "probability": 0.70},        # above CONFIDENCE_THRESHOLD
                    {"intent": runner_up, "probability": 0.55},     # margin 0.15 < MARGIN_THRESHOLD (0.20)
                ]
                result = route_intent("close call request", available_domains={domain})
                self.assertEqual(result["intent"], intent)
                self.assertEqual(result["status"], "ambiguous")

    @patch("agent.routing.intent_router.get_candidate_intents")
    @patch("agent.routing.intent_router.generate_routing_query")
    def test_ambiguous_both_confidence_and_margin_low(self, mock_query_gen, mock_get_candidates):
        mock_query_gen.return_value = _query("Vague task")
        mock_get_candidates.return_value = [
            {"intent": "sheets.read", "probability": 0.35},
            {"intent": "sheets.write", "probability": 0.30},  # margin 0.05
        ]
        result = route_intent("Vague task", available_domains={"sheets"})
        self.assertEqual(result["intent"], "sheets.read")
        self.assertEqual(result["status"], "ambiguous")

    # --------------------------------------------------------------------------
    # 4.4 Exact boundary threshold values
    # --------------------------------------------------------------------------
    @patch("agent.routing.intent_router.get_candidate_intents")
    @patch("agent.routing.intent_router.generate_routing_query")
    def test_exact_boundary_thresholds(self, mock_query_gen, mock_get_candidates):
        mock_query_gen.return_value = _query("Exact boundary")

        # confidence == threshold, margin == threshold -> confident
        mock_get_candidates.return_value = [
            {"intent": "email.send", "probability": 0.65},
            {"intent": "email.read", "probability": 0.45},
        ]
        res = route_intent("Exact boundary", available_domains={"email"})
        self.assertEqual(res["status"], "confident")

        # confidence == threshold, margin just under -> ambiguous
        mock_get_candidates.return_value = [
            {"intent": "email.send", "probability": 0.65},
            {"intent": "email.read", "probability": 0.4501},
        ]
        res = route_intent("Exact boundary", available_domains={"email"})
        self.assertEqual(res["status"], "ambiguous")

        # confidence just under threshold, margin == threshold -> ambiguous
        mock_get_candidates.return_value = [
            {"intent": "email.send", "probability": 0.6499},
            {"intent": "email.read", "probability": 0.40},
        ]
        res = route_intent("Exact boundary", available_domains={"email"})
        self.assertEqual(res["status"], "ambiguous")

    # --------------------------------------------------------------------------
    # 4.5 general / out_of_scope remapped to research.search — confident,
    #     ambiguous, and unavailable
    # --------------------------------------------------------------------------
    @patch("agent.routing.intent_router.get_candidate_intents")
    @patch("agent.routing.intent_router.generate_routing_query")
    def test_general_and_out_of_scope_remap_confident_and_unavailable(self, mock_query_gen, mock_get_candidates):
        for source_intent in ("general", "out_of_scope"):
            with self.subTest(source_intent=source_intent):
                mock_query_gen.return_value = _query("Explain gravity")
                other = "out_of_scope" if source_intent == "general" else "general"
                mock_get_candidates.return_value = [
                    {"intent": source_intent, "probability": 0.90},
                    {"intent": other, "probability": 0.05},
                ]

                res_available = route_intent("Explain gravity", available_domains={"research"})
                self.assertEqual(res_available["intent"], "research.search")
                self.assertEqual(res_available["domain"], "research")
                self.assertEqual(res_available["status"], "confident")

                res_unavailable = route_intent("Explain gravity", available_domains={"email"})
                self.assertEqual(res_unavailable["intent"], "research.search")
                self.assertEqual(res_unavailable["domain"], "research")
                self.assertEqual(res_unavailable["status"], "unavailable")

    @patch("agent.routing.intent_router.get_candidate_intents")
    @patch("agent.routing.intent_router.generate_routing_query")
    def test_general_remap_ambiguous(self, mock_query_gen, mock_get_candidates):
        mock_query_gen.return_value = _query("Something vague and open-ended")
        mock_get_candidates.return_value = [
            {"intent": "general", "probability": 0.55},
            {"intent": "out_of_scope", "probability": 0.50},  # margin 0.05
        ]
        result = route_intent("Something vague and open-ended", available_domains={"research"})
        self.assertEqual(result["intent"], "research.search")
        self.assertEqual(result["domain"], "research")
        self.assertEqual(result["status"], "ambiguous")

    # --------------------------------------------------------------------------
    # 4.6 Fallbacks and structural edge cases
    # --------------------------------------------------------------------------
    @patch("agent.routing.intent_router.get_candidate_intents")
    @patch("agent.routing.intent_router.generate_routing_query")
    def test_single_candidate_margin_equals_confidence(self, mock_query_gen, mock_get_candidates):
        for domain, intents in DOMAIN_INTENTS.items():
            intent = intents[0]
            with self.subTest(domain=domain, intent=intent):
                mock_query_gen.return_value = _query(f"only one guess for {intent}")
                mock_get_candidates.return_value = [{"intent": intent, "probability": 0.80}]

                result = route_intent(f"only one guess for {intent}", available_domains={domain})

                self.assertEqual(result["intent"], intent)
                self.assertAlmostEqual(result["confidence"], 0.80)
                self.assertAlmostEqual(result["margin"], 0.80)
                self.assertEqual(result["status"], "confident")

    @patch("agent.routing.intent_router.get_candidate_intents")
    @patch("agent.routing.intent_router.generate_routing_query")
    def test_empty_candidates_fallback(self, mock_query_gen, mock_get_candidates):
        mock_query_gen.return_value = _query("Unsupported operation")
        mock_get_candidates.return_value = []

        result = route_intent("Unsupported operation", available_domains={"email"})

        self.assertEqual(result["intent"], "general")
        self.assertEqual(result["domain"], "general")
        self.assertEqual(result["confidence"], 0.0)
        self.assertEqual(result["margin"], 0.0)
        self.assertEqual(result["status"], "unavailable")

    @patch("agent.routing.intent_router.get_candidate_intents")
    @patch("agent.routing.intent_router.generate_routing_query")
    def test_empty_candidates_fallback_even_when_all_domains_available(self, mock_query_gen, mock_get_candidates):
        """Fallback is 'general'/'unavailable' regardless of what's enabled — 'general' is never enabled itself."""
        mock_query_gen.return_value = _query("Totally unrecognized gibberish")
        mock_get_candidates.return_value = []

        result = route_intent("Totally unrecognized gibberish", available_domains=ALL_ACTIVE_DOMAINS)

        self.assertEqual(result["status"], "unavailable")

    @patch("agent.routing.intent_router.get_candidate_intents")
    @patch("agent.routing.intent_router.generate_routing_query")
    def test_empty_available_domains_through_route_intent(self, mock_query_gen, mock_get_candidates):
        mock_query_gen.return_value = _query("Send an email to Bob")
        mock_get_candidates.return_value = [
            {"intent": "email.send", "probability": 0.90},
            {"intent": "email.draft", "probability": 0.05},
        ]
        result = route_intent("Send an email to Bob", available_domains=set())
        self.assertEqual(result["intent"], "email.send")
        self.assertEqual(result["domain"], "email")
        self.assertEqual(result["status"], "unavailable")

    @patch("agent.routing.intent_router.get_candidate_intents")
    @patch("agent.routing.intent_router.generate_routing_query")
    def test_empty_string_message(self, mock_query_gen, mock_get_candidates):
        mock_query_gen.return_value = _query("")
        mock_get_candidates.return_value = []
        result = route_intent("", available_domains={"email"})
        self.assertEqual(result["status"], "unavailable")
        mock_query_gen.assert_called_once_with("")

    @patch("agent.routing.intent_router.get_candidate_intents")
    @patch("agent.routing.intent_router.generate_routing_query")
    def test_accepts_message_list_input(self, mock_query_gen, mock_get_candidates):
        """LangGraph passes state['messages'] (a list) to route_intent."""
        mock_query_gen.return_value = _query("Check calendar")
        mock_get_candidates.return_value = [
            {"intent": "calendar.search", "probability": 0.85},
            {"intent": "calendar.create", "probability": 0.10},
        ]

        messages = [MagicMock(content="Hi"), MagicMock(content="Check my calendar for tomorrow")]
        result = route_intent(messages, available_domains={"calendar"})

        mock_query_gen.assert_called_once_with(messages)
        self.assertEqual(result["intent"], "calendar.search")
        self.assertEqual(result["status"], "confident")

    @patch("agent.routing.intent_router.get_candidate_intents")
    @patch("agent.routing.intent_router.generate_routing_query")
    def test_accepts_message_dict_input(self, mock_query_gen, mock_get_candidates):
        """Some callers pass a raw {'text': ...} dict instead of a string or message list."""
        message = {"text": "is there any message from dev-learning channel?"}
        mock_query_gen.return_value = _query("is there any message from dev-learning channel?")
        mock_get_candidates.return_value = [
            {"intent": "slack.history", "probability": 0.75},
            {"intent": "slack.search", "probability": 0.20},
        ]

        result = route_intent(message, available_domains={"slack"})

        mock_query_gen.assert_called_once_with(message)
        self.assertEqual(result["intent"], "slack.history")
        self.assertEqual(result["domain"], "slack")
        self.assertEqual(result["status"], "confident")

    @patch("agent.routing.intent_router.get_candidate_intents")
    @patch("agent.routing.intent_router.generate_routing_query")
    def test_slack_send_with_attachment_style_message(self, mock_query_gen, mock_get_candidates):
        mock_query_gen.return_value = _query("Send a message to the dev-learning channel with an attachment")
        mock_get_candidates.return_value = [
            {"intent": "slack.send", "probability": 0.88},
            {"intent": "slack.search", "probability": 0.05},
        ]
        messages = [MagicMock(content="Send a message to the dev-learning channel with an attachment")]
        result = route_intent(messages, available_domains={"slack"})
        self.assertEqual(result["intent"], "slack.send")
        self.assertEqual(result["domain"], "slack")
        self.assertEqual(result["status"], "confident")


# ==============================================================================
# 5. Live Trained Pipeline Integration Tests (Real CSV & ML Model)
# These DO exercise the real classifier — not the LLM query-generation step —
# so they're slower but not network/LLM-dependent. Skippable via env var.
# ==============================================================================
@unittest.skipIf(os.environ.get("SKIP_LIVE_MODEL_TESTS") == "1", "Live model tests disabled")
class TestLiveModelIntegration(unittest.TestCase):

    def test_trained_model_classes_cover_all_fixture_intents(self):
        classes = set(model.classes_)
        for intent in ALL_INTENTS:
            with self.subTest(intent=intent):
                self.assertIn(intent, classes, f"Intent {intent} missing from trained model classes")

    def test_live_candidate_extraction_top1_per_domain(self):
        domain_queries = [
            ("email", "Send an email to Alice regarding our weekly meeting", "email.send"),
            ("calendar", "Schedule a meeting with the engineering team tomorrow at 3pm", "calendar.create"),
            ("docs", "Create a new document titled Quarterly Product Strategy", "docs.create"),
            ("sheets", "Append new row with latest sales numbers to the revenue sheet", "sheets.write"),
            ("slack", "Send a message to the general channel on Slack", "slack.send"),
            ("research", "Search the web for the latest advancements in quantum computing", "research.search"),
        ]

        for domain, query_text, expected_top_intent in domain_queries:
            with self.subTest(domain=domain, query=query_text):
                candidates = get_candidate_intents(query_text, available_domains=ALL_ACTIVE_DOMAINS, top_k=3)
                self.assertGreater(len(candidates), 0)
                predicted_intent = candidates[0]["intent"]
                predicted_domain = _domain_for_intent(predicted_intent)

                self.assertEqual(
                    predicted_domain, domain,
                    f"Expected domain '{domain}' for query '{query_text}', got '{predicted_domain}' ({predicted_intent})"
                )
                self.assertEqual(predicted_intent, expected_top_intent)


# ==============================================================================
# Test Runner
# ==============================================================================
if __name__ == "__main__":
    unittest.main(verbosity=2)