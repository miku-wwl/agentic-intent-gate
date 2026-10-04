# Day 2 — persistent Run and local approval contract freeze

**ARCHITECTURE / CONTRACTS ONLY**, 2026-10-04, Pacific/Auckland. The application still implements Day 1. No SQLite repository, Run endpoint, recovery runtime or approval UI exists yet. [ADR-012](adr/ADR-012-persistent-runs-local-approval.md) defines precedence; the [audit](day2-repository-audit.md) records the baseline and the [implementation plan](day2-implementation-plan.md) defines Prompt 2.

## Boundary and ownership

```text
validated Intent -> persisted Run -> one Day-1 PlanningService call
  -> independently validated Plan -> persist PlanValidated
  -> deterministic risk policy -> Ready or AwaitingApproval
  -> local APPROVE / REJECT -> Approved / Rejected
```

There is no tool I/O, including reads. A Run represents exactly one accepted Intent and one planning attempt. It is not a Stage 1 Turn, Action, authenticated approval or execution claim. Repeating creation makes a new Run; there is no Idempotency-Key or conversation admission system. A lost HTTP response does not undo acceptance or make repeated creation safe to deduplicate.

Use the existing vendor-neutral PlanningService, parser, schemas, trusted catalog and synthetic `service-demo: 1` context. Persist only the resulting validated Plan, never the raw model envelope/text, system prompt, provider exception or stack trace. The model cannot supply Run IDs, versions, timestamps, actor identity or approval policy.

## Run and public representation

The public object is [day2-run.schema.json](../schemas/day2-run.schema.json), referencing the unchanged Day 1 Intent and Plan schemas through an explicit local resource registry. Schema resolution must perform no network I/O.

| Public field | Exact meaning / ownership |
| --- | --- |
| run_id | Server-generated UUIDv4, canonical lowercase 36-character string; immutable. No client/model override |
| intent | `{ "text": string }`, exactly the accepted Day 1 Intent; immutable, without trimming or normalization |
| state | One of the eight RunState strings below |
| plan | `null` until acceptance of a validated Plan, or the unchanged Day 1 Plan object; immutable once stored |
| risk_class | `null` without a Plan; otherwise the independently computed trusted aggregate risk, equal to plan.risk_class |
| version | Integer 1…9007199254740991; starts at 1 and increases once per committed state mutation. Actual Day 2 paths use versions 1…5 |
| approval_required | Derived, not a stored column: `null` without a Plan, false for READ_ONLY, true for either write risk. Remains true after Approved or Rejected; it describes the policy requirement, not a pending action |
| created_at | Immutable server UTC timestamp, `YYYY-MM-DDTHH:MM:SS.ffffffZ` |
| updated_at | Same timestamp format; each mutation uses max(current server UTC, previous updated_at), so it cannot move backward. Version, not time, orders changes |
| error | `null` except in PlanRejected; otherwise exactly `{code, message}` using the fixed planning/recovery messages below |

Unknown approval need is `null`, not false. A human Rejected Run retains its validated Plan/risk and has `error: null`; it is distinct from PlanRejected, which has no Plan/risk. Neither Ready nor Approved means execution. Reason/actor/decision records and audit payloads are internal, not additional public Run fields. The later detail page can show current state without an audit endpoint.

Canonical Plan storage is `json.dumps(plan.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)` encoded as UTF-8, at most 16 KiB. On both write and load, apply the Day 1 strict parser/schema/semantic validator and compare the risk column to the computed Plan risk. Validate Intent, state/field/version combinations, real calendar timestamps, canonical bytes and decision/audit consistency too. Persisted JSON is not a shortcut around validation. Bad stored data fails closed; never repair it or return a trusted Plan from it.

JSON Schema's `format` keyword alone does not guarantee calendar validation, and the locked environment has no optional date-time checker. Independently parse the fixed UTC format with stdlib datetime, reject impossible dates and compare created/updated ordering; no new dependency is required.

## RunState and all allowed transitions

Reuse Day 1's CamelCase wire spelling for shared states. `PlanGenerated` remains a transient internal Day 1 observation: unvalidated output is never a persistent Run state.

