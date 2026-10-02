# Day 1 — minimal architecture freeze

Date: 2026-10-03, Pacific/Auckland. **Contract/design freeze only; application implementation has not started.** [ADR-011](adr/ADR-011-day1-planning-preview.md) defines precedence for this local preview; the [Stage 1 contracts](stage1-contracts.md) retain authority for later durable execution.

## Smallest slice and ownership

```text
POST /api/plans {text}
    → Intent
    → ModelProvider (untrusted JSON text)
    → strict parser + Plan schema + trusted Tool checks
    → validated Plan or sanitized rejection
    → same-origin display
```

One Python process, one request, one bounded model call. No Strands loop or other orchestration framework is needed for the preview. No database, durable acceptance, tools running in the model loop, approval, execution, or cloud adapter is included. A future Strands planner can implement the application port without placing SDK types in the core.

## Seven core types / interfaces

These are the minimum application definitions to implement next; prose/signatures are frozen today, not runnable Python classes. Wire field names follow the existing contracts. Domain values are immutable after validation and must not alias mutable provider dictionaries.

| Name | Minimal contract | Trust / Day 1 behavior |
| --- | --- | --- |
| Intent | `text: str` | Validated human input; no caller actor/role/provider fields. No durable event identity in a preview |
| Plan | `goal: str`, `target: str`, ordered `steps: tuple[PlanStep, ...]`, `risk_class: Risk` | Immutable validated proposal, never execution evidence |
| Risk | `READ_ONLY`, `SIDE_EFFECT`, `APPROVAL_REQUIRED` | Alias of existing ToolDefinition.risk_class; highest registry risk across steps must match the plan exactly |
| Approval | Alias of existing `ApprovalRequest` / `ApprovalDecision` contracts | Deferred, never constructed here. Exact Action/proposal hash, verified distinct approver, expiry and permission rules remain mandatory later; no boolean consent shortcut |
| ExecutionResult | Alias of existing `ToolResult` contract | Deferred, never constructed here. SUCCEEDED/FAILED/UNKNOWN plus effect_evidence and provider proof keep their existing meanings |
| ModelProvider | Async `generate_plan(Intent, tools, plan_schema, timeout_seconds) -> str`; classified `ModelProviderError` on failure | Produces untrusted text only, cannot validate itself into execution authority |
| Tool | Preview projection of `ToolDefinition`: `name`, `version`, `description`, `input_schema`, `risk_class` | Trusted immutable catalog; no callable or execution method; full Stage 1 definition remains required for execution |

`PlanStep` has only `tool_name: str`, `tool_version: str`, and `arguments: JSON object`. JSON wire arrays map to immutable ordered steps. There are no model-generated IDs, timestamps, URLs, approvals, execution statuses, dependencies, or shell commands.

```python
class ModelProvider(Protocol):
    async def generate_plan(
        self,
        intent: Intent,
        *,
        tools: Sequence[Tool],
        plan_schema: Mapping[str, object],
        timeout_seconds: float,
    ) -> str: ...
```

`ModelProviderError.code` is one of `MODEL_TIMEOUT`, `MODEL_UNAVAILABLE`, or `MODEL_RESPONSE_ERROR`. The adapter translates transport/vendor exceptions into this bounded error and keeps credentials, raw exceptions, prompts, and HTTP response objects out of the core/API. Deadline enforcement belongs to both the coordinator's monotonic total budget and the adapter's network timeout. Application parsing/validation happens after the provider returns; invalid plan text is `PLAN_VALIDATION_ERROR`, not a validated provider result.

## Structured plan contract

Canonical executable shape: [plan schema](../schemas/day1-plan.schema.json), JSON Schema Draft 2020-12. Request shape: [intent schema](../schemas/day1-intent.schema.json). Both reject extra fields. No migration, general workflow schema, or schema registry is introduced.

```json
{
  "goal": "Read the service-demo record",
  "target": "service-demo",
  "steps": [
    {
      "tool_name": "get_service_record",
      "tool_version": "1",
      "arguments": {"service_id": "service-demo"}
    }
  ],
  "risk_class": "READ_ONLY"
}
```

