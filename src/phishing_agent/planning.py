"""Small, validated plans for the three existing read-only tools."""
import json
from .state import current_facts

LIMITS = {"tasks": 4, "tools": 6, "llm": 8, "retrieval_extra": 1,
          "replans": 1, "repairs": 1, "clarifications": 2, "retries": 1}
TOOLS = {"retrieve_knowledge", "parse_email", "analyze_iocs"}


def tool_key(tool, params):
    return json.dumps([tool, params], sort_keys=True, ensure_ascii=False, default=str)


def default_plan(state):
    request = state["request"]
    tasks = []
    def add(tool, goal, params, dependency=()):
        tasks.append({"task_id": f"T{len(tasks) + 1}", "goal": goal, "tool": tool,
                      "params": params, "dependency": list(dependency),
                      "completion_condition": "tool result recorded", "status": "pending",
                      "evidence_ref": []})
    if request["kind"] == "email":
        params = ({"text": request["email_text"]} if request.get("email_text") is not None
                  else {"path": request["email_path"]})
        add("parse_email", "Observe the submitted email", params)
        # IOC work is selected after parsing, only if URLs exist.
        add("analyze_iocs", "Inspect extracted indicators", {"from_task": "T1"}, ("T1",))
        facts = current_facts(state.get("facts", []))
        if facts.get("credential_submitted") is True:
            focus = "response after credential entry"
        elif facts.get("clicked_link") is True:
            focus = "response after clicking a link without confirmed credential entry"
        elif facts.get("clicked_link") is False:
            focus = "response after only opening an email without clicking"
        else:
            focus = "phishing email indicators and possible response"
        query = request["question"] + " " + focus
        add("retrieve_knowledge", "Find " + focus, {"query": query}, ("T1",))
    else:
        add("retrieve_knowledge", "Find evidence for the question", {"query": request["question"]})
    return tasks


def validate_plan(tasks, request):
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= LIMITS["tasks"]:
        raise ValueError("plan task count outside budget")
    seen, calls = set(), set()
    for task in tasks:
        if not isinstance(task, dict) or not all(k in task for k in (
                "task_id", "goal", "tool", "params", "dependency", "completion_condition",
                "status", "evidence_ref")):
            raise ValueError("incomplete task")
        task_id, tool, params = task["task_id"], task["tool"], task["params"]
        if not isinstance(task_id, str) or not task_id or task_id in seen:
            raise ValueError("duplicate or invalid task id")
        if tool not in TOOLS or not isinstance(params, dict):
            raise ValueError("unregistered tool or invalid params")
        if not isinstance(task["goal"], str) or not task["goal"].strip() or not isinstance(task["completion_condition"], str) or not task["completion_condition"].strip():
            raise ValueError("task goal and completion condition required")
        if task["status"] != "pending" or task["evidence_ref"] != []:
            raise ValueError("new tasks must be pending without evidence")
        deps = task["dependency"]
        if not isinstance(deps, list) or len(deps) != len(set(deps)) or any(d not in seen for d in deps):
            raise ValueError("dependencies must refer to earlier tasks")
        if tool == "retrieve_knowledge" and (set(params) != {"query"} or not isinstance(params["query"], str) or not params["query"].strip()):
            raise ValueError("invalid knowledge params")
        if tool == "parse_email" and (request["kind"] != "email" or set(params) not in ({"text"}, {"path"}) or
                                      ("path" in params and (not isinstance(params["path"], str) or not params["path"].endswith(".eml"))) or
                                      ("text" in params and not isinstance(params["text"], (str, bytes)))):
            raise ValueError("invalid email params")
        if tool == "parse_email" and params != ({"text": request.get("email_text")} if request.get("email_text") is not None else {"path": request.get("email_path")}):
            raise ValueError("email params differ from submitted input")
        if tool == "analyze_iocs" and (params != {"from_task": deps[0]} if len(deps) == 1 else True):
            raise ValueError("IOC task requires one parsed-email dependency")
        if tool == "analyze_iocs" and next(t for t in tasks if t["task_id"] == deps[0])["tool"] != "parse_email":
            raise ValueError("IOC input must come from email parser")
        key = tool_key(tool, params)
        if key in calls:
            raise ValueError("duplicate tool task")
        calls.add(key)
        seen.add(task_id)
    return tasks