| Persistent state | Meaning | Normal version | Terminal |
| --- | --- | --- | --- |
| IntentReceived | Valid Intent and RUN_CREATED committed | 1 | No |
| PlanGenerating | Generation-start barrier committed; at most one provider attempt may follow | 2 | No |
| PlanValidated | Immutable validated Plan/risk committed; policy routing not yet committed | 3 | No |
| Ready | READ_ONLY cleared the deterministic policy gate | 4 | Yes |
| AwaitingApproval | Either write risk requires a local decision | 4 | No |
| Approved | One local APPROVE committed; no execution | 5 | Yes |
| Rejected | One local REJECT committed; validated proposal preserved | 5 | Yes |
| PlanRejected | Planning failed or startup found interrupted planning; no Plan | 2 or 3 | Yes |

The following eight edges are exhaustive. Creation is the separate initial insert at version 1, not an update from a hidden state.

| From | To | Required guard / event |
| --- | --- | --- |
| IntentReceived | PlanGenerating | Matching version, sufficient remaining planning budget; PLAN_GENERATION_STARTED |
| IntentReceived | PlanRejected | Accepted work cannot start, or startup interruption; PLAN_REJECTED or RECOVERY_REJECTED |
| PlanGenerating | PlanValidated | Matching version; the one generation result passes all Day 1 checks; PLAN_VALIDATED |
| PlanGenerating | PlanRejected | Planning/parser/schema/semantic/provider failure, cancellation or startup interruption; PLAN_REJECTED or RECOVERY_REJECTED |
| PlanValidated | Ready | Trusted risk READ_ONLY; RUN_READY or RECOVERY_ROUTED |
| PlanValidated | AwaitingApproval | Trusted risk SIDE_EFFECT or APPROVAL_REQUIRED; APPROVAL_REQUIRED or RECOVERY_ROUTED |
| AwaitingApproval | Approved | Valid APPROVE, matching expected_version; APPROVED |
| AwaitingApproval | Rejected | Valid REJECT, matching expected_version; REJECTED |

Every other edge, including self-transitions, is forbidden. In particular Approved→Rejected, Rejected→Approved, PlanRejected→Approved, Ready→Approved, and either decision back to AwaitingApproval are forbidden. All four terminal states have no outgoing edge. No Plan edit, Intent edit, in-place retry, terminal resurrection or metadata-only version update exists. A new planning attempt creates a new Run.

## Deterministic approval and decision

One trusted `requires_approval(Risk) -> bool` policy owns this mapping; reject unknown values instead of defaulting:

| Validated trusted risk | Resulting state | requires_approval |
| --- | --- | --- |
| READ_ONLY | Ready | false |
| SIDE_EFFECT | AwaitingApproval | true |
| APPROVAL_REQUIRED | AwaitingApproval | true |

Aggregate risk is still computed from the Day 1 trusted tools and must exactly match the model claim. Extra `approval_required`, `approved`, actor, endpoint or execution fields in model output remain invalid. There is no model-based policy evaluation.

The HTTP `ApprovalDecisionRequest` is [day2-approval.schema.json](../schemas/day2-approval.schema.json): required `decision` = APPROVE or REJECT and `expected_version` = positive integer ≤9007199254740991; optional `reason` = plain text ≤1024 characters / 4096 UTF-8 bytes. Omitted reason is stored as null; an empty supplied string is retained; explicit JSON null is invalid. Reject control characters except tab, CR and LF, invalid Unicode, duplicate keys, floating-point integer spellings and additional fields. Apply strict decoding before JSON Schema; a schema's integer type alone does not reject JSON `4.0`.

Only AwaitingApproval can accept a decision. Server-generated immutable `ApprovalDecision` records contain decision_id (UUIDv4), run_id, decision, reason, actor_type = **LOCAL_USER**, created_at, run_version_before and run_version_after. No caller actor field is accepted. LOCAL_USER establishes no authentication, ownership, distinct approver or authorization. There is no expiry/renewal workflow in Day 2. Approved records a local human gate for possible future execution admission; that later stage still needs its independently authorized execution/security contracts.

## Exact SQLite schema v1

