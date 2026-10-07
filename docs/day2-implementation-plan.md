# Day 2 Prompt 2 — persistence and local approval implementation plan

**PLAN ONLY**, 2026-10-04, Pacific/Auckland. Implement only after the next explicit backend prompt. [State architecture](day2-state-architecture-freeze.md), [ADR-012](adr/ADR-012-persistent-runs-local-approval.md), [Run schema](../schemas/day2-run.schema.json) and [decision schema](../schemas/day2-approval.schema.json) are normative. Paths below are prospective; no Day 2 backend/runtime exists now.

**Historical planning snapshot:** those prospective statements describe 2026-10-04. The backend, local approval and recovery were subsequently implemented in Prompt 2, the runtime migrated in Prompt 2.5, the UI completed in Prompt 3, and hardening verified in Prompt 4. See the [final freeze](day2-final-freeze.md) for current status. Preserve the original implementation order below as history; it is not an outstanding work list.

## Small implementation boundary

Reuse Python 3.13, FastAPI/httpx/jsonschema, Day 1 immutable Intent/Plan/Risk, strict parser, trusted catalog, PlanningService and LocalOpenAIProvider. sqlite3 is standard library; local schema references use the already installed jsonschema/referencing dependency chain, not remote retrieval. No ORM, separate service, new framework, asynchronous worker or frontend change is needed. Existing model prompt, 28/30-second budgets, strict validation and /api/plans behavior remain intact.

Aim for one focused backend coding block (roughly 4–6 hours, depending on crash-test setup). Complete the acceptance gate instead of adding optional endpoints/UI. Each item depends on the previous contracts, not on new architectural decisions.

## Dependency order and exact prospective files

| Order | Paths / responsibilities | Required acceptance |
| --- | --- | --- |
| 1 | `domain/run.py`: RunState, immutable Run snapshot, fixed Run errors, state/field/version/time rules. `domain/approval.py`: ApprovalChoice, strict ApprovalDecisionRequest, immutable local ApprovalDecision and the one requires_approval policy | All eight states/edges; terminal denial; state versions; approved != executed; request integers are strict; local marker is never caller supplied |
| 2 | `ports/run_repository.py`: synchronous named transaction Protocol; `adapters/sqlite_schema.py`: frozen v1 DDL, settings, schema compatibility/integrity checks; `adapters/sqlite_run_repository.py`: per-call connections, canonical codecs and guarded transactions | All three tables and constraints, no hidden ORM/memory fallback, no unversioned/partial schema repair; rollback and canonical read/write verification |
| 3 | `application/runs.py`: RunService composes repository + existing PlanningService. `create` validates, allocates uuid4/server time, commits received/start barriers, awaits one generation, commits valid Plan or safe rejection, routes policy; `get` is read-only | Persist before model call; /runs never accepts a client Plan; timeout becomes a persisted failed Run; no model call when creation/start commit fails |
| 4 | Same RunService `decide`: validate local decision request and call the repository's atomic approval operation | State/version guards, one decision + terminal state + one audit; conflicts/no-op do not append history |
| 5 | `application/recover_runs.py`: deterministic startup recovery, before serving and with no provider argument; use only guarded repository recovery operations | Interrupted received/generating -> rejected; committed validated -> policy route; every other state unchanged; repeated startup adds no duplicate event |
| 6 | Extend `adapters/configuration.py` for server-only AIG_DB_PATH; `main.py` creates/validates the real SQLite repository, recovers, supplies RunService; extend `local_http.py` with the three Run routes and fixed Day 2 error mapper | Invalid schema/storage prevents serving; real production composition always supplies RunService; no public bypass or test/mock flag |
| 7 | Add the deterministic test modules below and extend existing package resource verification only if needed | Full Day 1 suite stays green; new tests cover meaningful persistence/concurrency/recovery behavior |
| 8 | File-backed restart smoke using the real product entrypoint/configuration and a controlled provider in the explicit deterministic subprocess fixture | Reload same Run/Plan/decision/audit from the same file, genuine process termination at commit barriers, zero provider calls during recovery |

Keep the existing trusted factory call `create_app(planning_service)` as the Day 1 two-route compatibility composition; `create_app(planning_service, run_service=...)` adds the Run routes. Production main always supplies the validated SQLite-backed service. Current GET / and its legacy form stay unchanged; Run/approval UI belongs to a later explicitly requested slice.

## Repository operations and orchestration

Freeze a small Protocol, using domain values rather than sqlite rows/connections:

