# AgenticIntentGate — contracts

**Scope update — 2026-10-03:** these contracts govern later durable execution. The [Day 1 preview contracts](day1-architecture-freeze.md) add Intent/Plan and reuse the risk/tool vocabulary; Approval and ExecutionResult refer back here but are not instantiated in the preview. [ADR-011](adr/ADR-011-day1-planning-preview.md) defines the scope boundary.

**OUR DESIGN DECISION — normative Stage 1 contracts, not Python classes or database migrations.** State names are defined in [state machines](stage1-state-machine.md); transaction groups and identity algorithms in [idempotency and concurrency](stage1-idempotency-and-concurrency.md).

## Common types and limits

`ID` is an opaque, bounded UTF-8 string; generated logical IDs use the UUID namespacing rules in the identity document. `Timestamp` is UTC with timezone, serialized as RFC 3339. `JSON` is a validated JSON value. `Hash` is lowercase SHA-256 hex. `Version` is an integer starting at 1 and incremented on each conditional change. `R` means required, `O` optional/nullable. Lists preserve order. Fields are immutable unless explicitly identified as mutable below.

Trusted `deployment_scope` persists across restart/deploy and includes the configured tenant/environment. Changing it creates a new idempotency namespace, so it is never an automatic deployment-version suffix. MVP has one tenant; authorization still checks actor/conversation/resource membership. UTC clock accuracy within 30 seconds is a deployment requirement; faults or unacceptable skew disable approval decisions and new write claims.

Frozen limits: inbound text 8 KiB; semantic inbound envelope 16 KiB; tool arguments 4 KiB per call; tool output 8 KiB; final delivery payload 8 KiB; committed planning snapshot 64 KiB; at most three read calls, two distinct mutation proposals, four model requests per planning attempt, three planning attempts per Turn, and three external write/send attempts per logical Action/Delivery. Limits apply before any batch commit or I/O. Binary data, streaming final delivery, and large payload blob stores are deferred.

## InboundMessage

Owner: ingress adapter produces it; acceptance persists its immutable semantic fields in InboundEvent. Lifecycle: validation/authentication then atomic acceptance or rejection.

| Field | Type | Required | Meaning / mutability |
| --- | --- | --- | --- |
| deployment_scope | string | R | Trusted configuration, immutable |
| inbound_event_id | ID | R | Provider logical event ID or caller's stable idempotency key; immutable |
| provider_scope | string | R | Authenticated integration/account namespace; `local-client` for local HTTP |
| conversation_id | ID | R | Trusted normalized mapping; membership checked |
| actor_id | ID | R | From verified authentication, never from model or an unverified body |
| channel | enum | R | `HTTP`, `CLI`, or future adapter name |
| content | object `{text: string}` | R | Validated semantic input |
| reply_destination | bounded object | R | Authorized mailbox/address chosen by adapter; bound into input hash |
| received_at | Timestamp | R | Adapter receipt time for this delivery, not the logical event key |
| correlation_id | ID | R | Incoming valid trace hint or generated value; first acceptance establishes canonical value |
| inbound_attempt_id | ID | R | Generated per external delivery attempt, distinct from event and Turn |
| provider_metadata | bounded JSON object | O | Allowlisted nonsecret metadata; attempt-only fields excluded from semantic hash |

Invariant: repeated event key with different actor, conversation, content, or reply destination is an idempotency conflict, not a new Turn. Stable identity is mandatory even for providers without a reliable event ID; the adapter/client must supply a persisted key. Receipt timestamps, transport request IDs, and correlation hints do not change logical identity.

## RequestContext

Owner: authentication/ingress; immutable request-local context. It is not a reusable authorization credential stored on an Action.

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| request_id | ID | R | Physical command request |
| principal_id / actor_id | ID / ID | R | Verified subject; service transport principal may be separately recorded |
| authentication_method | enum | R | Local test assertion or signed actor assertion; AWS SigV4 is additional transport authentication |
| authenticated_at / expires_at | Timestamp / Timestamp | R | Credential validity |
| deployment_scope | string | R | Trusted scope |
| conversation_id | ID | O | Present for conversation-bound commands |
| correlation_id | ID | R | Attempt trace value; linked to canonical Turn correlation |
| authorization_context | object | R | Server-derived role/resource membership and policy revision, not caller claims |
| deadline | Timestamp | R | Bounded command deadline |
| provider_metadata | bounded object | O | Allowlisted transport context |

