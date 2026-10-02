# AgenticIntentGate — Stage 1 project charter

Date: 2026-10-03, Pacific/Auckland. **OUR DESIGN DECISION — frozen design for human review; implementation has not started.** Stage 1 PASS means precise architecture, not proven reliability or cloud readiness.

## Problem and objective

A model can suggest a valid tool call while the application loses its execution status, repeats a write after a retry, accepts an unauthorized approval, or reports success after response delivery fails. AgenticIntentGate will place explicit durable boundaries between a model's intent and external I/O. It will demonstrate controlled execution using Python, a real Strands Agent boundary, later Bedrock inference, and later AgentCore hosting.

The architecture centers on durable Turn, Action, Approval, and Delivery records. It promises conditional ownership and capability-dependent recovery within a retained deployment scope. It makes no universal exactly-once claim and does not treat an LLM response as evidence of an external effect.

## Research basis

**REFERENCE OBSERVATION:** [Stage 0](../research/stage0/twilio-reference-study.md) and [execution flows](../research/stage0/twilio-execution-flows.md) describe process-local deduplication, separate history and delivery operations, and object-local concurrency guards. [Stage 0.5](../research/stage0.5/review-package/docs/stage0.5-local-messaging-reproduction.md), tests 5/6/8 and Observed limitations, reports retained-token suppression, a failed outbound Action acknowledged successfully, and eviction permitting repetition. Reconstruction restored history rather than execution state. Multi-worker concurrency and crash atomicity were not tested there.

**OUR DESIGN DECISION:** use durable event-to-Turn mapping, immutable Action proposals, conditional execution claims, explicit UNKNOWN, and a separate delivery journal. These mechanisms are planned here and were not added to the reference.

## Principles

1. Authenticate callers; authorize resources/tools independently of approval.
2. Preserve logical identities across retries and restarts.
3. Commit a complete proposal plan before any side-effect adapter can run.
4. Claim execution atomically; never infer external failure from a timeout alone.
5. Reconstruct from stored facts; never resume a live Python task from history.
6. Prove contracts with deterministic models, real storage, and controlled faults locally.
7. Keep AWS SDK objects in adapters and keep deployment count small.
8. Record partial success and delivery failure without inventing rollback.

## MVP frozen

| Capability | Required demonstration later |
| --- | --- |
| Ingress | Generic authenticated HTTP and CLI fixtures; stable event ID or required client idempotency key |
| Agent | Real Strands Agent with deterministic contract-test Model and bounded tool-calling loop |
| Read tool | `get_service_record` |
| Side-effect tool | `append_service_note`, authorized operator, no additional approval |
| Approval tool | `set_maintenance_mode`, authorized operator plus distinct authorized approver |
| Durability | InboundEvent, Conversation, Turn, ToolCall, Action, Approval, Delivery, attempts, transition audit |
| Reliability | Duplicates, retries with evidence, competing claims, same-conversation conflict, restart, UNKNOWN and reconciliation |
| Delivery | One immutable final status summary per Turn, separate provider receipt/status |
| Interfaces | Status, approval decision, resume, recover, reconcile, and delivery retry commands; no UI |
| Cloud later | Bedrock + AgentCore + DynamoDB + IAM + CloudWatch; tiny controlled external demo ledger |

Limits: one configured tenant; multiple authorized actors and conversations; text only; at most three read calls and two distinct side-effect proposals per Turn; at most four model requests per planning attempt. All proposals are fixed before execution. Actions run sequentially in plan order, after all required approvals. No dependent action whose arguments need an earlier write result is permitted. Canonical size/deadline limits are in [contracts](stage1-contracts.md).

## Demonstration domain

A small service operations notebook has a seeded service record, append-only operator notes, and a maintenance flag. An operator can read a record, append a note, or request a maintenance change. This is sufficient to exercise the three risk classes, rejection, failure before/after an external write, retry, reconciliation by key, and audit. It requires no incident lifecycle, tickets, alerts, RAG, or dashboard.

The local external-service fixture has its own durable database/process boundary. The later AWS tool adapter targets a separate controlled DynamoDB demo table. Each effect and its provider idempotency receipt commit together in that external table, in a transaction separate from the gate's state transaction. This validates real external storage I/O with low cost; it does not validate any third-party business API. Core semantics depend on declared tool capabilities, not service-note names.

## Non-goals and post-MVP

Non-goals: RAG, chatbot wrapping as the deliverable, a Twilio clone, generic workflow/BPM/DSL, Temporal, LangGraph, MCP, Kubernetes, arbitrary microservices, distributed exactly-once computing, frontend, automatic compensation, cross-region correctness, and unbounded autonomous scheduling.

Post-MVP: Twilio or another channel adapter, voice, production identity provider, additional downstream APIs, dependent action plans, a queue and autonomous recovery only if a real liveness requirement justifies them, and stronger retention/backup/incident operations. Foundry Local is optional exploratory development.

## Success and evidence gates

Stage 1 succeeds when identities, state transitions, transaction groups, crash recovery, permissions, and retry rules agree across these documents. Human acceptance is still required before Stage 2.

Stage 2 local acceptance will require real subprocess termination/reconstruction, durable fixture effect counts and receipts, two independent claim contenders, approval races, all crash windows A–J, and independent delivery outcomes. Test assertions on model text alone are insufficient. Later real AWS acceptance is a separate narrow evidence gate and remains NOT VERIFIED now.

Portfolio positioning: explain and demonstrate reliable intent-to-effect boundaries in an AWS-oriented agent application. The portfolio value lies in inspectable failure evidence and limits, rather than a large service inventory.

## Cost constraints

Default tests require no AWS or paid model. No always-on cloud worker, queue, managed SQL database, NAT gateway, or voice service is selected. Later use one Region, on-demand DynamoDB, one serverless AgentCore runtime, capped model tokens/requests, short log retention, a named experiment budget, and explicit teardown. Do not assume a free tier or record speculative dollar estimates. Cloud deployment and spending require later authorization.
