# Agent Demo API Contract

Run locally with `uvicorn phishing_agent.api:app --reload`. The default adapter reads
`config.json`, ingests `data/`, and persists A1 checkpoints in
`.local/api-checkpoints.sqlite`. `GROQ_API_KEY` is required for live analysis.

The only allowed browser origin is `http://localhost:5173`.

## Endpoints

### `GET /api/health`

Response `200`:

```json
{"status":"ok"}
```

### `POST /api/analyze`

Creates and starts an A1 case. At least one of `email_text` and
`incident_description` must be a non-blank string. If `case_id` is omitted, the API
generates one. A supplied ID may contain letters, digits, `.`, `_`, `:`, and `-`,
with a maximum length of 128.

```json
{
  "email_text": "From: alerts@example.test\nSubject: Notice\n\nVisit http://example.test",
  "incident_description": "What should I do?",
  "case_id": "demo-001"
}
```

### `POST /api/cases/{case_id}/reply`

Answers the case's current clarification and resumes the same SQLite-checkpointed
A1 run. `reply` accepts a JSON boolean or an unambiguous yes/no string.

```json
{"reply": false}
```

### `GET /api/cases/{case_id}`

Returns the latest persisted case state without starting a new run.

## Case response

All case endpoints and their handled errors use the same stable shape. Missing
collections are `[]`; missing scalar/object values are `null`.

```json
{
  "case_id": "demo-001",
  "status": "needs_clarification",
  "facts": [],
  "suspicious_signals": [],
  "iocs": [],
  "answer": null,
  "clarification": {"field": "clicked_link", "question": "Did you click a link in the email, or only open the email?"},
  "evidence": [],
  "trace_summary": {"run_id": "...", "workflow_status": "waiting_clarification", "tools": [], "verification": [], "recovery": "clarify"},
  "usage": {"workflow_budget": {}, "model": null},
  "error": null
}
```

`status` is one of `running`, `needs_clarification`, `completed`, or `failed`.
An A1 bounded/limited conclusion is exposed as `completed`; its limitation remains
visible in `answer`, `trace_summary.workflow_status`, and `trace_summary.recovery`.

## Error contract

Errors retain the case response shape and set `status` to `failed`:

```json
{
  "case_id": "demo-001",
  "status": "failed",
  "facts": [],
  "suspicious_signals": [],
  "iocs": [],
  "answer": null,
  "clarification": null,
  "evidence": [],
  "trace_summary": null,
  "usage": null,
  "error": {"code": "case_not_found", "message": "case_id does not exist", "retryable": false}
}
```

- `400`: semantically invalid input, invalid reply, duplicate case, or no pending clarification
- `404`: unknown `case_id`
- `422`: JSON/schema validation failure
- `429`: upstream model rate limit (`retryable: true`)
- `500`: sanitized internal error

Error messages never include provider exception text, API keys, request email
content, or tracebacks.
