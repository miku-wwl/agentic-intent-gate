# Minimum faithful Twilio reproduction — plan only

Study date: 2026-10-02. This is a **FUTURE REPRODUCTION PLAN**, not implementation or authorization to start Stage 1. Basis: AWS `fbea687d73f1ab9021793e96e778c2373f3266ff` and Python `5677eeb72a783de730923121f4ee6b01984faf09`. See [reference facts](twilio-reference-study.md) and [execution contracts](twilio-execution-flows.md).

Source notation: **A:** = `references/twilio-agent-connect-aws/`; **P:** = `references/twilio-agent-connect-python/`; **D:** = `references/_dependency-evidence/`. All proposed fakes below are transport/service stand-ins; none were built.

## What must exist

The smallest useful first vertical slice is **one SMS-like Conversation Orchestrator event**, through the real proxy/wrapper/channel/connector/Agent abstractions, with fake external services. A second message in that conversation proves Agent reuse; another conversation proves isolation. Voice should be a separate next slice because its ingress, IDs and lifetime differ.

Required logical boundaries:

1. Signed Function URL-shaped webhook input and Lambda proxy parsing/session selection.
2. AgentCore invocation client boundary carrying runtimeSessionId and the wrapper payload.
3. AgentCore application entrypoint boundary, retaining TACAgentCoreApp dispatch behavior.
4. TAC Core + MessagingChannel, with ConversationSession, participant reconciliation, context retrieval and callback.
5. StrandsConnector, with the context-taking Agent factory, conversation cache and first-memory invocation behavior.
6. Strands Agent and model-provider abstraction.
7. Outbound channel → Twilio Actions API boundary.
8. FileSessionManager storage location scoped to simulated runtime session, plus external conversation/Memory service state.

Evidence for these boundaries: `A:src/tac_aws/proxy/agentcore_lambda.py:_handle_conversation_webhook`; `A:src/tac_aws/server/agentcore_app.py:TACAgentCoreApp`; `P:src/tac/channels/messaging.py:_handle_communication_created/send_response`; `A:src/tac_aws/connectors/strands_connector.py:StrandsConnector`; `A:deploy/agentcore_aws_lambda/agent/main.py:create_agent`.

## Proposed local component map

~~~mermaid
flowchart LR
  H["Fixture: signed Twilio HTTP event"]
  L["Reference Lambda proxy"]
  AC["Fake AgentCore invocation transport\nruntimeSessionId preserved"]
  APP["Reference TACAgentCoreApp / local SDK"]
  TAC["Reference TAC + SMS channel"]
  API["Fake Twilio config/participants/Memory/Actions"]
  CON["Reference StrandsConnector"]
  AG["Strands Agent + FileSessionManager"]
  MOD["Deterministic fake model adapter"]
  DISK["Session-specific local directory"]
  H --> L --> AC --> APP --> TAC --> CON --> AG --> MOD
  TAC <--> API
  AG <--> DISK
  CON -->|"final response"| TAC
~~~

Local fake AgentCore must retain **the invocation boundary and session-ID routing contract**; directly calling the Agent from the ingress would skip the behavior under study. Multiple logical components may share a test process initially; no arbitrary service decomposition is needed. A local SDK server can provide /invocations and /ws, but it cannot reproduce AWS-managed isolation or IAM. `D:bedrock-agentcore-1.11.0/bedrock_agentcore/runtime/app.py:BedrockAgentCoreApp`.

A fake model inside Strands is more faithful than replacing StrandsConnector/Agent with unrelated conversation logic. A fake Bedrock endpoint could be a later transport-focused alternative if it supports the required ConverseStream wire protocol. Neither was created.

## Local reproducibility classification

Classes: **A** locally unchanged; **B** local with configuration only; **C** local with fake/mock adapter; **D** real behavior requires AWS; **E** real behavior requires Twilio; **F** unknown. “Can run” is based on code/dependency boundaries, not a Stage 0 execution result.

