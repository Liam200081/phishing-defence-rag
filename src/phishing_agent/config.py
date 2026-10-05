"""Plain JSON configuration, validated before any API request."""
import json
import math
from pathlib import Path


def load_config(path):
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_config(config)
    return config


def validate_config(c):
    required = {"model", "embedding_model", "embedding_revision", "chunk_size", "chunk_overlap",
                "min_chunk_chars", "cleaning", "top_k", "context_budget_chars", "distance_metric",
                "generation", "prompt_version", "evaluation"}
    if set(c) != required:
        raise ValueError(f"Configuration keys differ: {set(c) ^ required}")
    for key in ("chunk_size", "top_k", "context_budget_chars"):
        if type(c[key]) is not int or c[key] <= 0:
            raise ValueError(f"{key} must be a positive integer")
    for key in ("chunk_overlap", "min_chunk_chars"):
        if type(c[key]) is not int or c[key] < 0:
            raise ValueError(f"{key} must be a nonnegative integer")
    if c["chunk_overlap"] >= c["chunk_size"]:
        raise ValueError("chunk_overlap must be smaller than chunk_size")
    if type(c["cleaning"]) is not bool:
        raise ValueError("cleaning must be boolean")
    for key in ("model", "embedding_model", "embedding_revision"):
        if not isinstance(c[key], str) or not c[key].strip():
            raise ValueError(f"{key} is required")
    if c["prompt_version"] != "b1-v1" or c["distance_metric"] != "cosine":
        raise ValueError("Unsupported prompt version or distance metric")
    g = c["generation"]
    if set(g) != {"max_tokens", "temperature", "top_p", "timeout_seconds"}:
        raise ValueError("Unsupported generation keys")
    if type(g["max_tokens"]) is not int or g["max_tokens"] <= 0:
        raise ValueError("max_tokens must be positive")
    for key, low, high in (("temperature", 0, 2), ("top_p", 0, 1), ("timeout_seconds", 0, float("inf"))):
        v = g[key]
        if type(v) not in (int, float) or not math.isfinite(v) or not low <= v <= high:
            raise ValueError(f"Invalid {key}")
    if g["top_p"] == 0 or g["timeout_seconds"] == 0:
        raise ValueError("top_p and timeout_seconds must exceed zero")
    if set(c["evaluation"]) != {"version", "legacy_diagnostics"} or c["evaluation"]["version"] != "b1-context-v1":
        raise ValueError("Unsupported evaluation config")
    if type(c["evaluation"]["legacy_diagnostics"]) is not bool:
        raise ValueError("legacy_diagnostics must be boolean")
