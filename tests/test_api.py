"""HTTP contract tests with an in-memory A1-shaped workflow fake."""
import os
from pathlib import Path
import sys
import unittest

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from phishing_agent.api import create_app


class FakeWorkflow:
    def __init__(self):
        self.cases = {}
        self.resume_calls = []
        self.raise_on_run = None

    @staticmethod
    def completed(case_id):
        return {
            "case_id": case_id,
            "run_id": "run-1",
            "status": "completed",
            "facts": [{"name": "email_from", "value": "sender@example.test",
                       "status": "observed", "run_id": "run-1"}],
            "evidence": [{"id": "I1", "kind": "iocs", "data": {
                "iocs": [{"kind": "url", "normalized": "http://example.test/",
                          "observations": ["http_scheme"]}], "warnings": []}}],
            "answer": {"answer": "Use the trusted reporting channel [E1].", "usage": {"total_tokens": 12}},
            "clarification": {"pending": None, "question": None, "asked": []},
            "tool_history": [{"tool": "parse_email", "status": "ok"}],
            "verification": [],
            "budget": {"tools": 1, "llm": 1},
            "recovery": "",
        }

    def run(self, case_id, request):
        if self.raise_on_run:
            raise self.raise_on_run
        if "clarify" in request["question"].lower():
            state = self.completed(case_id)
            state.update(status="waiting_clarification", answer={}, clarification={
                "pending": "clicked_link", "question": "Did you click a link?", "asked": ["clicked_link"]
            })
        else:
            state = self.completed(case_id)
        self.cases[case_id] = state
        return state

    def resume(self, case_id, facts):
        self.resume_calls.append((case_id, facts))
        state = self.completed(case_id)
        state["facts"].append({"name": next(iter(facts)), "value": next(iter(facts.values())),
                               "status": "user_reported", "run_id": "run-1"})
        self.cases[case_id] = state
        return state

    def state(self, case_id):
        return self.cases.get(case_id, {})


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.workflow = FakeWorkflow()
        self.client_context = TestClient(create_app(self.workflow))
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)

    def test_health(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_analyze_validation_distinguishes_semantic_and_schema_errors(self):
        semantic = self.client.post("/api/analyze", json={})
        schema = self.client.post("/api/analyze", json={"email_text": 42})
        self.assertEqual(semantic.status_code, 400)
        self.assertEqual(schema.status_code, 422)
        self.assertEqual(semantic.json()["error"]["code"], "invalid_input")
        self.assertEqual(schema.json()["error"]["code"], "schema_validation_error")

    def test_new_case_generates_case_id_and_completed_shape(self):
        response = self.client.post("/api/analyze", json={"incident_description": "What is phishing?"})
        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["case_id"])
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["suspicious_signals"], ["http_scheme"])
        self.assertEqual(set(body), {"case_id", "status", "facts", "suspicious_signals", "iocs",
                                    "answer", "clarification", "evidence", "trace_summary", "usage", "error"})
        self.assertIsNone(body["error"])

    def test_clarification_and_reply_continue_same_case(self):
        waiting = self.client.post("/api/analyze", json={
            "case_id": "case-clarify", "email_text": "From: x@example.test\n\nHello",
            "incident_description": "Clarify what I should do",
        })
        self.assertEqual(waiting.json()["status"], "needs_clarification")
        self.assertEqual(waiting.json()["clarification"]["field"], "clicked_link")
        resumed = self.client.post("/api/cases/case-clarify/reply", json={"reply": "no"})
        self.assertEqual(resumed.status_code, 200)
        self.assertEqual(resumed.json()["case_id"], "case-clarify")
        self.assertEqual(resumed.json()["status"], "completed")
        self.assertEqual(self.workflow.resume_calls, [("case-clarify", {"clicked_link": False})])

    def test_get_unknown_case_returns_uniform_404(self):
        response = self.client.get("/api/cases/missing")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["status"], "failed")
        self.assertEqual(response.json()["error"]["code"], "case_not_found")
        self.assertEqual(response.json()["facts"], [])

    def test_get_case_returns_latest_state(self):
        self.workflow.cases["existing"] = self.workflow.completed("existing")
        response = self.client.get("/api/cases/existing")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["case_id"], "existing")
        self.assertEqual(response.json()["status"], "completed")

    def test_upstream_429_is_sanitized(self):
        class Limited(Exception):
            status_code = 429

        self.workflow.raise_on_run = Limited("GROQ_API_KEY=secret email body secret")
        response = self.client.post("/api/analyze", json={
            "case_id": "limited", "incident_description": "What is phishing?"
        })
        serialized = response.text
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"]["code"], "upstream_rate_limited")
        self.assertNotIn("GROQ_API_KEY", serialized)
        self.assertNotIn("secret", serialized)
        self.assertNotIn("Traceback", serialized)

    def test_persisted_rate_limit_result_is_429(self):
        def limited_run(case_id, _request):
            state = FakeWorkflow.completed(case_id)
            state["status"] = "limited"
            state["answer"] = {"answer": "", "status": "request_error",
                               "error": {"type": "RateLimitError"}}
            self.workflow.cases[case_id] = state
            return state
        self.workflow.run = limited_run
        response = self.client.post("/api/analyze", json={
            "case_id": "limited-result", "incident_description": "What is phishing?"
        })
        self.assertEqual(response.status_code, 429)

    def test_internal_error_does_not_leak_key_traceback_or_email(self):
        self.workflow.raise_on_run = RuntimeError("sk-secret-key Traceback private email body")
        response = self.client.post("/api/analyze", json={
            "case_id": "broken", "email_text": "sensitive mail", "incident_description": "Check this"
        })
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"]["code"], "internal_error")
        for secret in ("sk-secret-key", "Traceback", "sensitive mail", "private email body"):
            self.assertNotIn(secret, response.text)

    def test_cors_allows_only_vue_demo_origin(self):
        allowed = self.client.options("/api/analyze", headers={
            "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"
        })
        rejected = self.client.options("/api/analyze", headers={
            "Origin": "https://example.test", "Access-Control-Request-Method": "POST"
        })
        self.assertEqual(allowed.headers.get("access-control-allow-origin"), "http://localhost:5173")
        self.assertIsNone(rejected.headers.get("access-control-allow-origin"))


if __name__ == "__main__":
    unittest.main()
