"""Prompt 3: browser -> real HTTP -> frozen RunService -> real temporary SQLite.

No route interception or fake browser responses. Only the model is controlled.
"""

import json
import threading

import httpx
import pytest
from playwright.sync_api import expect
from run_helpers import database_rows, plan_for

from agentic_intent_gate.domain.contracts import Risk

pytestmark = pytest.mark.browser


def create(page, app, text="Plan to read service-demo"):
    assert page.goto(app.url).status == 200
    page.locator("#run-intent").fill(text)
    with page.expect_response(app.url + "/api/runs") as pending:
        page.locator("#create-run").click()
    response = pending.value
    assert response.status == 201
    run = response.json()
    expect(page.locator("#run-id")).to_have_text(run["run_id"])
    expect(page.locator("#run-state")).to_have_text(run["state"])
    assert response.request.post_data_json == {"text": text}
    return run


def decisions(app):
    return database_rows(app.database)["approval_decisions"]


def test_e2e_1_read_only(page, run_app):
    app = run_app()
    requests = []
    page.on("request", lambda request: requests.append(request.url))
    run = create(page, app)
    assert run["state"] == "Ready" and run["version"] == 4
    expect(page.locator("#run-version")).to_have_text("4")
    expect(page.locator("#run-approval-required")).to_have_text("false")
    expect(page.locator("#run-risk")).to_have_text("READ_ONLY")
    expect(page.locator("#run-goal")).to_have_text(run["plan"]["goal"])
    expect(page.locator("#run-steps h3")).to_have_text("get_service_record · v1")
    expect(page.locator("#approve-run")).to_be_hidden()
    expect(page.locator("#reject-run")).to_be_hidden()
    assert app.provider.calls == 1 and not decisions(app)
    assert all(url.startswith(app.url + "/") for url in requests)
    assert set(database_rows(app.database)) == {"runs", "approval_decisions", "audit_events"}


@pytest.mark.parametrize("risk", [Risk.SIDE_EFFECT, Risk.APPROVAL_REQUIRED])
def test_e2e_2_write_waits_for_approval(page, run_app, risk):
    app = run_app(risk=risk)
    run = create(page, app, "Propose a service-demo change")
    assert run["state"] == "AwaitingApproval" and run["version"] == 4
    expect(page.locator("#run-risk")).to_have_text(risk.value)
    expect(page.locator("#run-approval-required")).to_have_text("true")
    expect(page.locator("#run-plan")).to_be_visible()
    expect(page.locator("#run-goal")).to_have_text(run["plan"]["goal"])
    expect(page.locator("#run-target")).to_have_text(run["plan"]["target"])
    assert (
        json.loads(page.locator("#run-steps pre").text_content())
        == run["plan"]["steps"][0]["arguments"]
    )
    expect(page.locator("#approval-summary")).to_contain_text("version 4")
    expect(page.locator("#approve-run")).to_be_enabled()
    expect(page.locator("#reject-run")).to_be_enabled()
    assert app.provider.calls == 1 and not decisions(app)


def test_e2e_3_approve(page, run_app):
    app = run_app(risk=Risk.APPROVAL_REQUIRED)
    run = create(page, app)
    with page.expect_response(app.url + f"/api/runs/{run['run_id']}/approval") as pending:
        page.locator("#approve-run").click()
    response = pending.value
    assert response.status == 200
    assert response.request.post_data_json == {"decision": "APPROVE", "expected_version": 4}
    expect(page.locator("#run-state")).to_have_text("Approved")
    expect(page.locator("#run-version")).to_have_text("5")
    expect(page.locator("#approval-form")).to_be_hidden()
    expect(page.locator("#approve-run")).to_be_disabled()
    expect(page.locator("#reject-run")).to_be_disabled()
    expect(page.locator("#approval-summary")).to_contain_text("No tools executed")
    for claim in ("Executed", "Succeeded", "Completed", "Applied", "Finished"):
        assert claim not in page.locator("#run-console").inner_text()
    assert app.repository.get(run["run_id"]).to_dict() == response.json()
    assert len(decisions(app)) == 1 and app.provider.calls == 1
    assert len(database_rows(app.database)["audit_events"]) == 5


