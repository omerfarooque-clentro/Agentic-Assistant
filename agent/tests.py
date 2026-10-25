from unittest.mock import patch

from django.test import TestCase
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.llm.messages import MAX_TOOL_MESSAGE_CHARS, messages_for_llm
from agent.routing.query_generator import has_conversational_reference
from agent.routing.intent_router import route_intent
from agent.llm.prompts import get_system_prompt, BASE_SYSTEM_PROMPT, GENERAL_SYSTEM_PROMPT


class MessagesForLlmTests(TestCase):
    def test_omits_tool_output_from_completed_turns(self):
        state = {
            "messages": [
                HumanMessage(content="Find the latest news"),
                AIMessage(content="", tool_calls=[{"name": "search", "args": {}, "id": "old"}]),
                ToolMessage(content="old " * 10000, tool_call_id="old"),
                AIMessage(content="Here are the results."),
                HumanMessage(content="okay"),
            ]
        }

        messages = messages_for_llm(state)

        self.assertEqual(messages[-1].content, "okay")
        self.assertNotIn("old old old", "\n".join(str(message.content) for message in messages))

    def test_keeps_active_tool_result_but_bounds_its_size(self):
        state = {
            "messages": [
                HumanMessage(content="Search for updates"),
                AIMessage(content="", tool_calls=[{"name": "search", "args": {}, "id": "current"}]),
                ToolMessage(content="x" * (MAX_TOOL_MESSAGE_CHARS * 2), tool_call_id="current"),
            ]
        }

        messages = messages_for_llm(state)

        tool_message = messages[-1]
        self.assertLessEqual(len(tool_message.content), MAX_TOOL_MESSAGE_CHARS + 100)
        self.assertIn("[Tool output truncated for context]", tool_message.content)

    def test_domain_scoped_prompts(self):
        state = {"messages": [HumanMessage(content="Send an email to John")]}
        email_messages = messages_for_llm(state, domain="email")
        self.assertIn("EMAIL GUIDELINES", email_messages[0].content)
        self.assertNotIn("SPREADSHEET GUIDELINES", email_messages[0].content)

        general_messages = messages_for_llm(state, domain="general")
        self.assertIn(GENERAL_SYSTEM_PROMPT, str(general_messages[0].content))

    def test_general_prompt_with_available_domains(self):
        state = {
            "messages": [HumanMessage(content="What tools are available?")],
            "available_domains": {"email", "calendar", "slack"},
        }
        general_messages = messages_for_llm(state, domain="general")
        prompt_text = str(general_messages[0].content)

        self.assertIn("AVAILABLE TOOLS:", prompt_text)
        self.assertIn("Gmail", prompt_text)
        self.assertIn("Google Calendar", prompt_text)
        self.assertIn("Slack", prompt_text)
        self.assertNotIn("Web Search", prompt_text)
        self.assertIn("NOT YET CONNECTED:", prompt_text)
        self.assertIn("Google Docs", prompt_text)
        self.assertIn("Google Sheets", prompt_text)

    def test_cross_domain_context_preservation(self):
        # Sequence: Turn 1 queries Sheets -> Turn 2 asks to send that to Slack
        state = {
            "messages": [
                HumanMessage(content="Read Q3 revenue from spreadsheet"),
                AIMessage(content="Q3 Revenue is $1.2M according to the spreadsheet."),
                HumanMessage(content="Send that to Sarah on Slack"),
            ]
        }
        slack_messages = messages_for_llm(state, domain="slack")
        # Ensure previous turn's assistant answer is preserved in prompt context for Slack agent
        history_text = "\n".join(str(m.content) for m in slack_messages)
        self.assertIn("Q3 Revenue is $1.2M", history_text)
        self.assertIn("Send that to Sarah on Slack", history_text)


