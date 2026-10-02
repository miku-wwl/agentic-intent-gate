# Twilio execution flows — Stage 0

Study date: 2026-10-02. Source commits: AWS `fbea687d73f1ab9021793e96e778c2373f3266ff`; Python `5677eeb72a783de730923121f4ee6b01984faf09`.

Source notation: **A:** = `references/twilio-agent-connect-aws/`; **P:** = `references/twilio-agent-connect-python/`; **D:** = `references/_dependency-evidence/`. REFERENCE FACT concerns A/P; DEPENDENCY FACT concerns the versioned D artifacts. All sequences are source-derived, not recorded runtime traces. [Dependency caveats](twilio-reference-study.md#dependency-versions-are-not-interchangeable) apply.

## 1. Messaging: inbound request, Lambda, AgentCore, TAC, Agent and response

~~~mermaid
sequenceDiagram
  participant T as Twilio Orchestrator
  participant L as Lambda proxy
  participant R as AgentCore service + SDK
  participant APP as TACAgentCoreApp
  participant C as TAC MessagingChannel
  participant API as Twilio APIs
  participant S as StrandsConnector
  participant A as Strands Agent
  participant B as Bedrock Runtime
  T->>L: Signed HTTP JSON COMMUNICATION_CREATED
  L->>L: Validate signature, decode, parse, extract conversationId
  L->>R: invoke_agent_runtime(runtimeSessionId=conversationId, payload)
  R->>APP: HTTP /invocations → http_handler(payload)
  APP->>C: gather process_webhook(data, idempotency_token)
  C->>C: Bounded token dedup, schema/channel/self filtering
  C->>API: Participants lookup / reconcile / optional profile create
  API-->>C: Participant IDs and optional profile
  C->>API: Memory Recall; Communications fallback on escaping exception
  API-->>C: TACMemoryResponse
  C->>S: TAC.trigger_message_ready(text, session, memory)
  S->>S: Get/create Agent keyed by conversation_id
  opt First initialized conversation with nonempty memory
    S->>A: invoke_async(memory prompt)
    A->>B: ConverseStream (dependency default)
    B-->>A: Stream events, collected AgentResult
    A-->>S: Memory-only result ignored
  end
  S->>A: invoke_async(user_message)
  A->>B: ConverseStream(system, messages, modelId)
  B-->>A: Stream events
  A-->>S: AgentResult
  S->>C: channels.send(session, str(result))
  C->>API: POST /v2/Conversations/{id}/Actions
  API-->>C: ActionResponse, or error logged/swallowed
  S-->>C: None (avoid TAC auto-send)
  C-->>APP: None
  APP-->>R: Dict status=ok (channel errors may be swallowed)
  R-->>L: SDK invocation response
  Note over L,R: Lambda does not read result body
  L-->>T: HTTP 200 {"success":true}
~~~

### Transition contracts

| Caller → callee | Repository/file/symbol | Input → output | Execution and boundaries | State/error behavior |
| --- | --- | --- | --- | --- |
| Twilio → Lambda handler | `A:deploy/agentcore_aws_lambda/lambda/index.py:lambda_handler` → `A:src/tac_aws/proxy/agentcore_lambda.py:lambda_handler` | Function URL event → statusCode/headers/body | Public network; Lambda process; synchronous Python handler | Signature gate before route; 403/404 or downstream handler |
| Handler → signature SDK | `A:src/tac_aws/proxy/validation.py:TwilioSignatureValidator.validate` → `D:twilio-9.10.9/twilio/request_validator.py:RequestValidator.validate` | Reconstructed URL + form dict/raw body + signature → bool | In-process, sync; cryptographic implementation external | Missing/invalid signature false; unexpected exceptions not caught by lambda_handler |
| Handler → AgentCore | `A:src/tac_aws/proxy/agentcore_lambda.py:_handle_conversation_webhook` | Original body/token encoded in wrapper JSON; conversation ID as runtimeSessionId → SDK response | boto3 sync, AWS-authenticated network/process boundary | Parse/missing ID400; ignored event200; SDK failure500; no app retry |
| SDK → app handler | `D:bedrock-agentcore-1.11.0/bedrock_agentcore/runtime/app.py:_handle_invocation/_invoke_handler` → `A:src/tac_aws/server/agentcore_app.py:http_handler` | Parsed dict → returned dict serialized as JSON | Managed service → ASGI; HTTP async handler on SDK worker loop | Outer invalid HTTP JSON400; app regular status=error dict can still be HTTP200 |
| App → messaging channels | `A:src/tac_aws/server/agentcore_app.py:_process_webhook` → `P:src/tac/channels/messaging.py:process_webhook` | webhook dict/token → None | async gather, in runtime process; one call for each configured messaging channel | Channel exceptions logged/swallowed; voice omitted |
| Channel → TAC callback | `P:src/tac/channels/messaging.py:_handle_communication_created` → `P:src/tac/core/tac.py:trigger_message_ready` | user text + ConversationSession + memory → str or None | async, local call; participant/profile/Recall calls cross Twilio network first | Token already recorded; reconcile failure drops message/trigger_error |
| TAC → connector | `A:src/tac_aws/connectors/strands_connector.py:_handle_message` | callback args → None | async, same process | Creates/reuses Agent; initialized flag marked before memory await |
| Connector → Agent | same connector → `D:strands-agents-1.41.0/strands/agent/agent.py:invoke_async` | string → AgentResult | async; framework loop and hooks | Agent history changes; files append/sync; exceptions become generic channel reply |
| Agent → model | `D:strands-agents-1.41.0/strands/event_loop/event_loop.py:_handle_model_execution` → `strands/models/bedrock.py:stream/_stream` | message/system/tool specifications → stream events | async queue with sync boto3 work in thread; Bedrock network boundary | Model throttling transformed/retried; other errors propagated |
| Connector → response channel | `A:src/tac_aws/connectors/channels.py:send` → `P:src/tac/channels/messaging.py:send_response` | complete result string; context.channel selects target → None/bool routing indication | async; Twilio Actions network boundary | No channel returns false; network send exceptions can be swallowed |
| Channel → Twilio API | `P:src/tac/context/conversation.py:create_action` | SendMessageActionRequest aliases: type/payload.from/to/content/channelSettings → ActionResponse | HTTPS Basic auth; httpx30s | raises HTTP/model error; channel catches network errors; no delivery retry |
| Runtime → Lambda → Twilio acknowledgement | proxy _handle_conversation_webhook | Invocation accepted/returned →200success | separate response HTTP paths | Acknowledgement can survive failed downstream processing; no model text returned through webhook body |

COMMUNICATION_CREATED input is `{eventType, data:{id, conversationId, accountId, author:{address,channel,participantId}, content:{type:"TEXT",text}, ...}}`. CONVERSATION_UPDATED input uses `data.id/status/configurationId`. Complete validation fields/aliases live in `P:src/tac/models/conversation.py:Communication/ConversationResponse` and fixtures in `P:tests/test_integration.py:create_communication_created_webhook`. The event/communication ID is not used as the durable duplicate key.

Recall HTTP/parsing failures are handled inside `P:src/tac/context/memory.py:MemoryClient.retrieve_memory` by returning empty memory; they do not automatically trigger `TAC.retrieve_memory`'s exception fallback. Profile lookup/no-profile and request validation failures can reach that fallback.

## 2. Voice bootstrap and direct WebSocket flow

~~~mermaid
sequenceDiagram
  participant T as Twilio Voice / ConversationRelay
  participant L as Lambda proxy
  participant R as AgentCore service + SDK
  participant W as TACAgentCoreWebSocketAdapter
  participant V as TAC Voice / RelayProvider
  participant CO as Twilio Orchestrator / Memory
  participant S as StrandsConnector
  participant A as Strands Agent / BedrockModel
  T->>L: Signed form /twiml (CallSid, From, To...)
  L->>L: Validate + generate_presigned_url(session_id=CallSid, expires=300)
  L-->>T: XML Connect/ConversationRelay(url, configuration, interruptible)
  T->>R: Direct WSS connection with signed query/session ID
  R->>W: websocket_handler wraps socket
  W->>V: handle_websocket → accept
  T->>W: setup JSON (callSid)
  W-->>T: text greeting, last=true
  W->>V: Return original setup frame
  V->>CO: Background list_conversations(channelId=CallSid,status=ACTIVE)
  CO-->>V: Exactly one conversation ID (poll if not ready)
  V->>CO: list_participants(conversation ID)
  V->>V: Session/socket/task maps keyed by conversation ID
  T->>V: Final prompt JSON (voicePrompt)
  V->>V: Await setup; cancel previous task; start new task
  V->>CO: Memory once/cache retrieval
  V->>S: trigger_message_ready(text, session, memory)
  S->>A: First memory invocation if applicable; then user invocation
  A-->>S: Completed AgentResult
  S->>V: channels.send(full text)
  V-->>T: {"type":"text","token":full text,"last":true}
  opt New final prompt or interrupt
    T->>V: prompt / interrupt
    V->>V: cancel_stream_task (up to 5 seconds)
    V-->>T: Empty text/last=true acknowledgement on interrupt
  end
  T--xR: WebSocket disconnect
  V->>V: Remove socket; cancel/remove task state
  Note over V,S: Orchestrated ConversationSession and Agent cache not ended here
~~~

**REFERENCE FACT:** Lambda does not relay speech frames or invoke the Strands Agent for voice. The presigner uses AWS credentials locally; it is not a model call. Voice protocol carries transcribed text, not audio, into this Agent. ASR/TTS are Twilio ConversationRelay responsibilities, external to the code. Evidence: `A:src/tac_aws/proxy/agentcore_lambda.py:_handle_voice_twiml`; `P:src/tac/channels/voice/conversation_relay/provider.py`.

| Transition | Exact source | Input/output, execution and state boundary |
| --- | --- | --- |
| Form → TwiML | `A:src/tac_aws/proxy/agentcore_lambda.py:_parse_form/_handle_voice_twiml` and `P:src/tac/channels/voice/conversation_relay/twiml.py:generate_twiml` | Flat form/TwiMLRequest → XML; sync, request-local; missing CallSid400/errors500 |
| Credentials → URL | `D:bedrock-agentcore-1.8.0/bedrock_agentcore/runtime/agent_core_runtime_client.py:generate_presigned_url` | Runtime ARN, CallSid, expiry300 → SigV4QueryAuth-signed wss URL; no voice business state |
| SDK WebSocket → wrapper/channel | `A:src/tac_aws/server/agentcore_app.py:websocket_handler/TACAgentCoreWebSocketAdapter` → `P:src/tac/channels/voice/channel.py:handle_websocket` | Socket/context → provider loop; async ASGI process; context.session_id not bound/checked against callSid |
| setup → session | `P:src/tac/channels/voice/conversation_relay/provider.py:handle_websocket/_initialize_conversation` | SetupMessage.callSid → CO conversation.id, participants, local ConversationSession/SessionState/socket mapping |
| Visibility polling | same _initialize_conversation | Up to10 attempts; 0.25s exponential capped1.5s; wrong count raises; API exceptions not generically retried |
| prompt → callback | same _handle_prompt_async/_handle_prompt | Only final (default true) → PromptMessage.voice_prompt; optional conversationId overrides routing variable; new task replaces cancelled task |
| connector → voice text | `A:src/tac_aws/connectors/strands_connector.py:_handle_message` → `P:src/tac/channels/voice/conversation_relay/provider.py:send_response` | AgentResult string → one text frame/last=true; no token-level stream from this connector |
| interrupt | provider _handle_interrupt_async; `P:src/tac/session/state.py:cancel_stream_task` | InterruptMessage → cancel, empty last frame, TAC interrupt callback; no connector-specific history reconciliation |
| disconnect | provider handle_websocket finally/_cleanup_connection | Remove socket/task map; keep Orchestrator ConversationSession; relay-only mode would call _end_conversation |

Setup requires the source-defined `SetupMessage` fields; prompts use type=prompt, voicePrompt and final. Pydantic validation and aliases are in `P:src/tac/models/voice.py`. First frame not setup closes the connection. Lookup failure is logged; welcome greeting may already have been sent. Some protocol routing values are accepted from prompt/interrupt conversationId; the source does not verify their equality to managed runtimeSessionId. This is a code observation, not a demonstrated exploit.

## 3. Model invocation and persistence

~~~mermaid
sequenceDiagram
  participant S as StrandsConnector
  participant F as Agent factory
  participant A as Strands Agent
  participant H as Session hooks / repository
  participant FS as FileSessionManager
  participant B as Bedrock Runtime
  S->>F: create_agent(ConversationSession), only if cache miss
  F->>FS: FileSessionManager(conversation_id,/mnt/workspace/.sessions)
  FS->>FS: Read session.json or create session
  F->>A: Agent(BedrockModel(Nova), system_prompt, manager)
  A->>H: AgentInitializedEvent
  H->>FS: Read agent.json + messages
  FS-->>A: Restore history/state if files exist
  S->>A: invoke_async(text)
  A->>A: Nonblocking invocation lock; append user message
  A->>H: MessageAddedEvent
  H->>FS: create_message + sync_agent
  A->>B: ConverseStream(modelId,system,messages,inferenceConfig)
  B-->>A: Content deltas + stop/usage metadata
  A->>H: Append assistant message; AfterInvocationEvent
  H->>FS: Message JSON + Agent snapshot
  A-->>S: AgentResult → str
  S->>S: Route output through TAC channel
~~~

The factory and cache are `A:deploy/agentcore_aws_lambda/agent/main.py:create_agent` and `A:src/tac_aws/connectors/strands_connector.py:_handle_message`. Framework facts are `D:strands-agents-1.41.0/strands/agent/agent.py:invoke_async/stream_async`, `strands/session/session_manager.py:register_hooks`, `strands/session/repository_session_manager.py:initialize/append_message/sync_agent` and `strands/models/bedrock.py:_format_request/_stream`.

**DEPENDENCY FACT:** the default Bedrock API is ConverseStream. Converse is an explicit streaming=false alternative. InvokeModel is not the called API here. The Agent consumes streamed model events into a result; response-to-Twilio occurs afterward. Optional toolConfig is only added when tool specs exist. The primary factory defines none.

**INFERENCE:** history persistence can precede successful Twilio delivery. There is no atomic boundary encompassing user message, inference result, saved files and Twilio Actions acknowledgement. A restart can restore a saved turn that was never delivered. The wrapper does not record the inference/delivery stage in a recoverable transaction.

## 4. Session and state lifecycle

~~~mermaid
stateDiagram-v2
  [*] --> RuntimeLoaded: Module import creates TAC / connector / app
  RuntimeLoaded --> LocalConversation: Messaging event or voice CO setup
  LocalConversation --> AgentCached: First callback invokes factory
  AgentCached --> AgentCached: Later messages reuse Agent
  AgentCached --> SavedHistory: Strands lifecycle hooks write JSON
  SavedHistory --> AgentCached: Same running Agent continues
  AgentCached --> LocalEnded: Wired CLOSED event
  LocalEnded --> RuntimeLoaded: Cleanup Agent cache + session map
  AgentCached --> ProcessLost: Restart / termination
  ProcessLost --> RuntimeLoaded: New process import
  RuntimeLoaded --> RestoredAgent: New callback with matching files/session identity
  RestoredAgent --> AgentCached: Restore messages/state; initialization flag is new
  ProcessLost --> FreshHistory: Files unavailable / retention reset
  FreshHistory --> AgentCached: New manager creates session
~~~

This diagram shows **possible code transitions**, not service guarantees. Wired CLOSED applies to the messaging handler; the primary voice path lacks that connection. Sources: `P:src/tac/channels/base.py:_start_conversation/_end_conversation`; `P:src/tac/channels/messaging.py:_handle_conversation_updated`; `A:src/tac_aws/connectors/strands_connector.py:_handle_conversation_ended`; `D:strands-agents-1.41.0/strands/session/repository_session_manager.py:__init__/initialize`.

Three identifiers must stay distinct:

~~~mermaid
flowchart TD
  CS["Twilio CallSid"]
  VR["Voice runtimeSessionId = CallSid"]
  CO["Twilio Orchestrator conversation.id"]
  MR["Messaging runtimeSessionId = conversation.id"]
  LOCAL["TAC ConversationSession + Agent cache key"]
  FILE["Strands session directory = conversation.id"]
  CS --> VR
  CS -->|"CO query channelId"| CO
  CO --> MR
  CO --> LOCAL --> FILE
~~~

For voice, FILE is under the managed mount associated with VR (CallSid), even though its directory suffix is CO conversation.id. For messaging, managed runtime identity and directory suffix both use conversation.id. This follows the presign/invoke routing and factory storage arguments. No automatic cross-session mount sharing is configured.

Voice lifecycle gap:

~~~mermaid
sequenceDiagram
  participant T as Twilio
  participant L as Lambda
  participant RV as Voice runtime session (CallSid)
  participant RM as Runtime session (conversation ID)
  T->>RV: Voice socket disconnect
  RV->>RV: Remove socket/tasks; keep orchestrated conversation
  T->>L: CONVERSATION_UPDATED CLOSED (data.id=conversation ID)
  L->>RM: invoke_agent_runtime(session=conversation ID)
  RM->>RM: Only messaging_channels.process_webhook
  Note over RV,RM: No VoiceChannel.process_webhook dispatch in TACAgentCoreApp
~~~

**INFERENCE:** even a CLOSED webhook cannot be assumed to evict the voice Agent from RV. This sequence is not a measured leak report. Evidence: `A:src/tac_aws/server/agentcore_app.py:http_handler` and `P:src/tac/channels/voice/conversation_relay/provider.py:_cleanup_connection`.

## 5. Identifiable error paths

~~~mermaid
sequenceDiagram
  participant T as Twilio
  participant L as Lambda
  participant APP as Runtime app
  participant C as TAC channel
  participant S as Connector / Agent
  alt Missing or invalid signature
    T->>L: HTTP webhook
    L-->>T: 403
  else AWS invocation fails
    L->>APP: invoke_agent_runtime
    APP--xL: SDK exception
    L-->>T: 500
  else Channel/reconciliation fails
    L->>APP: invoke_agent_runtime
    APP->>C: process_webhook(token recorded first)
    C--xAPP: Exception, or internally handled drop
    APP->>APP: Log/swallow per-channel failure
    APP-->>L: status=ok, or status=error dict
    L-->>T: 200 success (body not inspected)
  else Agent/model fails
    C->>S: callback
    S->>S: SDK/model retries only where applicable
    S->>C: Generic error text
    C->>T: Attempt outbound Action or voice text frame
    Note over C,T: Delivery may itself fail and be logged without propagation
  end
~~~

Exact error handlers: `A:src/tac_aws/proxy/agentcore_lambda.py:lambda_handler/_handle_conversation_webhook`; `A:src/tac_aws/server/agentcore_app.py:http_handler/_process_webhook`; `A:src/tac_aws/connectors/strands_connector.py:_handle_message`; `P:src/tac/channels/messaging.py:send_response/_handle_communication_created`; `P:src/tac/channels/voice/conversation_relay/provider.py:send_response`.

The duplicate token is recorded before successful completion. A same-token retry can therefore be suppressed after a failed first processing attempt. Conversely restart/eviction/missing token can permit repetition. No layer ties deduplication to model/file/delivery commit. Full [failure and restart matrices](twilio-reference-study.md) document the remaining cases.
