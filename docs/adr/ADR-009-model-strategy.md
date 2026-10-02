# ADR-009 — Deterministic contract Model, optional local LLM, later Bedrock

## Status

Frozen for Stage 1 human review, 2026-10-03; not implemented.

## Context

Reliability tests need controlled tool events, failures and timing. Real stochastic models cannot deterministically exercise crash barriers or prove storage semantics.

## Decision

Default to a scripted Strands Model producing realistic framework tool-use/text events without network. Foundry Local is OPTIONAL exploratory development. Real AWS uses explicit BedrockModel/configuration after local acceptance and cloud authorization. MiniStack is NO. Reserve/count model requests before I/O and constrain combined retry behavior.

## Consequences

One Agent boundary serves all providers. Local real-model availability cannot affect correctness acceptance. Exact real model ID/Region, request mapping, tool capability and package/retry configuration are checked in Stage 2. Bedrock Agents, knowledge bases and RAG are excluded.

## Rejected Alternatives

Only using mocked Agent objects misses real framework behavior. Real LLM output as the correctness oracle is nondeterministic and costly. A Bedrock emulator adds an unneeded protocol/fidelity dependency for current invariants.

## 2027 entry-review clarification — 2026-10-03

The Stage 1 decision means MiniStack is not required by the frozen architecture and is not part of the current emulator choice. It does not bar a future optional validation adapter. At the 2027 Stage 2 entry review, re-evaluate its Bedrock and AgentCore Runtime fidelity. Any adoption or change to this ADR must be recorded in a new or superseding ADR before implementation; this note preserves, rather than silently rewrites, the original Stage 1 decision.