| Component | Class | What is feasible / dependency that remains | Source basis |
| --- | --- | --- | --- |
| Twilio event/model parsing and TwiML generation | A | Pure local model/format logic with fixtures and installed dependencies | `A:src/tac_aws/proxy/agentcore_lambda.py:_parse_form`; `P:src/tac/models/voice.py`; `P:src/tac/channels/voice/conversation_relay/twiml.py` |
| Twilio HTTP signature algorithm | B | Test URL/auth token and locally generated valid/invalid signatures | `A:src/tac_aws/proxy/validation.py`; `D:twilio-9.10.9/twilio/request_validator.py` |
| Full Lambda proxy | C | Handler locally invoked; fake AWS clients/credentials for construction, invocation and presigning | `A:src/tac_aws/proxy/agentcore_lambda.py:AgentCoreLambdaProxy.__init__` |
| Deployed Lambda Function URL/IAM | D | Managed invocation/auth/resource behavior is AWS-specific | `A:deploy/agentcore_aws_lambda/cdk/lib/lambda-stack.ts` |
| BedrockAgentCoreApp local ASGI | A | SDK routes/server can run locally once dependent handlers are safe | `D:bedrock-agentcore-1.11.0/bedrock_agentcore/runtime/app.py:run` |
| TACAgentCoreApp | C | Same wrapper but TAC network dependencies must be supplied/faked | `A:src/tac_aws/server/agentcore_app.py` |
| Managed AgentCore routing/isolation/session storage | D | Local per-ID process/directory simulation cannot prove AWS microVM/storage behavior | `A:deploy/agentcore_aws_lambda/cdk/agentcore/agentcore.json` |
| TACConfig validation / channel models / task manager | A | Pure local data/defaults/cancellation with fixtures | `P:src/tac/core/config.py`; `src/tac/session/` |
| Full TAC initialization | C | get_configuration is a synchronous Twilio call when Orchestrator configured | `P:src/tac/core/tac.py:TAC.__init__` |
| SMS/channel routing + reconciliation + Recall + delivery | C | Fake configuration/participants/profile/communications/Actions responses; record side effects | `P:src/tac/channels/messaging.py`; `src/tac/context/` |
| Twilio Conversation Orchestrator / Memory server behavior | E | Real grouping/retention/permission/delivery behavior is not in local client code | `P:src/tac/context/conversation.py`; `memory.py` |
| StrandsConnector | C | Connector unchanged around real TAC/Agent; external model/service boundaries faked | `A:src/tac_aws/connectors/strands_connector.py` |
| Strands Agent with deterministic Model | C | Retains inference/message/session hooks with fake model events | `D:strands-agents-1.41.0/strands/agent/agent.py` |
| FileSessionManager | B | Writable local storage_dir and fixed conversation IDs; inspect real files/restore | `D:strands-agents-1.41.0/strands/session/file_session_manager.py` |
| BedrockModel/Amazon Nova inference | D | Real model access requires AWS credentials/IAM/region/model availability; fake adapter may replace it locally | `A:deploy/agentcore_aws_lambda/agent/main.py:create_agent` |
| Secrets fetch at entrypoint import | C | Fake get_secret_value or explicit nonsecret test config boundary | `A:deploy/agentcore_aws_lambda/agent/tac_config.py`; `lambda/credentials.py` |
| Voice JSON loop/cancellation/output | C | Fake WebSocket and fake CO lookup; same CallSid/conversation-ID distinction | `P:src/tac/channels/voice/conversation_relay/provider.py` |
| ConversationRelay ASR/TTS and live call delivery | E | Fake text frames cannot prove telephony/speech behavior | Same provider / TwiML source |
| Local FastAPI alternative | C | Can be served locally, but startup configuration and channel APIs still require fake clients | `P:src/tac/server/fastapi_server.py`; `A:src/tac_aws/server/fastapi_server.py` |
| Exact pinned deployment/dependency compatibility | F | Manifest/lock mismatch must be understood before choosing an executable package set | `A:deploy/agentcore_aws_lambda/agent/pyproject.toml; A:deploy/agentcore_aws_lambda/agent/uv.lock` |
| Cloud trace/metrics export | D/F | Framework can generate local spans; actual AWS exporter/runtime result remains unverified | External CDK Runtime and Strands/AgentCore SDK source |

