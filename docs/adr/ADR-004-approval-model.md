# ADR-004 — Durable approval of an immutable proposal

## Status

Frozen for Stage 1 human review, 2026-10-03; not implemented.

## Context

A boolean approval or a chat message cannot bind consent to exact tool arguments, approver authority and expiry. Approval must survive runtime loss and must not grant permissions.

## Decision

One ApprovalRequest binds Action/proposal hash, tool/version, canonical arguments, requester, risk, policy and a 15-minute expiry. A distinct authorized approver decides through HTTP/CLI. Decision/Action/receipt/audit commit together; first valid decision wins. States are PENDING, APPROVED, REJECTED, EXPIRED and system CANCELLED. All required approvals precede any plan mutation. Each claim still checks relevant consent validity and current authorization.

## Consequences

No UI/service is needed. Duplicate commands are idempotent; contradictory decisions conflict. Argument changes require a new Turn/proposal. Pending sibling proposals may be abandoned without mislabeling cancellation as expiry. Approved consent remains historical even after its usability expires.

## Rejected Alternatives

Free-form model approval text is not authority. Process-local waiting/SDK interrupts alone lose business truth on restart. Editable approved proposals invalidate consent. Self-approval does not demonstrate separation of duties.
