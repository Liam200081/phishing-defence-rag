"""The small persisted W0 state and user fact update rules."""
from typing import Any, TypedDict


class WorkflowState(TypedDict, total=False):
    case_id: str
    run_id: str
    request: dict[str, Any]
    facts: list[dict[str, Any]]
    tool_history: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    clarification: dict[str, Any]
    status: str
    answer: dict[str, Any]


def current_facts(records):
    """Only active user reports and direct observations may inform an answer."""
    return {row["name"]: row["value"] for row in records
            if row["status"] in {"user_reported", "observed"}}


def update_facts(records, updates, run_id, source="user_reported"):
    if source not in {"user_reported", "observed"}:
        raise ValueError("invalid fact source")
    result = [dict(row) for row in records]
    for name, value in updates.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("fact names must be nonempty strings")
        for row in result:
            if row["name"] == name and row["status"] != "superseded":
                row["status"] = "superseded"
        result.append({"name": name, "value": value, "status": "unknown" if value is None else source,
                       "run_id": run_id})
    return result


def mark_unknown(records, name, run_id):
    if name in current_facts(records):
        return records
    if any(row["name"] == name and row["status"] == "unknown" for row in records):
        return records
    return [*records, {"name": name, "value": None, "status": "unknown", "run_id": run_id}]
