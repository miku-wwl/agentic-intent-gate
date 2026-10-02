# AgenticIntentGate — local/cloud boundary

**OUR DESIGN DECISION — Stage 1.** This table freezes component responsibilities, not current deployments. One Python source tree will serve both modes with explicit composition/configuration.

| Capability | Local Mode | AWS Mode | Why |
| --- | --- | --- | --- |
| Ingress | Loopback HTTP routes and CLI/test fixtures | Trusted CLI invokes AgentCore HTTP operation envelope | Generic DTOs; no Twilio dependency or public webhook service |
| Model | Deterministic Strands-compatible contract Model by default | Explicit BedrockModel/model ID/Region and bounded retries | Correctness cannot depend on stochastic output |
| Agent | Real Strands Agent, fresh per planning attempt from durable committed history | Same planner and proposal-only tool registrations | Framework integration is real; cached Python objects are dispensable |
| Persistence | SQLite file, WAL/FULL, atomic domain transaction groups | On-demand DynamoDB gate table, conditions/transactions and strong base reads | Same observable port, backend parity tested separately |
| Approval | Authorized HTTP/CLI decision with signed test actor assertion | Same application use case via operation envelope and actor assertion | Durable immutable proposal binding without UI/service |
| Tools | Independent durable service fixture with keyed receipts/status lookup; adverse-capability fixtures for faults | Separate on-demand DynamoDB demo ledger table with provider mutation/receipt transaction | Observable external I/O separate from gate commit, no third-party service bill |
| Delivery | Independent durable mailbox fixture | Mailbox records/receipts in the separate demo table | Durable provider acceptance is observable; no claim of human/carrier receipt |
| Secrets | Synthetic test keys; developer private assertion key in OS-protected ignored file created later | Offline issuer keeps private key; runtime receives public verification key; AWS SDK uses execution role | No credentials in model/core/repo; no chosen external API secret |
| Telemetry | Structured JSON logs and captured spans/metrics; durable audit in gate store | Same schema to CloudWatch Logs, small metrics set; optional correlated OpenTelemetry trace export | Logs aid diagnosis; audit commits with state |
| Fault injection | Explicit fake clock/IDs, barriers, process kills, response loss, persistent fixture receipts | Named demo fault hooks only in restricted validation mode | Deterministic crash windows locally; narrow real-service evidence later |
| Hosting | One request-driven local process; independent fixtures only for validation | One serverless AgentCore runtime | No reliance on process-local durable state or always-on worker |
| Packaging | Stage 2 reproducible Python lock/build, local package only | CodeZip with Linux ARM64 dependencies; scoped S3 artifact | Keeps deployment small; compatibility requires later build checks |

## Explicit tool choices

**Foundry Local: OPTIONAL.** An exploratory adapter may be added after deterministic local acceptance. It must fit the Strands Model interface and expose tool-use events or decline tool capability. It is not part of correctness acceptance; exact endpoint/model/protocol must be checked when implemented. No installation or invocation occurred in Stage 1.

**LocalStack: OPTIONAL.** It is useful specifically for DynamoDB adapter API/conditional-transaction integration tests in the later AWS adapter phase. It is not required for default local operation. SDK request-shape tests alone cannot prove service behavior; the cloud gate must include actual DynamoDB conditional/transaction checks before external action cases, with an emulator parity run where available. Emulator PASS remains separate from real AWS PASS. No reuse of another project's containers/state.

**MiniStack: NOT REQUIRED BY THE FROZEN ARCHITECTURE.** The required local property is deterministic Strands tool-call/state behavior, which a custom Model tests directly. No MiniStack dependency or emulator implementation is frozen. Re-evaluate MiniStack at the 2027 Stage 2 entry review: if its Bedrock and AgentCore Runtime fidelity has materially improved and it adds useful AWS-boundary validation, record the choice and scope in a new or superseding ADR before implementation. Otherwise, proceed with the existing architecture without MiniStack. The frozen architecture and the current emulator choice are separate decisions.

## AgentCore boundary and operation mapping

