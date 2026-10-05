"""M4 routing, recovery, and termination tests without a model service."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from phishing_agent.graph import open_workflow
from phishing_agent.planning import default_plan, partition_corrected_tasks, validate_plan
from phishing_agent.verification import finding, verify
from phishing_agent.rag.context import build_context as real_build_context
import phishing_agent.graph as graph_module

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
            validate_plan([{k: v for k, v in task.items() if k != "fact_dependencies"}], request)
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
        self.assertEqual([(t["task_id"], t["status"]) for t in second["invalidated_tasks"]],
                         [("T3", "invalidated")])
        self.assertEqual([(t["task_id"], t["status"]) for t in second["retained_tasks"]],
                         [("T1", "ok"), ("T2", "ok")])
        self.assertEqual(second["verification"], [])
        self.assertEqual(len(self.retriever.calls), calls + 1)
        self.assertEqual([h.get("cached", False) for h in second["tool_history"]], [True, True, False])
        self.assertNotEqual(first["plan"][2]["params"], second["plan"][2]["params"])
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

    def test_valid_citation_cannot_support_invented_claim(self):
        state = {"request": {"kind": "knowledge", "question": "What is phishing?"},
                 "answer": {"answer": "Phishing links reveal employee salary data [E1]."},
                 "evidence": [{"id": "E1", "kind": "knowledge", "data": HIT}],
                 "context": real_build_context([HIT], 10000)["text"],
                 "facts": [], "tool_history": []}
        result = verify(state)
        self.assertEqual(result[0]["failure_type"], "unsupported_claim")
        self.assertEqual(result[0]["recovery_action"], "repair_answer")
        state["answer"]["answer"] = "Phishing links are not suspicious [E1]."
        self.assertEqual(verify(state)[0]["failure_type"], "unsupported_claim")
        state["answer"]["answer"] = "Phishing links prove account access [E1]."
        self.assertEqual(verify(state)[0]["failure_type"], "unsupported_claim")

    def test_each_substantive_sentence_needs_its_own_citation(self):
        state = {"request": {"kind": "knowledge", "question": "What is phishing?"},
                 "answer": {"answer": "Phishing links are risky [E1]. NASA runs all phishing campaigns."},
                 "evidence": [{"id": "E1", "kind": "knowledge", "data": HIT}],
                 "context": real_build_context([HIT], 10000)["text"],
                 "facts": [], "tool_history": []}
        self.assertEqual(verify(state)[0]["failure_type"], "missing_citation")
        state["answer"]["answer"] = "Phishing links are risky [E1]."
        self.assertEqual(verify(state), [])
        state["answer"]["answer"] = "NASA runs all phishing campaigns [E1]."
        self.assertEqual(verify(state)[0]["failure_type"], "unsupported_claim")

    def test_risk_direction_and_certainty_follow_cited_evidence(self):
        state = {"request": {"kind": "knowledge", "question": "What is phishing?"},
                 "answer": {"answer": ""},
                 "evidence": [{"id": "E1", "kind": "knowledge", "data": HIT}],
                 "context": real_build_context([HIT], 10000)["text"],
                 "facts": [], "tool_history": []}
        supported = (
            "Phishing links may pose a security threat [E1].",
            "Phishing links are risky [E1].",
            "Phishing links are not safe [E1].",
        )
        unsupported = (
            "Phishing links are risk free [E1].",
            "Phishing links are not suspicious [E1].",
            "Phishing links definitely pose a security threat [E1].",
            "Phishing links are malicious [E1].",
        )
        for answer in supported:
            with self.subTest(answer=answer):
                state["answer"]["answer"] = answer
                self.assertEqual(verify(state), [])
        for answer in unsupported:
            with self.subTest(answer=answer):
                state["answer"]["answer"] = answer
                self.assertEqual(verify(state)[0]["failure_type"], "unsupported_claim")

    def test_context_omission_rebuilds_before_final_answer(self):
        omitted = {"request": {"kind": "knowledge", "question": "What is phishing?"},
                   "answer": {"answer": "Phishing links require caution [E1]."},
                   "evidence": [{"id": "E1", "kind": "knowledge", "data": HIT}],
                   "context": "[E1] Phishing", "facts": [], "tool_history": []}
        self.assertEqual(verify(omitted)[0]["recovery_action"], "rebuild_context")
        calls = []
        def incomplete_then_full(hits, budget):
            calls.append(1)
            if len(calls) == 1:
                return {"text": "[E1] test.pdf | pages 1-1 | c1\nPhishing", "evidence": []}
            return real_build_context(hits, budget)
        with patch.object(graph_module, "build_context", side_effect=incomplete_then_full):
            state = self.workflow().run("context", {"kind": "knowledge", "question": "What is phishing?"})
        self.assertEqual(len(calls), 2)
        self.assertEqual(state["budget"]["repairs"], 1)
        self.assertEqual(state["status"], "completed")
        self.assertIn(HIT["text"], state["context"])

    def test_unrelated_fact_correction_retains_knowledge_task(self):
        w = self.workflow()
        request = {"kind": "knowledge", "question": "What is phishing?"}
        w.run("unrelated", {**request, "facts": {"device": "laptop"}})
        state = w.run("unrelated", {**request, "facts": {"device": "phone"}})
        self.assertEqual(state["invalidated_tasks"], [])
        self.assertEqual([(t["tool"], t["status"]) for t in state["retained_tasks"]],
                         [("retrieve_knowledge", "ok")])
        self.assertEqual(len(self.retriever.calls), 1)

    def test_fact_dependency_invalidation_propagates_to_descendants(self):
        tasks = [
            {"task_id": "A", "dependency": [], "fact_dependencies": ["clicked_link"], "status": "ok", "evidence_ref": ["E1"]},
            {"task_id": "B", "dependency": ["A"], "fact_dependencies": [], "status": "ok", "evidence_ref": ["E2"]},
            {"task_id": "C", "dependency": [], "fact_dependencies": [], "status": "ok", "evidence_ref": ["E3"]},
        ]
        invalidated, retained = partition_corrected_tasks(tasks, {"clicked_link"})
        self.assertEqual([t["task_id"] for t in invalidated], ["A", "B"])
        self.assertEqual([(t["task_id"], t["status"]) for t in retained], [("C", "ok")])

    def test_custom_plan_reuses_only_matching_task_and_params(self):
        class DistinctRetriever(Retriever):
            def retrieve(self, query, top_k):
                self.calls.append(query)
                return [{**HIT, "chunk_id": "chunk-" + query.replace(" ", "-")}]
        retriever = DistinctRetriever()
        def planner(state):
            clicked = next((f["value"] for f in reversed(state["facts"])
                            if f["name"] == "clicked_link" and f["status"] == "user_reported"), None)
            return [
                {"task_id": "T1", "goal": "Action guidance", "tool": "retrieve_knowledge",
                 "params": {"query": f"action {clicked}"}, "dependency": [],
                 "completion_condition": "tool result recorded", "status": "pending",
                 "evidence_ref": [], "fact_dependencies": ["clicked_link"]},
                {"task_id": "T2", "goal": "General guidance", "tool": "retrieve_knowledge",
                 "params": {"query": "general"}, "dependency": [],
                 "completion_condition": "tool result recorded", "status": "pending",
                 "evidence_ref": [], "fact_dependencies": []},
            ]
        w = self.workflow(retriever=retriever, planner=planner)
        request = {"kind": "knowledge", "question": "What is phishing?"}
        w.run("custom", {**request, "facts": {"clicked_link": True}})
        changed = w.run("custom", {**request, "facts": {"clicked_link": False}})
        self.assertEqual(retriever.calls, ["action True", "general", "action False"])
        self.assertEqual([t["task_id"] for t in changed["invalidated_tasks"]], ["T1"])
        self.assertEqual([t["task_id"] for t in changed["retained_tasks"]], ["T2"])
        self.assertEqual([h["task_id"] for h in changed["tool_history"] if h.get("cached")], ["T2"])

    def test_parse_retry_success_reactivates_ioc_dependency(self):
        real_parse = graph_module.parse_email
        calls = []
        def flaky_parse(**kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise TimeoutError()
            return real_parse(**kwargs)
        with patch.object(graph_module, "parse_email", side_effect=flaky_parse):
            state = self.workflow().run("parse-retry", {"kind": "email", "question": "Is this suspicious?",
                                                       "email_text": MAIL})
        self.assertEqual(len(calls), 2)
        self.assertEqual([(t["tool"], t["status"]) for t in state["plan"]],
                         [("parse_email", "ok"), ("analyze_iocs", "ok"), ("retrieve_knowledge", "ok")])
        self.assertEqual([h["status"] for h in state["tool_history"]], ["error", "ok", "ok", "ok"])
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
                              "status": "pending", "evidence_ref": [], "fact_dependencies": []})
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
