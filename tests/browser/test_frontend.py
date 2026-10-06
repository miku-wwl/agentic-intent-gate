"""Actual DOM behavior; controlled API responses are explicitly deterministic."""

import json

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser, pytest.mark.frontend]


def success(plan):
    return {"state": "PlanValidated", "plan": plan, "error": None}


def fulfill(route, body, status=200):
    route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


def open_page(page, url):
    response = page.goto(url)
    assert response.status == 200


def test_page_controls_labels_and_idle_state(page, ui_url):
    open_page(page, ui_url)
    expect(page).to_have_title("Runs & local approval · AgenticIntentGate")
    expect(page.get_by_label("Intent", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Generate Plan", exact=True)).to_be_enabled()
    expect(page.locator("#planner")).to_have_attribute("data-state", "IDLE")
    expect(page.locator("#result")).to_be_hidden()
    expect(page.locator("#error")).to_be_hidden()
    expect(page.get_by_text("Planning preview — no tools executed", exact=True)).to_be_visible()


@pytest.mark.parametrize("text", ["", " \t\n"])
def test_empty_intent_is_not_submitted(page, ui_url, text):
    requests = []
    page.route("**/api/plans", lambda route: requests.append(route))
    open_page(page, ui_url)
    page.get_by_label("Intent", exact=True).fill(text)
    page.get_by_role("button", name="Generate Plan", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("Enter an intent")
    expect(page.locator("#planner")).to_have_attribute("data-state", "ERROR")
    expect(page.locator("#result")).to_be_hidden()
    assert not requests


def test_loading_prevents_duplicates_and_clears_previous_error(page, ui_url, valid_plan):
    requests = []
    page.route("**/api/plans", lambda route: requests.append(route))
    open_page(page, ui_url)
    page.get_by_role("button", name="Generate Plan", exact=True).click()
    expect(page.get_by_role("alert")).to_be_visible()
    page.get_by_label("Intent", exact=True).fill("Read service-demo")
    with page.expect_request("**/api/plans"):
        page.get_by_role("button", name="Generate Plan", exact=True).click()
    expect(page.locator("#loading")).to_be_visible()
    expect(page.locator("#generate")).to_be_disabled()
    expect(page.locator("#planner")).to_have_attribute("data-state", "LOADING")
    expect(page.locator("#error")).to_be_hidden()
    expect(page.locator("#error-message")).to_have_text("")
    page.locator("#intent").press("Control+Enter")
    page.evaluate("document.getElementById('plan-form').requestSubmit()")
    assert len(requests) == 1
    fulfill(requests[0], success(valid_plan))
    expect(page.locator("#result")).to_be_visible()
    expect(page.locator("#generate")).to_be_enabled()
    expect(page.locator("#loading")).to_be_hidden()


def test_success_renders_all_fields_and_html_like_values_as_text(page, ui_url, valid_plan):
    attack = '<img src=x onerror="window.renderAttack=1">'
    valid_plan["goal"] = attack
    valid_plan["risk_class"] = "SIDE_EFFECT"
    valid_plan["steps"].append(
        {
            "tool_name": "append_service_note",
            "tool_version": "1",
            "arguments": {"service_id": "service-demo", "note": attack},
        }
    )
    page.route("**/api/plans", lambda route: fulfill(route, success(valid_plan)))
    open_page(page, ui_url)
    page.locator("#intent").fill("Record a note about service-demo")
    page.locator("#generate").click()
    expect(page.locator("#goal")).to_have_text(attack)
    expect(page.locator("#target")).to_have_text("service-demo")
    expect(page.locator("#risk")).to_have_text("SIDE_EFFECT")
    expect(page.locator("#state")).to_have_text("PlanValidated")
    expect(page.locator("#steps li")).to_have_count(2)
    expect(page.locator("#steps h3").nth(0)).to_have_text("get_service_record · v1")
    expect(page.locator("#steps h3").nth(1)).to_have_text("append_service_note · v1")
    expect(page.locator("#steps pre").nth(1)).to_contain_text(attack.replace('"', '\\"'))
    assert page.locator("#result img").count() == 0
    assert page.evaluate("window.renderAttack ?? null") is None


@pytest.mark.parametrize(
    "code,status,issue,message",
    [
        ("INVALID_INTENT", 400, "SCHEMA_REQUIRED", "non-empty intent"),
        ("REQUEST_TOO_LARGE", 413, "", "too large"),
        ("UNSUPPORTED_MEDIA_TYPE", 415, "", "request format"),
        ("MODEL_TIMEOUT", 504, "", "timed out"),
        ("MODEL_UNAVAILABLE", 503, "", "unavailable"),
        ("MODEL_RESPONSE_ERROR", 502, "", "incomplete response"),
        ("PLAN_VALIDATION_ERROR", 422, "PARSE_INVALID_JSON", "failed validation"),
        ("PLAN_VALIDATION_ERROR", 422, "SEMANTIC_RISK_MISMATCH", "semantic validation"),
        ("INTERNAL_ERROR", 500, "", "generation failed"),
    ],
)
def test_structured_errors_show_only_safe_user_message(page, ui_url, code, status, issue, message):
    body = {
        "state": "PlanRejected",
        "plan": None,
        "error": {
            "code": code,
            "message": "private traceback hidden prompt <script>bad()</script>",
            "issues": [{"path": "/", "code": issue}],
        },
    }
    page.route("**/api/plans", lambda route: fulfill(route, body, status))
    open_page(page, ui_url)
    page.locator("#intent").fill("Read service-demo")
    page.locator("#generate").click()
    expect(page.get_by_role("alert")).to_contain_text(message)
    expect(page.locator("#result")).to_be_hidden()
    expect(page.locator("#generate")).to_be_enabled()
    expect(page.locator("body")).not_to_contain_text("private traceback")
    assert page.locator("#error script").count() == 0


@pytest.mark.parametrize("failure", ["network", "nonjson", "bad-success", "malformed-error-code"])
def test_failed_new_request_clears_previous_plan(page, ui_url, valid_plan, failure):
    pending = []

    def handler(route):
        pending.append(route)
        if len(pending) == 1:
            fulfill(route, success(valid_plan))

    page.route("**/api/plans", handler)
    open_page(page, ui_url)
    page.locator("#intent").fill("Read service-demo")
    page.locator("#generate").click()
    expect(page.locator("#result")).to_be_visible()
    with page.expect_request("**/api/plans"):
        page.locator("#generate").click()
    expect(page.locator("#result")).to_be_hidden()
    expect(page.locator("#goal")).to_have_text("")
    expect(page.locator("#steps li")).to_have_count(0)
    if failure == "network":
        pending[1].abort("failed")
        message = "Could not reach the application"
    elif failure == "nonjson":
        pending[1].fulfill(status=500, content_type="text/html", body="<h1>private traceback</h1>")
        message = "unexpected response"
    elif failure == "malformed-error-code":
        fulfill(
            pending[1],
            {"state": "PlanRejected", "plan": None, "error": {"code": {"toString": "private"}}},
            502,
        )
        message = "unexpected response"
    else:
        fulfill(pending[1], {"state": "PlanValidated", "plan": None, "error": None})
        message = "unexpected response"
    expect(page.get_by_role("alert")).to_contain_text(message)
    expect(page.locator("#result")).to_be_hidden()
    expect(page.locator("#planner")).to_have_attribute("data-state", "ERROR")


def test_keyboard_submission_preserves_original_intent(page, ui_url, valid_plan):
    page.route("**/api/plans", lambda route: fulfill(route, success(valid_plan)))
    open_page(page, ui_url)
    text = " Read service-demo \n"
    page.locator("#intent").fill(text)
    with page.expect_request("**/api/plans") as request:
        page.locator("#intent").press("Control+Enter")
    assert request.value.post_data_json == {"text": text}
    assert request.value.method == "POST"
    assert request.value.url == ui_url + "/api/plans"
    expect(page.locator("#goal")).to_have_text(valid_plan["goal"])