Use Python's stdlib sqlite3; require SQLite ≥3.38.0 with JSON functions and STRICT tables available. Current checked interpreter is Python 3.13.9 / SQLite 3.51.0. STRICT constrains stored types, but API parsing must still reject coercion. [STRICT reference](https://sqlite.org/stricttables.html), [JSON support reference](https://sqlite.org/json1.html#compiling_in_json_support).

This is prospective DDL, not a runtime migration or an implemented repository. Initialize all three tables and `PRAGMA user_version = 1` in one transaction. No extra metadata table or ORM is selected.

```sql
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY NOT NULL CHECK(length(run_id) = 36),
    intent TEXT NOT NULL CHECK(length(intent) BETWEEN 1 AND 8192
        AND length(CAST(intent AS BLOB)) BETWEEN 1 AND 8192),
    state TEXT NOT NULL CHECK(state IN (
        'IntentReceived', 'PlanGenerating', 'PlanValidated', 'Ready',
        'AwaitingApproval', 'Approved', 'Rejected', 'PlanRejected')),
    validated_plan_json TEXT,
    risk_class TEXT CHECK(risk_class IS NULL OR risk_class IN (
        'READ_ONLY', 'SIDE_EFFECT', 'APPROVAL_REQUIRED')),
    version INTEGER NOT NULL CHECK(version BETWEEN 1 AND 9007199254740991),
    created_at TEXT NOT NULL CHECK(length(created_at) = 27),
    updated_at TEXT NOT NULL CHECK(length(updated_at) = 27 AND updated_at >= created_at),
    last_error_code TEXT,
    last_error_message TEXT CHECK(last_error_message IS NULL
        OR length(last_error_message) BETWEEN 1 AND 256),
    CHECK(validated_plan_json IS NULL OR CASE WHEN json_valid(validated_plan_json)
        THEN json_type(validated_plan_json) = 'object'
            AND length(CAST(validated_plan_json AS BLOB)) <= 16384
            AND json_extract(validated_plan_json, '$.risk_class') IS risk_class
        ELSE 0 END),
    CHECK(
        (state IN ('IntentReceived', 'PlanGenerating')
            AND validated_plan_json IS NULL AND risk_class IS NULL
            AND last_error_code IS NULL AND last_error_message IS NULL)
        OR (state = 'PlanRejected' AND validated_plan_json IS NULL AND risk_class IS NULL
            AND last_error_code IS NOT NULL AND last_error_code IN (
                'PLAN_VALIDATION_ERROR', 'MODEL_UNAVAILABLE', 'MODEL_TIMEOUT',
                'MODEL_RESPONSE_ERROR', 'INTERNAL_ERROR', 'RECOVERY_INTERRUPTED')
            AND last_error_message IS NOT NULL)
        OR (state IN ('PlanValidated', 'Ready', 'AwaitingApproval', 'Approved', 'Rejected')
            AND validated_plan_json IS NOT NULL AND risk_class IS NOT NULL
            AND last_error_code IS NULL AND last_error_message IS NULL)),
    CHECK(state <> 'Ready' OR risk_class = 'READ_ONLY'),
    CHECK(state NOT IN ('AwaitingApproval', 'Approved', 'Rejected')
        OR risk_class IN ('SIDE_EFFECT', 'APPROVAL_REQUIRED'))
) STRICT;

CREATE TABLE approval_decisions (
    decision_id TEXT PRIMARY KEY NOT NULL CHECK(length(decision_id) = 36),
    run_id TEXT NOT NULL UNIQUE REFERENCES runs(run_id),
    decision TEXT NOT NULL CHECK(decision IN ('APPROVE', 'REJECT')),
    reason TEXT CHECK(reason IS NULL OR (length(reason) <= 1024
        AND length(CAST(reason AS BLOB)) <= 4096)),
    actor_type TEXT NOT NULL CHECK(actor_type = 'LOCAL_USER'),
    created_at TEXT NOT NULL CHECK(length(created_at) = 27),
    run_version_before INTEGER NOT NULL CHECK(run_version_before BETWEEN 1 AND 9007199254740990),
    run_version_after INTEGER NOT NULL CHECK(run_version_after = run_version_before + 1)
) STRICT;

CREATE TABLE audit_events (
    event_id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    run_version INTEGER NOT NULL CHECK(run_version BETWEEN 1 AND 9007199254740991),
    event_type TEXT NOT NULL CHECK(event_type IN (
        'RUN_CREATED', 'PLAN_GENERATION_STARTED', 'PLAN_VALIDATED', 'PLAN_REJECTED',
        'RUN_READY', 'APPROVAL_REQUIRED', 'APPROVED', 'REJECTED',
        'RECOVERY_REJECTED', 'RECOVERY_ROUTED')),
    created_at TEXT NOT NULL CHECK(length(created_at) = 27),
    safe_payload_json TEXT NOT NULL CHECK(CASE WHEN json_valid(safe_payload_json)
        THEN json_type(safe_payload_json) = 'object'
            AND length(CAST(safe_payload_json AS BLOB)) <= 1024
            AND json_extract(safe_payload_json, '$.version_after') IS run_version
        ELSE 0 END),
    UNIQUE(run_id, run_version)
) STRICT;
```