The local HTTP operations are conceptually: POST inbound; GET Turn/status; GET pending approvals; POST approval decision; POST resume; POST recover; POST Action reconcile; POST Delivery retry/reconcile. CLI commands call the same application use cases. These are planned interfaces, not endpoints created now.

AWS wraps them as `operation`, `request_id`, `actor_assertion`, and an operation-specific body sent to `/invocations`. The response is a domain envelope with `status_code` semantics and separate ingress/Turn/Action/Delivery states. The caller must inspect this body; a successful InvokeAgentRuntime transport response is insufficient. `/ping` is health only. The [AgentCore service contract](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-service-contract.html) documents the hosting protocol; Stage 2 must validate the chosen SDK serialization and error propagation.

The trusted adapter derives `runtimeSessionId` from a configured mapping of deployment_scope/conversation_id and validates its expected format. Actor membership remains an application check. Status/recovery reconstruct from DynamoDB, so changing runtime session placement or losing a Python object does not lose accepted business state. No sessionStorage mount or AgentCore Memory is selected.

## Small AWS service inventory

| Selected service/resource later | Concrete requirement |
| --- | --- |
| Bedrock Runtime | Real model/tool-use integration validation; explicitly chosen model, not Bedrock Agents |
| AgentCore Runtime, one serverless runtime | Intended managed hosting/invocation validation; no Instances/EC2 mode |
| DynamoDB, gate table | Shared durable identity, version conditions, transactional ownership/approval/audit |
| DynamoDB, separate demo table | Independent external tool and mailbox effect/receipt evidence |
| IAM caller/deployer/runtime roles | Scoped invocation, deployment, persistence, model and log permissions |
| CloudWatch Logs and small metric namespace | Cloud correlation/failure evidence |
| S3, scoped package object/bucket | Required CodeZip deployment artifact; no business/session state |

S3 solves packaging, not recovery. AgentCore direct code deployment documents a Python ZIP and S3-backed artifact path; Linux architecture/dependency compatibility must be established later. [Direct code deployment](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-code-deploy-python.html). Select Python 3.13 for the intended local/cloud baseline, subject to independent package/platform compatibility validation in Stage 2. Do not copy the reference lockfile or its import alias.

Secrets Manager is **excluded from the mandatory service set** because the chosen tools have no third-party credentials and the runtime uses IAM plus a public assertion verification key. If a future adapter requires an API secret, it owns the retrieval/rotation boundary and gets a separately justified scoped Secrets Manager grant. This is a deliberate decision, not an assumption that secrets are safe in plaintext configuration.

Intentionally excluded: SQS, SNS, EventBridge, Step Functions, Lambda, API Gateway, ECS, EKS, EC2/Instances, RDS, ElastiCache, OpenSearch, Bedrock Knowledge Bases, AgentCore Gateway/Memory/Browser/Code Interpreter, EFS, managed sessionStorage, customer-managed KMS infrastructure, and always-on dashboard services. No selected invariant requires them. IAM/deployment tooling may use AWS STS through normal credential acquisition; it is not a business workflow component. No Terraform/CDK/CloudFormation files or deployment commands are created in Stage 1; the later direct API packaging path does not require copying generated reference infrastructure.

## What local tests cannot establish

SQLite proves only its adapter semantics and process/disk behavior under the tested environment. LocalStack cannot prove real IAM, regional service limits, managed runtime lifecycle, model entitlement, network behavior, or billing. Fixtures cannot prove Twilio, carrier delivery, or third-party idempotency retention. Actual Bedrock/AgentCore/DynamoDB/CloudWatch behavior remains NOT VERIFIED until separate authorized validation produces evidence.

## Later cost gate

Before any real cloud experiment: freeze account/Region/model allowlist, max 20 total model requests, max 512 output tokens per request, a reviewed bounded input/context budget, named resources, time/cost budget, scoped permissions, and teardown steps. Use on-demand tables and serverless request work; no queue or polling scheduler. Capture identifiers/digests/evidence, then delete experiment resources only under the authorized cleanup scope. Do not quote unverified prices or assume a free tier. A cloud model configuration that needs costly/inaccessible access cannot silently replace the accepted budget.