def test_e2e_4_reject_with_original_reason(page, run_app):
    app = run_app(risk=Risk.SIDE_EFFECT)
    run = create(page, app)
    reason = " Reviewed locally — reject 😀\n"
    page.locator("#approval-reason").fill(reason)
    with page.expect_response(app.url + f"/api/runs/{run['run_id']}/approval") as pending:
        page.locator("#reject-run").click()
    response = pending.value
    assert response.status == 200
    assert response.request.post_data_json == {
        "decision": "REJECT",
        "expected_version": 4,
        "reason": reason,
    }
    expect(page.locator("#run-state")).to_have_text("Rejected")
    expect(page.locator("#run-version")).to_have_text("5")
    expect(page.locator("#approval-form")).to_be_hidden()
    assert response.json()["plan"] == run["plan"] and response.json()["error"] is None
    assert decisions(app)[0][3] == reason
    assert app.provider.calls == 1 and len(decisions(app)) == 1


def test_e2e_5_failed_plan_is_persisted_and_safe(page, run_app):
    app = run_app(raw="private provider details <script>window.attack=1</script>")
    run = create(page, app)
    assert run["state"] == "PlanRejected" and run["version"] == 3
    expect(page.locator("#run-plan")).to_be_hidden()
    expect(page.locator("#run-plan-error-code")).to_have_text("PLAN_VALIDATION_ERROR")
    expect(page.locator("#run-plan-error-message")).to_have_text(
        "The generated proposal failed validation."
    )
    expect(page.locator("body")).not_to_contain_text("private provider details")
    with httpx.Client(trust_env=False) as client:
        assert client.get(app.url + f"/api/runs/{run['run_id']}").json() == run
    assert app.provider.calls == 1 and page.evaluate("window.attack ?? null") is None


def test_e2e_6_refresh_and_reload_are_pure_reads(page, run_app):
    app = run_app(risk=Risk.SIDE_EFFECT)
    run = create(page, app)
    before = database_rows(app.database)
    path = app.url + f"/api/runs/{run['run_id']}"
    with page.expect_response(path) as pending:
        page.locator("#refresh-run").click()
    assert pending.value.request.method == "GET" and pending.value.json() == run
    assert "run_id=" + run["run_id"] in page.url
    with page.expect_response(path) as pending:
        page.reload()
    assert pending.value.request.method == "GET" and pending.value.json() == run
    expect(page.locator("#run-id")).to_have_text(run["run_id"])
    expect(page.locator("#run-version")).to_have_text("4")
    assert before == database_rows(app.database)
    assert app.provider.calls == 1 and not decisions(app)
    assert (
        sum(
            entry["method"] == "GET" and entry["path"].endswith(run["run_id"])
            for entry in app.requests
        )
        == 2
    )


def test_e2e_7_stale_decision_requires_explicit_refresh(page, run_app):
    app = run_app(risk=Risk.SIDE_EFFECT)
    run = create(page, app)
    path = app.url + f"/api/runs/{run['run_id']}"
    with httpx.Client(trust_env=False) as client:
        winner = client.post(path + "/approval", json={"decision": "REJECT", "expected_version": 4})
    assert winner.status_code == 200
    before = database_rows(app.database)
    with page.expect_response(path + "/approval") as pending:
        page.locator("#approve-run").click()
    assert pending.value.status == 409
    assert pending.value.json()["error"]["code"] == "STALE_RUN_VERSION"
    expect(page.locator("#run-error-code")).to_have_text("STALE_RUN_VERSION")
    expect(page.locator("#run-error-help")).to_contain_text("Refresh Run explicitly")
    expect(page.locator("#approve-run")).to_be_disabled()
    expect(page.locator("#reject-run")).to_be_disabled()
    expect(page.locator("#run-version")).to_have_text("4")  # Last confirmed browser snapshot.
    with page.expect_response(path):
        page.locator("#refresh-run").click()
    expect(page.locator("#run-state")).to_have_text("Rejected")
    expect(page.locator("#run-version")).to_have_text("5")
    assert database_rows(app.database) == before and len(decisions(app)) == 1
    assert sum(entry["path"].endswith("/approval") for entry in app.requests) == 2
    assert app.provider.calls == 1


