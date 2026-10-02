# ADR-008 — Messaging/HTTP first, voice later

## Status

Frozen for Stage 1 human review, 2026-10-03; not implemented.

## Context

The reference reports show materially different messaging and voice routes/identities. Stage 0.5 validated messaging only; live voice, telephony and cancellation behavior remained unverified.

## Decision

MVP uses generic text HTTP/CLI ingress and durable operation status. Voice is a post-MVP optional channel adapter. Core contracts use conversation/event/actor IDs and channel metadata, not Twilio DTOs. No RAG, media-streaming, ASR/TTS or frontend is required.

## Consequences

The demo can focus on approval/idempotency/recovery. A later voice adapter must design interruption, delivery and runtime-session mapping independently and preserve core action safety.

## Rejected Alternatives

Building Twilio/voice first would import transport/deployment costs and ambiguous cancellation semantics without improving the central mutation invariants. Treating voice and messaging as one reference execution flow contradicts supplied evidence.
