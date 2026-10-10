# Adapter guide

Keep Hackathon/provider integrations removable and outside the core runtime. The existing
port is a boundary to implement, **not** a dynamic plugin registration API.

## Existing execution contract

The source of truth is
[tool_executor.py](../src/agentic_intent_gate/ports/tool_executor.py):

~~~python
async def execute(
    self, spec: ExecutionSpec, effect_key: str, context: AttemptContext
) -> ToolOutcome:
    ...
~~~

`ExecutionSpec` includes `tool_name`, `tool_version`, `target`, immutable `arguments`,
`adapter_id`, `binding_revision`, and `execution_scope`. The trusted compiler/registry
supply executable identity; a Plan cannot choose a new adapter, endpoint, credential,
or scope. A capability and persisted grant authorize the compiled Action before dispatch.

Validate the exact tool specification at the adapter boundary even after compilation.
Use exact types (not coercion), allowed keys, byte/size bounds, trusted target/scope, and
deterministic canonical arguments. Do not mutate the supplied arguments or silently
normalize one request into a different authorized effect.

`AttemptContext` contains an attempt ID, monotonic deadline, and cancellation event.
Check it before dispatch; bound provider operations. Cancellation/timeout after dispatch
does not prove no effect. An async interface cannot forcibly cancel a blocking SQLite
operation or reverse an accepted external request.

## Effect identity, receipts, and outcomes

Preserve the gateway's stable, admission-persisted UUIDv4 effect key; do not generate a
new one for a replay. HTTP command idempotency keys are separate from this effect identity.
Atomically reserve/bind it to the exact payload and execution scope in the effect store.
Persist the effect and a durable, unique receipt before reporting success.

- Identical key/payload/scope: return the original receipt, without repeating the effect.
- Same key with different payload: reject as a conflict; never replace the original row.
- Wrong scope/store identity: reject without effect.
- Known validation/conflict/identity failure: report confirmed failure only with reliable
  no-effect evidence.
- Exception, timeout, lost commit acknowledgement, or uncertain external response:
  report `UNKNOWN_OUTCOME`; do not retry blindly.

The gateway checks correlated effect key, execution scope, target, and frozen payload
digest. Success requires a valid UUID receipt and no failure reason. Confirmed failure
requires no receipt and a recognized `INPUT_REJECTED`, `PAYLOAD_CONFLICT`, or
`STORE_IDENTITY_MISMATCH` reason. Unsupported/malformed outcomes fail closed to Unknown.

The current `ExecutionSpec.payload_digest()` is demo-specific
(`demo-note-payload-v1`), not a general adapter digest framework. A new tool needs an
explicitly reviewed identity strategy and persisted compatibility behavior. Gateway
commit ambiguity is separate from effect-store ambiguity; neither permits a second
unclaimed dispatch. There is no implemented automatic Unknown reconciliation/retry API.

## Day 4 Dummy reference

[DeterministicDummyExecutor](../src/agentic_intent_gate/adapters/dummy_executor.py)
implements `dummy_record/1` for `test-local-dummy`, adapter `day4-dummy`, revision
`dummy-v1`, and a trusted canonical UUIDv4 scope.

Its only argument is a nonempty exact string `record`, bounded to 256 UTF-8 bytes and
1,024 bytes of canonical arguments. Its `dummy-record-v1` receipt digest includes the
complete adapter binding and validated arguments, in addition to the gateway-correlated
frozen digest.

The trusted injected `DummyReceiptStore.apply_once` returns typed `StoreApplyResult`.
The concrete SQLite store exists in
[test_dummy_adapter_contract.py](../tests/test_dummy_adapter_contract.py), not the public
runtime: STRICT tables, real test-owned files, `BEGIN IMMEDIATE`, parameterized SQL,
scope identity checks, atomic effect/receipt writes, and explicit connection cleanup.
The executor makes one store call. Tests cover replay, conflicting/concurrent calls,
storage errors, and commit-success/acknowledgement-loss → Unknown. A later explicit
direct replay can recover the original receipt; that is not an automatic Action retry.

The 105 contract tests and eight direct integration tests are the reference suite.
They do not establish production registration, browser execution, or external API safety.
The Dummy is not a public production tool and is not connected to the production Action
registry or UI. UUID correlation is not cryptographic proof from an untrusted store;
the injected Python store and host are trusted.

## Minimal Hackathon workflow

1. Place isolated provider code under `src/agentic_intent_gate/adapters/` in a clearly
   competition-specific module; keep provider credentials/policies out of domain and
   application code. Define a small allowed capability, stable identity/revision, trusted
   targets/scope, and bounded argument contract.
2. Write a deterministic input validator and canonical identity tests before networking.
   Reject model-selected URLs, arbitrary commands, extra keys, and ambiguous values.
3. Implement the port with separate provider credentials. Verify authorization at the
   runtime boundary; never treat approval text or an unverified client token as authority.
4. Use provider-native idempotency where available, a durable local receipt ledger, and
   bounded single-dispatch requests. Confirm the provider's key retention/conflict rules.
   A local SQLite receipt alone cannot guarantee exactly-once remote effects.
5. Map provider failures conservatively. Only prove no effect when the API contract and
   evidence support it; ambiguous acceptance/timeouts remain Unknown. Never log tokens,
   raw authorization headers, or unnecessary sensitive payloads.
6. Add focused tests for identity/input rejection, capability/policy denial, identical
   replay, payload/scope conflicts, parallel requests, receipt persistence across restart,
   cancellation, known no-effect failures, and ambiguous commits/responses.
7. Demonstrate a complete safe workflow: validated Plan → exact human approval →
   trusted compilation → verified authorization → durable claim → one effect →
   correlated persistent receipt → safe read/refresh; demonstrate denial and Unknown too.

**NOT IMPLEMENTED:** generic adapter registration, arbitrary tool compilation, general
payload-digest negotiation, generic result presentation, MCP integration, and public
reconciliation. To integrate a new adapter into the real application, separately review
the schema/catalog, compiler, concrete registry restrictions, authorization policy,
composition, persisted identities, and UI result handling. Merely adding a Python module
does not make a tool executable. Keep those changes explicit and regression-tested.

See [Architecture](ARCHITECTURE.md), [Development](DEVELOPMENT.md), and
[Security](SECURITY.md) before crossing the execution boundary.