Authentication assertion details and authorization freshness are in [security](stage1-security-observability.md). Expired caller tokens do not authorize resumed commands. Executor work uses the stored requester identity plus a fresh policy lookup, not a saved bearer token.

## InboundEvent and Conversation

InboundEvent: `event_key:R string`, all semantic InboundMessage fields, `semantic_hash:R Hash`, `turn_id:R ID`, `first_received_at:R Timestamp`, `correlation_id:R ID`. These are immutable. `duplicate_count:R integer`, `last_received_at:R Timestamp`, and `version:R Version` may change atomically with duplicate audit. Original acceptance is never overwritten. Attempt audit stores `inbound_attempt_id`, receipt time, original and incoming correlation IDs, and duplicate/conflict classification.

Conversation: `conversation_id:R ID`, `deployment_scope:R string`, `authorized_actor_ids:R set`, `next_sequence:R integer`, `active_turn_id:O ID`, `completed_history:R bounded list of immutable Turn-summary references`, `version:R Version`, `created_at/updated_at:R Timestamp`. Registry membership is admin configuration, audited when changed. Acceptance reserves the single active slot and assigns a monotonically increasing sequence. Terminal Turn finalization releases it atomically. History references are limited to the latest 20 summaries for model context; older durable Turn/audit records are not deleted by context truncation.

## Turn

Owner: TurnCoordinator. Identity/accepted input are immutable; only conditional store operations mutate lifecycle fields.

| Field | Type | Required | Meaning / mutability |
| --- | --- | --- | --- |
| turn_id / event_key | ID / string | R | Deterministic identity and unique InboundEvent link |
| conversation_id / conversation_sequence | ID / integer | R | Accepted conversation and serialized sequence |
| actor_id / deployment_scope | ID / string | R | Requester and scope |
| correlation_id | ID | R | Canonical root correlation |
| state | TurnState | R | Mutable under allowed transitions |
| input | validated content + reply destination | R | Immutable input snapshot |
| planning_attempt_count | integer | R | Mutable, charged before starting a planning attempt |
| model_request_count | integer | R | Mutable cumulative count; per-attempt cap also enforced |
| planning_model_request_count | integer | R | Mutable current-planning-attempt count, reset only by a new charged planning claim |
| processing_owner / processing_token | ID / ID | O | Mutable fenced processing claim; cleared when waiting/terminal |
| plan_committed_at | Timestamp | O | Set once at plan barrier, never cleared |
| planning_snapshot | bounded object | O | Set once: selected history, read results, assistant tool-use/messages, proposal receipts, draft text, model/config metadata |
| tool_call_ids / action_ids | ordered lists of IDs | R | Empty before plan; set once with plan commit |
| final_summary | bounded JSON | O | Set once at terminal finalization; generated from durable outcomes |
| delivery_id | ID | O | Set once with terminal finalization |
| last_error | FailureRecord | O | Mutable sanitized failure details |
| created_at / updated_at / completed_at | Timestamp | R / R / O | Created immutable, others lifecycle updates |
| version | Version | R | Mutable CAS version |

States: RECEIVED, PROCESSING, WAITING_APPROVAL, WAITING_RECONCILIATION, COMPLETED, FAILED. A terminal Turn never replans. A committed plan never invokes the model again, including after rejection or failure. A nonterminal Turn holds the conversation slot. COMPLETED means planned operations/read result are resolved successfully; Delivery may still be pending, failed, or unknown. FAILED retains individual Action successes and failures.

## ToolDefinition

