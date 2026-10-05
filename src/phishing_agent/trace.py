"""Immutable per-run JSON export and consistency-checked readback."""
import hashlib
import importlib.metadata
import json
import platform
import re
import subprocess
from pathlib import Path


def code_provenance(root):
    root = Path(root)
    def git(*args):
        try:
            return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.DEVNULL).decode().strip()
        except (OSError, subprocess.CalledProcessError):
            return None
    paths = list((root / "src").rglob("*.py"))
    paths += [root / p for p in ("config.json", "pyproject.toml", "evaluation/queries.json", "notebooks/B1_baseline.ipynb")]
    hashes = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths) if p.is_file()}
    versions = {}
    for package in ("chromadb", "groq", "pymupdf", "sentence-transformers", "transformers", "torch", "huggingface-hub", "python-dotenv"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "unavailable"
    status = git("status", "--porcelain")
    return {"git_commit": git("rev-parse", "HEAD") or "unknown", "git_dirty": status != "" if status is not None else "unknown",
            "source_files_sha256": hashes, "source_tree_sha256": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(),
            "python": platform.python_version(), "dependencies": versions}


def validate_run(run):
    if run["baseline"] != "B1" or run["schema_version"] != "b1-run-v1":
        raise ValueError("Not a B1 run")
    expected = {q["query_id"]: q["query"] for q in run["queries"]}
    if len(expected) != len(run["queries"]) or len(run["results"]) != len(expected):
        raise ValueError("Query count mismatch or duplicate IDs")
    seen = set()
    for row in run["results"]:
        key = row["query_id"]
        if key in seen or key not in expected or row["query"] != expected[key]:
            raise ValueError("Query alignment mismatch")
        seen.add(key)
        response = row["response"]
        expected_complete = (response["request_sent"] and response["returned_successfully"]
                             and bool(response["answer"].strip()) and response["finish_reason"] == "stop"
                             and response["error"] is None)
        if response["complete"] != expected_complete:
            raise ValueError("Response completeness mismatch")
        context = row["context"]
        text = context["text"]
        if context["used_chars"] != len(text) or len(text) > context["budget_chars"]:
            raise ValueError("Context budget mismatch")
        if row["response"]["request_sent"]:
            expected_message = "Question:\n" + row["query"] + "\n\nReference evidence:\n" + text
            if row["messages"][-1]["content"] != expected_message:
                raise ValueError("Generation context differs from saved context")
        elif text or context["evidence"]:
            raise ValueError("Unsent evidence must not be reported as generation context")
        retrieved = {h["chunk_id"]: h for h in row["retrieved"]}
        for e in context["evidence"]:
            if text[e["context_start"]:e["context_end"]] != e["text"]:
                raise ValueError("Evidence context offsets mismatch")
            if e["chunk_id"] not in retrieved or any(e[k] != v for k, v in retrieved[e["chunk_id"]].items()):
                raise ValueError("Evidence not traceable to retrieval")
        ev = row["evaluation"]
        if ev["context_sha256"] != hashlib.sha256(text.encode()).hexdigest():
            raise ValueError("Evaluation context differs from generation context")
        if ev["evidence_ids"] != [e["chunk_id"] for e in context["evidence"]]:
            raise ValueError("Evaluation evidence mismatch")
    expected_summary = {"queries": len(expected), "complete": sum(r["response"]["complete"] for r in run["results"]),
                        "incomplete": sum(not r["response"]["complete"] for r in run["results"]),
                        "evaluation_errors": sum(r["evaluation"]["legacy_diagnostic_metrics"]["status"] == "evaluation_error" for r in run["results"])}
    if run["summary"] != expected_summary:
        raise ValueError("Summary differs from query results")
    return run


def export_run(run, output_dir):
    validate_run(run)
    if not re.fullmatch(r"B1-[A-Za-z0-9_-]+", run["run_id"]):
        raise ValueError("Invalid run ID")
    folder = Path(output_dir) / run["run_id"]
    folder.mkdir(parents=True, exist_ok=False)
    path = folder / "run.json"
    with path.open("x", encoding="utf-8") as out:
        json.dump(run, out, ensure_ascii=False, indent=2, allow_nan=False)
        out.write("\n")
    return path


def read_run(path):
    try:
        return validate_run(json.loads(Path(path).read_text(encoding="utf-8")))
    except (KeyError, TypeError, IndexError) as exc:
        raise ValueError("Malformed B1 run record") from exc