The three version `1` preview definitions resolve the existing Stage 1 demo tools; version numbers are newly frozen for this slice, not an implemented registry:

| Tool | Exact arguments | Trusted risk |
| --- | --- | --- |
| get_service_record | service_id | READ_ONLY |
| append_service_note | service_id, note (1–1024 characters) | SIDE_EFFECT |
| set_maintenance_mode | service_id, enabled (boolean), expected_revision (integer ≥1) | APPROVAL_REQUIRED |

Default preview target allowlist is only `service-demo`, configured server-side. Every step's service_id must equal Plan.target. Goal is nonblank, ≤512 characters; target/IDs use bounded ASCII service identifiers; 1–5 ordered steps; ≤3 read proposals and ≤2 distinct mutation proposals. Reject repeated identical mutation proposals in a preview rather than changing the generated plan. The later Strands planner's pre-commit duplicate collapse remains its separate rule. Reject reads after the first mutation proposal: this preview has no results/dependent-step mechanism. No earlier write result can supply a later argument. All numbers are integers within 1…2^63−1; no floats or booleans masquerading as integers.

For maintenance, expected_revision must equal trusted preview configuration `service-demo: 1`. This is synthetic planning context, not a live service read or evidence of current external state. The prompt labels it as such. Later execution must obtain and recheck authoritative provider revisions; the preview cannot assert freshness. Changing target/context is trusted configuration, not a field the model or browser may set.

## Deterministic validation boundary

Implement the following order, without model-based judging:

1. Before model I/O: accept only application/json UTF-8, enforce 16 KiB request bytes, reject duplicate keys/nonfinite numbers and schema violations, require nonblank text ≤8192 characters AND ≤8 KiB UTF-8. No tool/target/provider/actor overrides in the body.
2. After generation: require one complete assistant content string; provider envelope ≤64 KiB, content ≤16 KiB UTF-8; reject non-stop finish reasons, tool calls, empty/invalid envelopes, and truncation. The adapter returns no partial streaming output.
3. Strictly decode exactly one JSON object with no duplicate keys, NaN/Infinity, prose, fences, trailing objects, or floating-point values. Do not salvage substrings, trim fields, coerce types, fill missing fields, or silently repair output.
4. Validate against the plan schema, including per-tool version/argument branches, bounds, enum, and no-extra-field rules.
5. Resolve steps against the trusted catalog; check configured target, target/argument agreement, read/mutation caps/order, no duplicate mutation, configured expected_revision, and ≤4 KiB UTF-8 compact JSON arguments per step. Compute aggregate risk from the registry (`READ_ONLY < SIDE_EFFECT < APPROVAL_REQUIRED`); reject any mismatch, including a downgrade.
6. Construct immutable Plan only after every check passes. Invalid output has no Plan in the API result and causes no tool I/O. Validation verifies the proposal contract, not natural-language fidelity, authorization, live service facts, or feasibility.

The schema alone cannot check cross-field target equality, aggregate risk, or trusted revision. Passing it is insufficient without step 5. Application semantic checks are planned, not implemented by today's schema checks. Known invalid live-probe risk is preserved in [evidence](evidence/day1-local-model-probe.json).

## Minimal state flow

```text
IntentReceived → PlanGenerating → PlanGenerated → PlanValidated
                         │               │
                         └───────────────┴──→ PlanRejected
```

IntentReceived follows successful request validation. PlanGenerating starts the sole provider attempt. PlanGenerated means complete candidate text was received, even if its JSON/schema is invalid. Only independent validation grants PlanValidated. Transport/timeout/envelope failures go directly from PlanGenerating to PlanRejected; invalid JSON/schema/policy goes from PlanGenerated to PlanRejected. No retry/repair states are added. Invalid HTTP input has `state=null` because no Intent was accepted. These are transient request states; they never mutate a durable Turn or imply an Action state.

## Model provider boundary and local configuration

Keep vendor HTTP construction and envelope extraction in `adapters/models/local_openai.py`; the application depends only on the above Protocol. Composition requires server-side `FOUNDRY_LOCAL_ENDPOINT` (base URL without `/v1`) and `FOUNDRY_LOCAL_MODEL` (actual wire ID). Discover the service URL using installed CLI help/status and confirm `/v1/models` at setup. No provider selection from the request, automatic model download/load/start, or cloud fallback.

