# Development instructions

Read this file, then only task-relevant code, tests, and documentation. Prefer targeted
searches over indiscriminate repository-wide reading. Inspect existing implementations
before designing or coding; distinguish implemented behavior from proposed integration.

Use explicit module imports. Internal subpackages use PEP 420; do not add package markers
or root re-exports without a verified need. Tool implementations belong in `infrastructure/tools/`.

Preserve the Day 1–4 runtime invariants: strict Plan validation, deterministic risk,
durable Run/approval binding, trusted Action compilation, verified authorization,
CAS, durable execution claims/results, stable effect identities, and fail-closed Unknown
handling. Approval is not execution. Treat the approval/execution boundary as
security-sensitive; never silently alter its meaning.

Prefer additive, removable competition adapters. Keep competition policies, provider
credentials, and external API details out of the core runtime. Any integration into the
compiler, registry, authorization, schema, or composition needs explicit review.
The isolated Dummy Adapter is not a production registration mechanism.

Minimize unrelated changes. Use focused tests during development; run broad regression
when the change or requested acceptance justifies it. Never silently weaken assertions,
skip failures to manufacture success, or invent passing results. Report the actual
changed files, executed commands, results, and checks that were not run.

Never stage, commit, push, or tag unless explicitly requested. Never reset, clean, stash,
rebase, or force-push without explicit authorization. Preserve user changes and existing
tags. Follow the current task's branch and Git constraints; do not infer publication
authority from an implementation request.
