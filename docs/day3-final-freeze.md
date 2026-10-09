# Day 3 final freeze review — FAIL

`DAY3_FINAL_FREEZE=FAIL`. Day 3 is **not ready for final freeze**. Independent fault
injection reproduced one unresolved P1 in the SQLite v2 Run transaction cleanup.
The production-defect stop rule ended this review. No corrective implementation,
checkpoint, tag or release was performed. [Structured evidence](evidence/day3-final-freeze.json)
records completed observations and gates that remain unverified.

## Baseline and scope

Started on clean `main` at `cfa371cd27ef833bac93edbf8d743f73851f7222`, the six-file
Prompt 5 checkpoint. HEAD, origin/main and direct GitHub main lookup matched.
`day2-final-freeze` still resolves to `c7d3e1ee051af40c69ec33d888ecf16475a2d789`.
The preceding Day 3 architecture, approval-binding, persistence, gateway, UI and
hardening checkpoints appear in the inspected Git history.

This review inspected the architecture/ADR contracts, binding and compiler,
capability/policy, gateway and Action transaction paths, and the real-backend
browser/process fixtures. It did **not** complete every required source, packaging,
demo or security gate before the blocker. Earlier reports are historical evidence,
not proof that this final review passed.

The selected architecture remains Run + Action, TURN_REQUIRED=NO. Documented Run
states are IntentReceived, PlanGenerating, PlanValidated, Ready, AwaitingApproval,
Approved, Rejected and PlanRejected. Action states are Pending, Authorized,
Executing, Succeeded, Failed and Unknown. Approval, authorization and execution
are distinct commands. The inspected gateway calls the adapter after its claim
transaction returns, uses one durable attempt and does not dispatch receipt replay.
The retained design is nine STRICT tables in explicitly selected SQLite v2; its
migration and all preservation obligations were not fully re-audited here.

## P1 blocker: v2 Run cleanup escapes normalization and can skip close

ID: `D3-FREEZE-P1-V2-RUN-CLEANUP`.

Root cause: [SQLiteV2RunRepository._transaction](../src/agentic_intent_gate/adapters/sqlite_v2_runs.py)
normalizes errors in its try/except, then accesses `connection.in_transaction`, calls
`rollback()` and calls `close()` sequentially in an unprotected finally (lines 69–73).
An exception from the property or rollback replaces the normalized RunStoreError;
rollback failure prevents close. The [Run HTTP handler](../src/agentic_intent_gate/adapters/local_http.py)
then returns the generic 500 envelope. This differs from the documented safe
storage-error contract and the reviewed Action cleanup protection in Prompt 5.

Two fresh, separately created v2 databases, deterministic planning, real RunService,
real FastAPI/Uvicorn loopback HTTP and actual SQLite connections reproduced:

| Injected boundary | HTTP observation | Durable state after explicit cleanup/reopen | Connection observation |
| --- | --- | --- | --- |
| Actual approval COMMIT, close handle, lose acknowledgement | 500 INTERNAL_ERROR; expected 503 DATABASE_UNAVAILABLE | Approved/v5; one decision, one BOUND_V1 row, five audit events | finally accesses the closed handle; cleanup close is not reached |
| Approval COMMIT failure, then rollback failure | 500 INTERNAL_ERROR; expected 503 DATABASE_UNAVAILABLE | AwaitingApproval/v4; zero decisions/bindings, four audit events | close not attempted; retained connection remains in a write transaction; another actual BEGIN IMMEDIATE is blocked |

Both databases passed integrity_check after cleanup. Neither case composed a tool
adapter, created an Action or called a tool. The diagnostic explicitly rolled back
and closed its own retained faulty connection before reopening. Both HTTP servers
and listeners stopped. This is a reproduced availability/error-contract defect,
not an observed approval bypass, fabricated successful response or duplicate effect.
Classification: P1, unresolved. No production fix was made.

The independent diagnostic is retained outside the repository; its safe measurements
are in `.runtime/day3-final-freeze/20261008T135428159Z/approval-cleanup.json`.
The first diagnostic attempt encountered a test-controller cross-thread cleanup
restriction; the corrected diagnostic connection permits controller cleanup after
the HTTP request. That correction changed only the external diagnostic fixture.

### Minimal clean-source reproduction of the cleanup/lock defect

From an installed development checkout, this creates only a fresh temporary database.
It demonstrates the same underlying transaction defect without needing retained data:

```powershell
@'
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from agentic_intent_gate.adapters import sqlite_v2_runs as module
from agentic_intent_gate.adapters.tool_catalog import build_validator

with TemporaryDirectory(prefix="aig-v2-cleanup-") as directory:
    db = Path(directory) / "control.sqlite3"
    repo = module.SQLiteV2RunRepository(db, build_validator())
    opened = []
    class Fault(sqlite3.Connection):
        close_attempted = False
        def commit(self):
            raise sqlite3.OperationalError("owned commit fault")
        def rollback(self):
            raise sqlite3.OperationalError("owned rollback fault")
        def close(self):
            self.close_attempted = True
            return super().close()
    def connect(path):
        c = sqlite3.connect(path, isolation_level=None, factory=Fault)
        c.row_factory = sqlite3.Row
        opened.append(c)
        return c
    try:
        with patch.object(module, "open_existing", connect):
            try:
                with repo._transaction(write=True):
                    pass
            except Exception as error:
                print("escaped_type", type(error).__name__)
        c = opened[0]
        print("close_attempted", c.close_attempted)
        print("still_in_transaction", c.in_transaction)
        probe = sqlite3.connect(db, isolation_level=None, timeout=0.05)
        try:
            try:
                probe.execute("BEGIN IMMEDIATE")
                print("independent_writer_blocked", False)
            except sqlite3.OperationalError:
                print("independent_writer_blocked", True)
        finally:
            probe.close()
    finally:
        for c in opened:
            sqlite3.Connection.rollback(c)
            sqlite3.Connection.close(c)
'@ | .venv/Scripts/python.exe -
```

