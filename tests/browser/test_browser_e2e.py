"""Real browser -> real FastAPI -> test-only provider -> real trusted validator."""

import json

import pytest
from playwright.sync_api import expect

from agentic_intent_gate.domain.contracts import ErrorCode
from agentic_intent_gate.ports.model_provider import ModelProviderError

pytestmark = pytest.mark.browser


def submit(page, url):
    assert page.goto(url).status == 200
    page.get_by_label("Intent", exact=True).fill("Plan to read the service-demo record.")
    with page.expect_response(url + "/api/plans") as response:
        page.get_by_role("button", name="Generate Plan", exact=True).click()
    return response.value


def test_full_browser_backend_validation_flow(page, deterministic_app, valid_plan):
    url, provider = deterministic_app()
    response = submit(page, url)
    assert response.status == 200 and provider.calls == 1
    assert response.request.post_data_json == {"text": "Plan to read the service-demo record."}
    assert response.json() == {"state": "PlanValidated", "plan": valid_plan, "error": None}
    expect(page.locator("#goal")).to_have_text(valid_plan["goal"])
    expect(page.locator("#target")).to_have_text(valid_plan["target"])
    expect(page.locator("#risk")).to_have_text("READ_ONLY")
    expect(page.locator("#steps h3")).to_have_text("get_service_record · v1")
    expect(page.locator("#steps pre")).to_contain_text('"service_id": "service-demo"')
    expect(page.locator("#state")).to_have_text("PlanValidated")


@pytest.mark.parametrize("phase", ["parse", "semantic"])
def test_actual_backend_rejection_has_no_displayed_plan(page, deterministic_app, valid_plan, phase):
    valid_plan["risk_class"] = "SIDE_EFFECT"
    raw = "Here is your plan: {}" if phase == "parse" else json.dumps(valid_plan)
    url, provider = deterministic_app(raw=raw)
    response = submit(page, url)
    assert response.status == 422 and provider.calls == 1
    assert response.json()["plan"] is None
    expected = "PARSE_" if phase == "parse" else "SEMANTIC_"
    assert response.json()["error"]["issues"][0]["code"].startswith(expected)
    expect(page.locator("#state")).to_have_text("PlanRejected")
    expect(page.get_by_role("alert")).to_contain_text("No plan was accepted")
    expect(page.locator("#result")).to_be_hidden()
    expect(page.locator("#steps li")).to_have_count(0)


@pytest.mark.parametrize(
    "failure,status,message",
    [
        (ModelProviderError(ErrorCode.MODEL_UNAVAILABLE), 503, "unavailable"),
        (ModelProviderError(ErrorCode.MODEL_TIMEOUT), 504, "timed out"),
        (RuntimeError("private-provider-details"), 500, "generation failed"),
    ],
)
def test_actual_provider_error_is_safe_in_browser(
    page, deterministic_app, failure, status, message
):
    url, provider = deterministic_app(error=failure)
    response = submit(page, url)
    assert response.status == status and provider.calls == 1
    expect(page.get_by_role("alert")).to_contain_text(message)
    expect(page.locator("#result")).to_be_hidden()
    expect(page.locator("body")).not_to_contain_text("private-provider-details")
