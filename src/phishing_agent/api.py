"""Thin FastAPI adapter for the persisted A1 workflow."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
import os
from pathlib import Path
import re
from typing import Any, Literal
import uuid

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .config import load_config
from .graph import open_workflow
from .llm import groq_completion
from .rag.ingest import ingest_pdfs
from .rag.retrieve import ChromaRetriever


ROOT = Path(__file__).resolve().parents[2]
MAX_EML_BYTES = 1_000_000
UPLOAD_CHUNK_BYTES = 64 * 1024
CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
TRUE_REPLIES = {"yes", "y", "true", "1", "是", "有", "点击了", "输入了"}
FALSE_REPLIES = {"no", "n", "false", "0", "否", "没有", "未点击", "未输入"}


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email_text: str | None = Field(default=None, max_length=1_000_000)
    incident_description: str | None = Field(default=None, max_length=20_000)
    case_id: str | None = Field(default=None, max_length=128)


class ReplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply: bool | str


class ErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool = False


class CaseResponse(BaseModel):
    case_id: str | None = None
    status: Literal["running", "needs_clarification", "completed", "failed"]
    facts: list[dict[str, Any]] = Field(default_factory=list)
    suspicious_signals: list[str] = Field(default_factory=list)
    iocs: list[dict[str, Any]] = Field(default_factory=list)
    answer: str | None = None
    clarification: dict[str, Any] | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    trace_summary: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None
    error: ErrorDetail | None = None


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, *, case_id: str | None = None,
                 retryable: bool = False):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.case_id = case_id
        self.retryable = retryable


def _error_response(error: ApiError) -> JSONResponse:
    payload = CaseResponse(
        case_id=error.case_id,
        status="failed",
        error=ErrorDetail(code=error.code, message=error.message, retryable=error.retryable),
    )
    return JSONResponse(status_code=error.status_code, content=payload.model_dump(mode="json"))


def _is_rate_limit(value: Any) -> bool:
    if getattr(value, "status_code", None) == 429:
        return True
    response = getattr(value, "response", None)
    if getattr(response, "status_code", None) == 429:
        return True
    if isinstance(value, dict):
        answer = value.get("answer") or {}
        detail = answer.get("error") or {}
        return answer.get("status") == "request_error" and detail.get("type") in {
            "RateLimitError", "TooManyRequestsError"
        }
    return False


def _case_status(state: dict[str, Any]) -> str:
    status = state.get("status")
    if status == "waiting_clarification":
        return "needs_clarification"
    if status in {"completed", "limited"}:
        return "completed"
    return "running"


def _to_response(state: dict[str, Any], case_id: str) -> CaseResponse:
    evidence = list(state.get("evidence") or [])
    ioc_payload = next((row.get("data") for row in evidence if row.get("kind") == "iocs"), {}) or {}
    iocs = list(ioc_payload.get("iocs") or [])
    signals: list[str] = []
    for ioc in iocs:
        for observation in ioc.get("observations") or []:
            if observation not in signals:
                signals.append(observation)
    for warning in ioc_payload.get("warnings") or []:
        reason = warning.get("reason") if isinstance(warning, dict) else str(warning)
        if reason and reason not in signals:
            signals.append(reason)

    answer_payload = state.get("answer") or {}
    clarification_payload = state.get("clarification") or {}
    clarification = None
    if clarification_payload.get("pending"):
        clarification = {
            "field": clarification_payload.get("pending"),
            "question": clarification_payload.get("question"),
        }
    budget = state.get("budget")
    model_usage = answer_payload.get("usage")
    usage = None
    if budget is not None or model_usage is not None:
        usage = {"workflow_budget": budget or {}, "model": model_usage}
    history = list(state.get("tool_history") or [])
    trace_summary = {
        "run_id": state.get("run_id"),
        "workflow_status": state.get("status"),
        "tools": [{"tool": row.get("tool"), "status": row.get("status")} for row in history],
        "verification": list(state.get("verification") or []),
        "recovery": state.get("recovery") or None,
    }
    return CaseResponse(
        case_id=case_id,
        status=_case_status(state),
        facts=list(state.get("facts") or []),
        suspicious_signals=signals,
        iocs=iocs,
        answer=answer_payload.get("answer") or None,
        clarification=clarification,
        evidence=evidence,
        trace_summary=trace_summary,
        usage=usage,
    )


def _valid_case_id(case_id: str) -> bool:
    return bool(CASE_ID.fullmatch(case_id))


def _reply_value(reply: bool | str) -> bool:
    if type(reply) is bool:
        return reply
    normalized = reply.strip().lower()
    if normalized in TRUE_REPLIES:
        return True
    if normalized in FALSE_REPLIES:
        return False
    raise ApiError(400, "invalid_reply", "reply must be a boolean or an unambiguous yes/no answer")


def _parse_email_failed(state: dict[str, Any]) -> bool:
    return any(row.get("tool") == "parse_email" and row.get("status") == "error"
               for row in state.get("tool_history") or [])


async def _read_eml(upload: UploadFile) -> bytes:
    filename = upload.filename or ""
    if Path(filename).suffix.lower() != ".eml":
        await upload.close()
        raise ApiError(400, "invalid_file_type", "file must have a .eml extension")
    content = bytearray()
    try:
        while chunk := await upload.read(UPLOAD_CHUNK_BYTES):
            content.extend(chunk)
            if len(content) > MAX_EML_BYTES:
                raise ApiError(413, "file_too_large", f"file exceeds the {MAX_EML_BYTES}-byte limit")
    finally:
        await upload.close()
    if not content:
        raise ApiError(400, "empty_file", "file must not be empty")
    return bytes(content)


@contextmanager
def _default_workflow():
    config_path = Path(os.getenv("PHISHING_API_CONFIG", str(ROOT / "config.json")))
    data_path = Path(os.getenv("PHISHING_API_DATA", str(ROOT / "data")))
    checkpoint_path = Path(os.getenv(
        "PHISHING_API_CHECKPOINT", str(ROOT / ".local" / "api-checkpoints.sqlite")
    ))
    config = load_config(config_path)
    completion = groq_completion(config)
    chunks, _ = ingest_pdfs(data_path, config)
    retriever = ChromaRetriever(chunks, config)
    try:
        with open_workflow(checkpoint_path, retriever, config, completion) as workflow:
            yield workflow
    finally:
        retriever.close()


def create_app(workflow=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.workflow_lock = asyncio.Lock()
        if workflow is not None:
            application.state.workflow = workflow
            yield
            return
        with _default_workflow() as live_workflow:
            application.state.workflow = live_workflow
            yield

    application = FastAPI(title="Phishing Defence Agent API", version="1.0.0", lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @application.exception_handler(ApiError)
    async def api_error_handler(_request: Request, exc: ApiError):
        return _error_response(exc)

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, _exc: RequestValidationError):
        return _error_response(ApiError(422, "schema_validation_error", "request schema is invalid"))

    @application.get("/api/health", response_model=HealthResponse)
    async def health():
        return HealthResponse()

    async def call_workflow(method: str, *args):
        try:
            async with application.state.workflow_lock:
                function = getattr(application.state.workflow, method)
                result = await asyncio.to_thread(function, *args)
        except ApiError:
            raise
        except Exception as exc:
            if _is_rate_limit(exc):
                raise ApiError(429, "upstream_rate_limited", "upstream model rate limit reached",
                               case_id=args[0] if args else None, retryable=True) from None
            raise ApiError(500, "internal_error", "the request could not be completed",
                           case_id=args[0] if args else None) from None
        if _is_rate_limit(result):
            raise ApiError(429, "upstream_rate_limited", "upstream model rate limit reached",
                           case_id=args[0] if args else None, retryable=True)
        return result

    async def start_case(request: dict[str, Any], case_id: str | None = None):
        selected_case_id = case_id or uuid.uuid4().hex
        if not _valid_case_id(selected_case_id):
            raise ApiError(400, "invalid_case_id", "case_id format is invalid",
                           case_id=selected_case_id)
        existing = await call_workflow("state", selected_case_id)
        if existing:
            raise ApiError(400, "case_exists", "case_id already exists", case_id=selected_case_id)
        state = await call_workflow("run", selected_case_id, request)
        if request.get("kind") == "email" and _parse_email_failed(state):
            raise ApiError(400, "email_parse_error", "the .eml message could not be parsed",
                           case_id=selected_case_id)
        return _to_response(state, selected_case_id)

    @application.post("/api/analyze", response_model=CaseResponse)
    async def analyze(payload: AnalyzeRequest):
        email_text = payload.email_text
        description = payload.incident_description
        if email_text is not None and not email_text.strip():
            raise ApiError(400, "invalid_input", "email_text must not be blank")
        if description is not None and not description.strip():
            raise ApiError(400, "invalid_input", "incident_description must not be blank")
        if email_text is None and description is None:
            raise ApiError(400, "invalid_input", "email_text or incident_description is required")
        if email_text is not None:
            request = {
                "kind": "email",
                "question": description.strip() if description else "Is this email suspicious?",
                "email_text": email_text,
            }
        else:
            request = {"kind": "knowledge", "question": description.strip()}
        return await start_case(request, payload.case_id)

    @application.post("/api/analyze/file", response_model=CaseResponse)
    async def analyze_file(file: UploadFile | None = File(default=None),
                           incident_description: str | None = Form(default=None)):
        if file is None:
            raise ApiError(400, "missing_file", "file is required")
        if incident_description is not None and not incident_description.strip():
            await file.close()
            raise ApiError(400, "invalid_input", "incident_description must not be blank")
        if incident_description is not None and len(incident_description) > 20_000:
            await file.close()
            raise ApiError(400, "invalid_input", "incident_description is too long")
        content = await _read_eml(file)
        request = {
            "kind": "email",
            "question": (incident_description.strip() if incident_description
                         else "Is this email suspicious?"),
            "email_text": content,
        }
        return await start_case(request)

    @application.post("/api/cases/{case_id}/reply", response_model=CaseResponse)
    async def reply(case_id: str, payload: ReplyRequest):
        if not _valid_case_id(case_id):
            raise ApiError(400, "invalid_case_id", "case_id format is invalid", case_id=case_id)
        state = await call_workflow("state", case_id)
        if not state:
            raise ApiError(404, "case_not_found", "case_id does not exist", case_id=case_id)
        pending = (state.get("clarification") or {}).get("pending")
        if state.get("status") != "waiting_clarification" or not pending:
            raise ApiError(400, "no_pending_clarification", "case has no pending clarification",
                           case_id=case_id)
        value = _reply_value(payload.reply)
        resumed = await call_workflow("resume", case_id, {pending: value})
        return _to_response(resumed, case_id)

    @application.get("/api/cases/{case_id}", response_model=CaseResponse)
    async def get_case(case_id: str):
        if not _valid_case_id(case_id):
            raise ApiError(400, "invalid_case_id", "case_id format is invalid", case_id=case_id)
        state = await call_workflow("state", case_id)
        if not state:
            raise ApiError(404, "case_not_found", "case_id does not exist", case_id=case_id)
        return _to_response(state, case_id)

    return application


app = create_app()