Audit-time base URL is `http://127.0.0.1:39839`, wire ID `Phi-4-mini-instruct-generic-gpu`, alias `phi-4-mini`. The port and catalog/wire naming may change; these observations are not permanent defaults. The Day 1 adapter accepts only loopback HTTP endpoints; follows no redirects and bypasses proxy environment settings. Reject incomplete/nonlocal configuration before startup. This local transport restriction belongs to the adapter, so future authenticated remote providers need not copy it.

Next implementation uses nonstreaming `POST {endpoint}/v1/chat/completions`, `n=1`, temperature 0, max_tokens 1024, a system prompt containing the exact plan schema, catalog, synthetic context and proposal-only instruction, plus the user text as a separate message. Do not send tool-call functions or use returned tool_calls. One attempt per request, no hidden transport retries, automatic repair, or provider/model fallback. The 30-second application budget includes validation/serialization; model I/O has at most 28 seconds. Timeout rejects the request; it does not prove the inference process stopped. No external effect is possible because no tools run.

The [official REST reference](https://learn.microsoft.com/en-us/azure/foundry-local/reference/reference-rest) supports this chat-completions/discovery boundary. Native JSON-schema enforcement is NOT VERIFIED and is not required by the design. Temperature 0 and a JSON prompt are not validity guarantees. Deterministic provider fixtures exercise valid and adverse outputs independently of the live model; they must never silently substitute for it in the live lane.

## HTTP and display contract

| Condition | HTTP | Response |
| --- | --- | --- |
| Validated preview | 200 | `{state:"PlanValidated", plan:Plan, error:null}` |
| Bad request JSON/schema/blank text | 400 | `{state:null, plan:null, error:{code:"INVALID_INTENT", message, issues}}` |
| Oversized request | 413 | Same null-state shape; code REQUEST_TOO_LARGE |
| Unsupported content type | 415 | Same null-state shape; code UNSUPPORTED_MEDIA_TYPE |
| Generated JSON/schema/policy invalid | 422 | `{state:"PlanRejected", plan:null, error:{code:"PLAN_VALIDATION_ERROR", message, issues}}` |
| Local model unavailable, model absent, upstream 429/5xx or connection refused | 503 | PlanRejected, plan null; code MODEL_UNAVAILABLE |
| Deadline exceeded | 504 | PlanRejected, plan null; code MODEL_TIMEOUT |
| Unexpected provider status/envelope/refusal/tool call/truncation | 502 | PlanRejected, plan null; code MODEL_RESPONSE_ERROR |
| Unexpected application fault | 500 | PlanRejected, plan null; code INTERNAL_ERROR; no traceback |

`issues` is a bounded list of at most ten `{path, code}` entries, never raw field values/provider bodies. `message` is a fixed sanitized description ≤256 characters. Request rejection precedes generation. The route never returns 202 or advertises durable acceptance/idempotency. Later durable ingress uses its separate Stage 1 semantics.

The next prompt's small same-origin page accepts text and displays ordered tool/arguments, risk, validation status, or sanitized error using textContent. It labels every result “Planning preview — no tools executed” and presents no approval/execution controls. Clear any previous Plan when starting/failing a request and disable concurrent submissions. Use no frontend framework or separate development server.

## Explicit deferrals and evidence

No persistent history, database, authentication/RBAC system, approval/execution, retries/recovery, shell tools, MCP, multi-agent orchestration, memory, Bedrock, AgentCore, Nebius, Vultr, sandbox, Docker, deployment, or production exposure. Serve the eventual preview only on loopback. The existing execution contracts apply when an execution slice is authorized; current validation does not weaken them.

Frozen now: type names, schemas, trusted risk/catalog, provider failure boundary, states, API errors, and implementation scope. Still to verify in the next prompt: pinned dependency compatibility, actual adapter timeout behavior, semantic-validator negatives, prompt success rate, real API/provider integration, and actual browser display. Architecture PASS is independent of a live model producing a valid plan on demand.