class ReferenceDetectorTests(TestCase):
    def test_detects_standalone_queries_without_references(self):
        self.assertFalse(has_conversational_reference("search my emails for travel tickets"))
        self.assertFalse(has_conversational_reference("what meetings do I have tomorrow?"))
        self.assertFalse(has_conversational_reference("create a new document called Roadmap"))
        self.assertFalse(has_conversational_reference("summarize the report"))

    def test_detects_conversational_references_and_pronouns(self):
        self.assertTrue(has_conversational_reference("send it to Ahmed on Slack"))
        self.assertTrue(has_conversational_reference("email that to Sarah"))
        self.assertTrue(has_conversational_reference("schedule the same meeting again"))
        self.assertTrue(has_conversational_reference("can you forward those to my manager?"))
        self.assertTrue(has_conversational_reference("send it"))
        self.assertTrue(has_conversational_reference("yes do it"))


class AdaptiveIntentRouterTests(TestCase):
    def test_standalone_query_bypasses_call1(self):
        # Standalone 1-turn query should skip Call #1 rewrite (call_metrics is None)
        result = route_intent(
            [HumanMessage(content="search my unread emails")],
            available_domains={"email", "calendar", "docs", "sheets", "slack", "research"},
        )
        self.assertEqual(result["domain"], "email")
        self.assertIsNone(result["call_metrics"])

    def test_general_conversation_classification(self):
        test_queries = [
            "hello, how are you?",
            "how are you doing today?",
            "What operations tasks can you assist me with?",
            "what tools are available",
            "what tools do you have access to?",
            "who are you and what do you do?",
        ]
        available_domains = {"email", "calendar", "docs", "sheets", "slack", "research"}
        for q in test_queries:
            result = route_intent(
                [HumanMessage(content=q)],
                available_domains=available_domains,
            )
            self.assertEqual(result["intent"], "general", f"Failed on query: {q}")
            self.assertEqual(result["domain"], "general", f"Failed on query: {q}")
            self.assertEqual(result["status"], "confident", f"Failed on query: {q}")
            self.assertIsNone(result["call_metrics"], f"Should not trigger Call #1 rewrite for standalone {q}")


class StreamTitleFilterTests(TestCase):
    def test_filter_removes_tag_and_extracts_title(self):
        from agent.llm.titles import StreamTitleFilter, extract_title_from_text

        f = StreamTitleFilter()
        chunks = ["Here is your response.\n\n<", "suggested_", "title>Weekly Team ", "Sync</suggested_title>"]
        out = []
        for c in chunks:
            out.append(f.process_chunk(c))
        rem, title = f.finalize()
        out.append(rem)

        streamed_text = "".join(out)
        self.assertEqual(streamed_text, "Here is your response.\n\n")
        self.assertEqual(title, "Weekly Team Sync")

    def test_extract_title_from_text(self):
        from agent.llm.titles import extract_title_from_text

        text = "Hello! Let me help you.\n\n<suggested_title>Calendar Assistance</suggested_title>"
        clean_text, title = extract_title_from_text(text)
        self.assertEqual(clean_text, "Hello! Let me help you.")
        self.assertEqual(title, "Calendar Assistance")

    def test_extract_title_unclosed_tag(self):
        from agent.llm.titles import extract_title_from_text

        text = "Here is the summary.\n\n<suggested_title>Executive Summary"
        clean_text, title = extract_title_from_text(text)
        self.assertEqual(clean_text, "Here is the summary.")
        self.assertEqual(title, "Executive Summary")


