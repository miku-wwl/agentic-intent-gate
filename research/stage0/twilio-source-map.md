# Twilio source map — Stage 0

Study date: 2026-10-02, Pacific/Auckland. AWS checkout: `fbea687d73f1ab9021793e96e778c2373f3266ff`. Python checkout: `5677eeb72a783de730923121f4ee6b01984faf09`. See [provenance and dependency caveats](twilio-reference-study.md#evidence-and-provenance).

Paths in the table are **relative to the stated repository directory**. Combine `references/<Repository>/<Path>` for a full source location. Dependency directories are under `references/_dependency-evidence/` and are separately hash-verified package snapshots, not Git substitutes.

Classification: **critical** = chosen execution path; **framework** = reusable library; **infra** = packaging/deployment; **tests** = verification source; **example** = demonstration wiring or alternative path.

## Primary execution and supporting framework

| Concept | Repository | Path | Class / Function | Responsibility |
| --- | --- | --- | --- | --- |
| Lambda entrypoint | twilio-agent-connect-aws | deploy/agentcore_aws_lambda/lambda/index.py | module initialization; lambda_handler alias | Critical: loads secret and env, creates proxy and exposes bound handler |
| Lambda secrets | twilio-agent-connect-aws | deploy/agentcore_aws_lambda/lambda/credentials.py | fetch_twilio_auth_token | Critical: Secrets get_secret_value; token validation; raises on startup failure |
| Ingress / routing | twilio-agent-connect-aws | src/tac_aws/proxy/agentcore_lambda.py | AgentCoreLambdaProxy.__init__/lambda_handler | Critical/framework: region/client setup; signature check and path dispatch |
| Voice bootstrap | twilio-agent-connect-aws | src/tac_aws/proxy/agentcore_lambda.py | _handle_voice_twiml/_parse_form | Critical: CallSid, presigned URL, XML response |
| TwiML layering | twilio-agent-connect-aws | src/tac_aws/proxy/agentcore_lambda.py | _build_twiml_options/_resolve_per_call_options | Framework: default/static/per-call field precedence; sync/async callback |
| Messaging ingress | twilio-agent-connect-aws | src/tac_aws/proxy/agentcore_lambda.py | _handle_conversation_webhook/_normalize_headers | Critical: parse/filter events, forward raw JSON/token, runtimeSessionId |
| Lambda webhook validation | twilio-agent-connect-aws | src/tac_aws/proxy/validation.py | TwilioSignatureValidator.validate/_construct_request_url/_decode_request_body | Critical/framework: Function URL request representation → external validator |
| Runtime application entrypoint | twilio-agent-connect-aws | deploy/agentcore_aws_lambda/agent/main.py | module initialization; app; __main__ | Critical/example: constructs TAC/connector/app, exports SDK app, runs server |
| Agent creation | twilio-agent-connect-aws | deploy/agentcore_aws_lambda/agent/main.py | create_agent | Critical/example: Nova model, system prompt and file manager per conversation |
| Runtime configuration / secrets | twilio-agent-connect-aws | deploy/agentcore_aws_lambda/agent/tac_config.py | get_twilio_credentials/create_tac_config | Critical/example: secret object → TACConfig with runtime env |
| TAC app | twilio-agent-connect-aws | src/tac_aws/server/agentcore_app.py | TACAgentCoreApp.__init__/run | Critical/framework: SDK app and HTTP/WebSocket handler registration |
| AgentCore HTTP handler | twilio-agent-connect-aws | src/tac_aws/server/agentcore_app.py | nested http_handler; _process_webhook | Critical: only messaging_channels; gather; errors logged/swallowed |
| AgentCore voice handler | twilio-agent-connect-aws | src/tac_aws/server/agentcore_app.py | nested websocket_handler | Critical: wraps socket, delegates to VoiceChannel; context unused |
| Voice greeting adapter | twilio-agent-connect-aws | src/tac_aws/server/agentcore_app.py | TACAgentCoreWebSocketAdapter.receive_json | Critical/framework: one greeting after setup; proxies other socket methods |
| Strands connector | twilio-agent-connect-aws | src/tac_aws/connectors/strands_connector.py | StrandsConnector.__init__/_handle_message/_handle_conversation_ended | Critical/framework: cached Agents, first-memory invocation, final-string routing and cleanup |
| Channel selection | twilio-agent-connect-aws | src/tac_aws/connectors/channels.py | ConnectorChannels.__init__/get/send | Critical/framework: voice/SMS/Chat, sender-gated RCS/WhatsApp, context.channel routing |
| TAC Core | twilio-agent-connect-python | src/tac/core/tac.py | TAC.__init__ | Critical/framework: config validation, synchronous CO config discovery, Memory client, callbacks |
| Memory resolution | twilio-agent-connect-python | src/tac/core/tac.py | TAC.retrieve_memory | Critical/framework: profile lookup/traits/Recall; Communications fallback |
| Callback contract | twilio-agent-connect-python | src/tac/core/tac.py | on_message_ready/trigger_message_ready; on_conversation_ended/trigger_conversation_ended | Critical/framework: sync/awaitable callbacks, str-or-None reply contract |
| Error callback | twilio-agent-connect-python | src/tac/core/tac.py | on_error/trigger_error | Framework: reports internally handled failures; primary main registers no handler |
| Partner identification | twilio-agent-connect-python | src/tac/core/tac.py | register_partner_connector | Critical/framework: identifies AWS connector in Twilio HTTP User-Agent |
| Shared TAC channel | twilio-agent-connect-python | src/tac/channels/base.py | BaseChannel.__init__/_start_conversation/_end_conversation | Critical/framework: local session dict and lifecycle callbacks |
| Webhook deduplication | twilio-agent-connect-python | src/tac/channels/base.py | _is_duplicate_webhook | Critical/framework: bounded OrderedDict, inserts before handling, no durable record |
| Channel filter | twilio-agent-connect-python | src/tac/channels/base.py | _is_event_for_this_channel | Critical/framework: communication author.channel; tracked conversation updates |
| Memory cache | twilio-agent-connect-python | src/tac/channels/base.py | _retrieve_memory_if_enabled | Critical/framework: always/once/never, once cache_lock, failure tolerance |
| Messaging input | twilio-agent-connect-python | src/tac/channels/messaging.py | MessagingChannel.process_webhook/_handle_communication_created | Critical/framework: token, schema/filter/self-message, session, callback |
| Participant reconciliation | twilio-agent-connect-python | src/tac/channels/messaging.py | _reconcile_participants/_promote_participant/_add_agent_participant | Critical/framework: type resolution/promote/add; failure drops inbound |
| Profile side effect | twilio-agent-connect-python | src/tac/channels/messaging.py | _resolve_customer_profile | Critical/framework: lookup or create Memory profile for customer where possible |
| Messaging lifecycle | twilio-agent-connect-python | src/tac/channels/messaging.py | _handle_conversation_updated | Critical/framework: CLOSED end; INACTIVE clears once cache |
| Messaging output | twilio-agent-connect-python | src/tac/channels/messaging.py | send_response/_build_channel_settings | Critical/framework: participant-based SendMessageActionRequest and Actions API |
| SMS | twilio-agent-connect-python | src/tac/channels/sms.py | SMSChannel.__init__/get_channel_name/get_agent_address | Critical/framework: sender validation and SMS-specific identity |
| Other messaging | twilio-agent-connect-python | src/tac/channels/chat.py; src/tac/channels/rcs.py; src/tac/channels/whatsapp.py | ChatChannel/RCSChannel/WhatsAppChannel | Framework: channel identity/config and shared messaging path |
| Voice TAC channel | twilio-agent-connect-python | src/tac/channels/voice/channel.py | VoiceChannel.__init__/handle_websocket/send_response/process_webhook | Critical/framework: default provider, delegated transport; cleanup handler not wired by AgentCore HTTP app |
| Voice default config | twilio-agent-connect-python | src/tac/channels/voice/conversation_relay/config.py | ConversationRelayProviderConfig; VoiceChannelConfig compatibility alias | Critical/framework: ThreadSafeSessionManager default and voice options |
| Voice session identity | twilio-agent-connect-python | src/tac/channels/voice/conversation_relay/provider.py | handle_websocket/_initialize_conversation | Critical/framework: setup CallSid → CO conversation ID; background lookup/poll |
| Voice prompt/interrupt | twilio-agent-connect-python | src/tac/channels/voice/conversation_relay/provider.py | _handle_prompt_async/_handle_prompt/_handle_interrupt_async | Critical/framework: final prompts, cancel previous task, memory and TAC callback |
| Voice output/disconnect | twilio-agent-connect-python | src/tac/channels/voice/conversation_relay/provider.py | send_response/_cleanup_connection | Critical/framework: text/last frames, socket/task removal; retain orchestrated conversation |
| Voice TwiML | twilio-agent-connect-python | src/tac/channels/voice/conversation_relay/twiml.py | generate_twiml; TwiMLBuilderConversationRelay | Critical/framework: XML <Connect><ConversationRelay> and configurable attributes |
| Shared TwiML scaffolding | twilio-agent-connect-python | src/tac/channels/voice/twiml.py | TwiMLBuilderBase | Framework: field layering and default WebSocket URL helpers |
| Socket contract/routing | twilio-agent-connect-python | src/tac/channels/websocket_protocol.py; src/tac/channels/websocket_manager.py | WebSocketProtocol; WebSocketManager | Critical/framework: duck-typed socket API, conversation → connection mapping |
| TAC session object | twilio-agent-connect-python | src/tac/models/session.py | ConversationSession; AuthorInfo | Critical/framework: conversation/call IDs, profile/participants/metadata/cache/lock |
| Voice JSON schemas | twilio-agent-connect-python | src/tac/models/voice.py | SetupMessage/PromptMessage/InterruptMessage/TwiMLRequest | Critical/framework: aliases/validation for CallSid, voicePrompt and control frames |
| Messaging schemas | twilio-agent-connect-python | src/tac/models/conversation.py | Communication/ConversationResponse/SendMessageActionRequest | Critical/framework: incoming event objects and outbound action wire format |
| TAC task-session interface | twilio-agent-connect-python | src/tac/session/base.py | SessionManager | Framework: task-state abstraction, separate from Strands history persistence |
| TAC task-session map | twilio-agent-connect-python | src/tac/session/thread_safe.py | ThreadSafeSessionManager | Critical/framework: RLock-protected local session dictionary |
| Task cancellation | twilio-agent-connect-python | src/tac/session/state.py | SessionState.cancel_stream_task | Critical/framework: cancel + wait up to 5 seconds |
| Twilio HTTP boundary | twilio-agent-connect-python | src/tac/context/base.py | BaseAPIClient._build_base_url/_get_client/_get_sync_client | Critical/framework: Basic auth, HTTPS, region, 30s timeout, User-Agent |
| Conversation API | twilio-agent-connect-python | src/tac/context/conversation.py | ConversationClient.get_configuration/list_conversations/list_participants/update_participant/add_participant/list_communications/create_action | Critical/framework: configuration, identity resolution, context fallback, delivery |
| Memory API | twilio-agent-connect-python | src/tac/context/memory.py | MemoryClient.lookup_profile/get_profile/retrieve_memory/create_profile | Critical/framework: remote profile/context and optional profile creation |
| Memory prompt | twilio-agent-connect-python | src/tac/adapters/prompt_builder.py | MemoryPromptBuilder.build | Critical/framework: TAC context → first Agent invocation text |
| Core configuration | twilio-agent-connect-python | src/tac/core/config.py | TACConfig/TwilioMemoryConfig/ConversationIntelligenceConfig | Framework: model validation/defaults/from_env; primary factory uses direct TACConfig |
| Logs and redaction | twilio-agent-connect-python | src/tac/core/logging.py; src/tac/utils/redaction.py | ContextLogger/setup_logging/JSONFormatter; masking helpers | Framework: contextual log fields and address scrubbing |
| Product telemetry | twilio-agent-connect-python | src/tac/core/analytics.py | _get_client/track_event/shutdown_analytics | Framework: Segment best-effort events and opt-out; not delivery logging |

## Infrastructure, packaging, examples and optional surfaces

| Concept | Repository | Path | Class / Function | Responsibility |
| --- | --- | --- | --- | --- |
| Stack composition | twilio-agent-connect-aws | deploy/agentcore_aws_lambda/cdk/bin/app.ts | main | Infra: reads project spec/env, injects runtime env, orders three stacks |
| Deployment env | twilio-agent-connect-aws | deploy/agentcore_aws_lambda/cdk/config/env-config.ts | loadEnvConfig | Infra: .env parsing and required AWS/Twilio nonsecret inputs |
| AgentCore stack | twilio-agent-connect-aws | deploy/agentcore_aws_lambda/cdk/lib/agentcore-stack.ts | AgentCoreStack | Infra: external application construct, runtime ARN and secret grant |
| Lambda stack | twilio-agent-connect-aws | deploy/agentcore_aws_lambda/cdk/lib/lambda-stack.ts | LambdaStack | Infra: Python 3.13/30s/1024MB, secret/invoke IAM, public Function URL |
| Secrets stack | twilio-agent-connect-aws | deploy/agentcore_aws_lambda/cdk/lib/secrets-stack.ts | SecretsStack | Infra: tac/twilio-credentials, DESTROY policy |
| Runtime spec | twilio-agent-connect-aws | deploy/agentcore_aws_lambda/cdk/agentcore/agentcore.json | runtimes[0] / filesystemConfigurations | Infra: CodeZip/Python 3.14/PUBLIC/HTTP, managed session mount, no Memory resource |
| Deployment scripts | twilio-agent-connect-aws | deploy/agentcore_aws_lambda/Makefile | bootstrap/build/secret-update/deploy-all/destroy | Infra: build/bootstrap/deploy workflow; read only during study |
| Deployment policy | twilio-agent-connect-aws | deploy/agentcore_aws_lambda/iam-policy.json | policy Statements | Infra/support: example deployer permissions, not application role or proven complete policy |
| Lambda requirements | twilio-agent-connect-aws | deploy/agentcore_aws_lambda/lambda/requirements.txt | dependency list | Infra: package version 2.0.0; transitive versions unresolved |
| Runtime dependencies | twilio-agent-connect-aws | deploy/agentcore_aws_lambda/agent/pyproject.toml; uv.lock | project metadata / package entries | Infra: current manifest vs stale TAC lock snapshot |
| AWS library packaging | twilio-agent-connect-aws | pyproject.toml; uv.lock; src/tac_aws/__init__.py | optional extras/package exports | Framework/packaging: separate Strands/Bedrock/AgentCore/server extras |
| Python library packaging/CLI | twilio-agent-connect-python | pyproject.toml; src/tac/__init__.py; src/tac/__main__.py | package exports / module-level version print | Framework/packaging: TAC 2.5.0, optional server and partner examples; CLI is not chosen deployment entrypoint |
| Local server | twilio-agent-connect-python | src/tac/server/fastapi_server.py; src/tac/server/config.py; src/tac/server/signature_validation.py | TACFastAPIServer/_register_routes; TACServerConfig; signature dependencies | Framework: voice+messaging webhooks and socket routes, separate validation implementation |
| AWS local/Fargate server | twilio-agent-connect-aws | src/tac_aws/server/fastapi_server.py; src/tac_aws/server/middleware.py | TACAWSFastAPIServer; ALBForwardedHeadersMiddleware | Framework: FastAPI wrapper/proxy header adaptation, not primary AgentCore app |
| Alternative Bedrock Agents | twilio-agent-connect-aws | src/tac_aws/connectors/bedrock_connector.py | BedrockConnector._build_invoke_fn_from_config/_handle_message/_parse_response | Framework: invoke_agent agentId/alias path, not BedrockModel ConverseStream |
| Alternative AgentCore client | twilio-agent-connect-aws | src/tac_aws/connectors/bedrock_agentcore/connector.py; src/tac_aws/connectors/bedrock_agentcore/http.py; src/tac_aws/connectors/bedrock_agentcore/websocket.py; src/tac_aws/connectors/bedrock_agentcore/config.py | BedrockAgentCoreConnector; handle_http_message/parse_streaming_response; handle_websocket_message | Framework: frontend TAC → separate agent runtime, supports SSE/WSS |
| Fargate examples | twilio-agent-connect-aws | deploy/strands_aws_fargate/; deploy/bedrock_aws_fargate/; deploy/agentcore_aws_fargate/ | *_server.py; agent/agent.py; Dockerfile; cloudformation.yaml | Infra/examples: ECS/ALB/container alternatives, separate lifecycle and resources |
| Getting started | twilio-agent-connect-aws | getting_started/examples/strands_agents.py; getting_started/examples/bedrock_agents.py; getting_started/examples/bedrock_agentcore_agents.py; getting_started/examples/.env.example | create_agent / example wiring | Examples: server/connector choices; not deployed proof |
| Python partner examples | twilio-agent-connect-python | getting_started/examples/partners/aws_strands.py; getting_started/examples/partners/aws_bedrock_agentcore.py; getting_started/examples/partners/aws_bedrock_agent.py | example callbacks/factories | Examples: SDK-oriented alternative integration code |
| Optional Strands Memory tool | twilio-agent-connect-aws | src/tac_aws/tools/strands/memory.py | create_memory_tool / nested retrieve_twilio_memory | Framework: @tool wrapper; absent from primary Agent tools |
| Optional TAC tools | twilio-agent-connect-python | src/tac/tools/memory.py; src/tac/tools/knowledge.py; src/tac/tools/handoff.py; src/tac/tools/base.py | create_memory_tool/create_knowledge_tool/create_studio_handoff_tool; TACTool | Framework: neutral schemas/injection and optional SDK adapters, Memory/Knowledge/Studio operations; not main Strands wiring |
| Optional intelligence | twilio-agent-connect-python | src/tac/intelligence/operator_result_processor.py | OperatorResultProcessor | Framework: configured CI processing/writes; not selected main configuration |
| Optional audio transports | twilio-agent-connect-python | src/tac/channels/voice/media_streams/ | OpenAIRealtimeProvider/GPTLiveProvider | Framework: separate raw-audio/OpenAI paths, not Nova/ConversationRelay slice |
| AWS CI/test configuration | twilio-agent-connect-aws | .github/workflows/ci.yml; pyproject.toml; pytest.ini | CI jobs / pytest settings | Support: lint/type/test definitions; not executed study evidence |
| Python CI/test configuration | twilio-agent-connect-python | .github/workflows/ci.yml; pyproject.toml | CI jobs / pytest settings | Support: lint/type/test definitions; not executed study evidence |

## External packages inspected

| Concept | Repository | Path | Class / Function | Responsibility |
| --- | --- | --- | --- | --- |
| Agent/runtime model | _dependency-evidence/strands-agents-1.41.0 | strands/agent/agent.py | Agent.__init__/invoke_async/stream_async/cleanup | External: inference/history, final result, object-level concurrency guard, tool cleanup |
| Model adapter | _dependency-evidence/strands-agents-1.41.0 | strands/models/bedrock.py | BedrockModel.__init__/_format_request/stream/_stream | External: boto3 credentials/region/config; ConverseStream and event queue |
| Inference loop | _dependency-evidence/strands-agents-1.41.0 | strands/event_loop/event_loop.py; strands/event_loop/_retry.py | event_loop_cycle/_handle_model_execution; ModelRetryStrategy | External: model/tool loop and default throttling retries |
| Session hooks | _dependency-evidence/strands-agents-1.41.0 | strands/session/session_manager.py | SessionManager.register_hooks | External: restore/append/sync lifecycle subscriptions |
| History restore/snapshot | _dependency-evidence/strands-agents-1.41.0 | strands/session/repository_session_manager.py | RepositorySessionManager.__init__/initialize/append_message/sync_agent | External: session creation/read and Agent restoration |
| File session manager | _dependency-evidence/strands-agents-1.41.0 | strands/session/file_session_manager.py | FileSessionManager._write_file/read_session/read_agent/list_messages/delete_session | External: JSON file structure, atomic replacement, no multi-writer transaction |
| AgentCore ASGI SDK | _dependency-evidence/bedrock-agentcore-1.11.0 | bedrock_agentcore/runtime/app.py | BedrockAgentCoreApp._handle_invocation/_invoke_handler/_handle_websocket/run | External: routes, contexts, worker loop and JSON/SSE response |
| Presigned URL SDK | _dependency-evidence/bedrock-agentcore-1.8.0 | bedrock_agentcore/runtime/agent_core_runtime_client.py | AgentCoreRuntimeClient.generate_presigned_url | External library-lock snapshot: SigV4 query auth and session query parameter |
| SDK retry/default config | _dependency-evidence/botocore-1.43.15 | botocore/configprovider.py; botocore/config.py; botocore/data/_retry.json; botocore/credentials.py | configuration providers / legacy retry data / credential resolver | External: defaults and overrides; not app retry policy |
| Twilio signature SDK | _dependency-evidence/twilio-9.10.9 | twilio/request_validator.py | RequestValidator.validate/compute_signature/compute_hash | External runtime-lock snapshot: HMAC/body hash validation |
| Runtime construct | _dependency-evidence/agentcore-cdk-0.1.0-alpha.35 | package/dist/cdk/constructs/components/primitives/runtime/AgentCoreRuntime.js | AgentCoreRuntime.constructor | External: role grants, CodeZip entrypoint/OTel, CfnRuntime and filesystem mappings |
| Build-mode selection | _dependency-evidence/agentcore-cdk-0.1.0-alpha.35 | package/dist/cdk/constructs/l3/AgentEnvironment.js | AgentEnvironment.constructor | External: CodeZip vs Container/ECR/CodeBuild branches |
| Runtime archive | _dependency-evidence/agentcore-cdk-0.1.0-alpha.35 | package/dist/cdk/constructs/bundling/zip/PythonBundledCodeZipAsset.js | PythonBundledCodeZipAsset.packageCode | External: packager and CDK S3 asset; may write OS temp if executed |
| Dependency packaging | _dependency-evidence/agentcore-cdk-0.1.0-alpha.35 | package/dist/lib/packaging/python.js | PythonCodeZipPackagerSync.installWithRetriesSync | External: target platform install from pyproject; does not freeze uv.lock |

## Relevant tests

| Concept | Repository | Path | Class / Function | Responsibility |
| --- | --- | --- | --- | --- |
| Lambda unit contracts | twilio-agent-connect-aws | tests/test_agentcore_lambda.py | TestAgentCoreLambdaProxy/TestAgentCoreLambdaProxyTwiML | Parsing, header handling, patched voice proxy/TwiML options |
| Signature adaptation | twilio-agent-connect-aws | tests/test_validation.py | TestTwilioSignatureValidator | Formatting/delegation; signature validator calls mocked |
| Strands lifecycle | twilio-agent-connect-aws | tests/test_strands_connector.py | TestStrandsConnector | Mock Agent factory/reuse/memory/routing/error/cleanup |
| Channel routing | twilio-agent-connect-aws | tests/test_connector_channels.py | TestChannelCreation/TestRouting/TestConnectorExposure | Channel enablement and stream buffering/routing |
| Optional Memory tool | twilio-agent-connect-aws | tests/test_memory_tool.py | TestMemoryTool | Mock API/schema/call/result/error behavior |
| Alternative connectors | twilio-agent-connect-aws | tests/test_bedrock_connector.py; tests/test_bedrock_agentcore_connector.py | connector test classes | Mock invocation/stream/error behavior |
| Package interface | twilio-agent-connect-aws | tests/test_package.py; tests/conftest.py | import/export tests; mock fixtures | Optional-dependency skips and mocked TAC/Agent |
| TAC integration | twilio-agent-connect-python | tests/test_integration.py | TestTACIntegration.test_sms_channel_end_to_end_workflow | Mocked multi-component SMS flow, not real-cloud E2E |
| Voice and tasks | twilio-agent-connect-python | tests/test_voice_channel.py; tests/test_interrupt.py; tests/test_session.py; tests/test_session_manager.py | voice/provider/task/map tests | Setup, polling, disconnect, cancellation, RLock and session independence |
| Deduplication | twilio-agent-connect-python | tests/test_chat_channel.py; tests/test_rcs_channel.py; tests/test_whatsapp_channel.py | test_deduplication/test_webhook_deduplication | Same-token sequential suppression |
| Messaging lifecycle/delivery | twilio-agent-connect-python | tests/test_sms_channel.py; tests/test_chat_channel.py; tests/test_rcs_channel.py; tests/test_whatsapp_channel.py | messaging/channel tests | Filtering, participant-aware responses, cache/session lifecycle |
| Reconciliation/error callbacks | twilio-agent-connect-python | tests/test_participant_reconciliation.py; tests/test_error_callback.py | promotion/409/drop tests | Mock external state transitions and recoverable error reporting |
| Memory/cache | twilio-agent-connect-python | tests/test_memory_mode_once.py; tests/test_memory_fallback.py; tests/test_profile_retrieval.py; tests/test_profile_lookup_in_memory.py; tests/test_recall_parsing_resilience.py | memory/cache/profile tests | Once lock/invalidation, API fallback, malformed items |
| Cryptographic/server contracts | twilio-agent-connect-python | tests/test_webhook_validation.py; tests/test_server.py | TestValidateTwilioWebhook and route tests | Computed signatures, forged body rejection, FastAPI dependencies |
| Logs/telemetry/config | twilio-agent-connect-python | tests/test_analytics.py; tests/test_redaction.py; tests/test_http_client_config.py; tests/test_config.py; tests/test_config_from_env.py | fixture/config/telemetry tests | Local logger/config behavior; Segment mocked |
| Optional features | twilio-agent-connect-python | tests/test_tools.py; tests/test_handoff.py; tests/test_knowledge.py; tests/test_intelligence.py; tests/test_outbound.py; tests/test_openai_realtime_provider.py; tests/test_gpt_live_provider.py | optional feature tests | Separate surfaces, not active primary Agent capabilities |
| Test isolation | twilio-agent-connect-python | tests/conftest.py | mock_conversation_configuration | Disables telemetry and mocks startup Twilio configuration retrieval |

No tests were executed here. A tests directory or “integration/end_to_end” name does not establish a real AWS/Twilio E2E result. No AWS test module directly covers TACAgentCoreApp or its greeting adapter; deployment/session-storage behavior remains unverified.
