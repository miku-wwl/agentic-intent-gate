"""Local schema resources and strict value codecs shared by the Day 2 contracts."""

import json
import re
from datetime import UTC, datetime
from functools import lru_cache
from uuid import UUID

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from agentic_intent_gate.domain.validation import fail, load_schema

MAX_VERSION = 9007199254740991
SCHEMA_BASE = "https://agentic-intent-gate.invalid/schemas/"


def canonical_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def valid_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = UUID(value)
        return parsed.version == 4 and str(parsed) == value
    except ValueError:
        return False


def parse_timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z", value
    ):
        fail("RUN_TIMESTAMP")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except ValueError:
        fail("RUN_TIMESTAMP")


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


@lru_cache(maxsize=2)
def contract_validator(name: str) -> Draft202012Validator:
    return build_contract_validator(name)


def build_contract_validator(name: str, *, plan_schema=None) -> Draft202012Validator:
    """Compose trusted consumer Plan shape without changing Run lifecycle rules/resources."""
    # Registry has no remote retriever. Every reference resolves to a bundled resource.
    registry = Registry()
    schemas = {}
    for filename in (
        "day1-intent.schema.json",
        "day1-plan.schema.json",
        "day2-run.schema.json",
        "day2-approval.schema.json",
    ):
        schema = (
            plan_schema
            if filename == "day1-plan.schema.json" and plan_schema is not None
            else load_schema(filename)
        ) | {"$id": SCHEMA_BASE + filename}
        schemas[filename] = schema
        registry = registry.with_resource(SCHEMA_BASE + filename, Resource.from_contents(schema))
    return Draft202012Validator(schemas[name], registry=registry)


def check_contract(name: str, value: object) -> None:
    if not contract_validator(name).is_valid(value):
        fail("RUN_CONTRACT")
