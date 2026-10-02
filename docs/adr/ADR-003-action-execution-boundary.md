# ADR-003 — One ActionExecutionGateway owns mutation I/O

## Status

Frozen for Stage 1 human review, 2026-10-03; not implemented.

## Context

Model tool-call text cannot establish authorization, execution ownership, success, or safe retries. Hidden writes in an ingress/channel bypass reliability controls.

## Decision

Only ActionExecutionGateway invokes mutation adapters after an immutable plan commit, fresh policy/authorization/approval checks, and an atomic READY→EXECUTING claim. It records attempt outcome or UNKNOWN and exposes reconciliation. Agent-facing mutation tools propose only. Read-only calls use a separately authorized read gateway.

## Consequences

There is one inspectable intent-to-write boundary. The gateway cannot invent arguments, select a business workflow, schedule generic tasks, or compensate automatically. Delivery has its own journal/claim and cannot change Action truth.

## Rejected Alternatives

Direct model-owned tools make safe crash/idempotency behavior too easy to bypass. Per-tool bespoke reliability logic fragments invariants. A generic workflow engine is larger than the selected fixed-plan coordinator.
