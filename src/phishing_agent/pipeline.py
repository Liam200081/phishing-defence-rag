"""Shared Notebook/CLI entry point for single-round B1 RAG."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from .config import load_config, validate_config
from .llm import generate, groq_completion, not_requested
from .metrics import evaluate
from .prompts import build_messages
from .rag.context import build_context, ContextBudgetError
from .rag.ingest import ingest_pdfs
from .rag.retrieve import ChromaRetriever
from .trace import code_provenance, export_run


def load_queries(path):
    queries = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_queries(queries)
    return queries


def validate_queries(queries):
    if not isinstance(queries, list) or not queries:
        raise ValueError("A nonempty query list is required")
    seen = set()
    for q in queries:
        if set(q) != {"query_id", "query"} or any(not isinstance(v, str) or not v.strip() for v in q.values()):
            raise ValueError("Each query requires a nonempty query_id and query")
        if q["query_id"] in seen:
            raise ValueError("Duplicate query_id")
        seen.add(q["query_id"])


def run_queries(queries, config, retrieve, completion, *, data_manifest, embedding,
                index_version, provenance, encode=None, execution_mode="live",
                run_id=None, created_at=None):
    validate_config(config)
    validate_queries(queries)
    run = {"schema_version": "b1-run-v1", "baseline": "B1",
           "run_id": run_id or "B1-" + uuid.uuid4().hex,
           "created_at": created_at or datetime.now(timezone.utc).isoformat(),
           "execution_mode": execution_mode, "code": deepcopy(provenance), "config": deepcopy(config),
           "model": config["model"], "embedding": deepcopy(embedding),
           "prompt_version": config["prompt_version"], "data": deepcopy(data_manifest),
           "index_version": index_version, "queries": deepcopy(queries), "results": []}
    for q in queries:
        hits, messages = [], []
        context = build_context([], config["context_budget_chars"])
        kind, _ = build_messages(q["query"], "", config["prompt_version"])
        try:
            hits = retrieve(q["query"], config["top_k"])
        except Exception as exc:
            response = not_requested("retrieval_error", {"type": type(exc).__name__})
        else:
            if not hits:
                response = not_requested("no_evidence")
            else:
                try:
                    context = build_context(hits, config["context_budget_chars"])
                except ContextBudgetError as exc:
                    response = not_requested("context_budget_exceeded", {"detail": str(exc)})
                else:
                    kind, messages = build_messages(q["query"], context["text"], config["prompt_version"])
                    response = generate(messages, config, completion)
        try:
            evaluation = evaluate(q["query"], response, context, config, encode)
        except Exception as exc:
            evaluation = evaluate(q["query"], response, context, config)
            evaluation["legacy_diagnostic_metrics"].update(status="evaluation_error", error_type=type(exc).__name__)
        run["results"].append({**q, "question_type": kind, "retrieved": hits, "context": context,
                               "messages": messages, "response": response, "evaluation": evaluation})
    run["summary"] = {"queries": len(queries), "complete": sum(r["response"]["complete"] for r in run["results"]),
                      "incomplete": sum(not r["response"]["complete"] for r in run["results"]),
                      "evaluation_errors": sum(r["evaluation"]["legacy_diagnostic_metrics"]["status"] == "evaluation_error" for r in run["results"])}
    return run


def run_b1(config_path="config.json", queries_path="evaluation/queries.json", pdf_dir="data",
           output_dir="experiments/b1", project_root="."):
    """Live entry point called identically by the CLI and presentation notebook."""
    config = load_config(config_path)
    queries = load_queries(queries_path)
    completion = groq_completion(config)
    chunks, manifest = ingest_pdfs(pdf_dir, config)
    retriever = ChromaRetriever(chunks, config)
    try:
        run = run_queries(queries, config, retriever.retrieve, completion,
                          data_manifest=manifest, embedding=retriever.embedding,
                          index_version=retriever.index_version, provenance=code_provenance(project_root),
                          encode=retriever.encode)
        path = export_run(run, output_dir)
        return run, path
    finally:
        retriever.close()