@pytest.mark.parametrize("decision,state", [("APPROVE", "Approved"), ("REJECT", "Rejected")])
def test_e2e_8_duplicate_decision_buttons_lock_immediately(page, run_app, decision, state):
    gate = threading.Event()
    app = run_app(risk=Risk.SIDE_EFFECT, approval_gate=gate)
    run = create(page, app)
    path = app.url + f"/api/runs/{run['run_id']}/approval"
    with page.expect_request(path) as pending:
        lock = page.evaluate(
            """decision => {
          document.getElementById(decision === 'APPROVE' ? 'approve-run' : 'reject-run').click();
          const locked = ['approve-run', 'reject-run'].every(
            id => document.getElementById(id).disabled
          );
          document.getElementById('approve-run').click();
          document.getElementById('reject-run').click();
          return locked;
        }""",
            decision,
        )
    assert lock
    assert pending.value.post_data_json == {"decision": decision, "expected_version": 4}
    expect(page.locator("#run-status")).to_have_text("Submitting local decision…")
    expect(page.locator("#run-state")).to_have_text("AwaitingApproval")
    expect(page.locator("#run-version")).to_have_text("4")
    expect(page.locator("#create-run")).to_be_disabled()
    expect(page.locator("#refresh-run")).to_be_disabled()
    assert not decisions(app)
    with page.expect_response(path):
        gate.set()
    expect(page.locator("#run-state")).to_have_text(state)
    expect(page.locator("#run-version")).to_have_text("5")
    assert len(decisions(app)) == 1 and len(database_rows(app.database)["audit_events"]) == 5
    assert sum(entry["path"].endswith("/approval") for entry in app.requests) == 1
    assert app.provider.calls == 1


def test_e2e_9_hostile_intent_goal_and_arguments_stay_text(page, run_app, validator):
    attack = (
        "<script>alert(1)</script><img src=x onerror=alert(1)> javascript:alert(1) "
        "</textarea><script>window.attack=1</script>"
    )
    proposal = plan_for(validator, Risk.SIDE_EFFECT).to_dict()
    proposal["goal"] = attack
    proposal["steps"][0]["arguments"]["note"] = attack
    app = run_app(raw=json.dumps(proposal))
    dialogs = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
    run = create(page, app, attack)
    expect(page.locator("#run-original-intent")).to_have_text(attack)
    expect(page.locator("#run-goal")).to_have_text(attack)
    expect(page.locator("#run-steps pre")).to_contain_text("javascript:alert(1)")
    assert (
        page.locator("#run-details :is(script, img, a), #run-plan :is(script, img, a)").count() == 0
    )
    assert not dialogs and page.evaluate("window.attack ?? null") is None
    assert app.repository.get(run["run_id"]).plan.to_dict() == proposal


def test_e2e_10_legacy_preview_remains_stateless(page, run_app):
    app = run_app()
    assert page.goto(app.url).status == 200
    page.locator("#intent").fill("Read service-demo")
    with page.expect_response(app.url + "/api/plans") as pending:
        page.locator("#generate").click()
    assert pending.value.status == 200
    expect(page.locator("#state")).to_have_text("PlanValidated")
    expect(page.locator("#goal")).to_have_text(pending.value.json()["plan"]["goal"])
    assert app.provider.calls == 1
    assert database_rows(app.database) == {"runs": [], "approval_decisions": [], "audit_events": []}


def test_duplicate_creation_keyboard_and_form_events_are_blocked(page, run_app):
    gate = threading.Event()
    app = run_app(provider_gate=gate)
    assert page.goto(app.url).status == 200
    text = " Read service-demo 😀\n"
    page.locator("#run-intent").fill(text)
    with page.expect_request(app.url + "/api/runs"):
        page.locator("#run-intent").press("Control+Enter")
        locked = page.evaluate("""() => {
          document.getElementById('run-form').requestSubmit();
          document.getElementById('run-form').requestSubmit();
          return document.getElementById('create-run').disabled;
        }""")
    assert locked
    expect(page.locator("#run-status")).to_have_text("Creating Run…")
    expect(page.locator("#run-loading")).to_be_visible()
    with page.expect_response(app.url + "/api/runs") as pending:
        gate.set()
    assert pending.value.request.post_data_json == {"text": text}
    expect(page.locator("#run-original-intent")).to_have_text(text)
    assert app.provider.calls == 1 and len(database_rows(app.database)["runs"]) == 1
