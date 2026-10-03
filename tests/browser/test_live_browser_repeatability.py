"""Five scheduled live browser attempts, including safe failures; never rerun on failure."""

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser, pytest.mark.live, pytest.mark.live_browser]


def test_five_independent_real_browser_attempts(page, live_product, browser_evidence, validator):
    url, server = live_product
    evidence, _output = browser_evidence
    output = Path(os.environ.get("HARDENING_EVIDENCE_DIR", ".runtime/hardening"))
    output.mkdir(parents=True, exist_ok=True)
    study = {
        "lane": "LIVE_REAL_BROWSER_REAL_PRODUCT_PROCESS",
        "server": server,
        "browser_interception": False,
        "mock_provider": False,
        "automatic_retries": 0,
        "tool_invocations": 0,
        "attempts": [],
    }
    evidence["live_browser_repeatability"] = study
    posts = []
    page.on("request", lambda request: posts.append(request) if request.method == "POST" else None)
    navigation = page.goto(url)
    assert navigation.status == 200
    for index in range(1, 6):
        record = {
            "attempt": index,
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "intent": "Plan to read the service-demo record.",
            "page_load_status": navigation.status,
            "page_still_loaded": page.title() == "Planning preview · AgenticIntentGate",
            "previous_result_visible": page.locator("#result").is_visible(),
        }
        count_before = len(posts)
        page.locator("#intent").fill(record["intent"])
        started = time.monotonic()
        with page.expect_response(url + "/api/plans", timeout=35000) as pending:
            page.locator("#generate").click()
            expect(page.locator("#result")).to_be_hidden()
            expect(page.locator("#goal")).to_have_text("")
            expect(page.locator("#steps li")).to_have_count(0)
            record["stale_plan_cleared_before_response"] = True
        response = pending.value
        record["seconds_to_response"] = round(time.monotonic() - started, 3)
        body = response.json()
        record.update(
            http_status=response.status,
            state=body["state"],
            error_code=body["error"]["code"] if body["error"] else None,
            response=body,
        )
        if response.status == 200:
            assert body["state"] == "PlanValidated" and body["error"] is None
            validator.parse_plan(json.dumps(body["plan"]))
            plan = body["plan"]
            expect(page.locator("#result")).to_be_visible()
            for key, value in (
                ("goal", plan["goal"]),
                ("target", plan["target"]),
                ("risk", plan["risk_class"]),
            ):
                expect(page.locator("#" + key)).to_have_text(value)
            expect(page.locator("#state")).to_have_text("PlanValidated")
            expect(page.locator("#steps li")).to_have_count(len(plan["steps"]))
            for step_index, step in enumerate(plan["steps"]):
                expect(page.locator("#steps h3").nth(step_index)).to_have_text(
                    f"{step['tool_name']} · v{step['tool_version']}"
                )
                assert (
                    json.loads(page.locator("#steps pre").nth(step_index).text_content())
                    == (step["arguments"])
                )
            record["classification"] = (
                "SUCCESS"
                if plan["risk_class"] == "READ_ONLY"
                and all(step["tool_name"] == "get_service_record" for step in plan["steps"])
                else "LIVE_INTENT_ALIGNMENT_MISMATCH"
            )
        else:
            assert body["state"] == "PlanRejected" and body["plan"] is None
            expect(page.get_by_role("alert")).to_be_visible()
            expect(page.locator("#state")).to_have_text("PlanRejected")
            expect(page.locator("#result")).to_be_hidden()
            expect(page.locator("#goal")).to_have_text("")
            expect(page.locator("#steps li")).to_have_count(0)
            record["classification"] = "SAFE_FAILURE"
        expect(page.locator("#generate")).to_be_enabled()
        record.update(
            seconds_to_final_render=round(time.monotonic() - started, 3),
            post_count=len(posts) - count_before,
            result_visible=page.locator("#result").is_visible(),
            error_visible=page.locator("#error").is_visible(),
            stale_plan_absent_after_failure=response.status == 200
            or not page.locator("#goal").text_content(),
        )
        assert record["post_count"] == 1
        study["attempts"].append(record)
        (output / "browser-matrix.json").write_text(
            json.dumps(study, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"BROWSER {index}/5 {record['classification']} {record['seconds_to_final_render']}s",
            flush=True,
        )
    page.screenshot(path=output / "browser-final.png", full_page=True)
    assert any(record["classification"] == "SUCCESS" for record in study["attempts"])
