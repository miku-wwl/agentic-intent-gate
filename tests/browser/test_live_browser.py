"""Opt-in, actual product entrypoint and phi-4-mini. No routes or provider mocks."""

import time

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser, pytest.mark.live, pytest.mark.live_browser]


def test_real_phi4_mini_plan_rendered_in_browser(page, live_product, browser_evidence, validator):
    url, server = live_product
    evidence, output = browser_evidence
    record = {
        "lane": "REAL PHI-4-MINI LIVE TEST",
        "server": server,
        "browser_interception": False,
        "mock_provider": False,
        "automatic_retries": 0,
        "tool_invocations": 0,
        "intent": "Plan to read the service-demo record.",
        "result": "FAIL",
    }
    evidence["live_browser"] = record
    requests = []
    page.on(
        "request", lambda request: requests.append(request) if request.method == "POST" else None
    )
    navigation = page.goto(url)
    record.update(page_loaded=navigation.status == 200, page_title=page.title(), page_url=page.url)
    assert navigation.status == 200
    page.get_by_label("Intent", exact=True).fill(record["intent"])
    started = time.monotonic()
    with page.expect_response(url + "/api/plans", timeout=35000) as response:
        page.get_by_role("button", name="Generate Plan", exact=True).click()
    response = response.value
    record.update(
        http_status=response.status,
        api_url=response.url,
        request=response.request.post_data_json,
        response=response.json(),
        elapsed_seconds=round(time.monotonic() - started, 3),
    )
    if response.status != 200:
        expect(page.get_by_role("alert")).to_be_visible()
        expect(page.locator("#result")).to_be_hidden()
        record["failure_handled_safely"] = True
        page.screenshot(path=output / "live-rejection.png", full_page=True)
    assert response.status == 200, "Real model did not produce an accepted Plan; evidence preserved"
    body = response.json()
    assert body["state"] == "PlanValidated" and body["error"] is None
    import json

    validator.parse_plan(json.dumps(body["plan"]))
    plan = body["plan"]
    expect(page.locator("#goal")).to_have_text(plan["goal"])
    expect(page.locator("#target")).to_have_text(plan["target"])
    expect(page.locator("#risk")).to_have_text(plan["risk_class"])
    expect(page.locator("#state")).to_have_text("PlanValidated")
    expect(page.locator("#steps li")).to_have_count(len(plan["steps"]))
    for index, step in enumerate(plan["steps"]):
        expect(page.locator("#steps h3").nth(index)).to_have_text(
            f"{step['tool_name']} · v{step['tool_version']}"
        )
        assert json.loads(page.locator("#steps pre").nth(index).text_content()) == step["arguments"]
    record["rendered"] = {
        key: page.locator("#" + key).text_content() for key in ("goal", "target", "risk", "state")
    }
    record["steps_rendered"] = page.locator("#steps").inner_text()
    record["result"] = "PASS"
    record["total_to_render_seconds"] = round(time.monotonic() - started, 3)
    page.screenshot(path=output / "live-success.png", full_page=True)
    # Actual client failure path after a live success: old Plan must disappear,
    # and empty input must not cause another provider request.
    page.get_by_label("Intent", exact=True).fill("")
    page.get_by_role("button", name="Generate Plan", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("Enter an intent")
    expect(page.locator("#result")).to_be_hidden()
    assert len(requests) == 1
    record["browser_post_requests"] = len(requests)
    record["empty_intent_failure_path"] = "PASS_NO_EXTRA_POST_NO_STALE_PLAN"