`runs.intent` stores the accepted text; API intent wraps it as `{text}`. Run risk is deliberately duplicated for queries and checked against its Plan; approval_required is not stored. `UNIQUE(approval_decisions.run_id)` permits at most one decision per Run. `audit_events.run_version` makes one event per state mutation enforceable; event_id is a server/SQLite-assigned global sequence with no caller input. No deletion/cascade, audit UPDATE, decision UPDATE or replacement operation is exposed by the application. Append-only is an application/repository guarantee, not protection from a database administrator.

Every audit payload has exactly `from_state` (null only at creation), `to_state`, `version_before` (0 only at creation), `version_after`, `risk_class`, `error_code`, `decision_id` (null except the human decision). Values come from trusted transitions; no Intent, Plan, reason, prompt, model content, credentials or exception text goes into the audit payload. Serialize it with the same deterministic JSON rules, at most 1 KiB. Creation writes one RUN_CREATED event at version 1; each following committed mutation writes exactly one event at its new version. Recovery routing uses only RECOVERY_ROUTED, not an additional RUN_READY/APPROVAL_REQUIRED event.

## Database path, safety settings and version checks

Prospective server configuration `AIG_DB_PATH` defaults to `.runtime/day2-runs.sqlite3`, relative to process startup cwd. Resolve it once to an absolute ordinary local-disk file path; no request override, `:memory:`, connection-URI option or network-share deployment. Its parent may be created if absent. The DB, `-wal` and `-shm` files are already covered by `.gitignore`'s `.runtime/` rule. An externally configured path needs equivalent private local storage; no machine-specific absolute path is committed.

| Selection | Reason / enforcement |
| --- | --- |
| foreign_keys = ON on every connection | Prevent orphan decisions/events; set before BEGIN and verify 1 |
| busy_timeout = 250 ms on every connection | Bound ordinary lock waiting; lock exhaustion becomes DATABASE_UNAVAILABLE, with no application transaction retry |
| journal_mode = WAL | Permit local reads during short writes; verify the returned mode before serving; no network filesystem |
| synchronous = FULL on every connection | Retain the selected local durability boundary; verify value 2 |
| BEGIN IMMEDIATE for writes | Obtain the write transaction before checking/mutating a Run; no deferred read-to-write upgrade |
| Explicit BEGIN for multi-query reads | Read Run, decision and audit consistency from one snapshot |

