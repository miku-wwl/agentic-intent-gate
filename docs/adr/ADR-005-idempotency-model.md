# ADR-005 — Scoped event identity and stable Action keys

## Status

Frozen for Stage 1 human review, 2026-10-03; not implemented.

## Context

The reference's bounded token cache can suppress a failed attempt or disappear after eviction/restart. An LLM tool-use ID is not a stable business operation key.

## Decision

Accept scoped logical event IDs atomically with Turn/slot/audit; exact duplicates map to the original Turn and do not execute. Different content under the same key conflicts. Stable core tool_call/action/approval/delivery IDs derive from the accepted event and frozen plan ordinals. Canonical hashes bind proposal content; a stable scope/Action/proposal key is passed downstream. Separate Turns intentionally have separate keys.

## Consequences

Clients lacking provider event IDs must persist a stable key before submitting. Retained durable records, not process caches or backend short retry tokens, define uniqueness. Provider keys prevent repeated effects only under their declared contract; no universal exactly-once guarantee. No automatic TTL on correctness records.

## Rejected Alternatives

Using only model IDs, content hashes, timestamps, provider delivery-attempt IDs or dictionaries confuses retries with intentional new effects. Cross-Turn argument dedup could suppress legitimate repeated actions and is not selected.
