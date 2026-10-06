"""Safe observation helpers for opt-in real local model tests; never substitute output."""

import json

import httpx

from agentic_intent_gate.domain.validation import ValidationFailure


class ObservedTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.requests = []
        self.delegate = httpx.AsyncHTTPTransport(retries=0, trust_env=False)

    async def handle_async_request(self, request):
        payload = json.loads(request.content)
        self.requests.append(
            {
                "method": request.method,
                "url": str(request.url),
                "model": payload["model"],
                "response_format": payload.get("response_format"),
            }
        )
        return await self.delegate.handle_async_request(request)

    async def aclose(self):
        await self.delegate.aclose()


def validate_and_record(validator, raw, record):
    stages = (
        "json_parse",
        "schema_validation",
        "semantic_validation",
        "trusted_risk_recomputation",
    )
    record.update(dict.fromkeys(stages, "NOT_RUN"))
    try:
        plan = validator.parse_plan(raw)
    except ValidationFailure as exc:
        record["issues"] = [{"path": issue.path, "code": issue.code} for issue in exc.issues]
        codes = {issue.code for issue in exc.issues}
        if any(code.startswith("PARSE_") for code in codes):
            record["json_parse"] = "FAIL"
        elif any(code.startswith("SCHEMA_") for code in codes):
            record.update(json_parse="PASS", schema_validation="FAIL")
        elif any(code.startswith("SEMANTIC_") for code in codes):
            record.update(json_parse="PASS", schema_validation="PASS", semantic_validation="FAIL")
            if "SEMANTIC_RISK_MISMATCH" in codes:
                record["trusted_risk_recomputation"] = "FAIL"
        raise
    record.update(dict.fromkeys(stages, "PASS"))
    return plan
