"""M4 routing, recovery, and termination tests without a model service."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from phishing_agent.graph import open_workflow
from phishing_agent.planning import default_plan, validate_plan
from phishing_agent.verification import finding, verify

CONFIG = {"top_k": 1, "context_budget_chars": 10000, "prompt_version": "b1-v1",
          "model": "test", "generation": {"max_tokens": 100, "temperature": 0, "top_p": 1}}
HIT = {"text": "Phishing links can be suspicious; opening alone does not prove account access.",
       "document": "test.pdf", "document_sha256": "a" * 64, "page_start": 1, "page_end": 1,
       "chunk_id": "c1", "score": 0.1, "score_kind": "distance", "index_version": "v1",
       "data_version": "v1", "embedding": {}}
MAIL = "From: alerts@example.org\nSubject: Alert\n\nVisit https://example.org/login"


class Retriever:
    chunks = [HIT]
    embedding = {}
    def __init__(self, results=None):
        self.calls = []
        self.results = results
    def retrieve(self, query, top_k):
        self.calls.append(query)
        return [HIT] if self.results is None else self.results


def completion(**kwargs):
    return {"choices": [{"message": {"content": "Phishing links require caution [E1]."},
                         "finish_reason": "stop"}]}


class M4Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "state.sqlite"
        self.retriever = Retriever()

    def workflow(self, **kwargs):
        context = open_workflow(self.path, kwargs.pop("retriever", self.retriever), CONFIG,
                                kwargs.pop("completion", completion), **kwargs)
        self.addCleanup(context.__exit__, None, None, None)
        return context.__enter__()

    def test_simple_question_short_plan(self):
        state = self.workflow().run("simple", {"kind": "knowledge", "question": "What is phishing?"})
        self.assertEqual([t["tool"] for t in state["plan"]], ["retrieve_knowledge"])
        self.assertEqual(state["budget"]["tools"], 1)
        self.assertEqual(state["budget"]["replans"], 0)
        self.assertEqual(state["status"], "completed")

    def test_email_plan_routes_only_needed_tools(self):
        w = self.workflow()
        linked = w.run("linked", {"kind": "email", "question": "Is this suspicious?", "email_text": MAIL})
        plain = w.run("plain", {"kind": "email", "question": "Is this suspicious?",
                               "email_text": "From: a@example.org\nSubject: Hello\n\nHello"})
        self.assertEqual([h["tool"] for h in linked["tool_history"]],
                         ["parse_email", "analyze_iocs", "retrieve_knowledge"])
        self.assertEqual([h["tool"] for h in plain["tool_history"]],
                         ["parse_email", "retrieve_knowledge"])
        self.assertEqual(plain["plan"][1]["status"], "skipped")

    def test_plan_validation_rejects_bad_tools_params_dependencies_and_duplicates(self):
        request = {"kind": "knowledge", "question": "What is phishing?"}
        task = default_plan({"request": request})[0]
        for change in ({"tool": "reputation_api"}, {"params": {}}, {"dependency": ["missing"]}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_plan([{**task, **change}], request)
        with self.assertRaises(ValueError):
            validate_plan([task, {**task, "task_id": "T2"}], request)
        with self.assertRaises(ValueError):
            validate_plan([dict(task, task_id=f"T{i}", params={"query": str(i)}) for i in range(5)], request)

    def test_replan_reuses_successful_tool_result(self):
        calls = []
        def verifier(state):
            calls.append(1)
            return [finding("changed_direction", "replan", "new evidence")] if len(calls) == 1 else []
        state = self.workflow(verifier=verifier).run("reuse", {"kind": "knowledge", "question": "What is phishing?"})
        self.assertEqual(len(self.retriever.calls), 1)
        self.assertEqual(state["budget"]["replans"], 1)
        self.assertEqual(state["plan_revision"], 2)
        self.assertEqual(len(state["invalidated_tasks"]), 1)

    def test_fact_correction_supersedes_and_invalidates(self):
        w = self.workflow()
        first = w.run("correction", {"kind": "email", "question": "Is this suspicious?",
                                     "email_text": MAIL, "facts": {"clicked_link": True}})
        calls = len(self.retriever.calls)
        second = w.run("correction", {"kind": "email", "question": "Is this suspicious?",
                                      "email_text": MAIL, "facts": {"clicked_link": False}})
        self.assertTrue(first["answer"])
        self.assertTrue(all(t["status"] == "invalidated" for t in second["invalidated_tasks"]))
        self.assertEqual(second["verification"], [])
        self.assertEqual(len(self.retriever.calls), calls)
        self.assertTrue(all(h.get("cached") for h in second["tool_history"]))
        self.assertEqual([f["status"] for f in second["facts"] if f["name"] == "clicked_link"],
                         ["superseded", "user_reported"])

    def test_unsupported_answer_detected_and_repaired_once(self):
        calls = []
        def bad_then_good(**kwargs):
            calls.append(1)
            body = "The account is safe." if len(calls) == 1 else "Phishing links require caution [E1]."
            return {"choices": [{"message": {"content": body}, "finish_reason": "stop"}]}
        state = self.workflow(completion=bad_then_good).run("repair", {"kind": "knowledge", "question": "What is phishing?"})
        self.assertEqual(state["budget"]["repairs"], 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(state["status"], "completed")

    def test_knowledge_gap_uses_targeted_retrieval_then_stops(self):
        retriever = Retriever([])
        state = self.workflow(retriever=retriever).run("gap", {"kind": "knowledge", "question": "Unknown policy?"})
        self.assertEqual(state["budget"]["retrieval_extra"], 1)
        self.assertEqual(state["budget"]["tools"], 2)
        self.assertEqual(state["status"], "limited")
        self.assertEqual(state["verification"][0]["recovery_action"], "retrieve")
        self.assertEqual([h["status"] for h in state["tool_history"]], ["no_result", "no_result"])

    def test_tool_budget_exhaustion_terminates(self):
        def planner(state):
            tasks = []
            for index in range(4):
                tasks.append({"task_id": f"T{index}", "goal": "Find evidence", "tool": "retrieve_knowledge",
                              "params": {"query": f"{state['plan_revision']} query {index}"},
                              "dependency": [], "completion_condition": "tool result recorded",
                              "status": "pending", "evidence_ref": []})
            return tasks
        def verifier(state):
            return [finding("changed_direction", "replan", "new evidence")]
        state = self.workflow(planner=planner, verifier=verifier).run(
            "budget", {"kind": "knowledge", "question": "What is phishing?"})
        self.assertEqual(state["budget"]["tools"], 6)
        self.assertEqual(state["status"], "limited")
        self.assertEqual(state["recovery"], "bounded_conclusion")

    def test_clarification_preserves_budget(self):
        w = self.workflow()
        waiting = w.run("clarify", {"kind": "email", "question": "Is my account compromised?", "email_text": MAIL})
        self.assertEqual(waiting["verification"][0]["recovery_action"], "clarify")
        used = waiting["budget"]["tools"]
        after = w.resume("clarify", {"clicked_link": True})
        self.assertEqual(after["budget"]["tools"], used)
        done = w.resume("clarify", {"credential_submitted": False})
        self.assertEqual(done["budget"]["tools"], used)
        self.assertEqual(done["budget"]["clarifications"], 2)

    def test_tool_failure_is_error_and_retry_is_bounded(self):
        class Broken(Retriever):
            def retrieve(self, query, top_k):
                self.calls.append(query)
                raise TimeoutError()
        retriever = Broken()
        state = self.workflow(retriever=retriever).run("broken", {"kind": "knowledge", "question": "What is phishing?"})
        self.assertEqual([h["status"] for h in state["tool_history"]], ["error", "error"])
        self.assertEqual(state["budget"]["retries"], 1)
        self.assertEqual(state["status"], "limited")

    def test_repeated_bad_answer_stops_after_repair_budget(self):
        def unsupported(**kwargs):
            return {"choices": [{"message": {"content": "Everything is safe."}, "finish_reason": "stop"}]}
        state = self.workflow(completion=unsupported).run("loop", {"kind": "knowledge", "question": "What is phishing?"})
        self.assertEqual(state["budget"]["repairs"], 1)
        self.assertEqual(state["budget"]["llm"], 2)
        self.assertEqual(state["status"], "limited")

    def test_verifier_detects_stale_answer_and_unperformed_action(self):
        state = {"answer": {"answer": "I have blocked it [E1].", "fact_revision": 1},
                 "evidence": [{"id": "E1"}], "facts": [{}, {}], "tool_history": []}
        self.assertEqual(verify(state)[0]["failure_type"], "unperformed_action")
        state["answer"]["answer"] = "Use caution [E1]."
        self.assertEqual(verify(state)[0]["failure_type"], "superseded_fact")


if __name__ == "__main__":
    unittest.main()
