# Twilio reference study — Stage 0

Study date: **2026-10-02, Pacific/Auckland**. Scope: reference source inspection only. No application implementation, deployment, AWS/Twilio API execution, commit, or push.

## Evidence and provenance

| Repository | Expected SHA | Actual checkout SHA | Role |
| --- | --- | --- | --- |
| [twilio/twilio-agent-connect-aws](https://github.com/twilio/twilio-agent-connect-aws/tree/fbea687d73f1ab9021793e96e778c2373f3266ff) | `fbea687d73f1ab9021793e96e778c2373f3266ff` | `fbea687d73f1ab9021793e96e778c2373f3266ff` | AWS adapters, Lambda proxy, Strands connector, AgentCore application and deployment example |
| [twilio/twilio-agent-connect-python](https://github.com/twilio/twilio-agent-connect-python/tree/5677eeb72a783de730923121f4ee6b01984faf09) | `5677eeb72a783de730923121f4ee6b01984faf09` | `5677eeb72a783de730923121f4ee6b01984faf09` | TAC Core, channel protocols, Twilio API clients, session/task management, optional tools |

Both repositories were cloned from these URLs under `references/`, checked out detached, and verified with `git rev-parse HEAD`. Their tracked working trees were preserved. Every source identifier below refers to these commits, not moving branches.

Evidence notation:

- **REFERENCE FACT**: directly established by these source trees.
- **DEPENDENCY FACT**: inspected external package source, with version and artifact integrity recorded in `references/_dependency-evidence/manifest.json`. Provided by that library, not implemented directly by either reference repository.
- **SERVICE DOCUMENTATION**: current official service documentation; does not prove an actual deployment.
- **INFERENCE**: consequence of identified code paths, explicitly distinguished from a runtime guarantee.
- **UNKNOWN — requires further verification.**: not established by the study.
- **FUTURE REPRODUCTION PLAN**: confined to [twilio-reproduction-plan.md](twilio-reproduction-plan.md); no final-project hardening design.

For concise source identifiers, **A:** means `references/twilio-agent-connect-aws/`; **P:** means `references/twilio-agent-connect-python/`; **D:** means `references/_dependency-evidence/`. Thus `A:src/tac_aws/proxy/agentcore_lambda.py:AgentCoreLambdaProxy.lambda_handler` names an exact repository, file and function. [The source map](twilio-source-map.md) expands the critical locations.

### Dependency versions are not interchangeable

| Evidence | Versions / behavior | Consequence |
| --- | --- | --- |
| `A:pyproject.toml` | AWS package 2.0.0; TAC >=2.3.0,<3; optional Strands >=1.30.0 and AgentCore SDK >=1.4.0 | Manifests alone do not uniquely resolve all dependencies |
| `P:pyproject.toml` | TAC 2.5.0 | The required supporting checkout is a source study target, not automatically what a PyPI install selects |
| `A:uv.lock` | TAC 2.3.0; Strands 1.38.0; AgentCore SDK 1.8.0; boto3/botocore 1.43.6; Twilio 9.10.5 | Library-development snapshot |
| `A:deploy/agentcore_aws_lambda/agent/pyproject.toml` | AWS package ==2.0.0; Strands >=1.40.0 | Runtime example manifest differs from its lock |
| `A:deploy/agentcore_aws_lambda/agent/uv.lock` | AWS package 1.1.0; TAC 1.0.1; Strands 1.41.0; AgentCore SDK 1.11.0; boto3/botocore 1.43.15; Twilio 9.10.9 | Stale TAC resolutions relative to current manifests; not evidence that the required two commits were packaged together |
| `A:deploy/agentcore_aws_lambda/lambda/requirements.txt` | AWS package [agentcore]==2.0.0, transitive dependencies not exactly pinned | Lambda package selection is distinct again |
| `A:deploy/agentcore_aws_lambda/cdk/package-lock.json` | @aws/agentcore-cdk 0.1.0-alpha.35 | Inspected deployment construct snapshot |

External source inspection used Strands 1.41.0, AgentCore SDK 1.11.0 (runtime-lock snapshot), SDK 1.8.0 (library-lock snapshot), boto3/botocore 1.43.15 and Twilio 9.10.9. Downloaded artifacts match the lockfile hashes. They were extracted for reading, not installed or executed. This is **not** a substitution for either requested repository, nor proof of the eventual deployed dependency set.

The Makefile runs `uv sync --frozen` for the agent. The CDK dependency independently packages with `uv pip install -r <pyproject> --target ...`, without a frozen lockfile flag. Therefore the local build environment and deployment archive need not contain the same dependency versions. **REFERENCE/DEPENDENCY FACT:** `A:deploy/agentcore_aws_lambda/Makefile:build`; `D:agentcore-cdk-0.1.0-alpha.35/package/dist/lib/packaging/python.js:PythonCodeZipPackagerSync.installWithRetriesSync`. Actual resolution/build success: **NOT VERIFIED**.

## Architecture and ownership

**REFERENCE FACT:** the primary example creates TAC, StrandsConnector and TACAgentCoreApp at module import. The connector creates an agent lazily for each conversation; the Lambda module creates a reusable proxy after loading its secret. `A:deploy/agentcore_aws_lambda/agent/main.py:create_agent/module initialization`; `A:deploy/agentcore_aws_lambda/lambda/index.py:module initialization`.

~~~mermaid
flowchart LR
  T["Twilio: voice / Conversation Orchestrator"]
  L["Lambda Function URL\nsignature validation + routing"]
  R["AgentCore Runtime\nHTTP /invocations and WebSocket /ws"]
  C["TACAgentCoreApp\nAWS repository"]
  TAC["TAC channels + API clients\nPython repository"]
  S["StrandsConnector\nAWS repository"]
  AG["Strands Agent + BedrockModel\nexternal Strands SDK"]
  B["Bedrock Runtime\nAmazon Nova Pro"]
  M["Twilio Conversation Memory"]
  F["FileSessionManager\n/mnt/workspace/.sessions"]
  T -->|"HTTP webhook"| L
  L -->|"messaging: invoke_agent_runtime"| R
  L -->|"voice: XML with presigned WSS URL"| T
  T -->|"voice: direct ConversationRelay WebSocket"| R
  R --> C --> TAC --> S --> AG --> B
  AG <--> F
  TAC <-->|"participants / Recall / Actions"| T
  TAC <--> M
  AG -->|"completed response"| S
  S -->|"channel.send_response"| TAC
~~~

This is supported by `A:src/tac_aws/proxy/agentcore_lambda.py:_handle_voice_twiml/_handle_conversation_webhook`, `A:src/tac_aws/server/agentcore_app.py:TACAgentCoreApp.__init__`, `A:src/tac_aws/connectors/strands_connector.py:StrandsConnector._handle_message` and `P:src/tac/channels/messaging.py:MessagingChannel.send_response`.

| Component | Responsibility in this reference |
| --- | --- |
| Twilio platform | Originates signed HTTP webhooks; ConversationRelay exchanges voice protocol frames; Conversation Orchestrator owns external conversation/participant resources; Memory API supplies external context. Server internals are outside the clones |
| Lambda ingress | Validates Twilio signatures; parses form/JSON; generates signed voice connection URL/TwiML; forwards selected messaging events to AgentCore. No LLM agent or application tool logic |
| AgentCore managed service | AWS-authenticated invocation/connection boundary and runtime-session routing; configured managed storage mount. Service scheduling, host replacement and crash recovery are external |
| External AgentCore SDK | ASGI app, /invocations, /ping, /ws, handler registration and server execution |
| TACAgentCoreApp (AWS repo) | Converts forwarded payload to webhook; dispatches messaging channels concurrently; wraps voice socket to emit a greeting |
| TAC Core (Python repo) | Validates config, discovers Twilio configuration/Memory store, retrieves context, owns callbacks |
| TAC channels (Python repo) | Maintain local ConversationSession records; reconcile participants; apply token deduplication; voice task/socket lifecycle; protocol-specific response delivery |
| StrandsConnector (AWS repo) | Owns conversation-to-Agent cache, invokes application factory, injects first-turn memory, awaits Agent, routes response, cleans cache on ended callback |
| Strands SDK | Agent inference loop, message history, hooks, session-file persistence, model retries and per-Agent concurrent-invocation guard |
| BedrockModel (Strands SDK) | Formats Converse messages and executes Bedrock ConverseStream by default |

Sources: `A:src/tac_aws/proxy/agentcore_lambda.py:AgentCoreLambdaProxy`; `A:src/tac_aws/server/agentcore_app.py:TACAgentCoreApp`; `P:src/tac/core/tac.py:TAC`; `P:src/tac/channels/base.py:BaseChannel`; `P:src/tac/channels/voice/conversation_relay/provider.py:ConversationRelayProvider`; `D:bedrock-agentcore-1.11.0/bedrock_agentcore/runtime/app.py:BedrockAgentCoreApp`; `D:strands-agents-1.41.0/strands/agent/agent.py:Agent`.

### Other examples are distinct architectures

`A:src/tac_aws/connectors/bedrock_connector.py:BedrockConnector._build_invoke_fn_from_config` uses Bedrock **Agents** `invoke_agent`, not the main Nova model route. `A:src/tac_aws/connectors/bedrock_agentcore/connector.py:BedrockAgentCoreConnector._handle_message` and its `http.py/websocket.py` helpers implement a TAC frontend calling a separate AgentCore agent. The Fargate version's `A:deploy/agentcore_aws_fargate/agent/agent.py` creates one module-level Agent and exposes token streams. Its agent lifecycle and streaming behavior must not be attributed to the Lambda + TACAgentCoreApp example.

The source structure, infrastructure files, packaging and tests are catalogued in [twilio-source-map.md](twilio-source-map.md). OpenAI media providers and optional TAC tools in the Python repository are supporting library surfaces, not active components of the chosen deployment.

## Execution, Lambda and runtime lifecycle

### Lambda contract

**REFERENCE FACT:** `A:deploy/agentcore_aws_lambda/lambda/index.py:lambda_handler` aliases `AgentCoreLambdaProxy.lambda_handler`. Inputs resemble Lambda Function URL events: `headers`, `rawPath`, `rawQueryString`, `body`, `isBase64Encoded`. Output is `{statusCode, headers, body}`; body is a JSON or XML string.

1. `TwilioSignatureValidator.validate` lowercases headers, reconstructs `https://<host><rawPath>?<rawQueryString>`, base64-decodes the body, preserves blank form values for validation and delegates to Twilio `RequestValidator`.
2. Invalid/missing signatures return 403. Validation runs **outside** downstream handlers' try/except blocks; unexpected validator errors can escape the Lambda handler.
3. `rawPath.startswith("/twiml")` parses a voice form; missing CallSid returns 400. Generates a URL with session_id=CallSid, expires=300 and layers TwiML options. Returns 200/application/xml.
4. `rawPath.startswith("/webhook")` parses JSON. Only COMMUNICATION_CREATED and CONVERSATION_UPDATED proceed. Other events return 200 with ignored=true.
5. The messaging payload is UTF-8 JSON bytes: `{"webhook_data": "<original JSON body>", "idempotency_token": "<header or null>"}`. Routing uses data.conversationId or data.id as `runtimeSessionId`. It synchronously calls boto3 `invoke_agent_runtime`.
6. The invocation response body is not read or interpreted. Lambda returns 200/success=true after the SDK call returns. This is not a delivery receipt.
7. Invalid encoding/JSON/missing conversation ID return 400; downstream errors in voice/messaging handlers log exceptions and return generic 500. Unknown paths return 404 after validation.

Evidence: `A:src/tac_aws/proxy/agentcore_lambda.py:lambda_handler/_handle_voice_twiml/_build_twiml_options/_resolve_per_call_options/_handle_conversation_webhook`; `A:src/tac_aws/proxy/validation.py:TwilioSignatureValidator.validate`.

Lambda does application-layer webhook authentication, parsing/protocol conversion, session selection, AWS invocation or presigning, and HTTP/XML error translation. It has no business agent logic, no explicit retry loop, no durable processed-event record, and no end-user authorization policy. AWS IAM authorizes its service calls. Region resolution is explicit argument > AWS_REGION > boto3 session, otherwise ValueError. Secrets are loaded at module import; warm reuse retains the proxy/client/token but is not a session-history mechanism.

### AgentCore and TAC application

**REFERENCE FACT:** `A:src/tac_aws/server/agentcore_app.py:TACAgentCoreApp.__init__` creates `BedrockAgentCoreApp()` and registers two nested functions:

- `http_handler(payload)` parses webhook_data, passes idempotency_token to every configured **messaging** channel via `asyncio.gather`, then returns `{"status":"ok"}`.
- `_process_webhook` catches/logs an individual channel failure without rethrowing. Consequently even channel processing failures can produce status=ok.
- Outer parsing/dispatch failures return a regular dict `{"status":"error","message":"Internal server error"}`, not an explicit HTTP error response.
- `websocket_handler(websocket, context)` wraps the socket and calls `VoiceChannel.handle_websocket`. It does not use context.session_id to choose a TAC session.
- `TACAgentCoreWebSocketAdapter.receive_json` emits one `{"type":"text","token":"Hello! How can I assist you today?","last":true}` on the first setup frame, before returning that frame. Greeting send failure is logged; setup processing continues.

**DEPENDENCY FACT:** SDK 1.11.0 `D:bedrock-agentcore-1.11.0/bedrock_agentcore/runtime/app.py:BedrockAgentCoreApp.__init__/_handle_invocation/_handle_websocket/_invoke_handler/run` registers POST /invocations, GET /ping and WebSocket /ws. HTTP async handlers run on a dedicated worker event loop; WebSocket handlers run directly on the ASGI loop. Returned dicts become JSON responses with the ordinary successful status. Runtime session headers enter SDK request context. `run` calls uvicorn on port 8080 with no explicit multi-process worker count from the application.

**INFERENCE:** the same imported TAC/connector objects can be retained across invocations in a surviving process. The application does not start a fresh Agent per HTTP request. Actual managed-service session placement, process/container reuse intervals, configured region support and lifecycle timing are **UNKNOWN — requires further verification**; no live runtime was invoked. HTTP and WebSocket access can touch shared channel/connector objects from different event loops; cache_lock is an asyncio lock, not a cross-loop/global transaction.

## Agent, Strands and model

**REFERENCE FACT:** `A:deploy/agentcore_aws_lambda/agent/main.py:create_agent` supplies:

- `BedrockModel(model_id="amazon.nova-pro-v1:0")`; no explicit region, inference parameters, endpoint, guardrail or client retry settings.
- A customer-service system prompt requiring short conversational replies.
- `FileSessionManager(session_id=context.conversation_id, storage_dir="/mnt/workspace/.sessions")`.
- No tools, custom hooks/callbacks, initial messages, or application state values.

**REFERENCE FACT:** `A:src/tac_aws/connectors/strands_connector.py:StrandsConnector.__init__/_handle_message/_handle_conversation_ended` stores factory-created Agents in `conversation_agents[conversation_id]`. Classification: **created per conversation, cached and reused within the process**. After process loss a subsequent message recreates it. Ended callback calls Agent.cleanup(), deletes the cache entry and discards initialized_sessions membership. No per-request factory call if an Agent already exists.

The connector invokes `agent.invoke_async` and converts the final result with `str(result)`. It never calls `agent.stream_async` in this slice. TAC voice can send token streams, but this connector sends one completed string. Model-level streaming is therefore different from streaming Twilio responses.

Memory injection occurs once per connector-cached conversation: add the conversation to `initialized_sessions`, build `MemoryPromptBuilder.build(memory_response, context)`, and, if nonempty, call Agent with that text **before** the user's message. The memory-only result is ignored but its invocation affects Agent history. Marking occurs before the await; a failed injection is not automatically retried next turn. SMS retrieves memory on every turn yet this connector only injects it on its first initialized turn. The initialized flag is not persisted with the Agent files and resets on process restart.

**DEPENDENCY FACT:** `D:strands-agents-1.41.0/strands/agent/agent.py:Agent.__init__/invoke_async/stream_async` gives the default Agent ID `default`, mutable messages/state, a default PrintingCallbackHandler, a conversation manager, session hooks and retry hooks. `invoke_async` drains `stream_async` and returns AgentResult. No application tool is registered by the primary factory.

### Bedrock boundary

**DEPENDENCY FACT:** `D:strands-agents-1.41.0/strands/models/bedrock.py:BedrockModel.__init__/_format_request/stream/_stream`:

| Item | Inspected dependency behavior |
| --- | --- |
| SDK | boto3 `bedrock-runtime` client, botocore signing/config |
| API | `converse_stream` when streaming is unset (default true); `converse` if explicitly false. No `InvokeModel` call in this model path |
| Request | modelId, messages (`role/content` blocks), system blocks, inferenceConfig; optional toolConfig/guardrails/additional fields when configured |
| Response | AWS event stream events, normalized by Strands inference loop; final AgentResult string routed by connector |
| Async boundary | boto3 streaming operation runs in a worker thread via asyncio.to_thread; events cross an asyncio queue |
| Region | Explicit region_name > boto session region > AWS_REGION > us-west-2. Factory does not pass region_name; deployment injects AWS_REGION |
| Credentials | Default boto3 Session credential resolution; intended deployed runtime execution role. No application access key supplied to BedrockModel |
| Timeout | Default Strands read timeout 120 seconds; botocore connect timeout default 60 seconds in inspected legacy defaults. No end-to-end turn deadline in reference application |
| Retry | Botocore request retries plus Strands throttling retry hook, detailed below |

IAM action names `bedrock:InvokeModel`/`InvokeModelWithResponseStream` in the deployment role are permissions; they do not establish that Python calls those API methods. The downloaded CDK construct grants these actions plus CountTokens. `D:agentcore-cdk-0.1.0-alpha.35/package/dist/cdk/constructs/components/primitives/runtime/AgentCoreRuntime.js:AgentCoreRuntime.constructor`.

The inspected Strands loop estimates input tokens before inference. BedrockModel.count_tokens only invokes Bedrock CountTokens when use_native_token_count is explicitly true; this factory does not enable it, so the default path uses the framework estimator. Permission to CountTokens does not mean this example calls that API. `D:strands-agents-1.41.0/strands/event_loop/event_loop.py:_estimate_input_tokens/_handle_model_execution`; `D:strands-agents-1.41.0/strands/models/bedrock.py:count_tokens`.

## Tools and side effects

**Primary Lambda/AgentCore Agent tools:** **Not implemented in reference.** No `tools=` argument in `A:deploy/agentcore_aws_lambda/agent/main.py:create_agent`.

**An optional tool does exist in the AWS library:** `A:src/tac_aws/tools/strands/memory.py:create_memory_tool/retrieve_twilio_memory` uses Strands `@tool`, receiving required profile_id:string, optional conversation_id:string|null and query:string|null. Strands derives tool metadata/schema from annotations/docstring. No custom ID-format or query-length check is performed in this wrapper. Downstream `P:src/tac/models/memory.py:MemoryRetrievalRequest` validates supplied query length1–1024 and numeric limits; profile/conversation TTID formatting is not validated here. The wrapper calls MemoryClient.retrieve_memory, returns model_dump(by_alias=true, exclude_none=true), and wraps exceptions that escape the client as ValueError. Recall HTTP/parsing errors are caught inside MemoryClient and return an empty response, so they do not necessarily produce a tool error. Network authentication is the client's Twilio API key/secret; side effect is a read request. Tested with mocked MemoryClient in `A:tests/test_memory_tool.py:TestMemoryTool`. It is not registered in the primary Agent.

The supporting Python library provides optional framework-neutral TACTool objects with OpenAI/Realtime/Anthropic schema formats and a lazy OpenAI Agents SDK adapter. They are not automatically registered as Strands tools and are **not imported or enabled by the primary main.py**. `P:src/tac/tools/base.py:TACTool/function_tool` derives visible schemas using TypeAdapter while hiding Annotated injected dependencies; configure_injection validates injected values. Direct __call__ merges arguments and invokes the implementation, without a general validation pass over model-provided values.

| Optional tool | Source / schema and registration | Output / errors / side effects / authentication |
| --- | --- | --- |
| Memory | `P:src/tac/tools/memory.py:create_memory_tool/retrieve_profile_memory`; visible query:string, injected MemoryClient/profile_id from session | Recall response model_dump; exceptions propagate; read-only external Memory request using API key/secret. No profile creation by this tool |
| Knowledge | `P:src/tac/tools/knowledge.py:create_knowledge_tool/search_knowledge`; visible query:string; injected client/base ID/top_k (default5). Factory async, fetches metadata unless name and description both provided | List of KnowledgeChunkResult; HTTP errors propagate. Metadata/search reads with API key/secret; docstring max query length not explicitly enforced by wrapper |
| Studio handoff | `P:src/tac/tools/handoff.py:create_studio_handoff_tool/_handoff_impl`; visible reason:string, injected TAC/session/static attributes; factory requires flow SID, CO and Memory store | Attempts INACTIVE and clearing callbacks (logs failures); voice sets pending_handoff_data for later socket end; digital sends Basic-auth Studio form POST and returns handoff_failed/error on send failure; otherwise handoff_initiated |

No caller ownership policy is added by these factories. Handoff state exists on ConversationSession and voice delivery supports it; that does not make handoff a configured main-path capability. Tests are in `P:tests/test_tools.py; P:tests/test_injectable_tool_args.py; P:tests/test_handoff.py; P:tests/test_knowledge.py`.

Non-tool side effects **are** present in the primary messaging path: participant promotion/addition, optional customer profile creation, and outbound Actions API requests. `P:src/tac/channels/messaging.py:_reconcile_participants/_resolve_customer_profile/_promote_participant/_add_agent_participant/send_response`. These happen in channel preparation/delivery, not model-generated tool calls.

## Session and identity model

| Stage | ID origin and use | Evidence |
| --- | --- | --- |
| Voice Lambda | Twilio form CallSid; required; used unchanged for presigned runtime session | `A:src/tac_aws/proxy/agentcore_lambda.py:_handle_voice_twiml` |
| Voice connection | SDK puts runtime session ID into signed URL query; AWS service validates presigned request | `D:bedrock-agentcore-1.8.0/bedrock_agentcore/runtime/agent_core_runtime_client.py:generate_presigned_url` |
| Voice TAC setup | SetupMessage.callSid; CO lookup channelId=CallSid/status=ACTIVE resolves exactly one conversation; TAC session uses returned conversation.id and records call_sid separately | `P:src/tac/channels/voice/conversation_relay/provider.py:handle_websocket/_initialize_conversation` |
| Messaging Lambda | COMMUNICATION_CREATED.data.conversationId or CONVERSATION_UPDATED.data.id | `A:src/tac_aws/proxy/agentcore_lambda.py:_handle_conversation_webhook` |
| Messaging runtime | Same conversation ID becomes runtimeSessionId; wrapper does not explicitly verify equality against an SDK context | `A:src/tac_aws/server/agentcore_app.py:http_handler` |
| TAC/Strands | ConversationSession.conversation_id keys local session, Agent cache and FileSessionManager directory | `P:src/tac/channels/base.py:_start_conversation`; `A:src/tac_aws/connectors/strands_connector.py:_handle_message`; `A:deploy/agentcore_aws_lambda/agent/main.py:create_agent` |
| User/profile | Caller address and participant/profile IDs are session attributes for Memory lookup; they are not the Agent cache key | `P:src/tac/models/session.py:ConversationSession`; `P:src/tac/core/tac.py:retrieve_memory` |

There is no application-generated session UUID in these primary paths. The SDK can generate a UUID when its session_id argument is omitted, but Lambda supplies CallSid. Relay-only TAC library mode uses CallSid as conversation_id; the primary configuration sets conversation_configuration_id, so it uses Orchestrator mode.

**INFERENCE:** one address can participate in multiple calls/conversations; the source does not enforce one session per user. Conversely all participants in one conversation select the same Agent cache entry. External Twilio grouping policy determines conversation creation/reuse; it is not an application user-session database.

**Critical routing gap:** voice runtime-session ID (CallSid) and TAC conversation ID differ. A CONVERSATION_UPDATED webhook is routed by Lambda to the conversation-ID runtime session. Additionally TACAgentCoreApp only forwards HTTP events to messaging_channels, not voice_channel. VoiceChannel.process_webhook can handle CLOSED/INACTIVE, but this primary wrapper never calls it. ConversationRelayProvider._cleanup_connection deliberately retains orchestrated voice ConversationSession until CLOSED. **INFERENCE:** normal voice ended cleanup/Agent-cache eviction cannot be assumed in this deployment wiring. Evidence: `A:src/tac_aws/server/agentcore_app.py:http_handler`; `P:src/tac/channels/voice/channel.py:process_webhook`; `P:src/tac/channels/voice/conversation_relay/provider.py:_cleanup_connection`.

## State inventory and memory

| State | Owner / location / classification | Write → read paths | Lifetime, persistence and recovery |
| --- | --- | --- | --- |
| Raw event/form/decoded payload | Lambda / request-local | handler parsing → routing/presign | Gone after invocation; no journal |
| Twilio token/proxy/AWS clients | Lambda module / process-local | Secrets get at import → validator/invocation | Warm reuse; cold start reloads; no explicit refresh |
| TAC config/API clients/callbacks | Runtime module / process-local | main.py/TAC.__init__ → channel operations | Reconstructed at process start; TAC init synchronously contacts Twilio configuration API |
| Channel _conversations / ConversationSession | Each TAC channel / session-local objects in process memory | _start_conversation and reconciliation → memory/delivery/end | Lost on process restart; not serialized by FileSessionManager |
| Participant/profile/metadata/cache/handoff fields | ConversationSession / session-local, process-local | reconciliation/retrieve_memory/cache operations → responses | External records may be queried again; local cache/locks/handoff disappear |
| Dedup tokens | Each BaseChannel OrderedDict / process-local | _is_duplicate_webhook inserts before processing → duplicate checks | Bounded capacity; lost on restart; no disk persistence |
| Socket routing/setup greeting flag | Provider WebSocketManager and socket adapter / connection/process-local | add_websocket/setup flag → send_response | Removed on disconnect; connection cannot resume from file state |
| Voice tasks/session map | ThreadSafeSessionManager + SessionState / session/process-local | get_or_create_session/stream_task → prompt/interrupt/cleanup | Map protected with RLock; cancelled and removed on disconnect; no persisted task |
| Agent object/messages/model state/retry counters | Strands Agent / session-local objects in process | invoke_async/inference loop → next model request | Cached by connector; file hooks persist selected messages/agent state, not live execution |
| initialized_sessions | StrandsConnector / process-local | _handle_message marks → first-turn injection check | Lost on restart; not restored from session files |
| Session metadata/agent snapshots/message JSON | Strands FileSessionManager / filesystem, session-local external managed mount in deployment | session hooks append/sync → manager initialize/read/list_messages | Persisted files; retention depends on configured filesystem. No whole-turn transaction |
| LLM context request/event queues/results | Strands/Bedrock / invocation-local plus Agent history | _format_request/stream → model and result | Context sent again from messages; no evidence of model-owned durable user memory |
| Twilio conversation/participants/actions | Twilio service / external | ConversationClient APIs → lookup/reconcile/send | Server-owned persistence; retention/atomicity/recovery guarantees not established by local source |
| Twilio Memory profiles/observations/summaries | Twilio Memory service / external | MemoryClient APIs → TAC.retrieve_memory/Recall | Actual remote Memory feature, not just a Python list; service retention guarantees unverified |
| Secrets resource/deployment config | AWS Secrets Manager/CDK files / external or workspace configuration | secret-update → get_secret_value | Independent of runtime process; deployed resource deletion policy DESTROY |

Sources for rows: `A:deploy/agentcore_aws_lambda/lambda/index.py`; `A:deploy/agentcore_aws_lambda/agent/main.py`; `P:src/tac/channels/base.py:BaseChannel`; `P:src/tac/models/session.py:ConversationSession`; `P:src/tac/channels/websocket_manager.py:WebSocketManager`; `P:src/tac/session/thread_safe.py:ThreadSafeSessionManager`; `P:src/tac/session/state.py:SessionState`; `A:src/tac_aws/connectors/strands_connector.py:StrandsConnector`; `P:src/tac/context/conversation.py:ConversationClient`; `P:src/tac/context/memory.py:MemoryClient`; `D:strands-agents-1.41.0/strands/session/session_manager.py:SessionManager.register_hooks`.

### Five different meanings of memory

1. **LLM context:** system + message content formatted into a Bedrock request.
2. **Conversation history:** Strands Agent.messages, with FileSessionManager message records.
3. **Session state:** TAC participant/routing/profile/cache/task/socket data; mostly process memory.
4. **Application state:** Agent state snapshots and module/config/cache objects; no custom business state machine.
5. **External memory:** Twilio Memory Recall of profiles, observations and summaries. The primary path retrieves it; no AgentCore Memory resource is configured (`memories: []`).

**REFERENCE FACT:** `P:src/tac/core/tac.py:TAC.__init__/retrieve_memory` discovers memory_store_id from Orchestrator configuration at startup. It resolves profile from author address, fetches profile traits, performs Memory Recall, and falls back to Orchestrator list_communications on failures. BaseChannel's once mode uses a cache_lock and clears cache on INACTIVE where that handler is wired. SMS uses always; voice uses once; main.py leaves Chat/RCS/WhatsApp at their default never mode.

The fallback only runs when an exception reaches TAC.retrieve_memory (for example, profile lookup failure/no profile or request-model validation). `P:src/tac/context/memory.py:MemoryClient.retrieve_memory` catches Recall HTTP/parsing failures and returns an empty MemoryRetrievalResponse, which TAC wraps without falling back. Thus an actual Recall outage can result in empty context rather than Communications fallback. Profile trait fetch failure is separately logged and retrieval can continue.

### FileSessionManager detail

**DEPENDENCY FACT:** `D:strands-agents-1.41.0/strands/session/file_session_manager.py:FileSessionManager` writes:

`/mnt/workspace/.sessions/session_<conversation_id>/session.json`

`/mnt/workspace/.sessions/session_<conversation_id>/agents/agent_default/agent.json`

`/mnt/workspace/.sessions/session_<conversation_id>/agents/agent_default/messages/message_<index>.json`

`SessionManager.register_hooks` restores on AgentInitializedEvent, appends/syncs on MessageAddedEvent and syncs after invocation. `RepositorySessionManager.initialize` restores state, conversation-manager state and messages. Missing session metadata creates a new session; missing agent data creates a new agent snapshot. Invalid JSON raises SessionException.

`_write_file` writes a fixed `<path>.tmp` then os.replace; there is no fsync, whole-turn transaction, cross-process lock or compare-and-swap. Atomic file replacement does not serialize two writers to the same path. `Agent.cleanup` cleans tools, not session directories. FileSessionManager.delete_session exists but is not called by the connector. No file cleanup/retention schedule is implemented by the application.

**REFERENCE FACT:** `A:deploy/agentcore_aws_lambda/cdk/agentcore/agentcore.json` requests sessionStorage at /mnt/workspace. **SERVICE DOCUMENTATION:** this preview storage survives stop/resume with the same runtimeSessionId, expires after 14 idle days and resets when the runtime version changes. This supports bounded session persistence, not permanent cross-deployment memory. Actual mount and recovery were not exercised. [AWS filesystem lifecycle documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-filesystem-configurations.html).

## Restart survival matrix

**YES** means retained under the stated same-identity/no-cleanup conditions, from source or documented managed behavior, **not a live test PASS**. **NO** means the original state is lost (it may be reconstructed). **UNKNOWN** means source/service evidence does not establish it. “Same container” below means the existing process also remains alive; a restarted process uses the process-restart column.

| State component | Next request same process | Next request same container | Lambda warm reuse | Lambda cold start | AgentCore new request, same runtimeSessionId | AgentCore process restart | AgentCore compute stop/resume, same ID/version | Deployment/runtime version update | Host replacement |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Lambda request body/local parsed state | NO | NO | NO | NO | NO | NO | NO | NO | NO |
| Lambda module proxy/token/clients | YES | YES | YES | NO | UNKNOWN | UNKNOWN | UNKNOWN | NO | NO |
| Runtime TAC/channel/Agent objects and initialized flags | YES | YES | UNKNOWN | UNKNOWN | UNKNOWN | NO | NO | NO | NO |
| Channel dedup tokens / cached Memory / voice tasks | YES | YES | UNKNOWN | UNKNOWN | UNKNOWN | NO | NO | NO | NO |
| Active voice WebSocket | YES | YES | UNKNOWN | UNKNOWN | UNKNOWN | NO | NO | NO | NO |
| Completed Strands session files on configured managed mount | YES | YES | UNKNOWN | UNKNOWN | YES | UNKNOWN | YES | NO | UNKNOWN |
| Unsaved inference/response-in-flight state | NO | NO | NO | NO | NO | NO | NO | NO | NO |
| External Twilio conversations/Memory records | YES | YES | YES | YES | YES | YES | YES | UNKNOWN | YES |
| Existing Secrets Manager secret value | YES | YES | YES | YES | YES | YES | YES | UNKNOWN | YES |

UNKNOWN in a cross-component cell means that scenario does not determine that component's lifecycle (Lambda warm/cold transitions do not specify AgentCore's lifetime, for example). WebSocket YES only describes the unchanged open connection, not reconnection.

Evidence: process-local definitions in state inventory; `D:strands-agents-1.41.0/strands/session/session_manager.py:register_hooks`; `A:deploy/agentcore_aws_lambda/cdk/agentcore/agentcore.json:filesystemConfigurations`; linked AWS lifecycle documentation. External Twilio/Secrets YES means a local compute restart does not itself delete those external records; no service retention guarantee is inferred. SecretsStack may delete the secret on stack destruction. For arbitrary crash, redeploy without a version change or host replacement, successful checkpoint recovery remains **UNKNOWN — requires further verification**.

For an **arbitrary AgentCore container/microVM restart** (as distinct from documented clean stop/resume), retained runtime objects/caches/tasks/sockets are **NO**, saved managed-mount files are **UNKNOWN**, in-flight unsaved inference is **NO**, and independent Twilio/Secrets records are **YES** subject to their external lifecycle. The selected artifact is CodeZip; this “container restart” scenario refers to its managed execution environment, not a Docker container defined by the application.

## Idempotency, retries and concurrency

### Duplicate delivery

**REFERENCE FACT:** suppression exists. `A:src/tac_aws/proxy/agentcore_lambda.py:_handle_conversation_webhook` forwards `i-twilio-idempotency-token`. `A:src/tac_aws/server/agentcore_app.py:http_handler` forwards it to channels. `P:src/tac/channels/base.py:_is_duplicate_webhook` checks an OrderedDict, evicts its oldest entry at capacity (default 10,000), inserts a new token and returns false. `P:src/tac/channels/messaging.py:process_webhook` checks it **before** validation/channel filtering/handling.

Consequences (**INFERENCE**):

- Same token can suppress reprocessing on the same channel instance while retained.
- A failed or malformed first attempt still consumes the token; no success-only commit/rollback.
- Missing token, eviction, new process/runtime session or a different token for the same event can permit processing again.
- Each channel has its own cache; dispatching to all channels consumes entries even on channels that subsequently ignore the event.
- No event-ID/communication-ID/MessageSid durable deduplication, timestamp freshness/replay ledger, conditional write, transactional “processed” record, or exactly-once delivery is implemented.

**Durable/cross-instance idempotency:** **Not implemented in reference.** CallSid/conversationId select a session; they do not establish unique processing. No assertion is made that Twilio repeats the same header token on retries; that service contract needs verification. Code search covered source/tests for idempotency, event/SID identifiers, duplicate/replay, retry, conditional writes and locks. `P:tests/test_chat_channel.py:test_deduplication` and `P:tests/test_whatsapp_channel.py:test_webhook_deduplication` exercise sequential same-token suppression.

### Retry inventory

| Layer | Actual behavior / trigger / maximum / backoff | Duplicate/state interaction |
| --- | --- | --- |
| Twilio delivery | Retry policy not configured or implemented in these repositories; UNKNOWN — requires further verification | Callback retries can repeat side effects; header semantics unverified |
| Lambda application | No retry loop around presign, get_secret_value or invoke_agent_runtime | Error becomes 500 or startup failure; no resume record |
| AWS SDK | Provided by boto3/botocore; application does not set mode/max_attempts. With the inspected wheel and no overrides, legacy mode uses 5 total attempts, randomized exponential base ×2, socket/selected 5xx/throttling policies. AWS_RETRY_MODE, AWS_MAX_ATTEMPTS/shared config and AWS_NEW_RETRIES_2026 can change mode/attempts | Transport retry is not application idempotency; response loss can leave downstream outcome uncertain |
| AgentCore SDK/server | Request dispatch and exception translation; no reference turn replay/recovery loop | Runtime failure does not resume Python tasks |
| TAC voice setup | Poll CO up to 10 times when result count is not exactly one; sleeps min(0.25 × 2^attempt,1.5), nine delays total 10.75s excluding API latency | Polling for resource visibility; a network exception is not retried by this loop |
| TAC context HTTP | httpx 30s timeout, follow_redirects; no explicit generic retry. Exceptions reaching TAC.retrieve_memory trigger Communications fallback; Recall client HTTP/parsing errors return empty memory | Fallback/empty context is not re-execution of failed delivery |
| Strands model | Inspected default ModelRetryStrategy retries ModelThrottledException, 6 total attempts, delays 4,8,16,32,64s (max-delay cap 240); resets on success/after invocation | Nested over SDK retry attempts; not a duplicate-webhook policy |
| Strands context limits | Agent can reduce conversation context and retry context-window overflow through its conversation manager | May change persisted/context history; not service outage recovery |
| Product telemetry | TAC Segment client max_retries=3, upload interval 10s, batch 20; best-effort shutdown waits up to 5s | Separate from message/Agent retry; failures never raise to application |

Evidence: `D:botocore-1.43.15/botocore/configprovider.py:BOTOCORE_DEFAUT_SESSION_VARIABLES/_resolve_new_retries` and `botocore/data/_retry.json:__default__`; `P:src/tac/channels/voice/conversation_relay/provider.py:_initialize_conversation`; `P:src/tac/context/base.py:_get_client/_get_sync_client`; `P:src/tac/core/tac.py:retrieve_memory`; `D:strands-agents-1.41.0/strands/event_loop/_retry.py:ModelRetryStrategy`; `strands/agent/agent.py:_execute_event_loop_cycle`; `P:src/tac/core/analytics.py:_get_client/shutdown_analytics`.

In this botocore wheel, the optional internal default customization is absent, so AWS_NEW_RETRIES_2026 unset resolves to false and selects legacy mode. Setting it true selects the new standard behavior; explicit retry mode/config still matters. Standard mode normally defaults to 3 total attempts, exponential jitter capped at 20s, with eligible transient/throttling errors and retry quota; adaptive mode additionally applies client rate limiting. These are dependency policies, not selected application settings. `D:botocore-1.43.15/botocore/configprovider.py:_resolve_new_retries`; `D:botocore-1.43.15/botocore/retries/standard.py:register_retry_handler/ExponentialBackoff/StandardRetryConditions`; `D:botocore-1.43.15/botocore/retries/adaptive.py:register_retry_handler`. Effective deployed policy remains UNKNOWN.

### Concurrency and ordering

**REFERENCE FACT:** HTTP dispatch uses gather across channels, not a per-conversation queue. The connector Agent cache/initialized set and BaseChannel conversation/dedup dictionaries have no explicit synchronization. Messaging awaits processing but concurrent invocations can overlap at API awaits. There is no global ordering across Lambda/runtime workers.

**DEPENDENCY FACT:** Strands 1.41 Agent defaults to a nonblocking threading.Lock and raises ConcurrencyException when the **same Agent object** is already invoked. This prevents simultaneous use of that object, not ordered queuing and not concurrent distinct Agents writing the same files. `D:strands-agents-1.41.0/strands/agent/agent.py:stream_async`.

Voice defaults to ThreadSafeSessionManager, which protects session-map operations with RLock. SessionState tracks one task; new final prompts/interrupts cancel the previous task, waiting up to 5s. ConversationSession.cache_lock protects once-mode cache operations within an event loop. These protections do not cover the entire request, file writes, participant side effects, Agent creation or separate processes. `P:src/tac/channels/voice/conversation_relay/config.py:ConversationRelayProviderConfig.session_manager`; `P:src/tac/session/thread_safe.py`; `P:src/tac/session/state.py:cancel_stream_task`.

**INFERENCE:** cancelling a TAC task does not establish that an already-running boto3 worker thread/network request stopped. No “model cancelled” acknowledgement, cross-process file locking, optimistic concurrency/version check or ordering guarantee appears in the primary application.

## Failure model

The following rows describe source behavior, not deployed experiments. “None” in recovery means **Not implemented in reference.**

| Failure | Detection / handling and response | Retry / state / cleanup / recovery |
| --- | --- | --- |
| Lambda initialization: secret/config/AWS credentials failure | credentials.py or tac_config.py logs and raises; missing required env raises KeyError; TAC init configuration fetch raises ValueError | No usable handler/app; SDK defaults only; no application recovery |
| Invalid/missing HTTP signature | validator false → Lambda 403 | No model call; no dedup state recorded |
| Validator throws (e.g. unexpected body type/JSON without bodySHA256 in external validator) | Validation outside handler try; exception escapes | Explicit defensive exception translation absent in this wrapper |
| Voice malformed form/CallSid absent | parse failure produces empty form; 400 if no CallSid; other voice handler exceptions →500 | No runtime call; presign/TwiML retry absent |
| Messaging malformed JSON/base64/missing ID | 400; non-object JSON or non-object data can cause attribute error →500 | No durable malformed-event ledger |
| Lambda invoke_agent_runtime fails/network/auth denied | catch/log/500 | SDK may retry; downstream outcome may be uncertain; no reconciliation |
| Lambda times out (configured 30s) | No timeout handler or cancellation/resume protocol in source | Platform termination response UNKNOWN; invocation/model side effects may continue; no end-to-end rollback |
| AgentCore HTTP inner JSON parsing fails | regular dict status=error; inspected SDK emits ordinary JSON response | Lambda ignores body; can still report success |
| TAC channel raises / Pydantic model fails | _process_webhook logs/swallow; HTTP status=ok | Token may already be inserted; no automatic retry or rollback |
| Participant lookup/promote/add fails/409 | reconciliation returns None; inbound dropped; TAC.trigger_error called; primary app registers no on_error handler | No model turn; possible partial participant/profile side effects; no recovery |
| Memory/profile retrieval fails | Lookup/no-profile/request-validation exceptions can trigger Communications fallback; Recall HTTP/parsing errors return empty memory; BaseChannel tolerates escaping failures | Cache may contain an empty result; initialized connector flag can prevent later injection |
| Bedrock access denied/timeout/network error | library exception → connector catch, logs and sends generic “please try again” | SDK defaults only for eligible errors; Agent history/files may already include user message; no transactional rollback |
| Bedrock throttled | converted to ModelThrottledException in inspected BedrockModel | Strands retries then connector error response if exhausted; can exceed Lambda 30s lifetime |
| Strands Agent factory/Agent/session file raises | connector catches Exception and attempts generic channel response | No cache reset/turn rollback; error-send failure may escape; cancellation is separate |
| Same Agent already running | inspected Strands ConcurrencyException | Connector sends generic error; no ordered queue |
| Messaging outbound Actions API fails | MessagingChannel.send_response catches/logs network error | No retry/delivery failure return to connector; connector/channel dispatch can appear successful |
| Voice missing/closed socket | logs and returns; send exceptions swallowed; disconnect finally removes socket/tasks | No reconnect/resume or replay of undelivered response |
| Voice setup lookup returns wrong count | up to 10 polls then RuntimeError; WebSocket handler logs/finally cleans resources it knows | No reinitialization of failed call |
| AgentCore process/compute crashes | No application-level crash checkpoint/task replay handler | Objects/tasks/sockets lost; only saved file/remote context may be restorable |
| Session files missing/corrupt | FileSessionManager read_session/read_agent; missing →new session/agent; corrupt JSON →SessionException | In-memory Agent may continue until reconstruction; no backup/recovery journal |
| Twilio repeats webhook | bounded token suppression if same retained token | Otherwise reprocessing possible; failed first event may instead be suppressed |
| Optional tool fails | optional memory wrapper raises ValueError | Primary agent has no tools; actual tool-error formatting provided by Strands, not exercised here |

Evidence: `A:src/tac_aws/proxy/agentcore_lambda.py`; `A:deploy/agentcore_aws_lambda/lambda/credentials.py:fetch_twilio_auth_token`; `A:deploy/agentcore_aws_lambda/agent/tac_config.py`; `A:src/tac_aws/server/agentcore_app.py`; `A:src/tac_aws/connectors/strands_connector.py:_handle_message`; `P:src/tac/channels/messaging.py:_handle_communication_created/send_response/_reconcile_participants`; `P:src/tac/channels/voice/conversation_relay/provider.py:handle_websocket/send_response`; `D:strands-agents-1.41.0/strands/session/file_session_manager.py`.

## Security and trust boundaries

~~~mermaid
flowchart LR
  U["Public internet / Twilio"]
  subgraph AWSAccount["AWS account"]
    L["Public Lambda URL\nAuthType NONE\nTwilio signature gate in code"]
    S["Secrets Manager\nTwilio credentials"]
    R["AgentCore service\nSigV4 / IAM authorization"]
    APP["Python runtime\nTAC + Strands"]
    B["Bedrock Runtime\nexecution-role IAM"]
  end
  U -->|"signed HTTP webhook"| L
  L -->|"get_secret_value"| S
  L -->|"SigV4 invocation"| R
  L -->|"presigned WSS URL in TwiML"| U
  U -->|"possession of signed URL"| R
  R --> APP --> B
  APP -->|"Twilio API key/secret over HTTPS"| U
  APP -->|"secret read"| S
~~~

**REFERENCE FACT:** Lambda's Function URL is public/AuthType NONE; its handler enforces signatures. Twilio SDK RequestValidator performs HMAC-SHA1 form signature checks and bodySHA256 handling for signed JSON URLs. `A:deploy/agentcore_aws_lambda/cdk/lib/lambda-stack.ts:LambdaStack`; `A:src/tac_aws/proxy/validation.py:TwilioSignatureValidator`; `D:twilio-9.10.9/twilio/request_validator.py:RequestValidator.validate/compute_signature`.

The AWS validator passes string JSON bodies directly without the supporting Python server's explicit bodySHA256-presence guard. JSON without that query field can cause the inspected Twilio validator to throw TypeError; it is not evidence of a safe JSON-body validation fallback. Core FastAPI separately fails closed in `P:src/tac/server/signature_validation.py:validate_twilio_webhook`, but the Lambda proxy does not call that function.

**REFERENCE FACT:** Lambda role receives Secrets read and InvokeAgentRuntime/InvokeAgentRuntimeWithWebSocketStream scoped to the runtime ARN and ARN/*. Presigned URL expiry is 300s. Application WebSocket handler does not independently validate Twilio's socket handshake signature; comments attribute this to unavailable custom headers and rely on AWS presigning. requestHeaderAllowlist contains X-Twilio-Signature, but the handler still never validates it. These comments do not establish current service header stripping.

The runtime secret contains TWILIO_ACCOUNT_SID/AUTH_TOKEN/API_KEY/API_SECRET; TAC HTTP clients use API key/secret Basic auth over HTTPS. Bedrock uses AWS SDK credentials, intended to come from the execution role. No application authorization for individual callers/profile IDs or separation of user access beyond conversation routing is implemented.

**DEPENDENCY FACT:** the inspected CDK Runtime construct grants Bedrock access to foundation-model/* across regions and account inference-profile/*, X-Ray write permissions, CloudWatch log actions, and broad Configuration Bundle actions. The selected JSON defines no Configuration Bundles, Memory, Gateways or policy engines. These grants come from the external construct, not explicit Twilio app logic; Lambda's scoped runtime invocation permission is not evidence that every role permission is narrowly scoped.

Encryption: public URLs and Twilio API clients use HTTPS/WSS. No customer-managed encryption setting for session files, no application encryption layer, no timestamp-based replay protection, no explicit rate limiting/WAF, and no secret rotation/refresh mechanism are present in the primary source. Managed-service encryption internals are **UNKNOWN — requires further verification**. This is a reference limitation inventory, not a hardening proposal.

## Configuration inventory

| Setting | Required / default / purpose | Secret? / owner | Evidence |
| --- | --- | --- | --- |
| AWS_PROFILE | Required by Makefile deployment/bootstrap targets; loaded from .env | Credential profile name, deployment | `A:deploy/agentcore_aws_lambda/Makefile` |
| AWS_ACCOUNT_ID, AWS_REGION | Required CDK config; example region us-east-1; runtime receives AWS_REGION | Deployment-specific, nonsecret | `A:deploy/agentcore_aws_lambda/cdk/config/env-config.ts:loadEnvConfig`; `cdk/bin/app.ts` |
| AGENTCORE_RUNTIME_ARN | Required Lambda import-time env | Resource identifier, Lambda | `A:deploy/agentcore_aws_lambda/lambda/index.py` |
| TWILIO_SECRET_ARN | Required Lambda/runtime | Secret resource identifier; value itself not credential | `A:deploy/agentcore_aws_lambda/lambda/index.py`; `agent/tac_config.py` |
| TWILIO_ACCOUNT_SID | Required secret object key | Account identifier; handled with credentials | `A:deploy/agentcore_aws_lambda/agent/tac_config.py:create_tac_config` |
| TWILIO_AUTH_TOKEN | Required secret for Lambda validation and TAC config | Secret | `A:deploy/agentcore_aws_lambda/lambda/credentials.py:fetch_twilio_auth_token` |
| TWILIO_API_KEY, TWILIO_API_SECRET | Required secret object keys for TAC HTTP APIs | Key identifier / secret | `A:deploy/agentcore_aws_lambda/agent/tac_config.py` |
| TWILIO_PHONE_NUMBER, TWILIO_CONVERSATION_CONFIGURATION_ID | Required deployment/runtime; config ID also Lambda | Twilio resource/config values | `A:deploy/agentcore_aws_lambda/cdk/config/env-config.ts`; `agent/tac_config.py` |
| TWILIO_RCS_SENDER_ID, TWILIO_WHATSAPP_NUMBER | Optional; only configured senders enable these channels | Resource identifiers | `A:src/tac_aws/connectors/channels.py:ConnectorChannels.__init__` |
| TWILIO_LOG_LEVEL | Optional runtime, default INFO | Runtime | `A:deploy/agentcore_aws_lambda/agent/tac_config.py` |
| TAC_ANALYTICS_DISABLED | Optional; exact string true disables product telemetry | Runtime/environment | `P:src/tac/core/analytics.py:_is_disabled` |
| Model/system prompt/storage_dir | Hardcoded Nova Pro/system prompt and /mnt/workspace/.sessions | Runtime code | `A:deploy/agentcore_aws_lambda/agent/main.py:create_agent` |
| Voice memory_mode / SMS memory_mode | once / always; other created messaging channels default never | Runtime code | `A:deploy/agentcore_aws_lambda/agent/main.py`; `P:src/tac/channels/messaging.py:MessagingChannelConfig` |
| dedup_capacity | 10,000 per channel, positive config value | Runtime/library | `P:src/tac/channels/messaging.py:MessagingChannelConfig` |
| Runtime build/entry/runtime/network/protocol | CodeZip/main.py/PYTHON_3_14/PUBLIC/HTTP | Deployment JSON | `A:deploy/agentcore_aws_lambda/cdk/agentcore/agentcore.json` |
| filesystemConfigurations | sessionStorage mount /mnt/workspace | Deployment | Same JSON |
| Lambda runtime/timeout/memory | Python 3.13 / 30s / 1024MB | Deployment | `A:deploy/agentcore_aws_lambda/cdk/lib/lambda-stack.ts` |
| AWS_RETRY_MODE/AWS_MAX_ATTEMPTS/AWS_NEW_RETRIES_2026/shared AWS config | External SDK can honor these; app does not set them | External SDK runtime | `D:botocore-1.43.15/botocore/configprovider.py` |

**Supporting-library configuration, not automatically used by primary tac_config.py:** TACConfig.from_env reads TWILIO_REGION, TWILIO_KNOWLEDGE_BASE_ID, TWILIO_STUDIO_HANDOFF_FLOW_SID, TWILIO_VOICE_PUBLIC_DOMAIN/WEBSOCKET_PATH/ACTION_PATH/CALL_EVENT_PATH, Memory configuration fields and Conversation Intelligence fields. Memory env inputs include TWILIO_MEMORY_PROFILE_TRAIT_GROUPS, retrieval limits/relevance threshold and phone trait mapping. Server config defines host/port and route/validation settings. Primary create_tac_config calls TACConfig directly and does **not** invoke from_env, so these optional env variables cannot be assumed to affect that deployment. `P:src/tac/core/config.py:TACConfig.from_env/TwilioMemoryConfig.from_env/ConversationIntelligenceConfig.from_env`; `P:src/tac/server/config.py:TACServerConfig.from_env`.

| Supporting-library input | Default / required scope | Secret? / source |
| --- | --- | --- |
| TWILIO_REGION | Optional Twilio API region; distinct from AWS_REGION | No; TACConfig.from_env |
| TWILIO_KNOWLEDGE_BASE_ID | Optional; creates KnowledgeClient when configured | Resource ID; TACConfig.from_env |
| TWILIO_STUDIO_HANDOFF_FLOW_SID | Optional core setting, required by optional Studio handoff factory | Resource ID; TACConfig/from_env and tools/handoff.py |
| TWILIO_VOICE_PUBLIC_DOMAIN | Required for the separate FastAPI voice server URL construction | Public domain; TACConfig/TACFastAPIServer |
| TWILIO_VOICE_WEBSOCKET_PATH / TWILIO_VOICE_ACTION_PATH / TWILIO_VOICE_CALL_EVENT_PATH | /ws, /conversation-relay-callback, /twilio/call-events | No; TACConfig.from_env |
| TWILIO_MEMORY_PROFILE_TRAIT_GROUPS | Optional comma-separated trait filter | No; TwilioMemoryConfig.from_env |
| TWILIO_MEMORY_OBSERVATIONS_LIMIT / TWILIO_MEMORY_SUMMARIES_LIMIT / TWILIO_MEMORY_COMMUNICATIONS_LIMIT | 20 / 5 / 0; Pydantic limits0–100 | No; TwilioMemoryConfig.from_env |
| TWILIO_MEMORY_RELEVANCE_THRESHOLD | 0.0; Pydantic range0–1 | No; TwilioMemoryConfig.from_env |
| TWILIO_MEMORY_PHONE_TRAIT_GROUP / TWILIO_MEMORY_PHONE_TRAIT_FIELD | Contact / phone | No; TwilioMemoryConfig.from_env |
| CONVERSATION_INTELLIGENCE_CONFIGURATION_ID / CONVERSATION_INTELLIGENCE_SUMMARY_OPERATOR_SID | Optional CI config/operator; omitted primary | Resource IDs; ConversationIntelligenceConfig.from_env |
| TWILIO_SERVER_HOST / TWILIO_SERVER_PORT | 0.0.0.0 / 8000 for FastAPI server | No; TACServerConfig.from_env |
| conversation_webhook_path / twiml_path / cintel_webhook_path | /webhook / /twiml / None; set programmatically | No; TACServerConfig fields |
| Voice session_manager/default_twiml_options/default_call_options | ThreadSafeSessionManager / None / None; programmatic tuning | No; ConversationRelayProviderConfig |
| SDK server run(port,host,**kwargs) | 8080; loopback outside container, all interfaces in container; app passes no overrides | No; external BedrockAgentCoreApp.run |

No application-specific CLI flags are supplied in the primary example. `P:src/tac/__main__.py` only prints the package version. Deployment options are Makefile targets/CDK arguments and .env/JSON values, not an agent prompt CLI.

## Deployment and AWS resource inventory

**REFERENCE FACT:** official source path is `A:deploy/agentcore_aws_lambda/Makefile` and CDK `bin/app.ts:main`: build agent environment and TypeScript, bootstrap, deploy SecretsStack, populate credentials, then AgentCoreStack and LambdaStack. SecretsStack creates `tac/twilio-credentials` with DESTROY removal policy. AgentCoreStack uses external AgentCoreApplication; LambdaStack constructs PythonFunction and Function URL. These commands were read, not executed.

~~~mermaid
flowchart TB
  CONFIG["Workspace .env + agentcore.json"] --> CDK["Makefile + CDK app"]
  CDK --> SS["SecretsStack"] --> SECRET["Secrets Manager: Twilio credentials"]
  CDK --> AS["AgentCoreStack + external construct"] --> RUNTIME["AgentCore Runtime: CodeZip / HTTP"]
  CDK --> LS["LambdaStack"] --> LAMBDA["Lambda + public Function URL"]
  PACKAGE["External Python CodeZip packager"] --> S3["CDK S3 code asset"] --> RUNTIME
  AS --> RR["Runtime execution role"]
  LS --> LR["Lambda execution role"]
  SECRET -->|"startup read"| LAMBDA
  SECRET -->|"startup read"| RUNTIME
  LR -->|"authorizes invocation and secret read"| LAMBDA
  RR -->|"authorizes model and secret access"| RUNTIME
  RUNTIME --- FS["Managed sessionStorage mount: /mnt/workspace"]
  TWILIO["Twilio voice / conversation webhooks"] --> LAMBDA
  LAMBDA -->|"messaging invocation"| RUNTIME
  TWILIO -->|"presigned voice WSS"| RUNTIME
  RUNTIME --> NOVA["Bedrock Runtime: Nova Pro"]
~~~

This is a source-derived resource/component map, not a synthesized or deployed stack. Evidence: `A:deploy/agentcore_aws_lambda/cdk/bin/app.ts:main`; `A:deploy/agentcore_aws_lambda/cdk/lib/secrets-stack.ts:SecretsStack`; `A:deploy/agentcore_aws_lambda/cdk/lib/agentcore-stack.ts:AgentCoreStack`; `A:deploy/agentcore_aws_lambda/cdk/lib/lambda-stack.ts:LambdaStack`; external `D:agentcore-cdk-0.1.0-alpha.35/package/dist/cdk/constructs/components/primitives/runtime/AgentCoreRuntime.js:AgentCoreRuntime.constructor` and `D:agentcore-cdk-0.1.0-alpha.35/package/dist/cdk/constructs/bundling/zip/PythonBundledCodeZipAsset.js:PythonBundledCodeZipAsset.packageCode`.

CodeZip is the selected AgentCore packaging mode. ECR/CodeBuild container provisioning from other examples is not required for this selected runtime mode. Lambda PythonFunction dependency bundling uses an external CDK construct; the deployment README requires Docker for this packaging. No Docker images were built/reused.

| AWS service / resource | Purpose / caller / API or resource / permissions | Required for chosen cloud slice? | Evidence |
| --- | --- | --- | --- |
| Lambda + Function URL | Twilio ingress; PythonFunction with public URL; invokes runtime/presigns | Yes | `A:deploy/agentcore_aws_lambda/cdk/lib/lambda-stack.ts` |
| Bedrock AgentCore Runtime | HTTP/WebSocket execution, runtimeSessionId routing, managed session mount; CfnRuntime / InvokeAgentRuntime / InvokeAgentRuntimeWithWebSocketStream | Yes | `A:src/tac_aws/proxy/agentcore_lambda.py`; `A:deploy/agentcore_aws_lambda/cdk/agentcore/agentcore.json` |
| Bedrock Runtime/model | Strands Nova inference; ConverseStream, IAM InvokeModelWithResponseStream | Yes for real model | `A:deploy/agentcore_aws_lambda/agent/main.py:create_agent`; `D:strands-agents-1.41.0/strands/models/bedrock.py:_stream` |
| IAM roles/policies | Lambda execution role; AgentCore service-assumed role; SDK AWS signing | Yes | LambdaStack; AgentCoreStack; external AgentCoreRuntime.constructor |
| Secrets Manager | Secret creation/update; get_secret_value at startup for Lambda/runtime; grantRead | Yes in official example | `A:deploy/agentcore_aws_lambda/cdk/lib/secrets-stack.ts`; `lambda/credentials.py`; `agent/tac_config.py` |
| CloudWatch Logs | Lambda/runtime logs; construct runtime log permissions | Official operational path | `D:agentcore-cdk-0.1.0-alpha.35/package/dist/cdk/constructs/components/primitives/runtime/AgentCoreRuntime.js`; Lambda PythonFunction |
| X-Ray | External construct grants tracing writes; default CodeZip OpenTelemetry launcher | Framework deployment behavior; not necessary for fake local flow | Same Runtime construct |
| S3 | CDK bootstrap/code assets and Runtime codeConfiguration.s3; not conversation state | Official CodeZip deployment | External Runtime/AgentEnvironment/PythonBundledCodeZipAsset; `A:deploy/agentcore_aws_lambda/iam-policy.json:S3CDKAssets` |
| CloudFormation | CDK bootstrap and three-stack deployment; not request execution | Official deployment | Makefile; iam-policy.json:CloudFormationDeployment |
| SSM Parameter Store | CDK bootstrap parameter permissions in deployment policy | Bootstrap support only | `A:deploy/agentcore_aws_lambda/iam-policy.json:SSMParameters` |
| ECR | Listed in bootstrap/deployment policy; optional Container path in construct; other Fargate examples use images | No application container repository required by selected CodeZip path | `A:deploy/agentcore_aws_lambda/iam-policy.json:ECRRepositories`; external AgentEnvironment constructor |
| STS | AssumeRole on cdk-* roles allowed in deployment policy | CDK deployment identity support; no direct application STS API call | `A:deploy/agentcore_aws_lambda/iam-policy.json:STSAssumeRole` |

Paths abbreviated in this table beginning cdk/agent/lambda or iam-policy.json are all under `references/twilio-agent-connect-aws/deploy/agentcore_aws_lambda/`. There is no selected S3/EFS external session store, DynamoDB, SQS, Step Functions or custom durable workflow. No AgentCore Memory resource is configured. The CDK package contains other constructs; importing it does not mean those resources are instantiated.

**Twilio dependencies:** a number/voice webhook, ConversationRelay, a Conversation Orchestrator configuration/webhook and attached Memory store, API credentials and optional channel senders. The deployment README describes configuring VoiceWebhookUrl and ConversationWebhookUrl outputs in Twilio. Actual availability/entitlements, conversation-creation timing and delivery guarantees remain untested.

**Deployment risks visible without execution:** stale agent lock versus manifest, external resolver variance, public network mode, broad framework role grants, runtime-version-dependent session retention, and sample deployer policy coverage. The sample policy grants secret creation/update and STS assumption of CDK roles; its direct CloudFormation resource list omits TacSecretsStack. Whether assumed bootstrap roles supply the remaining deployment/destruction permissions is **UNKNOWN — requires further verification**; this is not a demonstrated deployment failure. `A:deploy/agentcore_aws_lambda/iam-policy.json`; `A:deploy/agentcore_aws_lambda/cdk/bin/app.ts`. No policy correction was implemented.

## Observability

**REFERENCE FACT:** TAC provides structured key/value ContextLogger and console/JSON formatters, with phone/email scrubbing; TAC.__init__ selects console formatting. Channel/connector logs include conversation_id, channel, participant IDs and errors. Lambda validation logs reconstructed URL on invalid signatures; errors often include stack traces. No explicitly propagated end-to-end trace ID linking Lambda → AgentCore → TAC → Bedrock → Twilio Actions is implemented. `P:src/tac/core/logging.py:setup_logging/ContextLogger`; `A:src/tac_aws/proxy/validation.py:validate`.

Product usage analytics is explicitly implemented in the Python repository, using Segment, default enabled and opted out by TAC_ANALYTICS_DISABLED=true. It reports account/conversation/provider metadata via track_event and catches failures; it is not a delivery audit trail. `P:src/tac/core/analytics.py:track_event`.

**DEPENDENCY FACT:** AgentCore SDK has structured request/session context logs; Strands has OpenTelemetry Agent/model spans and result metrics. The inspected CDK Runtime construct defaults to an opentelemetry-instrument CodeZip launcher and grants X-Ray/Logs permissions. Framework telemetry is not application-defined operational alerts/SLOs. Successful export, CloudWatch ingestion, agent trace correlation and deployed dashboards are **NOT VERIFIED**. No reference-specific alarms, replay/delivery metrics or recovery dashboard are implemented.

## Testing strategy and evidence limits

All relevant test modules were inspected for test names, fixtures, dependency mocks and assertions; representative bodies were traced. **No pytest suite was run in this Stage 0 study.** No claim is made about test execution success. No SDK credentials were loaded and no application module with startup network calls was executed.

An AST inventory of both repositories' test source reads records 54 modules and 1,098 test functions, assertion counts and patch targets in `references/_dependency-evidence/test-inventory.json`. This is a source inventory, not an executed-test count.

| Repository test surface | What it establishes when executed | What it does not establish |
| --- | --- | --- |
| `A:tests/test_strands_connector.py` | Mock Agent factory/reuse, first memory invocation, context, channel routing, error response, cleanup | Real Bedrock/Strands session files/concurrent cloud behavior |
| `A:tests/test_agentcore_lambda.py` | Header/form helpers, TwiML generation/layering, sync/async customization, missing CallSid; patched voice proxy | Full messaging forwarding, runtime result handling, managed auth |
| `A:tests/test_validation.py` | URL/body/header transformation; underlying validator mocked for signature cases | Full cryptographic negative/replay coverage of Lambda JSON boundary |
| `A:tests/test_connector_channels.py` | Channel enablement, routing, buffering streams for messaging, voice stream pass-through | Actual Twilio delivery |
| `A:tests/test_memory_tool.py` | Optional tool metadata/calls/model_dump/error wrapping with mock MemoryClient | Live Memory semantics or main Agent tool use |
| `A:tests/test_bedrock_connector.py; A:tests/test_bedrock_agentcore_connector.py` | Alternative connector invoke/stream parsing/errors with mocks | Chosen TACAgentCoreApp lifecycle |
| `A:tests/test_package.py` | Import/export/package interface assertions; optional dependencies may skip | Runnable pinned deployment |
| `P:tests/test_integration.py` | Multi-component TAC workflows using AsyncMock/patch; “end_to_end” test name is a mocked SMS flow | AWS+Twilio+Bedrock E2E |
| `P:tests/test_webhook_validation.py; P:tests/test_server.py` | Real locally computed signatures and FastAPI route tests, including forged JSON and missing bodySHA256 | The AWS Lambda wrapper, which uses a separate validator |
| `P:tests/test_voice_channel.py; P:tests/test_interrupt.py; P:tests/test_session.py; P:tests/test_session_manager.py` | Voice setup/polling/disconnect/task cancellation, callback behavior, RLock-map independence/thread tests | Managed AgentCore placement or persisted task recovery |
| `P:tests/test_sms_channel.py; P:tests/test_rcs_channel.py; P:tests/test_whatsapp_channel.py; P:tests/test_chat_channel.py` | Channel filtering, responses, sessions; same-token dedup tests in RCS/WhatsApp/Chat | Durable replay exclusion or concurrent same-session exactly-once effects |
| `P:tests/test_memory_mode_once.py; P:tests/test_memory_fallback.py; P:tests/test_recall_parsing_resilience.py` | Cache/invalidation/lock, fallback and malformed Memory response resilience | Remote Memory persistence guarantee |
| `P:tests/test_participant_reconciliation.py; P:tests/test_error_callback.py` | Promotion/add/profile fallback/conflicts; dropped inbound error callback | Automatic recovery; primary example does not register on_error |
| `P:tests/test_analytics.py; P:tests/test_redaction.py; P:tests/test_http_client_config.py` | Telemetry disable/mocks, redaction, HTTP config | Service trace/metrics export |

Supporting optional features have tests in `P:tests/test_tools.py; P:tests/test_handoff.py; P:tests/test_knowledge.py; P:tests/test_intelligence.py; P:tests/test_outbound.py` and Media Streams/OpenAI tests. They are separate from the selected slice. Fixtures in `P:tests/conftest.py` disable analytics and mock configuration fetch; `A:tests/conftest.py` supplies MagicMock TAC and AsyncMock Agent. CI workflows specify dependency sync, lint/type checks and pytest; historical CI status was not queried or assumed. No test named for TACAgentCoreApp/http_handler/WebSocket adapter was found in the AWS tests; selected deployment CDK/managed-storage E2E coverage is not present there.

The most valuable tests to reproduce locally later are the actual Lambda event → forwarded payload contract, TAC participant reconciliation → Agent → outbound Action contract, token retention/failure cases, conversation-specific Agent reuse, voice CallSid → conversation lookup/task cancellation, and FileSessionManager reconstruction from saved files. These are **test-study recommendations**, not new tests or implemented fixes.

## Limitations and unresolved questions

See [twilio-gap-register.md](twilio-gap-register.md) for evidence and questions. The most consequential findings are bounded token deduplication, partial-success acknowledgement, voice lifecycle routing gaps, finite managed storage retention, missing global ordering/file synchronization and mismatched dependency snapshots.

**UNKNOWN — requires further verification:**

1. The concrete dependency versions in a freshly packaged Lambda/runtime and whether both required source versions interoperate unchanged.
2. Session-storage Preview support in the intended region, successful mount/restore, arbitrary process crash/host replacement guarantees and version-update behavior in practice.
3. The voice CLOSED event's effective runtime routing and whether a live deployment has another cleanup mechanism outside this example.
4. Twilio retry/token reuse/ordering and Memory/Orchestrator retention/access guarantees.
5. Real AgentCore invocation latency, timeout/error-body propagation and presigned WebSocket behavior with this deployment.
6. Actual model access and framework telemetry export/correlation.

No Temporal, LangGraph, MCP architecture, custom workflow/idempotency/retry/approval engine, database redesign or business domain was introduced. Such mechanisms are **Not implemented in reference** for this primary slice. [The reproduction plan](twilio-reproduction-plan.md) preserves the observed boundaries; it does not resolve these gaps.

## Stage 0 validation boundary

PASS applies to **the reference study and its artifacts**, not runtime/deployment correctness. Validation checks repository URL/HEAD/clean tracked trees, required document presence, source identifiers, dependency artifact integrity, diagrams' source text structure, and preservation of existing user files.

The completed checks and artifact hashes are recorded in [stage0-validation.json](../references/_dependency-evidence/stage0-validation.json). Source test inventory and dependency provenance are retained alongside it; none is an executed-test or deployment result.

Root baseline: branch main, HEAD `e3e008df6504c817b1fcaa3153c0e31f1935db10`. The CSV was already untracked. Baseline SHA256:

- README.md: `180BF059E3E3C6CCE95B75A2BE676D08796FDC10BE1B8FA74139AFCF768065B2`
- aws_bedrock_companies_107.csv: `C0F423D405507F5D781AD5F2D23A90AF726B9743F1C9C4B3F300B8C24436E693`

All explicit filesystem writes were confined to this workspace's references/ and docs/. No user project outside it was modified; no prior project code/artifact/image was reused. Public GitHub/package downloads and official documentation reads are the only external research access. AWS/Twilio resource creation, deployment, real-cloud inference/delivery, application implementation, commit/push and Stage 1: **not performed**.