Owner: trusted versioned tool registry/policy configuration. Immutable for any existing proposal; a new registry version is a new definition, not a mutation of old Actions.

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| name / version / description | string | R | Stable allowlisted tool identity and model-facing explanation |
| input_schema / output_schema | JSON schema | R | Strict schemas; unknown fields rejected |
| risk_class | enum | R | READ_ONLY, SIDE_EFFECT, APPROVAL_REQUIRED; registry-owned |
| side_effect_class | enum | R | NONE, APPEND, UPDATE |
| approval_policy | object | R | NONE or DISTINCT_APPROVER with permitted role, 15-minute TTL |
| authorization_policy | object | R | Requester role and permitted resource namespace |
| policy_revision | string | R | Proposal policy snapshot |
| timeout_seconds | integer | R | Maximum adapter I/O budget: read 5, mutation 10 |
| retry_policy | object | R | Classified limits/delays; never unconditional |
| idempotency_capability | enum + metadata | R | NONE or KEYED; namespace, retention guarantee, argument binding |
| reconciliation_capability | enum + metadata | R | STATUS_LOOKUP or NONE; consistency/completion meaning |

Recovery profiles combine capabilities: `LOOKUP_AND_KEYED`, `LOOKUP_ONLY`, `KEYED_ONLY`, `NONE`. Core never assumes status lookup from the presence of an idempotency key. A provider retention window must be known before permitting keyed replay from UNKNOWN. MVP demo receipts have no automatic expiry.

| Demo tool | Input schema concept | Risk / authorization | Recovery |
| --- | --- | --- | --- |
| get_service_record | `service_id:R string` in configured seeded service set | READ_ONLY, operator or approver | Read retries; no Action |
| append_service_note | `service_id:R string`, `note:R string` length 1–1024 | SIDE_EFFECT/APPEND, operator | LOOKUP_AND_KEYED |
| set_maintenance_mode | `service_id:R string`, `enabled:R boolean`, `expected_revision:R integer >=1` | APPROVAL_REQUIRED/UPDATE, operator; distinct maintenance approver | LOOKUP_AND_KEYED; stale revision is confirmed no-effect failure |

The maintenance adapter checks its external resource revision in the same transaction as mutation/receipt. Approval of stale data does not override the provider's revision check.

## ToolCall

Owner: planner adapter validates model proposals; coordinator durably commits them in the plan snapshot. No direct external mutation permission.

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| tool_call_id / turn_id | ID / ID | R | Core-owned stable call identity after plan commit |
| plan_ordinal | integer | R | Order of read/proposal calls in the accepted planning snapshot |
| tool_name / tool_version | string / string | R | Resolved registry definition |
| canonical_arguments | JSON object | R | Validated canonical representation |
| argument_hash | Hash | R | SHA-256 of canonical argument bytes |
| proposed_at | Timestamp | R | Planning timestamp |
| model_tool_call_id | string | O | Untrusted provider/framework correlation only |
| model_metadata | bounded object | O | Provider, model/config identifiers and request correlation; no credentials |
| read_result | ToolResult | O | Required for a completed READ_ONLY call |
| action_id | ID | O | Required for a committed side-effect proposal |

All fields immutable once committed. Proposal-only wrapper replies are separate planning receipts `{status:PROPOSAL_ONLY, ordinal, tool_name, argument_hash}`; they must never be recorded as ToolResult SUCCEEDED. Repeated identical side-effect proposals within one planning attempt are collapsed to one ordinal; explicit repeated identical effects in one Turn are outside MVP.

## Action

Owner: ActionExecutionGateway manages lifecycle through StateStore; coordinator creates immutable proposals in `commit_plan`.

| Field | Type | Required | Meaning / mutability |
| --- | --- | --- | --- |
| action_id / turn_id / tool_call_id | ID | R | Separate stable identities |
| action_ordinal | integer | R | Side-effect execution order |
| tool_name / tool_version | string | R | Immutable definition binding |
| canonical_arguments / argument_hash | object / Hash | R | Immutable argument binding |
| proposal_hash | Hash | R | Scope, requester, tool/version, args, risk, approval and policy binding |
| actor_id / conversation_id / deployment_scope / correlation_id | ID/string | R | Immutable origin |
| risk_class / side_effect_class / policy_revision | enum/string | R | Immutable registry snapshot |
| approval_requirement | object | R | Immutable NONE or bound policy |
| approval_id | ID | O | Deterministic reserved ID when approval required; request may not yet exist in PROPOSED |
| idempotency_key | string | R | Immutable downstream key, independent of model ID |
| recovery_profile / capability_snapshot | enum/object | R | Immutable declared tool capability; current adapter must still honor it |
| state | ActionState | R | Mutable conditionally |
| attempt_count / reconciliation_count | integer | R | Mutable, charged before I/O |
| execution_owner / claim_token / execution_started_at / execution_deadline | ID/Timestamp | O | Current execution attempt claim; nonexpiring ownership |
| retry_not_before | Timestamp | O | Mutable eligibility time after known safe failure |
| external_reference / result | string / ToolResult | O | Written only from confirmed adapter evidence |
| last_error / uncertainty_reason | FailureRecord/string | O | Mutable evidence of failure/uncertainty |
| created_at / updated_at | Timestamp | R | Lifecycle timestamps |
| version | Version | R | CAS version |