class EnvelopeAndReferenceDetectorTests(TestCase):
    def test_strip_message_envelope(self):
        from agent.routing.reference_detector import strip_message_envelope

        raw1 = "Date: 2026-09-13 13:49:00 (Timezone: UTC), Omer: search my unread emails"
        self.assertEqual(strip_message_envelope(raw1), "search my unread emails")

        raw2 = "Date: 2026-09-13 13:49:00, Alice: send it to Bob"
        self.assertEqual(strip_message_envelope(raw2), "send it to Bob")

        plain = "what tools are available"
        self.assertEqual(strip_message_envelope(plain), "what tools are available")

    def test_has_conversational_reference_with_envelope(self):
        from agent.routing.reference_detector import has_conversational_reference

        # With envelope, should still detect pronoun "it" correctly
        raw_ref = "Date: 2026-09-13 13:49:00 (Timezone: UTC), Omer: forward it to Sarah"
        self.assertTrue(has_conversational_reference(raw_ref))

        # With envelope, standalone query should NOT have reference
        raw_standalone = "Date: 2026-09-13 13:49:00 (Timezone: UTC), Omer: search my unread emails"
        self.assertFalse(has_conversational_reference(raw_standalone))


class MultiTurnRoutingTests(TestCase):
    def test_standalone_multi_turn_bypasses_query_generator(self):
        history = [
            HumanMessage(content="What operations tasks can you assist me with?"),
            AIMessage(content="I can assist with Gmail, Calendar, Docs, Sheets, and Slack."),
            HumanMessage(content="Date: 2026-09-13 13:49:00 (Timezone: UTC), Omer: search my unread emails"),
        ]
        available_domains = {"email", "calendar", "docs", "sheets", "slack", "research"}
        result = route_intent(history, available_domains=available_domains)
        self.assertEqual(result["domain"], "email")
        self.assertEqual(result["intent"], "email.search")
        self.assertIsNone(result["call_metrics"], "Standalone query in multi-turn must bypass Call #1!")

    @patch("agent.routing.intent_router.generate_routing_query")
    def test_reference_multi_turn_triggers_query_generator(self, mock_query_gen):
        mock_query_gen.return_value = {
            "type": "SINGLE",
            "query": "Forward the Q3 Budget email to Sarah",
            "metrics": {
                "name": "Query Rewrite (Call #1)",
                "model": "llama-3.1-8b-instant",
                "input_tokens": 120,
                "output_tokens": 15,
                "total_tokens": 135,
                "cached_tokens": 0,
                "latency_ms": 150.0,
            },
        }
        history = [
            HumanMessage(content="Search for emails from Alex"),
            AIMessage(content="Found email from Alex: Subject: Q3 Budget."),
            HumanMessage(content="Date: 2026-09-13 13:49:00 (Timezone: UTC), Omer: forward it to Sarah"),
        ]
        available_domains = {"email", "calendar", "docs", "sheets", "slack", "research"}
        result = route_intent(history, available_domains=available_domains)
        self.assertEqual(result["domain"], "email")
        mock_query_gen.assert_called_once()
        self.assertIsNotNone(result["call_metrics"], "Pronoun 'it' must trigger Call #1 rewrite!")
        self.assertEqual(result["call_metrics"]["name"], "Query Rewrite (Call #1)")


