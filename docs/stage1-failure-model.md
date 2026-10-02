# AgenticIntentGate — failure and recovery model

**OUR DESIGN DECISION — Stage 1; no failure tests have been implemented/run.** [Stage 0.5 limitation evidence](../research/stage0.5/review-package/docs/stage0.5-local-messaging-reproduction.md#observed-limitations) motivates explicit delivery and uncertainty records. It did not prove multi-worker or crash reliability.

## Failure taxonomy

Every durable transition appends a scoped audit entry containing failure class, before/after state, attempt/claim, correlation chain and evidence reference. Rejected pre-acceptance requests produce sanitized security logs; they do not create a Turn. Storage unavailability may prevent audit writes; it must also prevent execution.

| Failure | Retry / intervention | Durable transition | User-visible behavior / reconciliation |
| --- | --- | --- | --- |
| VALIDATION_ERROR | Correct input; no same invalid request retry | No acceptance, or pre-plan Turn FAILED for bounded planning envelope failure | 400/schema details; no I/O |
| AUTHENTICATION_ERROR | Fresh valid credential; no blind retry | No new acceptance/claim/decision | 401; no sensitive status disclosure |
| AUTHORIZATION_ERROR | Authorized admin review if permission needed | Before acceptance none; stored unclaimed proposal FAILED, Turn FAILED | 403 before acceptance or explicit stored denial; approval cannot bypass |
| MODEL_ERROR | Transient before plan may retry within planning/model limits; permanent no | PROCESSING remains for safe retry, or finalize FAILED when exhausted | 202 retry eligibility or terminal error; no mutations before plan |
| TOOL_VALIDATION_ERROR | Correct new intent; not retry same bad arguments | Before plan error/FAILED; before I/O Action FAILED | Exact sanitized schema issue; invalid output after possible mutation is UNKNOWN instead |
| APPROVAL_REJECTED | No same-proposal reopening | Approval REJECTED, Action REJECTED, siblings cancelled, Turn FAILED | Decision visible, no planned writes if rejected before execution |
| APPROVAL_EXPIRED | New intent/approval lifecycle needed | PENDING→EXPIRED and Action CANCELLED; expired approved-but-unclaimed Action CANCELLED; Turn FAILED | Explain expiry; prior executed success remains recorded |
| TRANSIENT_EXTERNAL_ERROR | Only if confirmed not applied or keyed guarantee; budget applies | EXECUTING→READY with eligible time; exhausted definitive no-effect →FAILED | Pending retry or final error; ambiguous cases are UNKNOWN |
| PERMANENT_EXTERNAL_ERROR | No automatic retry | Action FAILED only with definitive no unresolved effect | Stored provider/revision error; no invented compensation |
| DELIVERY_ERROR | Same payload/key only if confirmed not accepted or safe keyed guarantee | SENDING→PENDING or definitive FAILED; ambiguity→UNKNOWN | Turn/Action outcome unchanged; independent delivery status |
| CONCURRENCY_CONFLICT | Query/retry eligible command with current version | Losing claim/decision performs no update; busy new ingress has no Turn | 409 or stored state; never call adapter after lost claim |
| UNKNOWN_OUTCOME | Read-only reconciliation first; explicit capability/evidence-driven resolution | Action UNKNOWN + Turn WAITING_RECONCILIATION; or Delivery UNKNOWN | Clearly expose uncertainty; no “failed, try again” advice that could duplicate effect |
| IDEMPOTENCY_CONFLICT | Investigate/correct event or control key; no blind retry | No replacement/mutation of original | 409 key/content mismatch; audit conflict |
| STORAGE_ERROR | Reload receipt/strong state; retry exact domain op only when safe | No external I/O without confirmed commit; current state retained if unknown | 503/unavailable or latest snapshot; uncertainty about write commit is not absence |

## Crash windows A–J

The “user sees” column describes an authorized status query after recovery/status inspection; a crashed HTTP request itself may have no response.

| Window | Persisted truth | Safe next action | Retry safety / reconciliation | User sees |
| --- | --- | --- | --- | --- |
| A: inbound persisted, before processing | Atomic acceptance already has event mapping, RECEIVED Turn, conversation slot, audit | claim_turn using original Turn | Model planning safe; never create a second Turn | Accepted RECEIVED; resume available |
| B: PROCESSING persisted, before model call | Turn owner/token, input and planning attempt count; no committed plan | Authorized recover fences old planning token, then replan if budget remains | Model/read-only work may repeat; old worker cannot commit plan/claim after fence | PROCESSING/recovery required |
| C: model returned ToolCall, before Action write | No committed planning snapshot/Action batch; wrappers only proposed writes | Fence/replan under remaining planning budget; discard uncommitted draft | Safe because no side-effect adapter could run; model may produce a different plan | Accepted incomplete planning |
| D: Action written, before approval request | Whole immutable plan and PROPOSED Action(s) committed | prepare_action creates exact ApprovalRequest + WAITING_APPROVAL atomically, or READY | No external mutation yet; deterministic request ID avoids duplicates | PROPOSED or pending preparation |
| E: approval written, before Action executable | Request creation pairs PENDING + WAITING_APPROVAL. Decision commit pairs APPROVED + READY. No committed half-pair exists | Reload request/decision receipt; resume evaluates approvals | Approval call retries use decision_request_id; never infer decision from a log | WAITING_APPROVAL or READY according to committed decision |
| F: READY, before claim | Immutable Action/approval with no execution attempt | Recheck authorization/expiry/all approvals/order; conditional claim | Safe claim race; one winner, loser zero I/O | READY, or cancellation if approval now expired |
| G: EXECUTING, call not sent, crash | Claim/attempt exists; no durable proof of “not sent” distinguishable from H | Fence completion and record UNKNOWN, then reconcile | Do not reset to READY based on claimed timing; quiescence plus provider evidence/capability required | UNKNOWN / WAITING_RECONCILIATION |
| H: external effect succeeded, before local SUCCEEDED | EXECUTING attempt; independent provider may have durable effect/key receipt | Record UNKNOWN; look up same key/reference and resolve confirmed success | No blind replay. If lookup inconclusive, capability-specific same-key recovery requires quiescence and valid guarantee | UNKNOWN until proof, then Action SUCCEEDED |
| I: Action SUCCEEDED, outbound delivery fails | Confirmed Action result; Turn may still need finalization. Finalization atomically creates final Delivery | Finalize if needed; retry/reconcile Delivery independently | Never rerun Action. Send failure may be known no-acceptance or UNKNOWN | Action SUCCEEDED; Turn completed/failed as appropriate; separate delivery status |
| J: duplicate during any recovery state | Same event→same Turn, plan, Actions and audit remain | Authenticate/bind/hash-check and return original snapshot | No model/write/send just because duplicate arrives; explicit resume/recover uses existing IDs | 200 terminal or 202 accepted nonterminal, with uncertainty/approval details |

Additional windows: an acceptance commit with lost response is recovered through event identity; a plan commit with lost response is recovered through Turn plan marker; a claim commit with lost response requires reloading owner/token/attempt before any I/O; a result commit with lost response requires authoritative reread rather than re-invoking the provider. Delivery SENDING with lost sender becomes UNKNOWN. Audit cannot be committed separately from state. A corrupt/unavailable database disables claims and exposes unavailable status; caches are never fallback truth.

## UNKNOWN and evidence

Timeout, broken socket, process termination, malformed adapter result after send, and lost commit acknowledgement can mean the external operation applied. Cancellation of an async task does not prove its worker thread or provider stopped. `UNKNOWN` represents that fact without claiming success or no-effect failure.

Read-only reconciliation yields CONFIRMED_APPLIED, CONFIRMED_NOT_APPLIED_FINAL, or INCONCLUSIVE. A confirmed receipt must bind the exact deployment scope/key/proposal hash and external resource. A provider search with eventual consistency, a missing local result, or an operator guess is INCONCLUSIVE. The demo provider writes effect and keyed receipt together, so the receipt is authoritative positive proof. Missing receipt alone cannot rule out an old in-flight sender.

| Resolution | Required guards | Result |
| --- | --- | --- |
| Confirmed applied | Exact provider receipt/proof; expected UNKNOWN version | SUCCEEDED, no new mutation |
| Same-key recovery | Key guarantee and retention still valid; old executor quiesced; current authorization/approval; budget | UNKNOWN→READY then new claimed attempt with identical key/arguments |
| Retry without provider key | Authoritative final no-effect proof and old executor quiesced, current permission/approval/budget | UNKNOWN→READY; otherwise forbidden |
| Confirmed terminal no-effect failure | Provider finality proof, no outstanding sender; no permitted retry or permanent error | FAILED; Turn may finalize with partial results |
| No proof/capability | No valid resolution evidence | Remain UNKNOWN; human investigation; no mutation/slot release |

Non-reconcilable tools may remain blocked indefinitely. Manual resolution records verifiable external evidence and an authorized resolver; it is not a button to dismiss uncertainty. Compensation is a new side effect needing a separate proposal/authorization/approval; it is outside MVP.

## Retry policy frozen

There is no generic retry decorator around all failures. Every next attempt is charged before I/O and checked against a persisted eligible time and the current command deadline. Durable retry time does not imply an unattended scheduler.

| Kind | Default budget / delays | Conditions |
| --- | --- | --- |
| Model planning | Max 3 planning attempts/Turn; max 4 model requests per attempt; total cap 12 | Transient errors before commit only. Configure framework/provider transport retry so combined actual requests stay within these caps. Disable hidden nested retries in contract tests; if a provider cannot expose/count them, it cannot satisfy the capped lane until adapted |
| Read-only tool | Max 3 calls/attempt including retries, 5s/call; retry after 1s then 2s | Authorized reads only; schema/auth errors not retried; stale read cannot bypass provider revision check |
| Side-effect Action | Max 3 externally submitted attempts including keyed recovery, 10s/call; safe retry eligible after 1s then 2s | Only confirmed no-effect transient failure or capability-safe keyed recovery; same Action/key/args, fresh checks |
| Delivery | Max 3 submitted attempts, 5s/call; after 1s then 2s | Same immutable payload/key; known no-acceptance or safe keyed resolution; no blind resend UNKNOWN |
| Reconciliation lookup | Max 3 lookups per explicit command, 5s each; next lookup after 1s then 2s | Read-only; provider temporary failure leaves UNKNOWN. Later operator commands may retry lookup with a new command receipt; no effect retries granted |
| Storage operation | Within remaining request budget, exact conditional operation | Reload outcome/receipt after timeout; same operation binding; conflicts not interpreted as permission to execute |

If remaining command budget cannot accommodate call timeout plus a 2-second persistence reserve, return the stored 202 snapshot before starting it. A gateway validates the actual adapter deadline and capabilities. A budget-expired model draft is discarded unless its complete plan commit already succeeded. A budget-expired claimed write may still apply and becomes UNKNOWN, not FAILED.

Retries after definitive Action/Delivery FAILED are not permitted under the same ID. A new user intent can request a new operation, but UNKNOWN must be resolved before another same-conversation Turn is accepted. Reconciliation may prove success even after approval expires because recording truth is not granting a new write; expiry prohibits a new execution attempt.

## Fail-closed conditions

No external mutation if: input/proposal hash mismatch; unknown tool/version or capability downgrade; requester denied; approval absent/rejected/expired or wrong proposal/approver; claim not confirmed; stale Turn/Action token; earlier Action unresolved; unavailable store/audit transaction; untrustworthy clock for expiry; exhausted budget; insufficient time; unknown external outcome without safe resolution evidence.

If result persistence fails after an external effect, stop further Actions and retain/derive UNKNOWN on recovery. If a permanent registry change prevents reusing a keyed adapter, leave unresolved uncertainty for evidence-based resolution. Failure responses identify the original Turn/Action when the caller is authorized; they never encourage creating a new event to bypass uncertainty.

## Later failure evidence requirements

Stage 2 must terminate a subprocess, not merely raise a caught exception, at each relevant persisted/I/O barrier. The external fixture survives separately and records effect counts and receipts. Race tests use independent connections/processes. A paused-old-worker test must demonstrate no automatic takeover and rejected stale local completions. Non-keyed/inconclusive fixtures must remain UNKNOWN. These future requirements are in the [implementation plan](stage1-implementation-plan.md); no production reliability claim follows from Stage 1.
