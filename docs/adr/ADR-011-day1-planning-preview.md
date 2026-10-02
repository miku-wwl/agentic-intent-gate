# ADR-011 — Local structured planning preview before durable execution

## Status

Accepted for the Day 1 architecture freeze, 2026-10-03, Pacific/Auckland. The current user request authorizes this audit, contract definition, and limited documentation correction. Application implementation belongs to the next prompt.

## Context

At baseline `4df3bbe287e5e3b4a8527dee3de82451c629f70b`, every tracked file is Markdown. Stage 1 freezes a complete reliability MVP, but its 2027 schedule, no-UI scope, deterministic-model default, and storage-first implementation order do not describe the requested local planning sprint. There is no repository Foundry adapter to reuse. The host does have a running Foundry Local service and loaded phi-4-mini; one direct inference probe returned JSON with an invalid risk enum.

## Decision

Implement the next slice as one Python application's stateless, loopback planning preview. A vendor-neutral `ModelProvider` returns untrusted plan text; independent parsing, schema validation, and trusted tool-policy checks produce a `Plan` for display. Use the existing local OpenAI-compatible endpoint and phi-4-mini for the live lane; deterministic providers remain test fixtures. Keep the existing `READ_ONLY`, `SIDE_EFFECT`, and `APPROVAL_REQUIRED` vocabulary and three service-notebook tool definitions.

The preview invokes no tools, including read tools. It creates no durable Turn, Action, ApprovalRequest, execution claim, or Delivery. `PlanValidated` means a bounded proposal passed the preview checks; it grants no execution rights. A later execution system must construct and bind its authoritative records through the original Stage 1 gates. Never execute a submitted preview directly.

For this slice only, this ADR supersedes the old blanket 2027 implementation deferral and scopes ADR-001/002/005/007/008/009: no Strands loop, persistence/idempotent acceptance, durable 200/202 snapshots, or approval interface is needed here; a small plan-display page is in the next slice's scope; the live provider is local phi-4-mini. This is one bounded text-generation call, not a replacement agent framework. Real Strands, SQLite/DynamoDB, durable HTTP/CLI commands, and deterministic fault-model acceptance remain the selected later reliability architecture.

ADR-003/004/006/010 and all future execution safety invariants remain intact. No tools or callable mutation adapters are available to the preview. No cloud provider, deployment, distributed architecture, or production security implementation is authorized by this freeze. The 2027 ecosystem review remains attached to the deferred cloud/reliability roadmap, not a prerequisite for this local preview.

Normative current contracts: [Day 1 freeze](../day1-architecture-freeze.md) and the two schemas it links. Implementation order: [Day 1 plan](../day1-implementation-plan.md). Baseline and evidence limits: [audit](../day1-repository-audit.md).

## Consequences

The first API and display can be implemented in a few hours without first building the complete execution journal. Repeated preview requests may generate different proposals; restart loses request state. No durable acceptance, exactly-once behavior, authorization, approval, or real-world effect follows from validation. Prompting does not guarantee valid JSON or semantic correctness, so malformed or inconsistent output is rejected without repair, fallback, or execution.

The eventual planner may implement the same application port with a Strands adapter. Its framework DTOs remain inside that adapter; execution admission still follows Stage 1. This does not block future MCP, Bedrock, or sandbox adapters and does not implement them now.

## Rejected alternatives

Building the entire storage/approval/execution stack before the requested preview exceeds Day 1. Introducing a new orchestration framework or vendor SDK into the core adds no requirement. Returning free-form steps, trusting model risk, or treating validation as consent loses the deterministic proposal boundary. Silently rewriting historical ADRs would hide the scope change.
