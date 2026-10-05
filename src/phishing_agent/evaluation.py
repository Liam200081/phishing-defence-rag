"""M5 paired case execution, immutable traces, and review based scoring."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
import uuid

from .config import validate_config
from .graph import open_workflow
from .pipeline import run_queries
from .trace import code_provenance

MODES = {"B1": (False, False), "W0": (False, False), "A1": (True, True),
         "A1-no-planning": (False, True), "A1-no-reflection": (True, False)}
FAILURE_TYPES = {"retrieval/context", "planning", "tool routing/execution", "fact/state",
                 "clarification", "verification/reflection", "budget/termination", "answer/grounding",
                 "service/provider"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     default=str).encode()).hexdigest()


def load_cases(path, split, *, case_ids=None):
    if split not in {"development", "held_out"}:
        raise ValueError("split must be development or held_out")
    source = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = source["cases"]
    ids = [c["case_id"] for c in cases]
    if len(ids) != len(set(ids)) or any(c["split"] not in {"development", "held_out"} for c in cases):
        raise ValueError("invalid case manifest")
    selected = [c for c in cases if c["split"] == split and
                (case_ids is None or c["case_id"] in case_ids)]
    if not selected or (case_ids is not None and set(case_ids) != {c["case_id"] for c in selected}):
        raise ValueError("case IDs must exist entirely in the selected split")
    return selected


def knowledge_cases(path):
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    return [{"case_id": q["query_id"], "split": "knowledge_regression",
             "user_initial_input": q["query"], "expected_result_status": "complete",
             "success_conditions": ["Answer the knowledge question with supported evidence."],
             "forbidden_key_assertions": [], "allowed_tools": ["retrieve_knowledge"]} for q in rows]


def _usage(raw):
    if not isinstance(raw, dict):
        raw = raw.model_dump() if hasattr(raw, "model_dump") else {}
    usage = raw.get("usage") or {}
    if not isinstance(usage, dict):
        usage = usage.model_dump() if hasattr(usage, "model_dump") else {}
    return {"input_tokens": usage.get("prompt_tokens", usage.get("input_tokens")),
            "output_tokens": usage.get("completion_tokens", usage.get("output_tokens")),
            "total_tokens": usage.get("total_tokens")}


class Meter:
    def __init__(self, retriever, completion, *, gate=None, retry_429=0):
        self.retriever = retriever
        self.completion = completion
        self.gate = gate
        self.retry_429 = retry_429
        self.calls = []
        self.throttle_seconds = 0.0
        self.chunks = retriever.chunks
        self.embedding = retriever.embedding

    def retrieve(self, query, top_k):
        row = {"kind": "tool", "name": "retrieve_knowledge", "key": digest([query, top_k]),
               "status": "error"}
        self.calls.append(row)
        try:
            result = self.retriever.retrieve(query, top_k)
            row["status"] = "ok"
            return result
        except Exception as exc:
            row["error_type"] = type(exc).__name__
            row["status_code"] = getattr(exc, "status_code", None)
            raise

    def __call__(self, **kwargs):
        for attempt in range(self.retry_429 + 1):
            if self.gate:
                self.throttle_seconds += self.gate.wait()
            row = {"kind": "llm", "name": "generate", "key": digest(kwargs.get("messages")),
                   "status": "error", "usage": None, "attempt": attempt + 1}
            self.calls.append(row)
            try:
                raw = self.completion(**kwargs)
                row.update(status="ok", usage=_usage(raw))
                return raw
            except Exception as exc:
                row["error_type"] = type(exc).__name__
                row["status_code"] = getattr(exc, "status_code", None)
                if row["status_code"] != 429 or attempt == self.retry_429:
                    raise
                body = str(getattr(exc, "body", ""))
                if "tokens per day" in body.lower() or "TPD" in body:
                    row["rate_limit_scope"] = "daily"
                    raise
                headers = getattr(getattr(exc, "response", None), "headers", {}) or {}
                try:
                    delay = min(60.0, max(1.0, float(headers.get("retry-after", 15))))
                except (TypeError, ValueError):
                    delay = 15.0
                time.sleep(delay)
                self.throttle_seconds += delay

    def summary(self):
        counts = Counter(c["kind"] for c in self.calls)
        keys = Counter((c["kind"], c["key"]) for c in self.calls)
        token_rows = [c["usage"] for c in self.calls if c["kind"] == "llm" and c["usage"]]
        return {"llm_calls": counts["llm"], "tool_calls": counts["tool"],
                "repeated_calls": sum(n - 1 for n in keys.values() if n > 1),
                "usage": {k: sum(u[k] for u in token_rows if isinstance(u[k], int))
                          for k in ("input_tokens", "output_tokens", "total_tokens")},
                "usage_missing_calls": sum(c["kind"] == "llm" and
                                           (not c["usage"] or c["usage"]["total_tokens"] is None)
                                           for c in self.calls)}


class RateGate:
    def __init__(self, min_interval_seconds=0):
        if min_interval_seconds < 0:
            raise ValueError("min interval must be nonnegative")
        self.interval = min_interval_seconds
        self.last = None

    def wait(self):
        now = time.monotonic()
        delay = max(0.0, self.interval - (now - self.last)) if self.last is not None else 0.0
        if delay:
            time.sleep(delay)
        self.last = time.monotonic()
        return delay

def _request(case, root):
    question = case["user_initial_input"]
    if case["case_id"].startswith("Q") or case["scenario"] == "q8_context_regression":
        return {"kind": "knowledge", "question": question}
    mail = case["mail_input"]
    path = root / mail["path"]
    if hashlib.sha256(path.read_bytes()).hexdigest() != case["sample_sha256"]:
        raise ValueError("mail sample hash mismatch: " + case["case_id"])
    request = {"kind": "email", "question": question, "email_path": str(path)}
    facts = case.get("fixed_clarification_facts", {})
    mapped = {}
    if type(facts.get("clicked")) is bool:
        mapped["clicked_link"] = facts["clicked"]
    if type(facts.get("entered_information")) is bool:
        mapped["credential_submitted"] = facts["entered_information"]
    if facts.get("initial_report") == "password_entered":
        mapped.update(clicked_link=True, credential_submitted=True)
    request["facts"] = mapped
    if mail.get("body_overlay"):
        # The appended text is explicitly synthetic and remains untrusted mail input.
        request.pop("email_path")
        request["email_text"] = path.read_bytes().decode("utf-8", errors="replace") + "\n" + mail["body_overlay"]
    return request


def _replies(case):
    facts = case.get("fixed_clarification_facts", {})
    return {"clicked_link": facts.get("clicked"),
            "credential_submitted": facts.get("entered_information")}


def _snapshots(workflow, case_id):
    cfg = workflow._config(case_id)
    return [{"node": s.next[0] if s.next else "finished",
             "checkpoint_step": s.metadata.get("step"),
             "status": s.values.get("status"), "plan": s.values.get("plan", []),
             "facts": s.values.get("facts", []),
             "tool_history": s.values.get("tool_history", []),
             "evidence": s.values.get("evidence", []),
             "verification": s.values.get("verification", []),
             "answer": s.values.get("answer", {}),
             "budget": s.values.get("budget", {}), "clarification": s.values.get("clarification", {})}
            for s in reversed(list(workflow.graph.get_state_history(cfg))) if s.values]


def execute_case(case, mode, config, retriever, completion, root, sqlite_path, *, gate=None, retry_429=0):
    if mode not in MODES:
        raise ValueError("unknown mode")
    meter = Meter(retriever, completion, gate=gate, retry_429=retry_429)
    request = _request(case, Path(root))
    started = time.monotonic()
    replies_used, trace, error = [], [], None
    try:
        if mode == "B1":
            b1 = run_queries([{"query_id": case["case_id"], "query": request["question"]}],
                             config, meter.retrieve, meter, data_manifest={},
                             embedding=retriever.embedding, index_version=retriever.index_version,
                             provenance={}, encode=None)
            item = b1["results"][0]
            answer = item["response"]
            evidence = [{"id": "E" + str(i), "kind": "knowledge", "data": e,
                         "result_ref": "b1:retrieval"}
                        for i, e in enumerate(item["context"]["evidence"], 1)]
            status = "completed" if answer["complete"] else "limited"
            trace = [{"node": "B1", "status": status, "answer": answer, "evidence": evidence,
                      "retrieved": item["retrieved"], "context": item["context"],
                      "messages": item["messages"], "evaluation": item["evaluation"]}]
            diagnostic = item["evaluation"].get("legacy_diagnostic_metrics")
        else:
            planning, reflection = MODES[mode]
            injected = ({"analyze_iocs": "TimeoutError"}
                        if case.get("fixed_clarification_facts", {}).get("injected_failure") == "analyze_iocs timeout"
                        else None)
            with open_workflow(sqlite_path, meter, config, meter, planning_enabled=planning,
                               reflection_enabled=reflection, injected_tool_errors=injected) as workflow:
                state = workflow.run(case["case_id"], request, run_id=uuid.uuid4().hex)
                for _ in range(2):
                    if state.get("status") != "waiting_clarification":
                        break
                    field = state["clarification"]["pending"]
                    reply = _replies(case).get(field)
                    if type(reply) is not bool:
                        break
                    replies_used.append({"field": field, "value": reply})
                    state = workflow.resume(case["case_id"], {field: reply})
                if case.get("scenario") == "corrected_password_report" and state.get("status") != "waiting_clarification":
                    corrected = deepcopy(request)
                    corrected["facts"] = {"clicked_link": True, "credential_submitted": False}
                    state = workflow.run(case["case_id"], corrected, run_id=uuid.uuid4().hex)
                trace = _snapshots(workflow, case["case_id"])
            status, answer, evidence = state["status"], state.get("answer", {}), state.get("evidence", [])
            diagnostic = None
    except Exception as exc:
        error = {"type": type(exc).__name__, "detail": str(exc)[:300]}
        status, answer, evidence = "execution_error", {}, []
        if not trace:
            trace = [{"node": "execution_error", "status": status, "error": error,
                      "calls": deepcopy(meter.calls)}]
    elapsed = round(time.monotonic() - started, 6)
    usage = meter.summary()
    # Checkpoint snapshots are cumulative; the last one is the final logical history.
    final_history = trace[-1].get("tool_history", []) if trace else []
    if mode != "B1":
        all_history = {h["result_ref"]: h for event in trace for h in event.get("tool_history", [])
                       if not h.get("cached")}
        usage["tool_calls"] = len(all_history)
        keys = Counter(h.get("key") for h in all_history.values() if h.get("key"))
        usage["repeated_calls"] = (sum(n - 1 for n in keys.values() if n > 1) +
                                   sum(n - 1 for n in Counter(c["key"] for c in meter.calls
                                       if c["kind"] == "llm").values() if n > 1))
    findings = [f for event in trace for f in event.get("verification", [])]
    if any(c.get("status") == "error" for c in meter.calls):
        trace.append({"node": "service_calls", "status": status,
                      "calls": deepcopy(meter.calls)})
    return {"case_id": case["case_id"], "mode": mode, "split": case["split"],
            "scenario": case.get("scenario", "knowledge_regression"),
            "status": status, "answer": answer, "evidence": evidence, "trace": trace,
            "calls": meter.calls, "diagnostics": {**usage, "active_seconds": elapsed,
                "throttle_seconds": round(meter.throttle_seconds, 6),
                "clarification_asked": sorted({f for event in trace
                    for f in event.get("clarification", {}).get("asked", [])}),
                "clarification_replies": replies_used,
                "reflection_findings": findings,
                "repairs": max((e.get("budget", {}).get("repairs", 0) for e in trace), default=0),
                "replans": max((e.get("budget", {}).get("replans", 0) for e in trace), default=0),
                "tool_history": final_history, "legacy_diagnostic": diagnostic},
            "error": error, "review": None}


def run_experiment(cases, modes, config, retriever, completion, root, output_dir, *,
                   split, repeat=1, provenance=None, run_id=None,
                   min_interval_seconds=0, retry_429=0, preflight=None,
                   fail_on_service_error=True):
    validate_config(config)
    if not cases or any(c["split"] != split for c in cases) or len({c["case_id"] for c in cases}) != len(cases):
        raise ValueError("cases must have unique IDs and one split")
    if not modes or any(m not in MODES for m in modes) or len(modes) != len(set(modes)):
        raise ValueError("invalid modes")
    if type(repeat) is not int or repeat < 1:
        raise ValueError("repeat must be positive")
    if type(retry_429) is not int or retry_429 < 0 or retry_429 > 3:
        raise ValueError("retry_429 must be 0..3")
    gate = RateGate(min_interval_seconds)
    run_id = run_id or "M5-" + uuid.uuid4().hex
    folder = Path(output_dir) / run_id
    folder.mkdir(parents=True, exist_ok=False)
    try:
        manifest = {"schema_version": "m5-v1", "run_id": run_id,
                    "created_at": datetime.now(timezone.utc).isoformat(), "split": split,
                    "case_ids": [c["case_id"] for c in cases], "case_sha256": digest(cases),
                    "config": deepcopy(config), "config_sha256": digest(config),
                    "model": config["model"], "prompt_version": config["prompt_version"],
                    "index_version": retriever.index_version, "embedding": deepcopy(retriever.embedding),
                    "knowledge_snapshot": digest(retriever.chunks),
                    "code": provenance or code_provenance(root),
                    "preflight": preflight,
                    "runtime": {"min_interval_seconds": min_interval_seconds,
                                "retry_429": retry_429, "schedule": "case_interleaved_rotating",
                                "fail_on_service_error": fail_on_service_error},
                    "conditions": {m: {"planning": MODES[m][0], "reflection": MODES[m][1],
                                       "information": "question_only" if m == "B1" else "question_and_email"}
                                   for m in modes}, "repeat": repeat,
                    "pricing": None, "results": []}
        for repetition in range(repeat):
            for case_index, case in enumerate(cases):
                offset = (case_index + repetition) % len(modes)
                for mode in [*modes[offset:], *modes[:offset]]:
                    result = execute_case(case, mode, config, retriever, completion, root,
                                          folder / f"{repetition}-{mode}-{case['case_id']}.sqlite",
                                          gate=gate, retry_429=retry_429)
                    result["repetition"] = repetition
                    result["case_sha256"] = digest(case)
                    result["execution_order"] = len(manifest["results"])
                    manifest["results"].append(result)
                    if fail_on_service_error and any(c.get("kind") == "llm" and c.get("status") == "error"
                                                     for c in result["calls"]):
                        raise RuntimeError("model service error; partial run preserved, formal comparison invalid")
        path = folder / "run.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        return manifest, path
    except BaseException:
        # Keep the partial run for failure accounting and debugging.
        (folder / "partial.json").write_text(json.dumps(locals().get("manifest", {}), ensure_ascii=False,
                                                   indent=2, default=str) + "\n", encoding="utf-8")
        raise


def _evidence_text(row):
    data = row.get("data", {})
    return json.dumps(data, ensure_ascii=False, default=str) if isinstance(data, dict) else str(data)


def validate_current_version(run, root):
    """Reject old or dirty code before presenting a saved run as current evidence."""
    saved = run.get("code", {})
    current = code_provenance(root)
    if saved.get("git_dirty") is not False or current["git_dirty"] is not False:
        raise ValueError("formal scoring requires clean run and current worktrees")
    if (saved.get("git_commit") != current["git_commit"] or
            saved.get("source_tree_sha256") != current["source_tree_sha256"]):
        raise ValueError("saved run was produced by another code version")
    if digest(run["config"]) != run["config_sha256"]:
        raise ValueError("saved run config fingerprint mismatch")
    return True


def _failure_trace_relevant(failure_type, events):
    if failure_type == "service/provider":
        return any(any(c.get("kind") == "llm" and c.get("status") == "error"
                       for c in e.get("calls", [])) for e in events)
    if failure_type == "retrieval/context":
        return any(e.get("context") or e.get("retrieved") or
                   any(h.get("tool") == "retrieve_knowledge" for h in e.get("tool_history", []))
                   for e in events)
    if failure_type == "planning":
        return any(e.get("plan") for e in events)
    if failure_type == "tool routing/execution":
        return any(e.get("tool_history") or any(c.get("kind") == "tool" for c in e.get("calls", []))
                   for e in events)
    if failure_type == "fact/state":
        return any(e.get("facts") for e in events)
    if failure_type == "clarification":
        return any(e.get("clarification", {}).get("asked") or
                   e.get("clarification", {}).get("pending") for e in events)
    if failure_type == "verification/reflection":
        return any(e.get("verification") for e in events)
    if failure_type == "budget/termination":
        return any(e.get("status") in {"limited", "execution_error"} or
                   e.get("answer", {}).get("complete") is False or e.get("budget")
                   for e in events)
    return any(e.get("answer") or e.get("evidence") for e in events)


def score_result(result, case, review):
    """Review must supply claim-level judgments with exact answer and evidence excerpts."""
    if review is None:
        return {"success": None, "supported": 0, "claims": 0,
                "claim_counts": {"supported": 0, "unsupported": 0, "contradictory": 0},
                "failure": None}
    if review.get("case_id") != result["case_id"] or review.get("mode") != result["mode"]:
        raise ValueError("review identity mismatch")
    if review.get("expected_result_status") != case["expected_result_status"] or type(review.get("status_met")) is not bool or not review.get("status_reason"):
        raise ValueError("expected final status requires an explicit review")
    answer = result.get("answer", {}).get("answer", "")
    evidence = {e["id"]: e for e in result.get("evidence", [])}
    conditions = review.get("conditions")
    if not isinstance(conditions, list) or len(conditions) != len(case["success_conditions"]):
        raise ValueError("all predefined success conditions require review")
    if any(c.get("text") != expected or type(c.get("met")) is not bool or
           not c.get("reason") for c, expected in zip(conditions, case["success_conditions"])):
        raise ValueError("invalid condition review")
    claims = review.get("claims")
    if not isinstance(claims, list):
        raise ValueError("claims must be reviewed, including an empty list")
    claim_counts = {"supported": 0, "unsupported": 0, "contradictory": 0}
    for claim in claims:
        verdict = claim.get("verdict")
        if not claim.get("text") or claim["text"] not in answer or verdict not in claim_counts or "supported" in claim:
            raise ValueError("claim must quote saved answer and have an explicit verdict")
        if not claim.get("reason"):
            raise ValueError("claim review needs a reason")
        refs = claim.get("evidence_refs", [])
        quotes = claim.get("evidence_quotes", [])
        if verdict in {"supported", "contradictory"}:
            if not refs or not quotes or len(refs) != len(quotes):
                raise ValueError("supported or contradictory verdict requires exact evidence excerpts")
            sentence = next((part for part in re.split(r"(?<=[.!?])\s+|\n+", answer)
                             if claim["text"] in part), "")
            cited = set(re.findall(r"\[([EMI]\d+)\]", sentence))
            if verdict == "supported" and cited and (cited - evidence.keys() or cited - set(refs)):
                raise ValueError("answer citation does not match reviewed evidence")
            for ref, quote in zip(refs, quotes):
                if ref not in evidence or not quote or quote not in _evidence_text(evidence[ref]):
                    raise ValueError("review quote not found in saved evidence")
        else:
            if any(ref not in evidence for ref in refs) or len(refs) != len(quotes):
                raise ValueError("unsupported review has invalid evidence references")
            for ref, quote in zip(refs, quotes):
                if not quote or quote not in _evidence_text(evidence[ref]):
                    raise ValueError("review quote not found in saved evidence")
        claim_counts[verdict] += 1
    critical_error = review.get("critical_error")
    if type(critical_error) is not bool:
        raise ValueError("critical_error judgment required")
    if critical_error and not review.get("critical_error_reason"):
        raise ValueError("critical error needs reason")
    success = (result["status"] not in {"execution_error", "waiting_clarification"} and
               review["status_met"] and
               all(c["met"] for c in conditions) and not critical_error)
    failure = None
    if not success:
        failure = review.get("failure")
        if not isinstance(failure, dict) or failure.get("type") not in FAILURE_TYPES or not failure.get("reason"):
            raise ValueError("failed case requires typed, evidenced failure")
        refs = failure.get("trace_refs", [])
        if not refs or any(type(i) is not int or i < 0 or i >= len(result["trace"]) for i in refs):
            raise ValueError("failure requires actual trace indices")
        if not _failure_trace_relevant(failure["type"], [result["trace"][i] for i in refs]):
            raise ValueError("failure trace does not contain the cited failure category")
    return {"success": success, "supported": claim_counts["supported"],
            "claims": len(claims), "claim_counts": claim_counts, "failure": failure}


def score_run(run, cases, reviews):
    by_case = {c["case_id"]: c for c in cases}
    if set(by_case) != set(run["case_ids"]) or digest(cases) != run["case_sha256"]:
        raise ValueError("case manifest differs from run")
    expected = {(repetition, mode, case_id)
                for repetition in range(run["repeat"])
                for mode in run["conditions"] for case_id in run["case_ids"]}
    actual = [(r["repetition"], r["mode"], r["case_id"]) for r in run["results"]]
    if len(actual) != len(expected) or set(actual) != expected or any(
            r["case_sha256"] != digest(by_case[r["case_id"]]) or r["split"] != run["split"]
            for r in run["results"]):
        raise ValueError("run results do not cover every Case, mode and repetition exactly once")
    reviewed = {(r["repetition"], r["mode"], r["case_id"]): r for r in reviews}
    if len(reviewed) != len(reviews) or set(reviewed) - expected:
        raise ValueError("duplicate or extraneous review")
    rows = []
    for result in run["results"]:
        key = (result["repetition"], result["mode"], result["case_id"])
        score = score_result(result, by_case[result["case_id"]], reviewed.get(key))
        rows.append({"repetition": key[0], "mode": key[1], "case_id": key[2],
                     "status": result["status"], **score,
                     "diagnostics": result["diagnostics"]})
    summaries = {}
    for mode in run["conditions"]:
        subset = [r for r in rows if r["mode"] == mode]
        assessed = [r for r in subset if r["success"] is not None]
        successes = sum(r["success"] for r in assessed)
        claims = sum(r["claims"] for r in assessed)
        supported = sum(r["supported"] for r in assessed)
        claim_counts = {verdict: sum(r["claim_counts"][verdict] for r in assessed)
                        for verdict in ("supported", "unsupported", "contradictory")}
        tokens = sum(r["diagnostics"]["usage"]["total_tokens"] for r in subset)
        missing = sum(r["diagnostics"]["usage_missing_calls"] for r in subset)
        summaries[mode] = {"case_success_rate": {"numerator": successes, "denominator": len(assessed),
                                                   "value": successes / len(assessed) if len(assessed) == len(subset) else None},
                           "evidence_support_rate": {"numerator": supported, "denominator": claims,
                                                     "value": supported / claims if claims and len(assessed) == len(subset) else None,
                                                     "status": "unscored" if len(assessed) != len(subset) else
                                                               "N/A" if not claims else "computed",
                                                     "claim_counts": claim_counts},
                           "cost_per_successful_case": {"unit": "tokens", "total": tokens,
                              "value": tokens / successes if successes and not missing and len(assessed) == len(subset) else None,
                              "status": "unscored" if len(assessed) != len(subset) else
                                        "usage_incomplete" if missing else "not_computable_zero_success" if not successes else "computed"},
                           "reviewed_cases": len(assessed), "total_cases": len(subset),
                           "failure_types": dict(Counter(r["failure"]["type"] for r in assessed if r["failure"]))}
        if run["repeat"] > 1:
            rates = []
            for repetition in range(run["repeat"]):
                items = [r for r in subset if r["repetition"] == repetition]
                rates.append(sum(r["success"] for r in items) / len(items)
                             if items and all(r["success"] is not None for r in items) else None)
            summaries[mode]["repeat_case_success_rates"] = rates
            summaries[mode]["repeat_range"] = ([min(rates), max(rates)]
                                                 if all(v is not None for v in rates) else None)
    return {"run_id": run["run_id"], "case_sha256": run["case_sha256"],
            "rows": rows, "modes": summaries, "review_sha256": digest(reviews)}


def failure_analysis(run, score):
    lookup = {(r["repetition"], r["mode"], r["case_id"]): r for r in run["results"]}
    failures = []
    for row in score["rows"]:
        if row["success"] is not False:
            continue
        result = lookup[(row["repetition"], row["mode"], row["case_id"])]
        failure = row["failure"]
        events = [result["trace"][i] for i in failure["trace_refs"]]
        failures.append({"mode": row["mode"], "case_id": row["case_id"],
                         "repetition": row["repetition"], "final_status": row["status"],
                         "failure_type": failure["type"], "reason": failure["reason"],
                         "trace_refs": failure["trace_refs"],
                         "trace_events": events,
                         "plan_refs": sorted({t["task_id"] for event in events
                                              for t in event.get("plan", []) if "task_id" in t}),
                         "tool_refs": sorted({h["result_ref"] for event in events
                                              for h in event.get("tool_history", []) if "result_ref" in h}),
                         "evidence_refs": sorted({e["id"] for event in events
                                                  for e in event.get("evidence", []) if "id" in e}),
                         "verification_findings": [f for event in events
                                                    for f in event.get("verification", [])],
                         "clarification": result["diagnostics"]["clarification_asked"],
                         "replan": result["diagnostics"]["replans"],
                         "repair": result["diagnostics"]["repairs"],
                         "usage": result["diagnostics"]["usage"],
                         "llm_calls": result["diagnostics"]["llm_calls"],
                         "tool_calls": result["diagnostics"]["tool_calls"],
                         "active_seconds": result["diagnostics"]["active_seconds"]})
    return failures
