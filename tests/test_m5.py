"""M5 paired execution and auditable offline scoring."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from phishing_agent.config import load_config
from phishing_agent.evaluation import (Meter, digest, execute_case, failure_analysis,
    knowledge_cases, load_cases, run_experiment, score_result, score_run,
    validate_current_version)
from phishing_agent.eval_cli import main as eval_main

HIT = {"text": "Phishing links require caution and can be suspicious.",
       "document": "test.pdf", "document_sha256": "a" * 64, "page_start": 1, "page_end": 1,
       "chunk_id": "c1", "score": 0.1, "score_kind": "distance", "index_version": "test-index",
       "data_version": "test-data", "embedding": {}}


class Retriever:
    chunks = [HIT]
    embedding = {"model": "test-embedding"}
    index_version = "test-index"
    def retrieve(self, query, top_k):
        return [HIT]


def completion(**kwargs):
    return {"choices": [{"message": {"content": "Phishing links require caution [E1]."},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


def review_for(result, case, *, met=True, supported=True):
    return {"repetition": result.get("repetition", 0), "mode": result["mode"],
            "case_id": result["case_id"],
            "expected_result_status": case["expected_result_status"],
            "status_met": met, "status_reason": "Reviewed final state and answer",
            "conditions": [{"text": text, "met": met, "reason": "Reviewed saved answer and trace"}
                           for text in case["success_conditions"]],
            "critical_error": False,
            "claims": [{"text": "Phishing links require caution", "supported": supported,
                        "reason": "Compared exact answer and source excerpt",
                        "evidence_refs": ["E1"],
                        "evidence_quotes": ["Phishing links require caution"] if supported else []}],
            "failure": None if met and supported else
                {"type": "answer/grounding", "reason": "Reviewed claim lacks support or condition failed",
                 "trace_refs": [0]}}


class M5Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = load_config(ROOT / "config.json")
        self.retriever = Retriever()
        self.mail = self.root / "mail.eml"
        self.mail.write_text("From: a@example.org\nSubject: Alert\n\nVisit https://example.org/login",
                             encoding="utf-8")
        self.case = {"case_id": "test-event", "split": "development", "scenario": "clicked_no_entry",
                     "user_initial_input": "I clicked a link but did not enter information. What should I do?",
                     "mail_input": {"path": "mail.eml", "body_overlay": None},
                     "sample_sha256": hashlib.sha256(self.mail.read_bytes()).hexdigest(),
                     "fixed_clarification_facts": {"clicked": True, "entered_information": False},
                     "success_conditions": ["Give evidence based advice"],
                     "expected_result_status": "complete", "allowed_tools": ["parse_email", "analyze_iocs",
                                                                          "retrieve_knowledge"]}

    def test_paired_modes_share_baseline_and_keep_failures(self):
        run, path = run_experiment([self.case], ["B1", "W0", "A1"], self.config,
                                   self.retriever, completion, self.root, self.root / "out",
                                   split="development", provenance={"test": True})
        self.assertEqual(len(run["results"]), 3)
        self.assertEqual({r["case_sha256"] for r in run["results"]}, {digest(self.case)})
        self.assertEqual(run["config_sha256"], digest(self.config))
        self.assertEqual(run["conditions"]["B1"]["information"], "question_only")
        self.assertEqual(run["conditions"]["W0"]["information"], "question_and_email")
        self.assertEqual([r["execution_order"] for r in run["results"]], [0, 1, 2])
        self.assertTrue(path.exists())
        self.assertTrue(all(r["trace"] for r in run["results"]))
        reviews = [review_for(r, self.case, met=r["mode"] != "B1") for r in run["results"]]
        scored = score_run(run, [self.case], reviews)
        self.assertEqual(scored["modes"]["B1"]["case_success_rate"],
                         {"numerator": 0, "denominator": 1, "value": 0.0})
        self.assertEqual(scored["modes"]["B1"]["cost_per_successful_case"]["status"],
                         "not_computable_zero_success")
        self.assertEqual(scored["modes"]["W0"]["cost_per_successful_case"]["total"], 15)
        self.assertEqual(scored["modes"]["A1"]["evidence_support_rate"]["numerator"], 1)
        failures = failure_analysis(run, scored)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["failure_type"], "answer/grounding")
        self.assertEqual(failures[0]["trace_events"][0], run["results"][0]["trace"][0])
        self.assertEqual(failures[0]["evidence_refs"], ["E1"])
        self.assertEqual(score_run(json.loads(path.read_text()), [self.case], reviews), scored)
        missing = deepcopy(run)
        missing["results"].pop(0)
        with self.assertRaises(ValueError):
            score_run(missing, [self.case], reviews)

    def test_planning_and_reflection_ablate_one_factor(self):
        run, _ = run_experiment([self.case], ["W0", "A1", "A1-no-planning", "A1-no-reflection"],
                                self.config, self.retriever, completion, self.root,
                                self.root / "out", split="development", provenance={})
        conditions = run["conditions"]
        self.assertEqual(conditions["A1-no-planning"]["reflection"], conditions["A1"]["reflection"])
        self.assertNotEqual(conditions["A1-no-planning"]["planning"], conditions["A1"]["planning"])
        self.assertEqual(conditions["A1-no-reflection"]["planning"], conditions["A1"]["planning"])
        self.assertNotEqual(conditions["A1-no-reflection"]["reflection"], conditions["A1"]["reflection"])
        by_mode = {r["mode"]: r for r in run["results"]}
        def query(row):
            return next(t["params"]["query"] for t in row["trace"][-1]["plan"]
                        if t["tool"] == "retrieve_knowledge")
        self.assertNotEqual(query(by_mode["A1"]), query(by_mode["A1-no-planning"]))
        self.assertTrue(any(t["node"] == "verify" for t in by_mode["A1"]["trace"]))
        self.assertFalse(any(t["node"] == "verify" for t in by_mode["A1-no-reflection"]["trace"]))

    def test_invalid_reference_cannot_become_supported(self):
        result = execute_case(self.case, "B1", self.config, self.retriever, completion,
                              self.root, self.root / "b1.sqlite")
        good = review_for(result, self.case)
        self.assertTrue(score_result(result, self.case, good)["success"])
        bad = deepcopy(good)
        bad["claims"][0]["evidence_refs"] = ["E999"]
        with self.assertRaises(ValueError):
            score_result(result, self.case, bad)
        bad = deepcopy(good)
        bad["claims"][0]["evidence_quotes"] = ["Invented claim"]
        with self.assertRaises(ValueError):
            score_result(result, self.case, bad)
        bad_result = deepcopy(result)
        bad_result["answer"]["answer"] = "Phishing links require caution [E999]."
        with self.assertRaises(ValueError):
            score_result(bad_result, self.case, good)
        bad = review_for(result, self.case, supported=False)
        bad["failure"] = None
        self.assertTrue(score_result(result, self.case, bad)["success"])
        self.assertEqual(score_result(result, self.case, bad)["supported"], 0)
        bad["critical_error"] = True
        bad["critical_error_reason"] = "Unsupported central conclusion"
        bad["failure"] = {"type": "answer/grounding", "reason": "Unsupported central conclusion",
                          "trace_refs": [0]}
        self.assertFalse(score_result(result, self.case, bad)["success"])
        bad["failure"] = {"type": "clarification", "reason": "unrelated", "trace_refs": [0]}
        with self.assertRaises(ValueError):
            score_result(result, self.case, bad)

    def test_scripted_timeout_is_visible_and_counted(self):
        case = deepcopy(self.case)
        case["scenario"] = "tool_timeout"
        case["fixed_clarification_facts"]["injected_failure"] = "analyze_iocs timeout"
        result = execute_case(case, "A1", self.config, self.retriever, completion,
                              self.root, self.root / "timeout.sqlite")
        history = [h for event in result["trace"] for h in event.get("tool_history", [])]
        self.assertTrue(any(h["tool"] == "analyze_iocs" and h["status"] == "error" and
                            h["error_type"] == "TimeoutError" for h in history))
        self.assertGreaterEqual(result["diagnostics"]["tool_calls"], 3)

    def test_all_calls_including_failures_counted(self):
        calls = 0
        def flaky(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TimeoutError()
            return completion(**kwargs)
        meter = Meter(self.retriever, flaky)
        with self.assertRaises(TimeoutError):
            meter(messages=[{"content": "same"}])
        meter(messages=[{"content": "same"}])
        self.assertEqual(meter.summary()["llm_calls"], 2)
        self.assertEqual(meter.summary()["repeated_calls"], 1)
        self.assertEqual(meter.summary()["usage_missing_calls"], 1)
        self.assertEqual(meter.summary()["usage"]["total_tokens"], 15)

    def test_rate_limit_retry_keeps_failed_attempt(self):
        class Limited(Exception):
            status_code = 429
            response = type("Response", (), {"headers": {"retry-after": "1"}})()
        attempts = 0
        def flaky(**kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise Limited()
            return completion(**kwargs)
        with patch("phishing_agent.evaluation.time.sleep"):
            meter = Meter(self.retriever, flaky, retry_429=1)
            meter(messages=[{"content": "same"}])
        self.assertEqual([c["status"] for c in meter.calls], ["error", "ok"])
        self.assertEqual(meter.summary()["usage_missing_calls"], 1)
        self.assertEqual(meter.summary()["usage"]["total_tokens"], 15)

    def test_daily_limit_stops_without_wait_and_keeps_partial_run(self):
        class DailyLimit(Exception):
            status_code = 429
            body = {"error": {"message": "tokens per day limit reached"}}
        def unavailable(**kwargs):
            raise DailyLimit()
        with patch("phishing_agent.evaluation.time.sleep") as sleeping:
            with self.assertRaisesRegex(RuntimeError, "service error"):
                run_experiment([self.case], ["B1", "W0"], self.config,
                               self.retriever, unavailable, self.root, self.root / "out",
                               split="development", provenance={"test": True}, retry_429=2)
        sleeping.assert_not_called()
        partial = next((self.root / "out").glob("*/partial.json"))
        saved = json.loads(partial.read_text())
        self.assertEqual(len(saved["results"]), 1)
        self.assertEqual(saved["results"][0]["calls"][-1]["rate_limit_scope"], "daily")
        self.assertEqual(saved["results"][0]["diagnostics"]["usage_missing_calls"], 1)
        self.assertEqual(saved["results"][0]["trace"][-1]["node"], "service_calls")
        review = review_for(saved["results"][0], self.case, met=False)
        review["claims"] = []
        review["failure"] = {"type": "service/provider", "reason": "HTTP 429 daily limit",
                             "trace_refs": [len(saved["results"][0]["trace"]) - 1]}
        self.assertFalse(score_result(saved["results"][0], self.case, review)["success"])

    def test_split_guard_and_knowledge_regression(self):
        dev = load_cases(ROOT / "evaluation/cases/m2_cases.json", "development")
        hold = load_cases(ROOT / "evaluation/cases/m2_cases.json", "held_out")
        self.assertEqual(len(dev), 12)
        self.assertEqual(len(hold), 12)
        with self.assertRaises(ValueError):
            load_cases(ROOT / "evaluation/cases/m2_cases.json", "development",
                       case_ids=[hold[0]["case_id"]])
        with self.assertRaises(ValueError):
            run_experiment([dev[0], hold[0]], ["B1"], self.config, self.retriever,
                           completion, ROOT, self.root / "out", split="development")
        self.assertEqual(len(knowledge_cases(ROOT / "evaluation/queries.json")), 10)

    def test_repeat_reports_variation_without_dropping_a_case(self):
        case = knowledge_cases(ROOT / "evaluation/queries.json")[0]
        run, _ = run_experiment([case], ["B1"], self.config, self.retriever,
                                completion, self.root, self.root / "out",
                                split="knowledge_regression", repeat=2, provenance={})
        reviews = [review_for(r, case, met=r["repetition"] == 0) for r in run["results"]]
        summary = score_run(run, [case], reviews)["modes"]["B1"]
        self.assertEqual(summary["repeat_case_success_rates"], [1.0, 0.0])
        self.assertEqual(summary["repeat_range"], [0.0, 1.0])
        self.assertEqual(summary["case_success_rate"]["denominator"], 2)

    def test_interleaving_rotates_mode_order_by_case(self):
        cases = knowledge_cases(ROOT / "evaluation/queries.json")[:2]
        run, _ = run_experiment(cases, ["B1", "W0", "A1"], self.config, self.retriever,
                                completion, self.root, self.root / "out",
                                split="knowledge_regression", provenance={"test": True})
        self.assertEqual([(r["case_id"], r["mode"]) for r in run["results"]],
                         [("Q1", "B1"), ("Q1", "W0"), ("Q1", "A1"),
                          ("Q2", "W0"), ("Q2", "A1"), ("Q2", "B1")])

    def test_version_guard_and_immutable_review_scores(self):
        case = knowledge_cases(ROOT / "evaluation/queries.json")[0]
        run, path = run_experiment([case], ["B1"], self.config, self.retriever,
                                   completion, self.root, self.root / "out",
                                   split="knowledge_regression", provenance={"test": True})
        with patch("phishing_agent.evaluation.code_provenance", return_value={
                "git_commit": "c1", "git_dirty": False, "source_tree_sha256": "s1"}):
            run["code"] = {"git_commit": "c1", "git_dirty": False, "source_tree_sha256": "s1"}
            self.assertTrue(validate_current_version(run, self.root))
            run["code"]["git_commit"] = "old"
            with self.assertRaises(ValueError):
                validate_current_version(run, self.root)
        reviews = [review_for(run["results"][0], case)]
        review_path = self.root / "reviews.json"
        review_path.write_text(json.dumps(reviews), encoding="utf-8")
        args = ["score", str(path), "--reviews", str(review_path),
                "--queries", str(ROOT / "evaluation/queries.json"), "--allow-historical"]
        eval_main(args)
        first = sorted(path.parent.glob("score-*.json"))
        self.assertEqual(len(first), 1)
        content = first[0].read_text()
        eval_main(args)
        self.assertEqual(first[0].read_text(), content)
        reviews[0] = review_for(run["results"][0], case, met=False)
        review_path.write_text(json.dumps(reviews), encoding="utf-8")
        eval_main(args)
        self.assertEqual(len(list(path.parent.glob("score-*.json"))), 2)


if __name__ == "__main__":
    unittest.main()
