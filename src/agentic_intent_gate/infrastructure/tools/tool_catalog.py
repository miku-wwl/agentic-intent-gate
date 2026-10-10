"""Trusted proposal definitions, with no executable tools."""

from agentic_intent_gate.domain.contracts import Risk, Tool
from agentic_intent_gate.domain.validation import PlanValidator, load_schema

TARGET_REVISIONS = {"service-demo": 1}
_DEFINITIONS = {
    "get_service_record": ("Propose reading a service record.", Risk.READ_ONLY),
    "append_service_note": ("Propose appending an operator note.", Risk.SIDE_EFFECT),
    "set_maintenance_mode": ("Propose changing maintenance mode.", Risk.APPROVAL_REQUIRED),
}


def build_validator() -> PlanValidator:
    schema = load_schema("day1-plan.schema.json")
    tools = []
    for branch in schema["$defs"]["step"]["oneOf"]:
        properties = branch["properties"]
        name = properties["tool_name"]["const"]
        description, risk = _DEFINITIONS[name]
        tools.append(
            Tool(
                name=name,
                version=properties["tool_version"]["const"],
                description=description,
                input_schema={"$defs": schema["$defs"], **properties["arguments"]},
                risk_class=risk,
            )
        )
    return PlanValidator(
        tools,
        intent_schema=load_schema("day1-intent.schema.json"),
        plan_schema=schema,
        target_revisions=TARGET_REVISIONS,
    )
