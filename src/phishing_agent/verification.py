"""Bounded answer checks with explicit recovery actions."""
import re

STOP = {"the", "and", "are", "for", "with", "from", "this", "that", "your", "have",
        "does", "into", "only", "can", "may", "could", "should", "would", "about",
        "evidence", "supports", "supported", "answer", "guidance", "require", "requires", "using", "according"}


def _tokens(text):
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {"caution" if word in {"cautious", "caution", "suspicious"} else
            word[:-1] if word.endswith("s") and len(word) > 4 else word
            for word in words if word not in STOP and len(word) > 2}


def _evidence_text(row):
    data = row.get("data", {})
    if row.get("kind") == "knowledge":
        return data.get("text", "")
    if row.get("kind") == "email":
        return " ".join(str(data.get(k, "")) for k in ("from", "reply_to", "subject", "body"))
    if row.get("kind") == "iocs":
        return " ".join(str(i.get(k, "")) for i in data.get("iocs", [])
                        for k in ("normalized", "host", "observations"))
    return ""


def _unsupported_claim(body, evidence_by_id):
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", body):
        refs = re.findall(r"\[([EMI]\d+)\]", sentence)
        claim = re.sub(r"\[[EMI]\d+\]", "", sentence).strip()
        if not claim or not refs:
            continue
        source = " ".join(_evidence_text(evidence_by_id[ref]) for ref in refs)
        if not source:
            continue  # Legacy evidence without payload is handled by citation checks.
        claim_terms, source_terms = _tokens(claim), _tokens(source)
        if claim_terms - source_terms:
            return claim
        for negated in re.findall(r"\b(?:not|no|without)\s+([a-z]+)\b", claim.lower()):
            if not re.search(r"\b(?:not|no|without)\s+" + re.escape(negated) + r"\b", source.lower()):
                return claim
        # Specific identifiers and categorical verdicts need direct corroboration.
        identifiers = re.findall(r"\b(?:\d+(?:\.\d+)*|[\w.-]+@[\w.-]+|https?://\S+)\b", claim)
        if any(identifier.lower() not in source.lower() for identifier in identifiers):
            return claim
        if re.search(r"\b(?:guaranteed|definitely|certainly|always|never)\b", claim, re.I):
            return claim
        if re.search(r"\b(?:safe|malicious|compromised|breached)\b", claim, re.I) and not re.search(
                r"\b(?:safe|malicious|compromised|breached)\b", source, re.I):
            return claim
    return None


def finding(failure_type, action, detail):
    return {"finding": detail, "failure_type": failure_type, "recovery_action": action}


def verify(state):
    answer = state["answer"]
    body = answer.get("answer", "")
    evidence = state.get("evidence", [])
    evidence_by_id = {e["id"]: e for e in evidence}
    refs = set(evidence_by_id)
    history = state.get("tool_history", [])
    latest = {h.get("key") or h["tool"]: h for h in history}
    if any(h["status"] == "error" for h in latest.values()):
        return [finding("tool_error", "limited_retry", "tool failed; risk cannot be ruled out")]
    if not refs:
        return [finding("knowledge_gap", "retrieve", "no supporting evidence")]
    knowledge = [e for e in evidence if e.get("kind") == "knowledge"]
    context = state.get("context")
    if context is not None and knowledge and any(
            f"[{e['id']}]" not in context or e["data"]["text"] not in context for e in knowledge):
        return [finding("context_gap", "rebuild_context", "retrieved knowledge missing from model context")]
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
    unsupported = _unsupported_claim(body, evidence_by_id)
    if unsupported:
        return [finding("unsupported_claim", "repair_answer", "cited claim is not supported by cited content: " + unsupported[:160])]
    return []