These are deliberate local choices based on the [SQLite PRAGMA reference](https://sqlite.org/pragma.html) and [transaction reference](https://sqlite.org/lang_transaction.html). Storage/OS faults and hardware durability remain outside a hard latency or production guarantee. Keep WAL/SHM with an active database; copying just the live main file is not a backup guarantee.

Repository operations are short synchronous calls using a connection owned by that call, explicit transaction control (`isolation_level=None`), parameterized SQL, rollback/close on every error and no await inside a transaction. Never hold a transaction during model/network I/O or human waiting. This small local choice can briefly block the application event loop; throughput/production latency is not claimed. No worker, scheduler, connection shared across unrelated requests or distributed lock is selected.

Startup, before HTTP serving:

1. Check SQLite feature support. For an existing path open without create (`mode=rw` internally); inspect version/structure before changing persistent pragmas or records.
2. Absent file, or version 0 with no application objects: create only the above schema and set user_version=1 atomically. Failure rolls back initialization. Never use CREATE IF NOT EXISTS to conceal a partial schema.
3. Version 1: require exactly the three defined tables and their generated constraint indexes, matching columns/types/constraints/FKs/STRICT options. Compare sqlite_schema DDL to the frozen constants after collapsing whitespace and removing a trailing semicolon; reject extra tables/views/triggers/indexes or altered definitions.
4. Any other version, nonempty unversioned/partial schema, invalid file or unsupported required feature: fail startup; no drop, reset, repair, migration guessing or fallback to memory. Schema/version faults use DATABASE_SCHEMA_UNSUPPORTED; unreadable/corrupt data or failed integrity/contract checks use DATABASE_UNAVAILABLE.
5. Verify integrity_check and foreign_key_check, validate all persisted rows/Plans and linked audit/decision records, then apply the recovery rules. Do not serve any route if validation or recovery fails. Later migrations require explicit versioned code and acceptance evidence.

Exactly one application process / uvicorn worker may use a given Day 2 DB. The prior process must be stopped before startup recovery. SQLite CAS supports concurrent requests/connections; it does not prove distributed ownership or make recovery safe while another app is still generating a Plan.

## Atomic operations and conflict rules

Named repository operations, not a general public transition setter, own the following transaction groups:

| Operation | Atomic write / guard |
| --- | --- |
| create | New server Run ID + immutable Intent, IntentReceived/version 1, identical created/updated timestamp + RUN_CREATED |
| start_generation | CAS IntentReceived/current version -> PlanGenerating/version+1 + PLAN_GENERATION_STARTED, before model I/O |
| save_validated_plan | CAS PlanGenerating/current version -> PlanValidated/version+1, write immutable canonical Plan/computed risk + PLAN_VALIDATED |
| reject_planning | CAS IntentReceived or PlanGenerating/current version -> PlanRejected/version+1, fixed error, no Plan + PLAN_REJECTED |
| route_validated_plan | CAS PlanValidated/current version -> trusted policy result/version+1 + exactly one routing event |
| decide_approval | Validate snapshot, CAS AwaitingApproval/expected version, one immutable decision + terminal state/version+1 + one decision audit event |
| recover | One conditional mutation for an eligible Run + exactly one recovery event; no model call |

The internal read-only `recovery_candidates()` operation returns validated snapshots in run_id order for IntentReceived, PlanGenerating and PlanValidated, after startup has validated all stored Runs/history. It creates no list HTTP endpoint and performs no mutation; the recovery application uses only the named guarded write operations.

A CAS losing operation changes no row and appends no event/decision. Check Run existence first, then version, then state: unknown -> RUN_NOT_FOUND; stale -> STALE_RUN_VERSION even if the Run is already terminal; matching version/wrong state -> INVALID_RUN_TRANSITION. A repeated successful approval with the old version returns 409 STALE_RUN_VERSION, never success or a second decision. A terminal decision submitted with the current version returns 409 INVALID_RUN_TRANSITION. An APPROVE/REJECT race has one winner; a lock that exceeds busy_timeout may instead produce 503, with no claim that a decision happened. No Idempotency-Key system is inferred from CAS.

For approval, in a single BEGIN IMMEDIATE transaction: load and revalidate the Run; check the above guards; execute UPDATE with `WHERE run_id=? AND state='AwaitingApproval' AND version=?`, require rowcount=1; insert the server decision (UNIQUE run_id); append its audit event; COMMIT. The returned snapshot is published only after confirmed commit. Failure at any step rolls back all writes. Never INSERT OR REPLACE/UPSERT a Run or decision. A crash cannot commit an Approved/Rejected state without its matching decision and audit, or a decision without that state.

Audit versions must be contiguous 1…Run.version, with a legal transition chain ending in the current state. Approved/Rejected must have exactly one matching decision at versions 4→5, correct decision/state, same mutation timestamp and matching last audit decision_id. All other states have no decision. Reads/startup validate these cross-table invariants; DDL alone is not their complete enforcement.

Validate every audit payload's exact seven keys and its event-type/edge pairing against the frozen transition table. Each before/after version must be adjacent, and payload state/risk/error/decision values must agree with that transition. The creation timestamp equals Run.created_at; event timestamps never decrease and the last equals Run.updated_at. The first event is RUN_CREATED from null/v0 to IntentReceived/v1. No extra orphan event/decision is accepted at startup.

If storage/commit outcome cannot be confirmed, return DATABASE_UNAVAILABLE without an invented success or PlanRejected snapshot. Include the allocated/known run_id in the error envelope; the caller may GET it to learn whether a commit survived. Do not replay the write or model automatically. Creation has no exactly-once acceptance promise after a lost response.

## Startup recovery and cancellation

After successful schema/data validation, process eligible Runs in run_id order, one transaction per Run, before serving. A crash halfway through recovery leaves committed changes intact; the next startup processes only remaining eligible states. No polling recovery or background task exists.

| Persisted state | Restart operation | Version / event |
| --- | --- | --- |
| IntentReceived | PlanRejected, plan/risk null, RECOVERY_INTERRUPTED | +1 / RECOVERY_REJECTED |
| PlanGenerating | Same safe interrupted rejection; late previous results cannot pass CAS | +1 / RECOVERY_REJECTED |
| PlanValidated | Revalidate stored Plan, run only deterministic risk policy -> Ready or AwaitingApproval | +1 / RECOVERY_ROUTED |
| Ready, AwaitingApproval, Approved, Rejected, PlanRejected | Leave every field, version and history unchanged | No event |

RECOVERY_INTERRUPTED message is exactly `Planning was interrupted before a validated Plan was stored.` A received/finished raw model response without a committed Plan still counts as interrupted. A committed valid Plan survives even if its original HTTP response was lost. Startup never invokes phi-4-mini, retries planning, reconstructs raw output or executes a tool.

The creation request owns its one model task. On observed cancellation, stop that task; if still IntentReceived/PlanGenerating, attempt one conditional rejection with INTERNAL_ERROR and no Plan. If cancellation or a DB failure prevents recording that rejection, the existing intermediate state remains visible and is recovered at the next startup. If PlanValidated already committed, preserve it; complete policy routing only while the request is still active, otherwise startup routes it. No detached generation continues to make the request appear successful. Timeout does not prove the external inference engine stopped.

## HTTP contracts and fixed failures

The existing 30-second read/planning deadline starts at route entry. Before acceptance, use Day 1 media type, 16 KiB body, 8 KiB Intent UTF-8 and strict parsing checks. Pass the same deadline to PlanningService; the model remains ≤28 seconds and the existing two-second reserve applies. If an accepted Intent lacks the reserve to start generation, persist PlanRejected/MODEL_TIMEOUT directly. Final short persistence operations must complete or report storage failure; no guarantee is made that faulty disk I/O can be preempted at precisely 30 seconds.

Success bodies are the Run object directly. For the defined failure outcomes the Day 2 envelope is exactly `{ "run_id": <known/allocated UUID or null>, "error": { "code": <code>, "message": <fixed message> } }`. A run_id in an operational error is a lookup handle, not proof of acceptance. Day 2 omits Day 1 issue details from both persisted Run errors and its operational envelope; the legacy endpoint retains its full unchanged contract.

| Endpoint | Exact outcome |
| --- | --- |
| POST /api/runs | Request body exactly the Day 1 `{text}` Intent. Validate before UUID allocation/creation. After committed acceptance, synchronously generate, persist result and route policy; **201 Run** for Ready, AwaitingApproval or persisted PlanRejected. Location: `/api/runs/{run_id}`. A normal model timeout still returns 201 with PlanRejected/error, not a transport 504 hiding that Run |
| GET /api/runs/{run_id} | **200 Run** for any valid persisted state, including intermediates or failed Runs. Read-only: no model, recovery, audit append or policy update |
| POST /api/runs/{run_id}/approval | Strict ApprovalDecisionRequest; **200 terminal updated Run** on the one committed decision. No model or tool call |

No query parameter controls any of these endpoints; extra query keys are ignored and cannot override provider, identity, policy or state. Malformed/noncanonical UUID paths and unknown canonical Run IDs return 404 RUN_NOT_FOUND. For approval, path syntax precedes media/body validation, which precedes lookup, version then state checks. A valid-path malformed body is 400 even if that UUID is unknown. Server/model IDs are never selected from the body.

| Failure code | HTTP / exact fixed message |
| --- | --- |
| INVALID_INTENT | 400; existing Day 1 message; no acceptance |
| INVALID_APPROVAL | 400; `The request does not match the approval contract.` |
| REQUEST_TOO_LARGE / UNSUPPORTED_MEDIA_TYPE | 413 / 415; existing Day 1 messages; approval body also has the 16 KiB cap and UTF-8 media rule |
| RUN_NOT_FOUND | 404; `The Run was not found.` |
| STALE_RUN_VERSION | 409; `The Run version does not match expected_version.` |
| INVALID_RUN_TRANSITION | 409 for state conflicts; `The Run state does not allow this operation.` |
| DATABASE_UNAVAILABLE | 503; `The Run store is unavailable.` No SQLite path, SQL or exception text |
| DATABASE_SCHEMA_UNSUPPORTED | Startup-only; `The Run database schema is not supported.` Exit before serving |
| RECOVERY_INTERRUPTED | Run.error only, not an HTTP status; exact recovery message above |
| PLAN_VALIDATION_ERROR / MODEL_UNAVAILABLE / MODEL_TIMEOUT / MODEL_RESPONSE_ERROR / INTERNAL_ERROR | Existing fixed Day 1 messages when persisted in PlanRejected; creation returns 201. An unexpected fault with no confirmed persisted outcome uses HTTP 500 INTERNAL_ERROR plus known run_id |

There is no list, audit-events, update/delete, retry, approval-reversal or execution endpoint. Reject unknown body fields including actor_type/actor_id, run_id, plan, risk_class, approval_required, endpoint and execute. The proposed schemas are not additional model-prompt schemas.

## Compatibility and security scope

POST /api/plans retains Day 1 request/response/status/timeout semantics. It creates no Run, approval or execution authority, even if it returns the same Plan. GET / and its current form remain unchanged in this prompt and Prompt 2; a later Day 2 UI slice will explicitly move product submissions to /api/runs and use GET/expected_version for decisions. Future execution must start from persisted Run state plus its independently reviewed security/execution gates, never a submitted preview.

Prompt 2 may extend the trusted factory as `create_app(planning_service, *, run_service=None)`: the default preserves the existing two-route Day 1 composition/tests; supplying RunService adds the three Run routes. The production main composition must always supply the SQLite-backed RunService after validated startup/recovery. No environment flag, public request field, mock substitution or memory store may bypass the production Run gate.

This design protects against model-invented policy/risk/tool/target, invalid Plan persistence, request-level endpoint override, stale/duplicate decision races, partial application writes and hidden generation retry on restart. It does not establish authentication, authorization, multi-user ownership, a distinct approver, safe remote access, distributed ownership, production DB HA or tool execution safety. Loopback and ordinary local OS/database trust remain assumptions. Local web/browser threat protections beyond the existing boundary require a separate accepted security slice; do not call LOCAL_USER authenticated.

Accepted Intent, Plan arguments and decision reason are persisted user data and can be sensitive; contract validation is not a secret-content filter. Never put application/provider credentials or diagnostic raw output into any field, audit payload or log, and do not claim arbitrary user prose has been scrubbed. No encrypted secret store, administrator-proof ledger, backup/retention policy or production guarantee is provided. CPU model reliability remains WEAK; a failed generation is now intended to survive as a failed Run, not become more reliable.

## Twelve persistence invariants

1. Run ID is server-generated and immutable.
2. Intent is immutable after Run creation.
3. A validated Plan is immutable after acceptance.
4. Only trusted deterministic risk policy decides approval need.
5. Only AwaitingApproval can transition to Approved or Rejected.
6. Each successful state mutation increments version exactly once; GET/conflicts change nothing.
7. Decision + state/version update + corresponding audit event commit atomically.
8. Duplicate/stale approval creates no second decision or audit mutation.
9. Startup never silently retries model generation.
10. Approved does not execute tools; no Day 2 state invokes tools.
11. Raw model output is not stored as trusted state; loading a Plan repeats deterministic validation.
12. No terminal rejected Run is resurrected; all four terminal states remain terminal.

These are frozen requirements, not implemented persistence/recovery guarantees. Prompt 2 must supply the file-backed behavioral and process-restart evidence listed in the implementation plan.
