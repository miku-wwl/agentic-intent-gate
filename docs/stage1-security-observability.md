# AgenticIntentGate — security and observability

**OUR DESIGN DECISION — Stage 1; controls are specified, not implemented.** Approval records user consent for one proposal; authorization determines whether the requesting actor and executor may cause that effect. Authentication establishes identity. These are separate checks.

## Trust boundaries

| Boundary | Trusted input / required check | Failure behavior |
| --- | --- | --- |
| Ingress caller → adapter | Signed actor identity, bounded request, scoped conversation membership, logical event key | Reject before acceptance on invalid auth/schema; redact credentials |
| Adapter → core | Verified RequestContext and normalized InboundMessage | Reject actor/scope mismatch; no caller-provided role overrides |
| Agent/model → planner | Model output is untrusted text/tool intent | Strict tool/schema/cap/registry checks; no authorization derived from content |
| Planner → Action gateway | Only committed immutable Action IDs | Reload/hash-check stored proposal; no direct mutation callable exposed to Agent |
| Approver → decision use case | Authenticated distinct approver, resource permission, exact proposal_hash/version | Wrong actor/hash/expiry fails closed; races use durable conditions |
| Gateway → tool adapter | Confirmed claim, frozen arguments/key, fresh policy, deadline | Loser/stale claim performs no I/O; ambiguous send becomes UNKNOWN |
| Application → storage | Named conditional transaction group; adapter's IAM/file permissions | No unavailable/corrupt store fallback to dictionaries; no mutation without durable audit/claim |
| Runtime → AWS services | Execution role permissions, configured table/model/Region | Least privilege; model cannot select arbitrary ARN/table/endpoint |
| Tool/delivery provider → core | Schema-checked receipt with exact key/hash/resource binding | Invalid/missing evidence after possible write is UNKNOWN |
| Recovery operator → resolution | Authorized recovery role and independently checkable evidence | No unsubstantiated UNKNOWN dismissal or unsafe takeover |

MVP actor registry has `operator`, `maintenance_approver`, `recovery_operator`, and `admin` roles with explicit service/conversation membership. Admin configuration changes are audited. Requester may append an authorized service note; maintenance requires an authorized operator plus a distinct permitted approver. A recovery operator can read/reconcile evidence and resume an already authorized Action, but does not acquire the requester's mutation permission by holding recovery authority.

## Authentication design

Local contract tests inject an explicitly marked test authentication adapter with fixed principals; that adapter is disabled outside test mode. Local HTTP exploration uses signed short-lived actor assertions. Later AWS uses SigV4 for trusted CLI-to-runtime access **plus** an application actor assertion; the runtime does not assume the payload actor_id or an opaque runtime session header identifies the human.

The application assertion is a JWT with pinned EdDSA/Ed25519 verification algorithm, configured public keys/key IDs, exact issuer and audience, `sub` mapped to actor_id, `deployment_scope`, `iat`, `nbf`, `exp` (max five-minute lifetime), `jti`, and a canonical operation/request-body hash binding. The offline demo issuer holds its private signing key and may issue separate operator/approver assertions. The runtime has only verification keys. Reject `none`, algorithm substitution, unknown key IDs, invalid issuer/audience/scope/time/body hash, and unregistered subjects. No remote key URL supplied in a token is followed.