class MetricsCollectorTests(TestCase):
    def test_extract_call_metrics_structure(self):
        from agent.metrics import extract_call_metrics, CallMetrics

        mock_response = AIMessage(
            content="Hello world",
            usage_metadata={"input_tokens": 100, "output_tokens": 25, "total_tokens": 125},
        )
        metrics = extract_call_metrics(
            response=mock_response,
            step_name="Test Call",
            latency_ms=250.5,
            model_name="openai/gpt-oss-120b",
        )
        self.assertEqual(metrics["name"], "Test Call")
        self.assertEqual(metrics["input_tokens"], 100)
        self.assertEqual(metrics["output_tokens"], 25)
        self.assertEqual(metrics["total_tokens"], 125)
        self.assertEqual(metrics["latency_ms"], 250.5)
        self.assertEqual(metrics["model"], "openai/gpt-oss-120b")

    def test_aggregate_turn_metrics(self):
        from agent.metrics import aggregate_turn_metrics, CallMetrics

        call1: CallMetrics = {
            "name": "Query Generator (Call #1)",
            "model": "llama-3.1-8b-instant",
            "input_tokens": 150,
            "output_tokens": 30,
            "total_tokens": 180,
            "cached_tokens": 0,
            "latency_ms": 300,
        }
        call2: CallMetrics = {
            "name": "Email Agent (Call #2)",
            "model": "openai/gpt-oss-120b",
            "input_tokens": 800,
            "output_tokens": 120,
            "total_tokens": 920,
            "cached_tokens": 50,
            "latency_ms": 1200,
        }
        import time
        start_time = time.perf_counter() - 1.5
        turn_metrics = aggregate_turn_metrics([call1, call2], start_time=start_time)

        self.assertEqual(turn_metrics["total_tokens"], 1100)
        self.assertEqual(turn_metrics["input_tokens"], 950)
        self.assertEqual(turn_metrics["output_tokens"], 150)
        self.assertEqual(turn_metrics["cached_tokens"], 50)
        self.assertEqual(turn_metrics["llm_calls"], 2)
        self.assertEqual(len(turn_metrics["breakdown"]), 2)

    def test_extract_call_metrics_with_list_content_blocks(self):
        from agent.metrics import extract_call_metrics

        mock_response = AIMessage(content="Search completed.")
        # Sequence of messages where item 7 has list content (e.g. ToolMessage from search)
        messages = [
            HumanMessage(content="msg0"),
            AIMessage(content="msg1"),
            HumanMessage(content="msg2"),
            AIMessage(content="msg3"),
            HumanMessage(content="msg4"),
            AIMessage(content="msg5"),
            HumanMessage(content="msg6"),
            ToolMessage(content=[{"type": "text", "text": "weather data"}], tool_call_id="call_7"),
        ]
        metrics = extract_call_metrics(
            response=mock_response,
            step_name="Research Agent (Call #2)",
            latency_ms=300.0,
            prompt_text_or_messages=messages,
        )
        self.assertEqual(metrics["name"], "Research Agent (Call #2)")
        self.assertGreater(metrics["input_tokens"], 0)


class TitleGenerationTests(TestCase):
    def test_clean_heuristic_title(self):
        from agent.llm.titles import clean_heuristic_title

        self.assertEqual(
            clean_heuristic_title("who won the pakistan tour of england test series?"),
            "Pakistan Tour Of England Test Series",
        )
        self.assertEqual(
            clean_heuristic_title("can you please search for flight tickets to London?"),
            "Flight Tickets To London",
        )
        self.assertEqual(
            clean_heuristic_title("Date: 2026-09-13 14:30:00 (Timezone: UTC), Omer: summarize Q3 revenue report"),
            "Summarize Q3 Revenue Report",
        )

    def test_is_substantive_for_title(self):
        from agent.llm.titles import is_substantive_for_title

        # Trivial greetings should not be substantive
        self.assertFalse(is_substantive_for_title("hi"))
        self.assertFalse(is_substantive_for_title("hello"))
        self.assertFalse(is_substantive_for_title("hey there"))
        self.assertFalse(is_substantive_for_title("Date: 2026-09-13 14:30:00, Omer: hi"))

        # Real tasks should be substantive
        self.assertTrue(is_substantive_for_title("summarize Q3 revenue report"))
        self.assertTrue(is_substantive_for_title("check my recent slack messages"))
        self.assertTrue(is_substantive_for_title("hi", assistant_response="Summary <suggested_title>Revenue Sync</suggested_title>"))

    def test_is_domain_ambiguous(self):
        from agent.routing.reference_detector import is_domain_ambiguous

        # Explicit service names are NOT ambiguous
        self.assertFalse(is_domain_ambiguous("check slack messages from arsalan"))
        self.assertFalse(is_domain_ambiguous("search gmail for invoice"))
        self.assertFalse(is_domain_ambiguous("search web for latest news"))

        # Ambiguous messages without domain keyword ARE ambiguous
        self.assertTrue(is_domain_ambiguous("check latest message from arsalan"))
        self.assertTrue(is_domain_ambiguous("check lates meesage from arsalan"))
        self.assertTrue(is_domain_ambiguous("show unread messages"))