States: PROPOSED, WAITING_APPROVAL, READY, EXECUTING, SUCCEEDED, FAILED, UNKNOWN, REJECTED, CANCELLED. Immutable argument changes require a new Turn/proposal/approval; no in-place edits or reuse of old approval. SUCCEEDED means evidence of this effect, not Delivery success. FAILED means definitive failure/no unresolved effect, not a generic timeout. UNKNOWN forbids blind execution.

## ApprovalRequest and ApprovalDecision

Owner: approval service use case in the same application; StateStore creates request together with Action WAITING_APPROVAL. Human decisions are immutable; expiry is a server decision.

| ApprovalRequest field | Type | Required | Meaning |
| --- | --- | --- | --- |
| approval_id / action_id / turn_id | ID | R | Unique immutable request/proposal binding |
| requested_by | ID | R | Stored requesting actor |
| reason | string | R | Server-generated risk reason; model suggestion may be labeled supplementary |
| risk_class / tool_name / tool_version | enum/string | R | Bound definition |
| canonical_arguments / argument_hash / proposal_hash | object/Hash | R | Exact human-reviewable proposal |
| policy_revision / eligible_approver_role | string | R | Bound approval policy |
| state | ApprovalState | R | PENDING, APPROVED, REJECTED, EXPIRED, CANCELLED |
| created_at / expires_at | Timestamp | R | Immutable, expiry after 15 minutes |
| decision | ApprovalDecision | O | Set once |
| version | Version | R | Mutable CAS |

| ApprovalDecision field | Type | Required | Meaning |
| --- | --- | --- | --- |
| decision_request_id | ID | R | Stable control-command key for duplicate decisions |
| approval_id / action_id / proposal_hash | ID/Hash | R | Exact target binding |
| status | enum | R | APPROVED, REJECTED, EXPIRED, or system-only CANCELLED |
| approver_id | ID | R | Verified human for approval/rejection; `system:expiry` for EXPIRED or `system:turn-abort` for CANCELLED |
| decided_at | Timestamp | R | Trusted server time |
| comment | bounded string | O | Sanitized human explanation; required evidence reference for operator resolution is separate |
| authentication_reference / authorization_policy_revision | string | R | Verification/key/policy references, never raw token |

Pending decisions are first-writer-wins under version/expiry checks. Same decision_request_id + same content returns its stored receipt; different content under the same ID conflicts. A different decision after terminal approval conflicts. APPROVED is immutable but only usable until expires_at; approved requests do not transition to EXPIRED later. Execution checks the validity timestamp, and an expired unclaimed Action is CANCELLED. Approver must have permission on the exact service and be distinct from requested_by.

Cancelling an unexecuted Action while its approval is PENDING atomically cancels that request with a system decision and reason. CANCELLED is needed for abandonment after another proposal fails; it does not falsely describe early abandonment as time expiry. Human commands may submit only APPROVED or REJECTED. Previously APPROVED decisions remain historical facts if their Actions are later cancelled.

## ToolExecutionContext and ToolResult

ToolExecutionContext owner: gateway creates immutable context for one claimed attempt; adapter receives only required values.

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| actor_id / conversation_id / turn_id | ID | R | Original requester and scope |
| action_id | ID | O | Required for mutations; absent for read-only calls |
| tool_call_id / correlation_id / deployment_scope | ID/string | R | Correlation and isolation |
| authorization_context | server-derived object | R | Fresh permitted resources and policy revision |
| idempotency_key / claim_token / attempt_number | string/ID/integer | O | Required for mutation attempt |
| deadline | Timestamp | R | Hard adapter budget; does not prove cancellation |

