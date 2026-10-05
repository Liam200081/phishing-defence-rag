"""Bounded answer checks with explicit recovery actions."""
import re


def finding(failure_type, action, detail):
    return {"finding": detail, "failure_type": failure_type, "recovery_action": action}


def verify(state):
    answer = state["answer"]
    body = answer.get("answer", "")
    evidence = state.get("evidence", [])
    refs = {e["id"] for e in evidence}
    history = state.get("tool_history", [])
    latest = {h.get("key") or h["tool"]: h for h in history}
    if any(h["status"] == "error" for h in latest.values()):
        return [finding("tool_error", "limited_retry", "tool failed; risk cannot be ruled out")]
    if not refs:
        return [finding("knowledge_gap", "retrieve", "no supporting evidence")]
    if not body.strip() or answer.get("truncated"):
        return [finding("answer_form", "repair_answer", "empty or truncated answer")]
    cited = set(re.findall(r"\[([EMI]\d+)\]", body))
    if cited - refs:
        return [finding("invalid_citation", "repair_answer", "citation has no active evidence")]
    if refs and not cited:
        return [finding("unsupported_claim", "repair_answer", "answer has no evidence citation")]
    request = state.get("request", {})
    if request.get("kind") == "knowledge":
        terms = {w for w in re.findall(r"[a-z]{4,}", request.get("question", "").lower())
                 if w not in {"what", "which", "does", "with", "about", "explain", "define", "should"}}
        if terms and not any(term in body.lower() for term in terms):
            return [finding("off_topic", "repair_answer", "answer does not address the question topic")]
    if re.search(r"\b(?:my|your|the) account (?:is|was|has been) (?:safe|compromised|accessed)\b", body, re.I):
        return [finding("unsupported_claim", "repair_answer", "account status is not established by these observations")]
    if re.search(r"\b(?:i|we) (?:have |already )?(?:blocked|deleted|reset|reported|quarantined)\b", body, re.I):
        return [finding("unperformed_action", "repair_answer", "answer claims a response action was executed")]
    if re.search(r"\b(?:definitely|certainly|confirmed) (?:safe|compromised|malicious)\b", body, re.I):
        return [finding("unsupported_certainty", "repair_answer", "certainty exceeds available observations")]
    # Source changes invalidate drafts before this function is reached; this is a final guard.
    if answer.get("fact_revision", len(state.get("facts", []))) != len(state.get("facts", [])):
        return [finding("superseded_fact", "replan", "answer predates corrected facts")]
    return []