class LeadGenerationToolTests(TestCase):
    def test_lead_generation_routes_to_research_without_compound_split(self):
        from agent.routing.reference_detector import is_compound_multi_domain

        query = "100 lead generation for us, uk, aus, dubai size 10-50 their mail linkedin number add to my sheet"
        available = {"research", "sheets", "email"}

        # Should not split into multi-agent LLM sheet writing because pure Python writes directly to Sheets
        self.assertFalse(is_compound_multi_domain(query, available))

        routed = route_intent([HumanMessage(content=query)], available_domains=available)
        self.assertEqual(routed["domain"], "research")
        self.assertEqual(routed["intent"], "research.search")
        self.assertIsNone(routed["call_metrics"])

    @patch("agent.tools.lead_generation_tool.requests.post")
    @patch("agent.tools.lead_generation_tool._get_live_google_sheets_token", return_value="mock-google-oauth-token")
    @patch("agent.tools.lead_generation_tool.LeadGenerationPipeline")
    def test_tavily_lead_search_pure_python_sheets_writer_and_math_telemetry(
        self, mock_pipeline_cls, _mock_token, mock_requests_post
    ):
        from agent.tools.lead_generation_tool import create_lead_generation_tool
        from agent.cards.registry import resolve_builder
        from lead_generation.models import (
            AttributionStatus,
            CompanyRecord,
            IdentityConfidence,
            LeadGrade,
            LeadRecord,
            RoutingStatus,
            SmtpStatus,
        )

        # Mock pipeline returning 2 unique verified startup leads
        comp1 = CompanyRecord(
            canonical_domain="posthog.com",
            canonical_name="PostHog",
            routing_status=RoutingStatus.MX_RESOLVED,
            inferred_provider="GOOGLE_WORKSPACE",
        )
        comp2 = CompanyRecord(
            canonical_domain="ziina.com",
            canonical_name="Ziina",
            routing_status=RoutingStatus.MX_RESOLVED,
            inferred_provider="MICROSOFT_365",
        )
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        lead1 = LeadRecord(
            campaign_role="Python Developer",
            location_bucket="United Kingdom",
            company=comp1,
            job_title_signal="Product Engineer (+2 more open roles)",
            job_url_signal="https://jobs.ashbyhq.com/posthog/1",
            vacancy_is_live=True,
            exec_full_name="James Hawkins",
            normalized_exec_name="james hawkins",
            exec_title="Co-Founder & CEO",
            identity_source_url="https://uk.linkedin.com/in/j-hawkins",
            identity_observed_at=now,
            identity_confidence=IdentityConfidence.CURRENT_CONFIRMED,
            best_email="james.hawkins@posthog.com",
            attribution_status=AttributionStatus.PUBLISHED_DIRECT_MATCH,
            smtp_status=SmtpStatus.SMTP_RCPT_ACCEPTED,
            lead_grade=LeadGrade.GRADE_A,
            linkedin_url="https://uk.linkedin.com/in/j-hawkins",
            phone_number="+44 20 3808 9700",
            phone_type="PUBLISHED_TEL_LINK",
        )
        lead2 = LeadRecord(
            campaign_role="Python Developer",
            location_bucket="Dubai, UAE",
            company=comp2,
            job_title_signal="Senior Backend Engineer (Python)",
            job_url_signal="https://jobs.ashbyhq.com/ziina/1",
            vacancy_is_live=True,
            exec_full_name="Faisal Toukan",
            normalized_exec_name="faisal toukan",
            exec_title="Co-Founder & CEO",
            identity_source_url="https://ae.linkedin.com/in/faisal-toukan",
            identity_observed_at=now,
            identity_confidence=IdentityConfidence.CURRENT_CONFIRMED,
            best_email="faisal.toukan@ziina.com",
            attribution_status=AttributionStatus.INFERRED_CANDIDATE,
            smtp_status=SmtpStatus.SMTP_RCPT_ACCEPTED,
            lead_grade=LeadGrade.GRADE_C,
            linkedin_url="https://ae.linkedin.com/in/faisal-toukan",
            phone_number=None,
            phone_type="NONE",
        )

        mock_pipeline = mock_pipeline_cls.return_value
        mock_pipeline.run_campaign.return_value = {
            "leads": [lead1, lead2],
            "boards": [1, 2, 3],
            "jobs": [1, 2],
            "dropped_companies": [{"slug": "unverified", "domain": "unverified.io", "reason": "MISSING_LINKEDIN_URL"}],
            "regional_quotas": {"US": 25, "UK": 25, "Australia": 25, "Dubai": 25},
        }

        from unittest.mock import MagicMock

        # Mock Google Sheets API create + values:append responses
        create_resp = MagicMock()
        create_resp.raise_for_status.return_value = None
        create_resp.json.return_value = {
            "spreadsheetId": "sheet_12345",
            "spreadsheetUrl": "https://docs.google.com/spreadsheets/d/sheet_12345/edit",
        }
        append_resp = MagicMock()
        append_resp.raise_for_status.return_value = None
        append_resp.json.return_value = {"updates": {"updatedRows": 3}}
        mock_requests_post.side_effect = [create_resp, append_resp]

        tool_fn = create_lead_generation_tool(user=None)
        payload = tool_fn.invoke({
            "role": "Python Developer",
            "locations": "US, UK, Australia, Dubai",
            "company_size": "10-50",
            "quota": 100,
        })

        # Verify pure-Python Google Sheets insertion occurred
        self.assertTrue(payload["pure_python_export"]["synced_to_google_sheets"])
        self.assertEqual(payload["pure_python_export"]["spreadsheet_id"], "sheet_12345")
        self.assertEqual(payload["pure_python_export"]["rows_inserted"], 2)

        # Verify raw telemetry for LLM mathematical reasoning & 3-axis confidence summary
        self.assertEqual(payload["raw_funnel_counts"]["unique_companies_evaluated"], 3)
        self.assertEqual(payload["raw_funnel_counts"]["unique_verified_leads_exported"], 2)
        self.assertEqual(payload["raw_funnel_counts"]["leads_with_verified_linkedin"], 2)
        self.assertEqual(payload["raw_funnel_counts"]["leads_with_verified_phone"], 1)
        self.assertEqual(payload["grade_distribution_counts"]["GRADE_A"], 1)
        self.assertEqual(payload["grade_distribution_counts"]["GRADE_C"], 1)
        self.assertEqual(payload["identity_confidence_counts"]["CURRENT_CONFIRMED"], 2)
        self.assertEqual(payload["routing_status_counts"]["MX_RESOLVED"], 2)

        # Verify frontend sheet_view card builds cleanly from payload
        card_type, builder = resolve_builder("tavily_lead_search")
        self.assertEqual(card_type, "sheet_view")
        card_data = builder(payload)
        self.assertIsNotNone(card_data)
        self.assertEqual(card_data["url"], "https://docs.google.com/spreadsheets/d/sheet_12345/edit")
        self.assertEqual(len(card_data["rows"]), 2)

    def test_tavily_lead_search_triggers_approval_node(self):
        from langchain_core.messages import AIMessage
        from agent.graph.approval import APPROVAL_TOOL_NAMES
        from agent.graph.nodes import APPROVAL_DOMAINS, scoped_should_continue

        self.assertIn("tavily_lead_search", APPROVAL_TOOL_NAMES)
        self.assertIn("research", APPROVAL_DOMAINS)

        # When research agent calls tavily_lead_search, it MUST route to "approval"
        lead_call_msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "call_lead_1",
                    "name": "tavily_lead_search",
                    "args": {
                        "role": "Python Developer",
                        "locations": "US, UK, Australia, Dubai, Remote",
                        "company_size": "10-50",
                        "quota": 100,
                    },
                }
            ],
        )
        self.assertEqual(
            scoped_should_continue({"messages": [lead_call_msg]}, "research"),
            "approval",
        )

        # When research agent calls tavily_search or get_weather, it routes directly to "tools"
        web_search_msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "call_web_1",
                    "name": "tavily_search",
                    "args": {"query": "latest AI news"},
                }
            ],
        )
        self.assertEqual(
            scoped_should_continue({"messages": [web_search_msg]}, "research"),
            "tools",
        )

    def test_disconnected_service_routes_to_connect_prompt_and_affirmative_followup(self):
        from langchain_core.messages import AIMessage, HumanMessage
        from agent.graph.nodes import supervisor_router
        from agent.routing.intent_router import route_intent

        # 1. "check mail" when email is disconnected (only "research" available) must route to email unavailable -> connect_prompt
        res1 = route_intent([HumanMessage(content="check mail")], available_domains={"research"})
        self.assertEqual(res1["domain"], "email")
        self.assertEqual(res1["status"], "unavailable")
        self.assertEqual(
            supervisor_router({"domain": res1["domain"], "routing_status": res1["status"], "available_domains": {"research"}}),
            "connect_prompt",
        )

        # 2. Affirmative follow-up "yes" after a disconnect prompt must also route to connect_prompt (never hallucinate)
        res2 = route_intent(
            [
                HumanMessage(content="check mail"),
                AIMessage(content="Gmail isn't connected yet."),
                HumanMessage(content="yes"),
            ],
            available_domains={"research"},
        )
        self.assertEqual(res2["domain"], "email")
        self.assertEqual(res2["status"], "unavailable")

    def test_lead_generation_progress_callback_and_background_cancel_registry(self):
        from agent.tasks import (
            clear_thread_cancel,
            format_deterministic_lead_summary,
            is_thread_cancelled,
            request_thread_cancel,
            update_job_progress,
        )

        clear_thread_cancel(999)
        self.assertFalse(is_thread_cancelled(999))

        prog = update_job_progress(
            {
                "step": "verifying_mail",
                "message": "Searching (14/100) → Verifying mail & E.164 phone for posthog.com...",
                "completed": 14,
                "quota": 100,
            },
            thread_id=999,
        )
        self.assertEqual(prog["completed"], 14)
        self.assertEqual(prog["quota"], 100)
        self.assertIn("Searching (14/100)", prog["message"])

        cancel_info = request_thread_cancel(999)
        self.assertTrue(cancel_info["cancelled"])
        self.assertTrue(is_thread_cancelled(999))
        clear_thread_cancel(999)

        summary_md = format_deterministic_lead_summary(
            {
                "campaign_parameters": {
                    "target_role": "Python Developer",
                    "target_locations": ["US", "UK"],
                    "company_size_filter": "10-50 employees",
                    "requested_quota": 100,
                },
                "raw_funnel_counts": {
                    "ats_boards_scanned": 12,
                    "filtered_vacancies_matched": 30,
                    "unique_verified_leads_exported": 20,
                    "companies_dropped_total": 5,
                    "leads_with_verified_linkedin": 20,
                    "leads_with_corporate_email": 18,
                    "leads_with_verified_phone": 6,
                },
                "grade_distribution_counts": {"GRADE_A": 10, "GRADE_B": 6, "GRADE_C": 2, "REVIEW_QUEUE": 2},
                "location_distribution_counts": {"US": 12, "UK": 8},
                "pure_python_export": {
                    "synced_to_google_sheets": True,
                    "spreadsheet_url": "https://docs.google.com/spreadsheets/d/sheet_123/edit",
                    "rows_inserted": 20,
                },
            }
        )
        self.assertIn("20/100 Verified Startup Leads", summary_md)
        self.assertIn("3-Axis Epistemic Confidence Summary", summary_md)
        self.assertIn("https://docs.google.com/spreadsheets/d/sheet_123/edit", summary_md)

