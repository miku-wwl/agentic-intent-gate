# Changelog

## 0.3.0 - 2026-10-23

- Add startup-configured OpenAI Chat Completions, native Anthropic Messages, and
  operator-configured OpenAI-compatible HTTPS planning providers.
- Keep Ollama as the default, including legacy `LOCAL_LLM_BASE_URL` and
  `LOCAL_LLM_MODEL` configuration. Optional `.env` values are overridden by process
  values of the same name; conflicting new/legacy Ollama aliases fail closed.
- Bound requests and responses, disable redirects and ambient proxies, redact provider
  errors, and reject malformed, incomplete, refused, or tool-call responses. No automatic
  cloud fallback, repair, or retry is added.
- Preserve the public `agentic_intent_gate.runtime` consumer API, strict Plan/risk checks,
  exact approval binding, separate authorization/execution, stable effect identities,
  durable claims/results, CAS, and fail-closed Unknown recovery.
- Preserve the default five-second and explicitly bound extended execution budgets.
- Publish a GitHub GA release with an annotated tag, wheel, sdist, and SHA-256 checksums.
  GA describes this distribution milestone, not enterprise production readiness.

No breaking consumer API or persisted Action/schema changes relative to v0.2.0.
`python-dotenv` is an added runtime dependency. Cloud use is an explicit operator choice;
live cloud/Ollama inference is not part of deterministic qualification. Python 3.13 and
MIT licensing are unchanged. No PyPI publication.

See the [v0.3.0 handoff](docs/V0.3.0-CONSUMER-HANDOFF.md) and
[release notes](https://github.com/miku-wwl/agentic-intent-gate/releases/tag/v0.3.0).

## 0.2.0

Trusted configurable execution budgets with five seconds by default and explicit,
identity-bound profiles up to fifteen seconds. See the
[historical handoff](docs/V0.2.0-CONSUMER-HANDOFF.md).