ToolResult owner: tool adapter returns evidence; gateway validates and records it.

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| status | enum | R | SUCCEEDED, FAILED, UNKNOWN |
| output | validated bounded JSON | O | Sanitized schema-checked result |
| external_reference | string | O | Receipt/transaction ID; required for demo mutation success |
| failure_class / message | enum/string | O | Required for FAILED/UNKNOWN |
| effect_evidence | enum | R | NOT_APPLICABLE for reads; CONFIRMED_APPLIED, CONFIRMED_NOT_APPLIED, MAY_HAVE_APPLIED for mutations |
| reconciliation_metadata | bounded object | O | Key, lookup consistency, receipt version, finality, evidence reference |
| started_at / finished_at | Timestamp | R | Attempt observation times |

Mutation SUCCEEDED requires CONFIRMED_APPLIED. FAILED requires CONFIRMED_NOT_APPLIED or confirmed provider terminal failure. Ambiguous exceptions are UNKNOWN/MAY_HAVE_APPLIED. Invalid adapter output after a potentially successful write is UNKNOWN, not a safe validation retry. A cached keyed result remains success for the original Action.

## OutboundDelivery

Owner: finalization creates immutable content; delivery coordinator changes lifecycle. One final summary per Turn is sufficient for MVP; approval instructions/status are query responses, not extra push deliveries.

| Field | Type | Required | Meaning / mutability |
| --- | --- | --- | --- |
| delivery_id / turn_id / conversation_id / correlation_id | ID | R | Immutable logical response identity |
| deployment_scope / channel / destination | string/enum/object | R | Immutable authorized route |
| payload / payload_hash | object/Hash | R | Immutable final summary |
| idempotency_key / capability_snapshot | string/object | R | Stable provider key; capability declared explicitly |
| state | DeliveryState | R | PENDING, SENDING, SENT, FAILED, UNKNOWN |
| attempt_count / reconciliation_count | integer | R | Mutable precharged counts |
| execution_owner / claim_token / send_started_at / send_deadline | ID/Timestamp | O | Mutable nonexpiring send claim |
| retry_not_before | Timestamp | O | Mutable safe retry eligibility |
| provider_reference / last_error | string/FailureRecord | O | Receipt/error evidence |
| created_at / updated_at / version | Timestamp/Version | R | Lifecycle |

SENT means the adapter's receiving provider accepted the payload durably under its documented contract. It does not mean a person read it, SMS carrier delivery, or universal exactly-once messaging. Local mailbox and AWS demo mailbox retain keyed receipts. Generic non-keyed providers may leave UNKNOWN; retries could duplicate messages and need explicit evidence/decision as defined in the failure model.

## Supporting records

ExecutionAttempt / DeliveryAttempt: immutable identity `(logical_id, attempt_number)`; `owner`, `claim_token`, `started_at`, `deadline`, optional terminal observation/result and `version`. A claim and its attempt row are created atomically. Each adapter call has one attempt; SDK retries for mutation transports are disabled unless they demonstrably preserve the same keyed provider contract.

ReconciliationEvidence: `evidence_id`, target ID/attempt/key/hash, observation time, provider result (`CONFIRMED_APPLIED`, `CONFIRMED_NOT_APPLIED_FINAL`, `INCONCLUSIVE`), source/receipt reference, capability validity, optional quiescence evidence, authorized resolver. Absence in an eventually consistent lookup is INCONCLUSIVE. Operator-supplied proof is required to resolve an adapter with no lookup; UNKNOWN cannot be manually dismissed without proof.

QuiescenceEvidence: prior owner and claim token, stopped invocation/process reference, verifier identity/time, evidence reference, and reason it cannot send further I/O. A deadline, Python cancellation request, lease age, or a missing heartbeat alone is insufficient.

FailureRecord: `failure_class`, sanitized `message`, `effect_evidence`, `observed_at`, optional `external_reference`, `retry_not_before`, and `evidence_reference`. AuditEvent: immutable ID, aggregate ID/version, operation ID and canonical operation hash, before/after states, actor/owner, reason/evidence hashes, canonical correlation chain, timestamp. Every durable state transition and claim commits its audit in the same transaction. Ordinary logs are not the durable audit store.
