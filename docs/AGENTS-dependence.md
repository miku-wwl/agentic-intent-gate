# Hackathon Agent Instructions — Reuse Agentic Intent Gate

## Purpose

This repository is a **Hackathon application that consumes an existing Python Agent Runtime**. Build the competition-specific product, not another agent execution framework.

**Shared runtime:** [Agentic Intent Gate](https://github.com/miku-wwl/agentic-intent-gate)<br>
**Python distribution:** `agentic-intent-gate`<br>
**Public integration module:** `agentic_intent_gate.runtime`<br>
**Qualified version target:** `v0.1.0` (verified annotated Git tag)<br>
**Pinned release commit:** `fd742b783058a53400c465b4ab59c6e80715895a`

> The tag above peels to the pinned commit. Use the full commit SHA, not a moving `main` branch, for reproducible builds. When upgrading, verify the new release tag and pin its peeled commit. Private-repository access requires authenticated Git in local development and CI; never embed tokens in URLs, code, or logs.

## Dependency Setup

Declare the dependency in this project's `pyproject.toml` (adapt to its existing dependency-management format):

```toml
[project]
dependencies = [
  "agentic-intent-gate @ git+https://github.com/miku-wwl/agentic-intent-gate.git@fd742b783058a53400c465b4ab59c6e80715895a",
]
```

Do not replace an existing `[project]` or `dependencies` section blindly; merge the entry into the current configuration. Confirm the installed package resolves to the pinned commit. A Git tag identifies a release; the full commit SHA is the recommended immutable dependency reference.

## Reuse the Core

The shared Runtime already implements these mechanisms within its supported scope:

- Strict structured Plan validation and trusted tool schemas
- Persistent Runs and exact Plan-to-Approval binding
- Independent execution authorization
- Explicit trusted `ToolExecutor` registration
- Single-step `SIDE_EFFECT` Action execution
- SQLite-backed durable Action state and receipts
- CAS/concurrency protections, effect identity, idempotency safeguards
- Explicit ambiguous/`Unknown` outcome behavior (no blind retry)

**Do not copy, fork, or reimplement these runtime capabilities in this repository.** Keep the core package version pinned, and use its published integration API rather than internal monkeypatches.

## Public Integration Entry Point

Start by inspecting the **installed version** of `agentic_intent_gate.runtime` and the core repository's `examples/consumer/app.py`. The public integration surface includes:

```python
from agentic_intent_gate.runtime import (
    ToolBinding,
    ToolCapability,
    ExecutableRegistry,
    BoundCapabilityAuthorization,
    open_runtime,
)
```

Check actual constructor/function signatures; do not guess or invent methods. Register consumer executors explicitly in trusted startup code, with bounded input schema, exact tool/version/target/adapter identity, execution scope, and authorization.

## Competition-Specific Responsibilities

Implement **only** the requirements specific to this Hackathon:

1. Product concept, required competition integrations, frontend and demo experience.
2. Trusted consumer tool definitions, strict input schemas, and concrete executors.
3. External service/API/MCP clients where genuinely required by the competition.
4. Consumer-specific authorization and credentials management.
5. An appropriate consumer `ModelProvider`, if model-driven Plan generation is required.
6. Product-specific tests, evaluation, deployment, documentation and submission.

**Demo boundaries:** The core's built-in local LLM prompt is tailored to `service-demo/service_id`. Its bundled UI admission controls are also Demo-specific. Non-Demo consumers must provide suitable planning behavior and use the supported HTTP/API admission path or build their own UI. Do not claim the bundled Demo UI automatically works for arbitrary consumer tools.

## Supported Scope and Non-Goals

The qualified first baseline supports **one explicitly registered trusted `SIDE_EFFECT` tool per runtime composition and single-step execution**. It is **not** a general-purpose multi-agent workflow engine. Do not assume support for generic `READ_ONLY` execution, multi-tool/multi-step orchestration, dynamic plugin discovery, arbitrary MCP execution, production multi-user authentication, distributed HA, or guaranteed exactly-once remote side effects.

If a competition requires one of these features, identify the missing capability before implementation. Prefer a thin competition-owned layer where safe. Propose a separately reviewed minimal core extension only when unavoidable; never silently bypass core safety gates.

## Mandatory Safety Boundaries

- Model output proposes a Plan; it never registers executors, grants authorization, selects arbitrary Python code, or defines trusted credentials or scopes.
- Validate the exact tool identity, target, schema, and trusted binding before compiling or executing.
- Human Approval and execution Authorization are **separate gates**; approval alone must never trigger a side effect.
- Never substitute an approval for a different Plan or skip durable Action claiming.
- Treat ambiguous effects as `Unknown`: no automatic retry or unsafe reconciliation.
- Where an external provider offers idempotency keys, use its documented guarantees; do not promise exactly-once delivery without proof.
- Do not place secrets, capability tokens, or private Git credentials in source, examples, CI logs, or screenshots.

## Acceptance Criteria

An adapter/integration is ready only when an external consumer, using the pinned installed package and **without modifying core source**, can demonstrate:

**Validated Plan → Persistent Run → Exact Approval → Authorization → Durable Action → Consumer Executor → Verified Effect → Receipt**

Cover at least the happy path, unknown/unregistered tool, wrong target/binding/scope, missing authorization, stale approval, duplicate or conflicting effect, and ambiguous outcome. Verify persisted effects, not only printed success messages. Tests can use deterministic planning without a live model.

## Agent Work Protocol

Before changing code:

1. Read this file and inspect existing project and competition requirements.
2. Confirm the pinned dependency installs and inspect the *actual* installed public API.
3. Read `examples/consumer/app.py` in the core repository as the reference pattern.
4. Propose the smallest product-specific implementation and tests.
5. Highlight any feature the current runtime does not support rather than inventing an API.

During work, keep changes scoped and preserve the core's approval, authorization, identity, idempotency, and durable-execution guarantees. Do not commit, push, tag, or release unless explicitly instructed.

**Guiding rule: Build the Hackathon application; reuse the Agentic Intent Gate Runtime.**
