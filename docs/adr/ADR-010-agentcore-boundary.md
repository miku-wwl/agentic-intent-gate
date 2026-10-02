# ADR-010 — AgentCore hosts the application; durable truth stays external

## Status

Frozen for Stage 1 human review, 2026-10-03; not implemented.

## Context

Saved conversation files and process-local Agent/session objects are not a durable execution journal. Runtime routing/session identity does not establish end-user authorization.

## Decision

Later host one serverless Python application on AgentCore HTTP with CodeZip packaging and a scoped S3 artifact. Persist business records in DynamoDB. Reconstruct Agents/plans/status after runtime restart. Use trusted CLI SigV4 invocation plus a verified application actor assertion and conversation binding. Local mode calls the same core through HTTP/CLI and real framework adapters.

## Consequences

No managed sessionStorage or AgentCore Memory is selected. A new runtime placement can continue from durable facts; it cannot resume live tasks. Cloud validation must prove package/platform, IAM, invocation/error envelope, actual persistence, restart evidence and telemetry separately. Runtime lifecycle failure may leave Actions UNKNOWN until safe provider evidence resolves them.

## Rejected Alternatives

Using runtime session files or process caches as sole business state fails shared ownership and durable recovery requirements. Lambda/ECS/EKS or a separate gateway add components without an MVP ingress need. AgentCore as a generic durable workflow engine is outside its selected responsibility.
