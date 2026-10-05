"""All retrieved evidence fits in full, or generation is explicitly blocked.

Budget unit: Unicode characters, including labels and separators. It is not a
token-window estimate. No hidden per-chunk truncation or silent evidence drop.
"""


class ContextBudgetError(ValueError):
    pass


def build_context(hits, budget_chars):
    if type(budget_chars) is not int or budget_chars < 0:
        raise ValueError("budget_chars must be a nonnegative integer")
    pieces, evidence, cursor = [], [], 0
    ids = set()
    for rank, hit in enumerate(hits, 1):
        if hit["chunk_id"] in ids:
            raise ValueError("Duplicate chunk ID")
        ids.add(hit["chunk_id"])
        if not hit["text"].strip():
            raise ValueError("Empty retrieved chunk")
        label = f"[E{rank}]"
        header = f"{label} {hit['document']} | pages {hit['page_start']}-{hit['page_end']} | {hit['chunk_id']}\n"
        separator = "\n\n" if pieces else ""
        start = cursor + len(separator) + len(header)
        piece = separator + header + hit["text"]
        cursor += len(piece)
        pieces.append(piece)
        evidence.append({**hit, "citation_id": f"E{rank}", "context_start": start, "context_end": cursor})
    text = "".join(pieces)
    if len(text) > budget_chars:
        raise ContextBudgetError(f"Full retrieved evidence requires {len(text)} characters; budget is {budget_chars}. Increase budget or explicitly lower top_k; no request was sent.")
    return {"text": text, "evidence": evidence, "used_chars": len(text), "budget_chars": budget_chars,
            "policy": "complete_chunks_or_explicit_failure", "truncated": False}
