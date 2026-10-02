# ADR-007 — Bounded synchronous processing and explicit progress commands

## Status

Frozen for Stage 1 human review, 2026-10-03; not implemented.

## Context

Short ingress lifetime and human waits make a single completion response unreliable. A queue provides automatic progress but adds outbox/worker/visibility and operational requirements.

## Decision

Persist acceptance, then attempt work within a 30-second application request budget. Return 200 terminal or 202 accepted nonterminal snapshots, with separate Turn/Action/Delivery state. Duplicates return existing status and do not trigger work. Authorized resume/recover/decision/reconcile/delivery commands drive later progress; no background task survives the response by design.

## Consequences

Caller timeout does not imply failure or rollback. Stored records support reconstruction, but automatic eventual completion and expiry notification have no MVP SLA. Retry eligibility is enforced when commands run. Initial authentication/schema/conflict/storage errors have explicit unaccepted status semantics.

## Rejected Alternatives

Waiting synchronously for human approval is infeasible. Acknowledge+queue requires a durable work outbox/dispatcher and worker for a liveness requirement the MVP deliberately defers. A fire-and-forget Python task lacks durable progress and is not selected.