- `create(run_id, intent, created_at) -> Run`: application allocates the immutable server UUID/time, repository writes v1 plus RUN_CREATED.
- `get(run_id) -> Run | None`: one read snapshot; revalidate persisted Plan/domain/decision/audit facts.
- `start_generation(run_id, expected_version) -> Run`: received CAS before I/O.
- `save_validated_plan(run_id, expected_version, plan: Plan) -> Run`: validate/canonicalize and commit the once-only Plan barrier.
- `reject_planning(run_id, expected_version, error_code) -> Run`: only received/generating, trusted fixed errors, no raw issue/provider text persisted.
- `route_validated_plan(run_id, expected_version, recovery=False) -> Run`: the one imported requires_approval policy; never caller/model routing.
- `decide_approval(run_id, request: ApprovalDecisionRequest) -> Run`: repository allocates decision UUID/time and atomically commits the decision/state/audit.
- `recover_interrupted(run_id, expected_version) -> Run`: one RECOVERY_INTERRUPTED rejection plus event; the recovery service routes PlanValidated with recovery=True.
- `recovery_candidates() -> tuple[Run, ...]`: internal read-only, validated received/generating/validated snapshots in run_id order; not a list HTTP API. Startup validates all persisted Runs/history before this selection.

Exceptions distinguish not-found, stale version, invalid transition and database unavailable; the HTTP adapter maps them to the frozen safe envelope. The port has no generic update, delete, replace, model retry or execution method. After confirmed commit return the validated snapshot assembled in that transaction. On a failed/unconfirmed commit return a safe error/known Run handle, never infer no commit or replay the operation.

Normal read flow versions are 1 received -> 2 generating -> 3 validated -> 4 Ready. Write flow ends at v4 AwaitingApproval then v5 Approved/Rejected. A received interruption fails at v2; generating interruption fails at v3. Keep state mutations/audit coupled; do not record request-local PlanGenerated/raw output as durable state. Policy routing is a separate transaction so the frozen PlanValidated recovery barrier is real.

Cancellation is handled by the request, with no detached task: stop generation; attempt the one defined safe conditional rejection before Plan acceptance; otherwise preserve committed PlanValidated or an interrupted intermediate state for the next startup. Do not add an automatic recovery loop. SQLite failures must not turn an unconfirmed operation into a successful API body.

## New behavioral tests required in Prompt 2

| Prospective module | Cases that must exercise behavior |
| --- | --- |
| tests/test_run_contracts.py | Complete transition/forbidden matrix; per-state fields/versions; immutable Intent/Plan; three-risk policy; invalid decision fields/types/reason; fixed error/time/canonical rules |
| tests/test_sqlite_run_repository.py | Real temporary file + independent connections; pragmas/version/structure; FK/unique/type/byte bounds; close/reopen persistence; fault rollback at each transaction step; no orphan decision/audit; conflict writes nothing; bad stored JSON/risk/state/link fails closed |
| tests/test_run_service.py | Repository barriers precede one controlled provider call; read/write risk routing; rejected JSON/risk/target never stored as Plan; provider timeout/unavailable safely persisted; creation/start/storage failure counts; request isolation; observed cancellation at received/generating/validated boundaries |
| tests/test_runs_api.py | Exact 201/200/400/404/409/413/415/503 bodies and Location; accepted failed Run retrievable by GET; malformed body creates zero Runs/calls; UUID/path validation; stale repeat/current-terminal conflict precedence; no actor/endpoint/Plan override; legacy route behavior |
| tests/test_run_recovery.py | Genuine terminated subprocess plus file reopen at received, generation-start, validated-commit, policy-routing and approval pre/post-commit barriers; policy-only recovery; all terminal/waiting states unchanged; repeated startup no duplicates; zero model/tool calls during recovery |

Approval races must use two separate database connections and synchronization, not an in-memory mock lock. With a bounded lock hold, exactly one decision wins and the loser reports stale/state conflict; an intentionally held lock may report DATABASE_UNAVAILABLE. Assert final Run version, decision count and audit count, not just status codes. Repeating the winning body with its old version must produce 409 with zero extra decision/event.

Crash tests must kill a child process at controlled barriers and reload committed facts in a fresh process; raising/catching an exception alone is not restart evidence. A crash during approval must yield either the whole pre-decision snapshot or the whole committed terminal decision, never a half-state. Preserve evidence of both success and failure paths. Fixture-only providers stay outside production main.

## Completion gate and limits

Run complete pytest (default excludes live), `ruff check .`, `ruff format --check .`, `git diff --check`, schema contract checks and the existing package workflow with bundled resources/from outside cwd. Record Day 1/new test counts separately, actual file-backed persistence and actual process-restart observations. Check test process/port cleanup and no tracked DB/WAL/SHM/credentials. Preserve research/handoff and historical evidence.

A deterministic PASS must not become a phi-4-mini reliability claim. A live call is not needed to prove SQLite/CAS/recovery and must not be silently substituted or retried. Real execution, authenticated authorization/consent, future Action admission, TTL/retention/backups, public Run UI, list/audit APIs, cloud/distributed state and production reliability remain deferred. Commit/push is a separately instructed delivery action.
