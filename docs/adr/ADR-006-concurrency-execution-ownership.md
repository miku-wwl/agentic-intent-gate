# ADR-006 — Conditional claims without automatic lease takeover

## Status

Frozen for Stage 1 human review, 2026-10-03; not implemented.

## Context

Two independent runtimes must not execute one Action concurrently. A paused worker may still send after a clock/lease deadline; storage fencing cannot stop an arbitrary provider request.

## Decision

Use CAS versions and durable Action execution owner/token/attempt claims with no automatic expiry. A Turn has a separately fenced processing token and one active conversation slot. A stale Action becomes UNKNOWN; any replay additionally requires prior-executor quiescence and safe provider evidence/capability. Same-conversation new ingress conflicts; different conversations run independently; Actions in a Turn run sequentially.

## Consequences

Safety takes precedence over unattended liveness. Recovery operators may need to prove prior execution ended or investigate provider receipts. Clock timeout does not transfer execution rights. Old token completions cannot overwrite state. Cross-conversation service revisions belong to the external adapter contract.

## Rejected Alternatives

Process locks do not protect multiple workers. Automatic expiring leases can overlap external sends without downstream fencing. FIFO queues would not solve unknown effects and are unnecessary for conflict-based conversation admission.
