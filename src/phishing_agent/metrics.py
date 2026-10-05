"""Diagnostic scores, never factual-accuracy or hallucination rates."""
import hashlib
import math


def cosine(a, b):
    if len(a) != len(b):
        raise ValueError("Embedding dimensions differ")
    den = math.sqrt(sum(x*x for x in a) * sum(y*y for y in b))
    return sum(x*y for x, y in zip(a, b)) / den if den else None


def evaluate(query, response, context, config, encode=None):
    """Only actual generation context enters B1 diagnostics or manual review.

    Formula windows are retained from B0, but applied to SENT evidence only.
    These proxy scores are not a substitute for claim-level assessment.
    """
    text = context["text"]
    result = {"version": config["evaluation"]["version"],
              "context_sha256": hashlib.sha256(text.encode()).hexdigest(),
              "evidence_ids": [e["chunk_id"] for e in context["evidence"]],
              "response_complete": response["complete"],
              "grounding": {"status": "not_assessed", "score": None,
                            "reason": "Requires human claim-level review of the saved answer and actual context"},
              "legacy_diagnostic_metrics": {"status": "disabled", "context_relevance": None,
                  "answer_relevance": None, "faithfulness_proxy": None,
                  "interpretation": "Embedding cosine similarity only; not factual correctness or hallucination rate",
                  "evidence_scope": "actual_generation_context_only; not directly comparable to B0 mismatched windows"}}
    legacy = result["legacy_diagnostic_metrics"]
    if not config["evaluation"]["legacy_diagnostics"]:
        return result
    if not response["complete"] or not context["evidence"]:
        legacy["status"] = "not_scored_incomplete_or_no_evidence"
        return result
    if encode is None:
        legacy["status"] = "unavailable_encoder"
        return result
    snippets = [text[e["context_start"]:e["context_end"]][:500] for e in context["evidence"]]
    answer = response["answer"][:500]
    context_window = " ".join(snippets)[:1000]
    vectors = encode([query, answer, context_window] + snippets)
    scores = [cosine(vectors[0], v) for v in vectors[3:]]
    legacy.update(status="scored", context_relevance=round(sum(scores)/len(scores), 4) if all(s is not None for s in scores) else None,
                  answer_relevance=cosine(vectors[0], vectors[1]), faithfulness_proxy=cosine(vectors[1], vectors[2]))
    for name in ("answer_relevance", "faithfulness_proxy"):
        if legacy[name] is not None:
            legacy[name] = round(legacy[name], 4)
    legacy["inputs"] = {"query": query, "answer_window": answer, "evidence_windows": snippets,
                        "faithfulness_context_window": context_window}
    return result


def align_by_query_id(left, right):
    def mapping(rows):
        result = {}
        for row in rows:
            key = row["query_id"]
            if key in result:
                raise ValueError(f"Duplicate query_id: {key}")
            result[key] = row
        return result
    a, b = mapping(left), mapping(right)
    if a.keys() != b.keys():
        raise ValueError("Query ID sets differ")
    if any(a[key]["query"] != b[key]["query"] for key in a):
        raise ValueError("Query text differs for matching query_id")
    return [(a[key], b[key]) for key in a]
