# Project 2 — Stage 1 Handoff

This package is the frozen handoff from `bedrock-research` into the new Project 2 repository.

## Status at handoff

- Stage 0 — Twilio reference study: PASS
- Stage 0.5 Gate A — reproducibility freeze: PASS with one explicit import/export compatibility alias
- Stage 0.5 Gate B — local messaging reference reproduction: PASS
- Real AWS deployment: NOT VERIFIED
- Real Twilio delivery: NOT VERIFIED
- Real Bedrock inference: NOT VERIFIED
- Voice reproduction: NOT STARTED
- Project 2 product architecture/implementation: NOT STARTED

## Reference source pins

- `twilio/twilio-agent-connect-aws`
  - `fbea687d73f1ab9021793e96e778c2373f3266ff`
- `twilio/twilio-agent-connect-python`
  - `5677eeb72a783de730923121f4ee6b01984faf09`

The full reference repositories are intentionally NOT copied into this new project handoff.
The source-level findings, source map, execution flows, gaps, reproduction code, evidence,
tests, lockfile, and validation package are included instead.

## Read order for Stage 1

Read these before proposing architecture:

1. `research/stage0/twilio-reference-study.md`
2. `research/stage0/twilio-execution-flows.md`
3. `research/stage0/twilio-gap-register.md`
4. `research/stage0/twilio-reproduction-plan.md`
5. `research/stage0/twilio-source-map.md`
6. `research/stage0.5/review-package/docs/stage0.5-reproducibility-freeze.md`
7. `research/stage0.5/review-package/docs/stage0.5-local-messaging-reproduction.md`
8. `research/stage0.5/review-package/REVIEW_README.md`

## Important Stage 0/0.5 findings to preserve as constraints

The reference baseline demonstrated:

- Messaging and Voice are materially different ingress/runtime flows.
- Reference messaging uses process-local bounded idempotency-token suppression, not durable idempotency.
- The token is recorded before successful completion, so a failed first attempt can suppress a same-token retry.
- Lambda success is not a delivery receipt; downstream failures can still lead to success acknowledgement.
- Strands `FileSessionManager` can restore conversation/agent history, but this is not in-flight workflow recovery.
- Model/history persistence and outbound delivery are not one atomic transaction.
- No durable cross-process exactly-once mechanism, whole-turn transaction, delivery reconciliation, or workflow recovery exists in the studied slice.
- The two pinned source trees expose one import-path incompatibility for `generate_twiml`; Stage 0.5 used a minimal compatibility alias without modifying reference source.
- The local Stage 0.5 slice used a deterministic fake model and fake cloud/Twilio transports while preserving the real Lambda/TAC/Strands/Agent boundaries.
- Voice was intentionally not reproduced in Stage 0.5.

## Boundary for the new repository

Everything under `research/` is evidence/reference material.

Do not turn `research/stage0.5/review-package/reproduction/` into the product implementation.
Do not copy the local reproduction harness wholesale into production code.

Stage 1 should produce architecture and contracts for OUR project first.
Implementation starts only after the Stage 1 architecture freeze is explicitly accepted.

## Package integrity

See `MANIFEST.sha256` for hashes of all files in this handoff.