Observed defect signature: OperationalError escapes, close_attempted=False,
still_in_transaction=True and independent_writer_blocked=True. A corrective task
must separately prove normalized storage errors, close attempts even when cleanup
fails, preserved approval atomicity and absence of a retained write lock.

## Regression evidence: incomplete current run

The complete deterministic suite was started with a fresh external basetemp and
the existing `not live` selection. It was stopped after the reproduced blocker.
During stop, pytest terminal flushing emitted `OSError: [Errno 22] Invalid argument`;
the browser session record contains pytest exit code 3. This run is incomplete and
is not a complete-regression PASS.

JUnit records 451 unique named completed passing cases, zero assertion failures,
one runner-internal error, and one unnamed incomplete testcase. Its suite attribute
is tests=452, whereas the XML has 453 testcase elements including the internal-error
record. No unnamed or runner-internal record is counted as a deterministic pass.
JUnit elapsed time is 324.163 seconds. Complete collected/deselected counts and the
runner stopwatch's final wall-clock value were not captured; the expected historical
1089/5 values are not substituted for those missing current facts.

The current browser artifact records all 126 browser cases passing, five closed
Edge 154.0.4258.53 browser/driver contexts, 127 stopped HTTP servers, removed Run/Action
business directories and a PASS dynamic scan against 50 managed test credentials.
These existing lanes include controlled-response frontend cases and actual
HTTP/SQLite-backed Run and Action E2E; their distinction remains explicit. The
126 cases are a subset of the partial regression, not an additional test batch.

Current process telemetry records 15 stopped children and completed crash-window
facts A–E. Only four corresponding hardening test calls have named JUnit PASS
records before the stop; E has telemetry but no completed named test result. No
current A–H acceptance is claimed. [Prompt 5](day3-prompt5-execution-hardening.md)
records historical A–H and three 38-case focused runs; that matrix's independent
final re-audit remains incomplete.

Historical structured evidence was reconciled for the cumulative baselines:
Day 1 191; Day 2 686; Prompt 2 850; Prompt 3 1002; Prompt 4 1051; Prompt 5 1089.
These values are not added together. The historical Prompt 5 execution gate remains
PASS and its evidence is unchanged; it does not cover this newly reproduced Run defect.

## Unfinished gates and resource/security observations

The fresh positive execution/reopen smoke, dedicated negative smoke, complete
regression, wheel/sdist, isolated installation, resource/lock checks and practical
demo reproduction remain NOT RUN or INCOMPLETE under the defect stop rule. The
freeze cannot certify clean-install or competition-adapter readiness.

Ruff, format and diff checks passed. Scoped cleanup checked 144 distinct recorded
ports with zero current listeners; recorded children, browser contexts and HTTP
servers stopped. Current task process checks found no retained pytest/fault/restart
worker. Synthetic external databases and ignored evidence are retained for review;
none are claimed deleted. No user database, permanent capability or Ollama service
was touched. Pattern/path checks and managed-browser-credential checks are scoped
observations; the full repository/package security audit was not completed.

## Readiness and known limitations

Reusable surfaces observed include ModelProvider, PlanningService, RunService,
exact-bound compiler/admission, authorization, ToolExecutor, gateway, repository,
recovery and the UI shell. Current compiler/registry policy restricts execution to
append_service_note/1 for service-demo. New external bindings, schemas, permission,
effect-evidence and quiescence contracts require their own review and tests; arbitrary
tools are not already supported. Day 4 readiness is NOT VERIFIED while the P1 and
unfinished mandatory validation gates remain.

Documented limitations remain: synchronous SQLite behind the async demo interface
has cooperative soft timeouts; no hard disk/effect cancellation or universal bounded
latency. Production identity/RBAC, distinct authenticated consent, multi-worker HA,
distributed durability, physical power-loss evidence, universal exactly-once effects,
automatic retry/reconciliation, multi-step execution and real competition/cloud/MCP
adapters remain deferred. These limitations do not downgrade the reproduced P1 to P2.

## Final gate and next boundary

Observed P0: 0 in the completed review scope; complete security certification was
not performed. Unresolved P1: 1. Only this report and its new structured evidence
were added. Product code, tests, schemas, dependencies, earlier evidence and tags
remain unchanged; there was no commit/push/tag/release.

`DAY3_FINAL_FREEZE=FAIL`

`DAY3_COMPETITION_ADAPTER_READINESS=NOT_VERIFIED`

Next: a separately scoped corrective task for the v2 Run cleanup, followed by a
new independent final-freeze run completing every mandatory gate. Day 4 remains
outside this task.
