# AgenticIntentGate — idempotency and concurrency

**OUR DESIGN DECISION — normative Stage 1.** These semantics require later executable contract tests for both storage adapters. They have not been verified by implementation. [Reference source findings](../research/stage0/twilio-reference-study.md#idempotency-retries-and-concurrency) describe process-local suppression; our design uses retained durable records.

## Identity hierarchy

`deployment_scope` is trusted, stable configuration, such as `agentic-intent-gate/demo/dev`. Provider scope is an authenticated integration namespace. Every tuple below is encoded as an unambiguous canonical JSON array, never joined using ambiguous raw delimiters. The namespace constants are versioned constants in the future identity module and must not change during ordinary deploys.

| ID | Creator / creation time | Deterministic / durable | Scope and reuse |
| --- | --- | --- | --- |
| conversation_id | Ingress maps provider conversation, or server registers local conversation before input | Mapping deterministic for scoped provider ID; local registration UUID; durable | Deployment/tenant; reused across sequential Turns; membership is independently checked |
| inbound_event_id | Provider logical event, or client persists Idempotency-Key before first request | Stable input, not server timestamp; durable at acceptance | `(deployment_scope, channel, provider_scope)`; reused only for retries of the same semantic event |
| inbound_attempt_id | Ingress on every transport delivery | Random UUID; stored in audit | Physical attempt; never used for dedup or Turn identity |
| turn_id | Acceptance derives UUIDv5 under fixed Turn namespace from event_key | Deterministic; durable | One event_key → one Turn; reused by duplicate/query/resume |
| tool_call_id | Coordinator at accepted plan commit, UUIDv5 from `(turn_id, plan_ordinal)` | Deterministic for committed plan; durable | Core identity; model_tool_call_id is metadata only |
| action_id | Coordinator at plan commit, UUIDv5 from `(turn_id, action_ordinal)` | Deterministic; durable | Stable per fixed mutation slot; not derived solely from LLM ID or arguments |
| approval_id | Coordinator reserves UUIDv5 from `(action_id, proposal_hash, "approval-v1")` | Deterministic; durable when Action commits; request later created | One immutable proposal, one approval lifecycle; no renewal/rebinding |
| delivery_id | Terminal finalization derives UUIDv5 from `(turn_id, "final-v1")` | Deterministic; durable | One final logical response, reused on send/reconcile attempts |
| correlation_id | Ingress accepts valid bounded trace hint or creates UUID | Not necessarily deterministic; first value durable | Root trace for Turn; duplicate attempt's hint is linked, not substituted |
| processing_token / claim_token | Store claim caller generates UUID for one ownership grant | Random; durable with claim | Never reused for a different attempt; version and owner fenced |
| decision_request_id / operation_id | API client persists UUID for a control command; coordinator derives stable transition operation ID where needed | Stable per command; durable receipt/audit | Same ID + different command bytes conflicts; not an Action idempotency key |

`event_key = SHA256(canonical(["event-v1", deployment_scope, channel, provider_scope, inbound_event_id]))`. The semantic hash binds conversation, authenticated actor, content, and authorized reply destination. It excludes receipt time, transport attempt ID, credentials, and transient provider headers. Event key collision with different semantic hash is a conflict requiring investigation; no second Turn is created.

UUIDv5 provides stable names, not authorization or secrecy. Every ID lookup is scoped and permission checked. Hash matching is supplemented by comparing stored canonical identity/content where needed; no cryptographic hash is trusted as proof of caller privilege.

## Canonicalization and Action keys

Validate strict input schema and apply explicit registry defaults once. Canonical format `aig-json-v1`: UTF-8 JSON; object keys sorted lexicographically by Unicode code point; arrays retain order; compact separators; booleans/null lowercase; strings serialized deterministically with JSON escaping. Reject duplicate JSON keys, nonfinite numbers, floating-point arguments, and unexpected fields. MVP numbers are bounded signed integers with plain decimal representation and zero normalized to `0`. Do not trim or case-fold text implicitly; allowed resource identifiers have their own schema. Future decimal support requires a versioned canonical contract.

`argument_hash = SHA256(canonical_arguments_bytes)`.

`proposal_hash = SHA256(canonical(["proposal-v1", deployment_scope, actor_id, tool_name, tool_version, canonical_arguments, risk_class, side_effect_class, approval_requirement, policy_revision]))`.

`idempotency_key = "aig1-" + SHA256(canonical([deployment_scope, action_id, proposal_hash]))`. Pass it unchanged to the tool adapter on every retry or keyed resolution. Delivery key uses its own namespace and `(deployment_scope, delivery_id, payload_hash, channel, destination)`.

A Turn may have zero, one, or two distinct Actions. Identical repeated side-effect proposals in one planning attempt collapse by `(tool/version, argument_hash, proposal policy)` before ordinals are frozen. Ordinals follow the accepted first-occurrence order. Attempts before a plan commit may produce another plan because no write is permitted yet. After commit the same slot with different arguments is a hard conflict; it cannot replace or extend the plan.

Separate Turns intentionally receive separate Action IDs/keys even for identical arguments. A note submitted in two new events may append twice. Cross-Turn business operation deduplication is not inferred from matching text. A caller retry must reuse its original event key; a future business-operation key requires an explicit contract and ADR.

## Acceptance and duplicate responses

`accept_inbound` atomically checks membership/expected Conversation version and a free slot, inserts InboundEvent and RECEIVED Turn, assigns sequence, reserves the active slot, and appends acceptance audit. There is no durable “event accepted” record without its Turn. This tightens crash A: persistence includes both.

Before checking a busy slot, load an existing event mapping strongly. An exact duplicate returns the original authorized snapshot (200 terminal, 202 nonterminal), increments duplicate audit/counter conditionally, and performs no model call, Action execution, or delivery resend. A different payload under that key returns 409 IDEMPOTENCY_CONFLICT. Unauthorized users cannot probe an existing Turn by guessing its event ID.

A new event during an active same-conversation Turn returns 409 CONCURRENCY_CONFLICT and `accepted=false`, with authorized conflict details/retry guidance. Nothing is inserted for that rejected event. Retrying it after the slot is released can accept it. Accepted order is storage commit order; no promise of provider chronology or buffering of rejected requests.

If the first request died after acceptance, duplicate/status shows that original Turn and offers the explicit resume/recover command. A provider without a trustworthy logical event ID must supply a durable client/adapter key. A payload hash or receipt timestamp alone cannot distinguish a replay from an intentional repeated message; missing key returns 428 and no acceptance.

## StateStore port and atomic groups

Inputs always include trusted scope, operation ID, and relevant expected versions/tokens. Outputs distinguish APPLIED, ALREADY_APPLIED (same binding), CONFLICT, NOT_FOUND, and UNAVAILABLE. A transport timeout is not proof an operation did not commit. On uncertainty, strongly reload operation receipt and affected aggregates before continuing; unresolved storage commit means no external call.

| Operation | Atomic effect and condition |
| --- | --- |
| accept_inbound | Unique event + new Turn + Conversation free-slot/version update + audit; all or none |
| load_turn_snapshot | Consistent accepted Turn, committed plan and linked state; mixed-version snapshots retried or flagged, never used to claim |
| record_duplicate | Existing event/hash binding + duplicate count/version + attempt audit; no new Turn |
| claim_turn | Eligible nonterminal state/version and no current owner → owner/token; increment planning_attempt_count if plan absent; Turn audit |
| reserve_model_request | Current planning owner/token, no committed plan, per-attempt/Turn limits → increment both model counters + stable request-operation audit before actual model I/O; a lost reservation response must be reloaded before invoking |
| recover_turn | Authorized recovery CAS replaces processing token; does not grant a second Action execution claim; all subsequent operations use the new Turn token |
| commit_plan | Current Turn token/version, plan absent, bounded validated snapshot → immutable ToolCalls, PROPOSED Actions, Turn plan/list/version, audit; all or none |
| prepare_action | Current proposal hash/state, valid registry/policy and Turn token → READY, or ApprovalRequest PENDING + Action WAITING_APPROVAL + audit; all or none |
| fail_unexecuted_action | PROPOSED/READY, current Turn token and confirmed pre-I/O policy/schema/capability denial → FAILED + failure evidence + audit; never operates on EXECUTING/UNKNOWN |
| transition_turn_waiting | Current Turn token/version plus authoritative child predicates → waiting state, clear owner, audit |
| decide_approval | PENDING/version and trusted expiry/identity/hash checks → immutable decision + matching Action READY/REJECTED/CANCELLED + decision receipt + audit; all or none |
| claim_action_execution | READY/version/hash, current Turn token, all required approvals usable, earlier Actions SUCCEEDED, current authorization, budget/eligibility → EXECUTING + new attempt + owner/token/deadline/counter + audit |
| record_attempt_outcome | Exact Action claim/attempt and current Turn token → SUCCEEDED, safe READY, or definitive FAILED + attempt result + audit; no extra external call |
| record_action_unknown | Exact active attempt/version plus current recovery/processing token → Action UNKNOWN + fenced token + attempt uncertainty + Turn WAITING_RECONCILIATION/owner clear + audit atomically |
| record_reconciliation | Expected UNKNOWN/version → evidence/count + resolved state or same UNKNOWN + audit; conditional current safety/authorization guards for READY |
| cancel_unexecuted_action | PROPOSED/WAITING_APPROVAL/READY and never claimed currently → CANCELLED + audit; pending approval, if present, gets system CANCELLED in the same group; cannot hide EXECUTING/UNKNOWN |
| finalize_turn | No unresolved/in-progress Actions; expected versions → terminal Turn + immutable summary + Delivery PENDING + Conversation history/slot release + audit |
| claim_delivery_send | PENDING/version/eligibility/budget → SENDING + attempt + owner/token/deadline/counter + audit |
| record_delivery_outcome | Exact send claim → SENT/safe PENDING/definitive FAILED/UNKNOWN + attempt evidence + audit |
| reconcile_delivery | UNKNOWN/version → evidence/count and safe resolved state or same UNKNOWN + audit |
| expire_approval | PENDING and now >= expires_at → system EXPIRED + Action CANCELLED + audit; approved validity is checked without rewriting its decision |

Read commands for pending approvals, active Turns and unresolved deliveries return scoped durable records. Mutable authorization registry changes require their own admin CAS/audit operation. For execution, registry revision guards join the claim transaction so a concurrent revocation cannot be ignored at claim time. Revocation after a claim cannot guarantee cancellation of I/O already in flight; later attempts reauthorize.

Operation receipts/audit IDs prevent duplicate transitions after lost responses. A transaction's backend retry token is not the durable event/Action key. Repeating exact backend request bytes with a transaction token is allowed within that backend's validity; otherwise reload durable receipts and versions.

## Adapter mapping and storage footprint

SQLite: local persistent file, WAL, synchronous=FULL, foreign-key/uniqueness checks, short BEGIN IMMEDIATE transactions, conditional UPDATE predicates and row-count assertions. Never hold a transaction across model/tool/delivery network I/O. Use a finite busy timeout within request budget; rollback the complete domain operation on error. Backups must include a consistent WAL snapshot; copying only a live main database file is not a promised recovery method. Durability still depends on the OS/filesystem honoring flushes. [SQLite synchronous behavior](https://sqlite.org/pragma.html#pragma_synchronous).

DynamoDB: one on-demand gate table; one separate external demo ledger/mailbox table. Gate records use explicit typed keys: scoped EVENT/hash; CONVERSATION/id with META and history refs; TURN/id with META and CALL/ordinal; ACTION/id, APPROVAL/id, and DELIVERY/id each with META; ACTION/id and DELIVERY/id with ATTEMPT/number and RECONCILIATION/command keys; AUDIT/aggregate with version/operation keys; CONTROL/request ID for decision receipts. Turn META lists Action IDs, so direct Action/Approval/Delivery lookup and strong child reads need no secondary index. No GSI drives correctness. Small demo operator lists may use a bounded scoped Scan; it is discovery only, followed by authoritative base-table reads. Larger discovery needs a later index decision.

Use conditional TransactWriteItems for named multi-record operations and conditions on the same Update rather than a separate ConditionCheck against that same item. Read ownership/receipt truth with strongly consistent base-table reads; use transactional reads for atomic multi-record snapshots or versions plus recheck. DynamoDB's transaction API is atomic, forbids two operations against the same item in one transaction, and has a short client-token idempotency window. Our retained domain ledger supplies long-lived uniqueness. [Transaction API](https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_TransactWriteItems.html), [read consistency](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/HowItWorks.ReadConsistency.html).

MVP transaction groups remain below 25 items and 256 KiB through the plan/payload caps. Bound all audit entries before committing; split no invariant-critical group. All backend constraints are validated before acceptance/plan commit. No automatic TTL is enabled on correctness records or provider receipts. Explicit demo teardown can delete a complete namespace only when no active/unresolved operation remains. Claims cover retained storage, not database destruction, backup restoration to an older point, or changed deployment scope.

## Execution ownership and stale workers

Action ownership is nonexpiring. READY→EXECUTING commits one owner and token. Two independent workers race the condition; exactly one may enter the adapter. The loser returns the stored state/conflict and never performs I/O. A deadline is an observation budget, not a lease or a right to steal the Action.

Turn processing uses a separate fence. It prevents simultaneous plan commits and sequencing. An authorized recovery command may replace a stale Turn processing token by CAS; old planners cannot commit/claim Actions afterward. This is safe because pre-plan model/read activity has no write effect. A claim already committed before that fence remains EXECUTING and blocks another write. Recovery records it UNKNOWN; it never simply resets it to READY.

If an old executor sends after the recovery fence, the local attempt result cannot overwrite newer truth. UNKNOWN is retained until provider evidence resolves it. Before UNKNOWN→READY, the operator must establish that the old executor cannot send another request, plus a valid provider recovery contract. Leases/fencing protect local writes but cannot fence an arbitrary external provider. No claim of automatic recovery from an indefinitely paused live process is made.

## Concurrency matrix

| Race | Frozen behavior |
| --- | --- |
| Different conversations | Independent Turn slots/claims; may plan/execute concurrently; SQLite briefly serializes storage writes |
| Different new events, same conversation | One acceptance wins; other 409 with no event/Turn insertion; accepted ordering is commit order |
| Same event duplicate during any state | Same semantic hash → original Turn; no new execution; conflicting hash →409 |
| Two Actions in one Turn | All required approvals first, then action_ordinal order; first unresolved/failure stops later writes |
| Two approval callbacks | Atomic first valid decision wins; identical command retry returns receipt; contradictory second decision409 |
| Two claims for one Action | One EXECUTING winner; loser never calls adapter |
| Approval expires at claim | Transaction checks now < expires_at; otherwise never claim, cancel unexecuted Action and fail Turn |
| Policy revoked at claim | Transaction guards current registry revision; denial blocks write regardless of approval |
| Two conversations mutate same service | External expected_revision guards maintenance updates; note appends have independent keys; no global cross-conversation ordering promise |
| Delivery retry while a new Turn runs | Independent send claim; cannot rerun prior Actions; outbound summaries may arrive out of chronological order and carry Turn sequence |

## Concrete examples

Event `evt-42` in conversation `conv-A` accepts Turn T1. Lost HTTP response and retry with a new inbound_attempt_id still return T1. If T1's Action A1 appended a note but crashed before storing SUCCEEDED, the duplicate exposes UNKNOWN after recovery. A provider receipt for A1's stable key resolves SUCCEEDED without appending again.

Event `evt-43` later contains the same note. It creates T2/A2 with a different key, so the second append is intentional. Reusing `evt-42` with changed note instead returns conflict.

T1 proposes append-note A1 plus maintenance A2. Both are durably committed before execution. A2 approval is pending, so even READY A1 cannot run. After the distinct approver approves, resume claims A1 then A2. If A2's expected_revision fails, A1 remains SUCCEEDED and T1 is FAILED with that partial result.

Workers W1 and W2 load READY A1/version3. W1 claims version4/attempt1. W2's condition fails and it performs zero external calls. W1 disappears; a recovery owner can fence its local completion and mark UNKNOWN. Clock expiry alone never grants W2 permission to repeat A1.

## Downstream recovery profiles

| Capability profile | UNKNOWN behavior |
| --- | --- |
| LOOKUP_AND_KEYED | Lookup receipt first; confirmed effect succeeds. Inconclusive lookup stays UNKNOWN. With old executor quiesced, provider key/argument/retention guarantee may permit the same-key retry within budget |
| LOOKUP_ONLY | Confirmed applied resolves success; replay only after authoritative final no-effect proof and prior-owner quiescence; search absence is insufficient |
| KEYED_ONLY | No invented lookup. Explicit same-key resolution attempt requires known guarantee/retention, quiescence, current permission/approval and budget; keep UNKNOWN until that attempt is allowed |
| NONE | No automatic replay. Operator records verifiable provider proof to resolve applied or final no-effect; absent proof it remains UNKNOWN and blocks the conversation |

A keyed retry can avoid another provider effect only under that provider's contract. The gate does not guarantee exactly-once external actions or whole-turn atomicity.
