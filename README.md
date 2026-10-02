# AgenticIntentGate

AgenticIntentGate is a planned reliability layer that turns model-generated tool intent into controlled external actions. It focuses on durable identities, authorization, human approval, idempotency, recovery, uncertain outcomes, and delivery audit.

**Current stage: Stage 1 Architecture Freeze, ready for human review.** This repository contains architecture and research documents. Application code, tools, databases, deployment, and reliability tests are not implemented. Real AWS, Bedrock, AgentCore, and Twilio behavior is not verified.

```mermaid
flowchart LR
  I[HTTP or CLI ingress] --> T[Durable Turn]
  T --> P[Strands Agent proposes tools]
  P --> A[Immutable Actions]
  A --> G[Policy and approval gate]
  G --> E[Controlled execution]
  E --> R[Results and reconciliation]
  R --> D[Independent Delivery record]
```

The selected design is one Python application with bounded synchronous processing and explicit resume/recover commands. SQLite serves local mode; DynamoDB serves AWS mode. Bedrock supplies the later real model, and AgentCore hosts the application without owning durable business state. Voice is post-MVP.

Start with the [project charter](docs/stage1-project-charter.md), [architecture](docs/stage1-architecture.md), [contracts](docs/stage1-contracts.md), and [consistency review](docs/stage1-review.md). The [implementation plan](docs/stage1-implementation-plan.md) describes later work, subject to explicit architecture acceptance and Stage 2 authorization.

`research/` contains immutable Stage 0 source-study and Stage 0.5 local-reproduction reports. Their observations explain the reliability requirements; they are not our implementation. [STAGE1_HANDOFF.md](STAGE1_HANDOFF.md) is preserved as supplied. Its archived manifest, reproduction code, and review-package references are not prerequisites for this architecture; only the reports included here were used.
