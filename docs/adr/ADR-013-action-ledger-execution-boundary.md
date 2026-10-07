# ADR-013 — Durable Action ledger and separate execution boundary

## Status

Accepted for Day 3 Prompt 1 architecture freeze, 2026-10-07. DOC-ONLY; not implemented. Authoritative details: [Day 3 architecture](../day3-action-execution-architecture-freeze.md) and [implementation plan](../day3-implementation-plan.md).

Amended by [ADR-014](ADR-014-approval-plan-binding.md), Day 3 Prompt 1.5 on 2026-10-08 (Pacific/Auckland). That ADR is authoritative for exact approval identity, truthful legacy migration and v2 read/admission validation. The stopped Prompt 2 audit was CORRECTLY_BLOCKED_BY_ARCHITECTURE_GAP; no product implementation existed or changed.

## Context

Day 2 has immutable durable Runs/Plans, trusted risk routing and one local decision. It has no tool I/O, authenticated authorization or Action identity. Approved/v5 is terminal planning-gate truth, not an effect. A process can die after a remote effect and before storing a result; treating this as failure and resending can duplicate the effect.

Audit HEAD is 622edb4bf030fc0a0f8cd9b43336ad8e8153894b, one inventory-only commit after the immutable day2-final-freeze checkpoint c7d3e1ee051af40c69ec33d888ecf16475a2d789.

## Decision

Use Run + Action, defer Turn. An Action is the durable identity for one server-compiled Plan-step operation, distinct from an execution attempt. Only an explicit admission command after the stored Run is Approved and its BOUND_V1 approval identity matches the current Plan may create it. Approved state/current valid Plan alone is insufficient. Admission and authorization do not execute. MVP admits exactly one append_service_note/1 SIDE_EFFECT step, not a subset of a larger Plan.

Keep Pending -> Authorized -> Executing -> Succeeded / Failed / Unknown as an independent Action lifecycle/version. A trusted authorization port consumes verified PrincipalContext, binds exact Action specification/policy/subject, and is freshly checked at the separate execute command. LOCAL_USER never grants authority. The eventual local demo uses one private execution capability; absent verified authority means deny. APPROVAL_REQUIRED tools remain disabled until the authenticated distinct-consent/expiry requirements exist.

The gateway alone makes one durable state/version CAS claim with a unique attempt_id and dispatches the reviewed bound adapter only after confirmed commit. The adapter receives validated specification, stable effect identity and bounded attempt context; it cannot mutate gate truth. Metadata catalog and executable registry are separate.

Gate transactions are short: claim -> external call outside the transaction -> result commit. The separate demo/provider store cannot participate in a gate DB transaction. Succeeded requires positive effect evidence; Failed requires proof of no effect; every uncertain dispatch/result becomes Unknown. Startup deterministically marks abandoned Executing claims Unknown and never resends or queries a tool.

Persist scoped accepted command receipts separately from a stable server-generated Action effect key. Default is no automatic execution retry and one attempt. Downstream keys support only the provider's actual scope/payload/retention guarantee; no general exactly-once external effect claim is made. Future evidence-based reconciliation requires CAS, authority and prior-executor quiescence; arbitrary operator assertions or lookup absence are insufficient.

Use a future explicit atomic SQLite v1 -> v2 migration to add Action, authorization, attempt, receipt, Action-event and approval_plan_bindings records: nine total tables. Preserve old table definitions/business rows and v1 default rejection behavior. Existing approval decisions receive LEGACY_UNBOUND companions with null proof, never a digest of the currently stored Plan. These terminal legacy Runs remain readable but non-executable for MVP; create a distinct v2 Run/new approval rather than reset or rebind one. New v2 APPROVE/REJECT decisions atomically store the exact contextual digest with Run/decision/audit; v2 reconstruction and admission validate it. The external approval body stays unchanged. Current v1 and all Day 2 schemas are untouched. Old-code rollback needs the consistent pre-migration v1 data backup.

## Precedence

This ADR selects the next local sprint boundary and defers Turn/conversation/worker machinery for it; it does not implement or broadly replace the deferred Stage 1 architecture. Preserve [ADR-003](ADR-003-action-execution-boundary.md), [ADR-004](ADR-004-approval-model.md), [ADR-006](ADR-006-concurrency-execution-ownership.md) and [ADR-012](ADR-012-persistent-runs-local-approval.md) safety boundaries. Local Run approval remains an additional gate, never authenticated consent. The only enabled future tool is SIDE_EFFECT; stronger APPROVAL_REQUIRED execution remains denied.

ADR-014 strengthens future execution eligibility without claiming Day 2 already bound exact Plan bytes. It supersedes any interpretation that a migrated v1 Approved Run alone is admission-eligible. Later capability permission cannot replace missing exact Plan approval; compiler/spec validation must retain that link.

## Consequences

Run planning/approval remains reusable and frozen; Action truth can survive response loss/restart independently. At most one local claim/dispatch is selected under single-owner assumptions, sacrificing automatic progress when uncertain. Unknown can require operator investigation. Hashes/invariants are not tamper-proof storage or a malicious-adapter sandbox.

Future implementation must supply actual v2 migration, capability verification/authorization, CAS/receipts, isolated effect adapter, crash/race and browser evidence while keeping the 686-test floor green. These guarantees are not asserted by this documentation acceptance.

## Rejected Alternatives

Approval-triggered calls collapse consent and effects. Execution fields on Run violate frozen lifecycle/version boundaries. Turn has no distinct current invariant. Automatic leases/retries can overlap stale sends; local state checks alone cannot prove a remote effect did not happen. Callable metadata, arbitrary imports and model-provided endpoints bypass the gate. A transaction across tool I/O cannot make external effects atomic with SQLite.

## Deferred

Full user authentication/RBAC, authenticated distinct-approver consent/expiry, APPROVAL_REQUIRED effects, multi-step/read-tool execution, Turn/conversations, workers/HA/cloud/MCP, automatic retry, public reconciliation and production durability/security certification. Prompt 1 adds no code, migration, runtime configuration, live calls, commit, push or tag.
