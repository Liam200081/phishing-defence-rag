"""Thin contract over the M1 Chroma retriever; no new index or generation path."""

def retrieve_knowledge(query, retriever, top_k=3, sources=None):
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be nonempty text")
    if sources is not None and (not isinstance(sources, (list, tuple, set)) or
                                not all(isinstance(s, str) for s in sources)):
        raise ValueError("sources must be document names")
    if type(top_k) is not int or top_k < 1:
        raise ValueError("top_k must be positive")
    hits = retriever.retrieve(query, len(retriever.chunks) if sources is not None else top_k)
    if sources is not None:
        hits = [h for h in hits if h["document"] in sources][:top_k]
    return [{**{k: h[k] for k in ("text", "document", "document_sha256", "page_start",
                               "page_end", "chunk_id", "score", "score_kind",
                               "index_version", "data_version")}, "embedding": retriever.embedding}
            for h in hits]
