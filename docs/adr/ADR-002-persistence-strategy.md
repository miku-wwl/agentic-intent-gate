# ADR-002 — SQLite locally, DynamoDB on AWS

## Status

Frozen for Stage 1 human review, 2026-10-03; not implemented.

## Context

Events, Turns, Actions, approvals, claims, deliveries and audit must survive restart and update under conditional atomic rules. Default local work should require little setup, while cloud workers need shared durable state.

## Decision

One StateStore port exposes named bounded transaction groups. SQLite uses file-backed WAL/FULL and short conditional write transactions. AWS uses an on-demand DynamoDB gate table with conditional transactions/strong reads. Run the same behavioral suite against both adapters, with optional fresh LocalStack API parity checks. The independent demo ledger/mailbox uses a separate file/table and separate transactions.

## Consequences

Local work is lightweight; two mappings must preserve atomicity and version semantics. No automatic correctness-record TTL or reliance on AgentCore files. Cloud parity needs actual service validation before action cases. Neither local tests nor emulator tests prove cloud reliability.

## Rejected Alternatives

Mandatory LocalStack reduces mapping drift but burdens every local test. SQLite everywhere fails shared cloud durability without more hosting/storage machinery. PostgreSQL/Redis/Kafka add operations with no current invariant requiring them. Full comparison is in [architecture options](../stage1-architecture-options.md).
