"""One generation request; completeness metadata is always retained."""


def not_requested(status, detail=None):
    return {"request_sent": False, "returned_successfully": False, "answer": "", "finish_reason": None,
            "usage": None, "empty": True, "truncated": False, "complete": False,
            "status": status, "error": detail, "response_id": None, "response_model": None}


def generate(messages, config, completion):
    """completion is an SDK-compatible callable, injectable for offline tests."""
    g = config["generation"]
    try:
        raw = completion(model=config["model"], messages=messages, max_tokens=g["max_tokens"],
                         temperature=g["temperature"], top_p=g["top_p"])
    except Exception as exc:
        # Do not persist exception messages, which can contain credentials or request bodies.
        return {**not_requested("request_error", {"type": type(exc).__name__}), "request_sent": True}
    try:
        payload = raw if isinstance(raw, dict) else raw.model_dump()
        choices = payload.get("choices") or []
        choice = choices[0] if choices else {}
        answer = (choice.get("message") or {}).get("content") or ""
        if not isinstance(answer, str):
            raise ValueError("Non-text content")
        finish = choice.get("finish_reason")
        empty = not answer.strip()
        truncated = finish in {"length", "max_tokens", "max_output_tokens"}
        status = "invalid_response" if not choices else (
            "length_terminated" if truncated else "empty_response" if empty else
            "complete" if finish == "stop" else "incomplete_termination")
        return {"request_sent": True, "returned_successfully": True, "answer": answer,
                "finish_reason": finish, "usage": payload.get("usage"), "empty": empty,
                "truncated": truncated, "complete": status == "complete", "status": status,
                "error": None, "response_id": payload.get("id"), "response_model": payload.get("model")}
    except (AttributeError, TypeError, ValueError, IndexError):
        return {**not_requested("invalid_response", {"type": "MalformedResponse"}),
                "request_sent": True, "returned_successfully": True}


def groq_completion(config):
    import os
    from groq import Groq
    from dotenv import load_dotenv
    load_dotenv()
    if not os.getenv("GROQ_API_KEY"):
        raise ValueError("GROQ_API_KEY is required for live generation")
    client = Groq(timeout=config["generation"]["timeout_seconds"], max_retries=0)
    return client.chat.completions.create
