# ADR-001 — Strands Agent as the proposal planner

## Status

Frozen for Stage 1 human review, 2026-10-03; not implemented.

## Context

Stage 0.5 retained a real Strands Agent and deterministic Model boundary while reconstructing saved history. Our project requires real tool calling and provider adaptation, with reliability owned outside framework-local state.

## Decision

Use a fresh Strands Agent for each bounded planning attempt, reconstructed from committed domain history. Register authorized read wrappers and proposal-only mutation wrappers. Commit the complete planning snapshot/Actions before external mutation. Once committed, resume fixed Actions directly and produce a deterministic outcome summary; never reinvoke the model for that Turn.

## Consequences

Real Agent behavior is exercised without placing approval/execution safety in a suspended SDK loop. An uncommitted plan may be regenerated; it cannot have caused writes. Dependent post-write replanning and autonomous multi-step mutation loops are outside MVP. Exact dependencies/API compatibility must be pinned and tested independently in Stage 2.

## Rejected Alternatives

Custom agent loop loses the requested framework boundary without a concrete advantage. Framework interrupts alone do not replace durable approval/Action truth. LangGraph/Temporal introduce unrelated orchestration scope. Caching one Agent per conversation is optional optimization and cannot be authoritative.