Use an established JWT/crypto library in Stage 2 and test negative cases; do not implement custom cryptography. Verification of algorithms, issuer/subject and audience follows the [JWT best current practices](https://datatracker.ietf.org/doc/rfc8725/). This is a constrained demo issuer, not a production identity-provider deployment. Role grants come from server policy, not token/model claims. Key rotation uses a versioned public-key allowlist; revoked actors/keys block subsequent commands/claims.

The assertion does not need to remain valid while an Action waits. Each approval/resume/recover command requires a fresh authenticated caller; the gateway rechecks the stored requester's current authorization. The original requester’s token is never persisted/replayed as execution authority. Runtime IAM transport success alone cannot substitute for application authorization. AgentCore supports IAM/JWT inbound approaches, but our chosen controlled CLI path keeps end-user binding explicit. [AgentCore inbound authentication](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-oauth.html).

## Authorization, policy and approval

Authorize ingress conversation access, each read resource, each proposed mutation, each approver, each query, and each recovery operation. ToolDefinition defines required roles/resources. Canonical resource IDs resolve through an allowlist; models cannot provide arbitrary URLs, filesystem paths, table names, credential names, or IAM policies.

Proposal validation uses a policy snapshot; execution checks current permissions and registry compatibility. The claim transaction guards the current policy revision. Revocation before claim blocks I/O; revocation after a committed claim cannot guarantee stopping an in-flight external request. Current policy may require stronger approval than the frozen proposal: fail it and require a new proposal rather than quietly widening old consent.

An ApprovalRequest stores the exact immutable tool/version/arguments/risk/policy hash. The approver sees that content via HTTP/CLI and submits a decision_request_id, target IDs/hash, decision and comment. The first valid decision and Action state change commit together. Expiry and system abandonment are explicit, auditable decisions. Approved consent never bypasses current requester/tool permission checks.

## Secrets and IAM intent

No raw tokens, keys, AWS credentials, or external secrets are included in logs, model context, Action arguments, audit, or tracked files. Future developer private keys are generated only when implementing the auth adapter, protected by OS access controls and ignored. Contract-test keys are synthetic and clearly identified. The offline demo issuer is a trusted administration boundary; compromise of its signing key defeats demo actor authentication and must be addressed before production use.

AWS runtime uses its execution role, not embedded access keys. Intended grants are exact configured gate/demo tables and their required Get/Query/Scan/Put/Update/transaction actions, the chosen Bedrock model/inference-profile resources and invocation actions, the selected log groups/metric namespace, and the package object read permissions required by deployment. No runtime table deletion, IAM mutation, Secrets Manager access, arbitrary Bedrock models, or unrelated AgentCore capabilities are intended. Exact effective transaction IAM action evaluation and resource/condition coverage must be checked against current service documentation and negative tests in Stage 2.

The caller role is restricted to the chosen runtime invocation. The deployer is separate and holds only later approved provisioning/package permissions, including narrowly scoped PassRole. The runtime trust policy binds the account/runtime as supported. The CLI derives routing IDs from authorized conversations. AgentCore does not automatically establish user/session ownership; the application enforces that binding. [Runtime security](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-security-best-practices.html).

Local SQLite/tool-fixture files need OS permissions and distinct state files. AWS tables use managed encryption at rest, network calls use TLS, and log access is scoped. Customer-managed keys, production IdP/SSO, managed secret rotation, WAF and formal retention/backup operations are deferred with explicit review before public exposure. The chosen MVP has no public cloud webhook.

## Durable audit versus diagnostic logs

AuditEvent commits with aggregate transitions and claims. It records `event_id`, `operation_id`, canonical `operation_hash`, `event_type`, UTC time, deployment scope, aggregate type/ID and before/after version/state, requester/approver/executor identity as applicable, auth/policy references, argument/proposal/payload hash as applicable, failure class, effect evidence, provider/evidence reference and correlation IDs. Audit entries are append-only at the application level. This is not a tamper-proof ledger against an AWS/table administrator.

Tool arguments/input text are kept only where operationally required in protected domain records. Logs contain hashes/allowlisted resource identifiers, not full prompts, notes, auth assertions, destinations, or raw provider exception bodies. Comments and external references are size checked and sanitized.

Canonical chain: `correlation_id → conversation_id → turn_id → tool_call_id → action_id → approval_id` and `turn_id → delivery_id`. Read-only calls have no action_id. Physical `request_id`, `inbound_attempt_id`, claim token hash, attempt number and provider trace/reference connect retries to the same logical root. Duplicate incoming trace IDs are linked to the original canonical correlation.

## Structured log/event schema

| Field | Required use |
| --- | --- |
| timestamp, level, event_type, component, deployment_scope | Every diagnostic event |
| request_id, correlation_id | Every request event; canonical root after acceptance |
| conversation_id, turn_id, tool_call_id, action_id, approval_id, delivery_id | Present when known; explicit null otherwise, never invented mappings |
| operation_id, attempt_number, owner_id, aggregate_version | State/attempt correlation |
| from_state, to_state, failure_class, effect_evidence | Transition/outcome events |
| argument_hash, proposal_hash, payload_hash, provider_reference | Relevant evidence references; no raw secrets |
| duration_ms, model_id, policy_revision, evidence_reference | Relevant timing/config/decision context |

Required event types: `inbound.accepted`, `inbound.duplicate_mapped`, `inbound.conflict`, `turn.transition`, `model.started`, `model.completed`, `model.failed`, `tool.proposed`, `action.created`, `approval.requested`, `approval.decided`, `action.claimed`, `execution.started`, `execution.succeeded`, `execution.failed`, `action.unknown`, `reconciliation.attempted`, `reconciliation.resolved`, `reconciliation.inconclusive`, `delivery.attempted`, `delivery.sent`, `delivery.failed`, `delivery.unknown`, `ownership.conflict`, `security.denied`.

Diagnostic `execution.started` is emitted immediately before I/O; its presence/absence is not durable proof that a provider request was sent. Audit claim/attempt remains authoritative. A log export outage does not authorize unrecorded execution; successful database audit commits may proceed with a local diagnostic export failure metric.

## Minimum metrics and cloud mapping

| Metric | Measurement / later CloudWatch mapping |
| --- | --- |
| turn_latency_ms | Acceptance to terminal state, including human wait; separate processing duration from wait |
| model_failures_total / model_requests_total | Classified model attempts and total actual requests |
| tool_execution_failures_total | Tool/risk/failure_class, without Action ID dimension |
| approval_latency_ms | Requested to decision; expiry/cancellation labeled |
| duplicate_inbound_total | Mapped duplicate count by channel |
| action_retries_total | Attempt numbers >1, reason/evidence class |
| unknown_outcomes_total / unresolved_unknown_count | Separate Action/Delivery counters and current unresolved gauge |
| reconciliation_outcomes_total | Applied/no-effect/inconclusive/lookup failure |
| delivery_failures_total | Known failure versus uncertainty |
| concurrency_conflicts_total | Slot/claim/decision/policy revision category |

IDs are log/span attributes, not high-cardinality metric dimensions. Dimensions are bounded component/channel/tool/risk/failure class. CloudWatch Logs receives JSON; a few metrics use embedded metric records or a small publisher adapter. OpenTelemetry spans may map ingress/planning/model/execution/reconciliation/delivery with stored root IDs and provider trace hints. No excessive dashboard suite or tracing service is required for correctness. A later single trace and log query must join the chain and demonstrate a failure; SDK-generated spans alone are not the business audit.