No Docker dependency is needed for the initial fake-service study. If packaging is studied later, use newly built artifacts within this workspace; do not reuse images/caches from other projects.

## What stays faithful

| Preserve | Reason / reference evidence |
| --- | --- |
| Lambda event and response shapes; original JSON body in wrapper payload | Proxy behavior and errors are part of the architecture: `A:src/tac_aws/proxy/agentcore_lambda.py` |
| Signature validation before routing | Actual ingress trust boundary: `A:src/tac_aws/proxy/validation.py` |
| runtimeSessionId separate from user/profile identity | Routing is conversation-based for messaging and CallSid-based for voice |
| TAC callback arguments and str-or-None contract | `P:src/tac/core/tac.py:trigger_message_ready` |
| Participant reconciliation before model and participant-based outbound Action | Channel performs external side effects before/after inference: `P:src/tac/channels/messaging.py` |
| Per-conversation Agent factory/cache and separate first-memory invocation | `A:src/tac_aws/connectors/strands_connector.py:_handle_message` |
| Complete-string Strands response delivery | Model streaming must not be confused with this connector's output |
| FileSessionManager hooks, actual file structure and same-ID restore | `D:strands-agents-1.41.0/strands/session/` |
| Bounded local deduplication, not an invented durable ledger | `P:src/tac/channels/base.py:_is_duplicate_webhook` |
| Reference error acknowledgement and cleanup behavior | Expose the documented gaps in reproduction; changing them immediately would obscure fidelity |
| Voice setup/prompt/interrupt/disconnect and separate IDs | `P:src/tac/channels/voice/conversation_relay/provider.py` |

The fake service must not silently make acknowledgements, idempotency or recovery stronger than the reference. A first reproduction should be able to demonstrate swallowed errors, retained voice conversation state and token-loss behavior as limitations.

## What can initially be fake

- **Twilio ingress:** signed fixtures for COMMUNICATION_CREATED and CONVERSATION_UPDATED, and later voice form/setup/prompt/interrupt frames.
- **AgentCore service transport:** a local invocation adapter recording runtimeSessionId and forwarding the unchanged wrapper payload to the actual app handler. It can route to simulated per-ID environments without claiming AWS isolation.
- **Twilio API responses:** fixed configuration with memory_store_id; participants/profile/Recall/history replies; an Actions receiver recording payloads.
- **Bedrock:** deterministic Strands-compatible model output/events/errors. Nova remains the eventual real-model binding, not something a fake can prove.
- **Secrets/AWS credentials:** dummy values and mocked fetch/client construction; no user credentials or cloud metadata endpoint needed.
- **Managed mount:** an explicitly local per-runtime-session directory; it models file retention under chosen scenarios only.

There is no external S3 session repository to replace in the primary path. Its external storage boundary is the AgentCore managed mount beneath FileSessionManager; Twilio remote Memory is a separate boundary. `A:deploy/agentcore_aws_lambda/cdk/agentcore/agentcore.json`; `A:deploy/agentcore_aws_lambda/agent/main.py:create_agent`.

## Minimum vertical slice and required state

The first slice's input is one valid signed messaging webhook with a stable conversation ID/token and text, resolvable customer/AI_AGENT participants, and either predictable Recall or a deliberate Communications fallback. Its observable output is **an outbound Action payload**, not just Lambda200 or an Agent string.

State required for that slice:

| State | Minimum purpose |
| --- | --- |
| Fake external conversation/participant/profile records | Match lookup/reconcile/response contract |
| TAC channel local ConversationSession | Author/agent IDs, metadata and memory/cache behavior |
| Per-channel token OrderedDict | Demonstrate same-token suppression and known limitations |
| Connector Agent map + initialized_sessions | Prove reuse and initial memory behavior |
| Strands history/agent files | Inspect first/second turn and recreate Agent with saved messages |
| Simulated runtime session mapping + session storage directory | Keep routing identity separate from the file-session ID |
| Recorded outbound Actions | Verify actual response boundary and failure behavior |

ConversationSession/Agent objects are not a replacement for live Python task execution recovery. A restart test can prove saved-history reconstruction locally; it cannot establish AWS host/session/storage guarantees.

Minimum acceptance observations for later implementation:

1. Valid webhook traverses every boundary and records the expected outbound Action.
2. Second message with new token reuses that conversation's Agent/history.
3. Separate conversation uses a separate Agent and history.
4. Same retained token suppresses a repeat; missing/new token permits behavior observed in source.
5. A known processing failure reproduces the actual acknowledgement/error behavior.
6. Recreate Agent against retained session files; separately remove/use a fresh directory and observe new history.
7. Voice slice subsequently verifies CallSid → CO ID, completed text response, task cancellation and disconnect retention.

These are plan criteria, not tests written or results obtained.

## Recommended order, with the Stage 0 boundary intact

1. Resolve the exact package/source compatibility question using the two required checkouts and declared metadata; record the chosen reproducible environment. Preserve stale lockfiles as evidence rather than silently updating them.
2. Establish local fixture/service boundaries and telemetry disabled for the experiment.
3. Exercise Lambda signature/parsing/session-selection contract with fake AWS transport.
4. Run the TACAgentCoreApp → SMS → reconciliation/context → Strands → fake model → outbound Actions vertical slice.
5. Inspect conversation-specific Agent reuse, memory injection and FileSessionManager reconstruction.
6. Exercise the source's error/duplicate/concurrency limits without adding new mechanisms.
7. Add the separate voice protocol slice, preserving the routing mismatch as a documented reference finding.
8. Only in a separately authorized cloud phase, verify official CodeZip/Lambda/Secrets/IAM deployment, Nova, managed storage and Twilio delivery.

The current task ends at this document. None of these steps authorizes starting Stage 1.

## What eventually requires AWS and Twilio

Real AWS reproduction requires Lambda Function URL, AgentCore Runtime/session storage, Bedrock model access, IAM roles and Secrets Manager. The official deployment additionally uses CDK/CloudFormation/S3 assets and logs/framework tracing. The selected CodeZip AgentCore path does not require the Fargate image infrastructure. `A:deploy/agentcore_aws_lambda/cdk/` and inspected external AgentCoreRuntime/AgentEnvironment constructs.

Real Twilio reproduction requires configured voice/messaging ingress, ConversationRelay for live voice, Conversation Orchestrator configuration/participants/Actions, a Memory store and credentials/channel resources. Fakes only verify client contracts.

## Incidental details and what should not be copied

Incidental details include demo greeting/prompt wording, stack names, example phone/account values, deployment operator profile names and choosing an OS-specific local directory to stand in for the managed mount. Preserve the architectural function rather than blindly copying these values.

Do not copy credentials, example permissions as a proven production policy, README timing claims as guarantees, unrelated alternative agent lifecycles, optional OpenAI providers/tools into the main Strands slice, or transient state under the name “durable memory.” Do not treat the stale dependency lock as proof of the packaged source.

No final business domain, Temporal/LangGraph/MCP architecture, workflow scheduler, durable state machine, approval engine, database redesign or detailed hardening mechanism belongs in this plan. In the selected reference these are **Not implemented in reference.** The [gap register](twilio-gap-register.md) records questions without designing fixes.
