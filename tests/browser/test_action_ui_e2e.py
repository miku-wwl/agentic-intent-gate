"""Browser -> real HTTP -> v2 ledger/gateway -> isolated real demo notes.

No fabricated Action responses. The response-loss case discards only a response
after the actual HTTP command has committed. No trace/video/header capture.
"""

import json
import sqlite3
import threading
from contextlib import closing
from uuid import uuid4

import pytest
from playwright.sync_api import expect
from run_helpers import plan_for

from agentic_intent_gate.domain.contracts import Risk
from agentic_intent_gate.domain.run import RunState
from agentic_intent_gate.domain.run_validation import canonical_json

pytestmark = pytest.mark.browser


def open_run(page, app, *, action_id=None):
    url = app.url + "?run_id=" + app.run.run_id
    if action_id:
        url += "&action_id=" + action_id
    assert page.goto(url).status == 200
    expect(page.locator("#run-id")).to_have_text(app.run.run_id)
    expect(page.locator("#action-console")).to_be_visible()


def enter_capability(page, app, *, value=None, verified=True):
    control = page.locator("#action-capability")
    assert control.get_attribute("type") == "password"
    try:
        control.fill(app.token if value is None else value)
        page.locator("#use-capability").click()
    except Exception:
        pytest.fail("Capability entry failed; credential diagnostics suppressed", pytrace=False)
    expect(control).to_have_value("")
    if verified:
        expect(page.locator("#capability-status")).to_have_text(
            "Capability verified for Action visibility."
        )
        wait_idle(page)


def wait_idle(page):
    expect(page.locator("#action-console")).to_have_attribute("aria-busy", "false")


def command(page, app, operation, state):
    action_id = page.locator("#action-id").text_content()
    path = (
        f"/api/runs/{app.run.run_id}/actions"
        if operation == "admit"
        else f"/api/actions/{action_id}/{operation}"
    )
    with page.expect_response(app.url + path) as pending:
        page.locator("#" + operation + "-action").click()
    response = pending.value
    assert response.status in {200, 201}
    expected = (
        {"expected_run_version": 5}
        if operation == "admit"
        else {"expected_action_version": 1 if operation == "authorize" else 2}
    )
    assert response.request.post_data_json == expected
    expect(page.locator("#action-state")).to_have_text(state)
    wait_idle(page)
    return response


def mutations(app):
    return [entry for entry in app.requests if entry["method"] == "POST"]


def assert_no_secret(page, app):
    # Bool-only failure avoids pytest/Playwright echoing the managed test credential.
    exposed = app.token in page.content() or app.token in page.locator("body").inner_text()
    if exposed:
        pytest.fail("Managed credential exposed in rendered page", pytrace=False)
    assert page.locator("#action-capability").input_value() == ""
    assert (
        page.evaluate("Object.keys(localStorage).length + Object.keys(sessionStorage).length") == 0
    )
    assert page.context.cookies() == []
    assert page.evaluate("async () => (await indexedDB.databases()).length") == 0


def capture_safe(page, app, browser_evidence, name):
    assert_no_secret(page, app)
    evidence, directory = browser_evidence
    screenshot = directory / name
    page.screenshot(path=str(screenshot), full_page=True)
    evidence.setdefault("action_screenshots", []).append(screenshot.name)


def forced_command(page, app, operation, *, credential=True, body=None):
    path = (
        f"/api/runs/{app.run.run_id}/actions"
        if operation == "admit"
        else f"/api/actions/{app.action.action_id}/{operation}"
    )
    headers = {"Idempotency-Key": str(uuid4())}
    if credential:
        headers["Authorization"] = "Bearer " + app.token
    payload = (
        body
        if body is not None
        else (
            {"expected_run_version": 5}
            if operation == "admit"
            else {"expected_action_version": app.action.version}
        )
    )
    try:
        return page.request.post(
            app.url + path,
            headers=headers,
            data=payload,
        )
    except Exception:
        pytest.fail("HTTP command failed; credential diagnostics suppressed", pytrace=False)


@pytest.mark.parametrize("value", [None, "LOCAL_USER", "admin", "0" * 64])
def test_no_invalid_or_localuser_capability_cannot_mutate(page, action_app, value):
    app = action_app()
    open_run(page, app)
    before = app.ledger()
    expect(page.locator("#admit-action")).to_be_disabled()
    if value is not None:
        enter_capability(page, app, value=value, verified=False)
        expect(page.locator("#action-error-code")).to_have_text("AUTHENTICATION_REQUIRED")
    assert app.ledger() == before and app.calls == 0 and not app.external()["notes"]
    denied = forced_command(page, app, "admit", credential=False)
    assert denied.status == 401
    assert app.ledger() == before
    assert_no_secret(page, app)


@pytest.mark.parametrize(
    "operations,state,operation",
    [
        (("read",), "Pending", "authorize"),
        (("read",), "Authorized", "execute"),
        (("read", "admit", "execute"), "Pending", "authorize"),
        (("read", "admit", "authorize"), "Authorized", "execute"),
        (("read", "authorize", "execute"), None, "admit"),
    ],
)
def test_server_operation_scope_hides_controls_and_denies_forced_command(
    page, action_app, operations, state, operation
):
    app = action_app(operations=operations, action_state=state)
    open_run(page, app)
    enter_capability(page, app)
    expect(page.locator("#" + operation + "-action")).to_be_hidden()
    before = app.ledger()
    denied = forced_command(page, app, operation)
    assert denied.status == 403 and denied.json()["error"]["code"] == "ACTION_NOT_AUTHORIZED"
    assert app.ledger() == before and app.calls == 0 and not app.external()["notes"]
    assert_no_secret(page, app)


def test_unconfigured_capability_fails_closed_in_browser(page, action_app):
    app = action_app(unconfigured=True)
    open_run(page, app)
    enter_capability(page, app, verified=False)
    expect(page.locator("#action-error-code")).to_have_text("AUTHENTICATION_REQUIRED")
    expect(page.locator("#action-details")).to_be_hidden()
    assert not app.ledger()["actions"] and app.calls == 0
    assert_no_secret(page, app)


@pytest.mark.parametrize("operation", ["admit", "authorize", "execute"])
def test_rapid_duplicate_and_inflight_clicks_send_one_stable_command(page, action_app, operation):
    state = {"admit": None, "authorize": "Pending", "execute": "Authorized"}[operation]
    result = {"admit": "Pending", "authorize": "Authorized", "execute": "Succeeded"}[operation]
    app = action_app(action_state=state)
    open_run(page, app)
    enter_capability(page, app)
    app.mutation_gate = threading.Event()
    lock = page.evaluate(
        """operation => {
      const button = document.getElementById(operation + '-action');
      button.click(); const disabled = button.disabled;
      button.click(); button.click(); return disabled;
    }""",
        operation,
    )
    assert lock
    expect(page.locator("#" + operation + "-action")).to_be_disabled()
    expect(page.locator("#action-status")).to_contain_text("in progress")
    app.mutation_gate.set()
    expect(page.locator("#action-state")).to_have_text(result)
    wait_idle(page)
    posts = mutations(app)
    assert len(posts) == 1 and posts[0]["idempotency_key"]
    rows = app.ledger()
    assert len(rows["actions"]) == 1
    assert len(rows["action_authorizations"]) == (0 if operation == "admit" else 1)
    assert len(rows["action_attempts"]) == (1 if operation == "execute" else 0)
    assert app.calls == (1 if operation == "execute" else 0)
    assert len(app.external()["notes"]) == (1 if operation == "execute" else 0)


@pytest.mark.parametrize("operation", ["authorize", "execute"])
def test_two_tabs_same_version_one_cas_and_safe_stale_refresh(page, action_app, operation):
    app = action_app(action_state="Pending" if operation == "authorize" else "Authorized")
    second = page.context.new_page()
    second.set_default_timeout(5000)
    try:
        for tab in (page, second):
            open_run(tab, app, action_id=app.action.action_id)
            enter_capability(tab, app)
        app.barrier_operation = operation
        page.locator("#" + operation + "-action").click()
        second.locator("#" + operation + "-action").click()
        expected = "Authorized" if operation == "authorize" else "Succeeded"
        for tab in (page, second):
            expect(tab.locator("#action-state")).to_have_text(expected)
            wait_idle(tab)
        posts = mutations(app)
        assert sorted(entry["status"] for entry in posts) == [200, 409]
        assert len({entry["idempotency_key"] for entry in posts}) == 2
        assert (
            sum(
                tab.locator("#action-error-code").text_content() == "STALE_ACTION_VERSION"
                for tab in (page, second)
            )
            == 1
        )
        assert len(app.ledger()["action_authorizations"]) == 1
        assert len(app.ledger()["action_attempts"]) == (1 if operation == "execute" else 0)
        assert app.calls == (1 if operation == "execute" else 0)
        assert len(app.external()["notes"]) == (1 if operation == "execute" else 0)
    finally:
        app.barrier.set()
        second.close()


def test_real_happy_path_claim_effect_receipt_and_durable_reload(
    page, action_app, browser_evidence
):
    app = action_app()
    open_run(page, app)
    assert not mutations(app) and not app.ledger()["actions"] and app.calls == 0
    enter_capability(page, app)
    command(page, app, "admit", "Pending")
    assert app.calls == 0 and not app.ledger()["action_authorizations"]
    capture_safe(page, app, browser_evidence, "action-pending.png")
    command(page, app, "authorize", "Authorized")
    assert app.calls == 0 and len(app.ledger()["action_authorizations"]) == 1
    capture_safe(page, app, browser_evidence, "action-authorized.png")
    app.execute_gate = threading.Event()
    page.locator("#execute-action").click()
    expect(page.locator("#action-state")).to_have_text("Executing")
    assert len(app.ledger()["action_attempts"]) == 1 and app.calls == 1
    capture_safe(page, app, browser_evidence, "action-executing.png")
    app.execute_gate.set()
    expect(page.locator("#action-state")).to_have_text("Succeeded")
    wait_idle(page)
    external = app.external()
    assert len(external["notes"]) == len(external["receipts"]) == 1
    expect(page.locator("#action-receipt")).to_have_text(external["receipts"][0][2])
    expect(page.locator("#run-state")).to_have_text("Approved")
    expect(page.locator("#run-version")).to_have_text("5")
    assert app.runs.get(app.run.run_id).version == 5
    before, posts = app.ledger(), len(mutations(app))
    assert_no_secret(page, app)
    page.reload()
    expect(page.locator("#capability-status")).to_contain_text("Capability required")
    expect(page.locator("#action-details")).to_be_hidden()
    enter_capability(page, app)
    expect(page.locator("#action-state")).to_have_text("Succeeded")
    assert app.ledger() == before and app.external() == external and app.calls == 1
    assert len(mutations(app)) == posts == 3
    capture_safe(page, app, browser_evidence, "action-happy-path.png")


@pytest.mark.parametrize(
    "state", ["Pending", "Authorized", "Executing", "Succeeded", "Failed", "Unknown"]
)
def test_all_six_real_states_controls_and_reload_are_pure(page, action_app, state):
    app = action_app(action_state=state, outcome="unknown" if state == "Unknown" else "success")
    open_run(page, app, action_id=app.action.action_id)
    enter_capability(page, app)
    expect(page.locator("#action-state")).to_have_text(state)
    expect(page.locator("#action-version")).to_have_text(str(app.action.version))
    for operation, available in (
        ("authorize", state == "Pending"),
        ("execute", state == "Authorized"),
    ):
        button = page.locator("#" + operation + "-action")
        if available:
            expect(button).to_be_enabled()
        else:
            expect(button).to_be_hidden()
            expect(button).to_be_disabled()
    if state == "Unknown":
        expect(page.locator("#action-warning")).to_contain_text("may have completed")
        expect(page.locator("#action-warning")).to_contain_text("Automatic replay is disabled")
    before, external, calls = app.ledger(), app.external(), app.calls
    page.reload()
    enter_capability(page, app)
    expect(page.locator("#action-state")).to_have_text(state)
    assert app.ledger() == before and app.external() == external and app.calls == calls
    assert mutations(app) == []
    assert_no_secret(page, app)


@pytest.mark.parametrize("state", [RunState.Ready, RunState.AwaitingApproval, RunState.Rejected])
def test_unapproved_run_admission_controls_and_server_denial(page, action_app, state):
    app = action_app(
        state=state, risk=Risk.READ_ONLY if state is RunState.Ready else Risk.SIDE_EFFECT
    )
    open_run(page, app)
    enter_capability(page, app)
    expect(page.locator("#admit-action")).to_be_hidden()
    before = app.ledger()
    denied = forced_command(page, app, "admit", body={"expected_run_version": app.run.version})
    assert denied.status == 409 and denied.json()["error"]["code"] == "RUN_NOT_ACTION_ELIGIBLE"
    assert app.ledger() == before and app.calls == 0 and not app.external()["notes"]


def test_legacy_approved_run_shows_actual_unbound_denial(page, action_app):
    app = action_app(legacy=True)
    open_run(page, app)
    enter_capability(page, app)
    before = app.ledger()
    page.locator("#admit-action").click()
    expect(page.locator("#action-error-code")).to_have_text("LEGACY_APPROVAL_UNBOUND")
    expect(page.locator("#action-error-message")).to_contain_text("distinct new Run")
    wait_idle(page)
    expect(page.locator("#action-details")).to_be_hidden()
    assert app.ledger() == before and app.calls == 0 and not app.external()["notes"]


@pytest.mark.parametrize("kind", ["maintenance", "multi-step"])
def test_unsupported_whole_plan_never_partially_admitted(page, action_app, validator, kind):
    proposal = plan_for(
        validator, Risk.APPROVAL_REQUIRED if kind == "maintenance" else Risk.SIDE_EFFECT
    ).to_dict()
    if kind == "multi-step":
        proposal["steps"].append(
            {
                **proposal["steps"][0],
                "arguments": {"service_id": "service-demo", "note": "second note"},
            }
        )
    app = action_app(proposal=proposal)
    open_run(page, app)
    enter_capability(page, app)
    expect(page.locator("#admit-action")).to_be_hidden()
    denied = forced_command(page, app, "admit")
    assert denied.status == 422 and denied.json()["error"]["code"] == "PLAN_NOT_EXECUTABLE"
    assert not app.ledger()["actions"] and app.calls == 0 and not app.external()["notes"]


def test_tampered_approval_plan_fails_real_read_and_admission(page, action_app):
    app = action_app()
    proposal = app.run.plan.to_dict()
    proposal["steps"][0]["arguments"]["note"] = "valid but never approved replacement"
    with closing(sqlite3.connect(app.database)) as connection:
        connection.execute(
            "UPDATE runs SET validated_plan_json=? WHERE run_id=?",
            (canonical_json(proposal), app.run.run_id),
        )
        connection.commit()
    page.goto(app.url + "?run_id=" + app.run.run_id)
    expect(page.locator("#run-error-code")).to_have_text("DATABASE_UNAVAILABLE")
    expect(page.locator("#action-console")).to_be_hidden()
    denied = forced_command(page, app, "admit")
    assert denied.status == 409 and denied.json()["error"]["code"] == "APPROVAL_PLAN_MISMATCH"
    assert app.calls == 0 and not app.external()["notes"]


def test_unknown_after_real_effect_has_no_replay_and_survives_reload(
    page, action_app, browser_evidence
):
    app = action_app(action_state="Pending", outcome="unknown")
    open_run(page, app)
    enter_capability(page, app)
    command(page, app, "authorize", "Authorized")
    command(page, app, "execute", "Unknown")
    expect(page.locator("#action-warning")).to_contain_text("may have completed")
    expect(page.locator("#execute-action")).to_be_hidden()
    before, external = app.ledger(), app.external()
    assert app.calls == 1 and len(external["notes"]) == len(external["receipts"]) == 1
    page.evaluate("document.getElementById('execute-action').click()")
    page.reload()
    enter_capability(page, app)
    expect(page.locator("#action-state")).to_have_text("Unknown")
    assert app.ledger() == before and app.external() == external and app.calls == 1
    assert len(before["action_attempts"]) == 1 and len(mutations(app)) == 2
    assert_no_secret(page, app)
    evidence, directory = browser_evidence
    screenshot = directory / "action-unknown.png"
    page.screenshot(path=str(screenshot), full_page=True)
    evidence.setdefault("action_screenshots", []).append(screenshot.name)


@pytest.mark.parametrize("read_lost", [False, True])
def test_response_lost_after_real_commit_refreshes_without_reexecution(page, action_app, read_lost):
    app = action_app(action_state="Authorized")
    open_run(page, app)
    enter_capability(page, app)
    committed = False
    detail_pattern = "**/api/actions/" + app.action.action_id

    def discard_command_response(route):
        nonlocal committed
        # Transport fault only: the real request, durable claim, adapter and commit happen.
        actual = route.fetch(max_redirects=0, max_retries=0)
        assert actual.status == 200
        assert actual.json()["action"]["state"] == "Succeeded"
        committed = True
        route.abort("failed")

    def optional_read_loss(route):
        if committed and read_lost:
            route.abort("failed")
        else:
            route.continue_()

    page.route(detail_pattern, optional_read_loss)
    page.route("**/execute", discard_command_response)
    page.locator("#execute-action").click()
    expect(page.locator("#action-error-code")).to_have_text("NETWORK_ERROR")
    wait_idle(page)
    assert committed and app.repo.get(app.action.action_id).state.value == "Succeeded"
    assert app.calls == 1 and len(app.ledger()["action_attempts"]) == 1
    external = app.external()
    assert len(external["notes"]) == len(external["receipts"]) == 1
    if read_lost:
        expect(page.locator("#action-state")).to_have_text("Authorized")
        expect(page.locator("#execute-action")).to_be_disabled()
        page.evaluate("document.getElementById('execute-action').click()")
        page.unroute(detail_pattern, optional_read_loss)
        page.locator("#refresh-actions").click()
    expect(page.locator("#action-state")).to_have_text("Succeeded")
    wait_idle(page)
    assert len(mutations(app)) == 1 and app.calls == 1 and app.external() == external
    assert_no_secret(page, app)


@pytest.mark.parametrize(
    "attack",
    [
        "<script>window.actionAttack=1</script>",
        '<img src=x onerror="window.actionAttack=1">',
        "&lt;script&gt;window.actionAttack=1&lt;/script&gt;",
        "</pre></section><svg onload=window.actionAttack=1>",
        "Unicode note 中文 🧭\nsecond line\n<script>window.actionAttack=1</script>",
        "x" * 960 + "<script>window.actionAttack=1</script>",
    ],
    ids=["script", "image-handler", "entities", "closing-tags", "unicode-newline", "long-note"],
)
def test_real_plan_and_action_text_is_inert_and_preserved(page, action_app, validator, attack):
    proposal = plan_for(validator, Risk.SIDE_EFFECT).to_dict()
    proposal["goal"] = attack[:256]
    proposal["steps"][0]["arguments"]["note"] = attack
    app = action_app(proposal=proposal)
    dialogs, errors = [], []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.type), dialog.dismiss()))
    page.on("pageerror", lambda error: errors.append(str(error)))
    open_run(page, app)
    enter_capability(page, app)
    command(page, app, "admit", "Pending")
    arguments = json.loads(page.locator("#action-arguments").text_content())
    assert arguments["note"] == attack
    command(page, app, "authorize", "Authorized")
    command(page, app, "execute", "Succeeded")
    assert page.evaluate("window.actionAttack === undefined")
    assert (
        page.locator("#action-console img, #action-console svg, #action-console script").count()
        == 0
    )
    assert not dialogs and not errors
    assert page.url.startswith(app.url + "/?")
    assert app.external()["notes"][0][2] == attack
    assert_no_secret(page, app)


@pytest.mark.parametrize("width", [375, 1000])
def test_action_accessibility_keyboard_and_responsive_layout(page, action_app, width):
    page.set_viewport_size({"width": width, "height": 850})
    app = action_app(action_state="Pending")
    open_run(page, app)
    expect(page.get_by_label("Local demo capability", exact=True)).to_have_attribute(
        "type", "password"
    )
    page.locator("#action-capability").focus()
    page.keyboard.press("Tab")
    expect(page.locator("#use-capability")).to_be_focused()
    assert page.locator("#use-capability").evaluate(
        "button => getComputedStyle(button).outlineStyle !== 'none'"
    )
    enter_capability(page, app)
    assert page.locator("#action-status").get_attribute("role") == "status"
    assert page.locator("#action-error").get_attribute("role") == "alert"
    expect(page.locator("#authorize-action")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_clear_capability_navigation_and_reentry_only_read(page, action_app):
    app = action_app(action_state="Authorized")
    open_run(page, app, action_id=app.action.action_id)
    enter_capability(page, app)
    before = app.ledger()
    page.locator("#clear-capability").click()
    expect(page.locator("#action-details")).to_be_hidden()
    expect(page.locator("#refresh-actions")).to_be_disabled()
    assert_no_secret(page, app)
    enter_capability(page, app)
    expect(page.locator("#action-state")).to_have_text("Authorized")
    page.goto("about:blank")
    open_run(page, app, action_id=app.action.action_id)
    expect(page.locator("#action-details")).to_be_hidden()
    enter_capability(page, app)
    assert app.ledger() == before and mutations(app) == [] and app.calls == 0


def test_capability_without_read_operation_cannot_activate_controls(page, action_app):
    app = action_app(operations=("admit", "authorize", "execute"))
    open_run(page, app)
    enter_capability(page, app, verified=False)
    expect(page.locator("#action-error-code")).to_have_text("ACTION_NOT_AUTHORIZED")
    expect(page.locator("#admit-action")).to_be_disabled()
    assert not mutations(app) and not app.ledger()["actions"] and app.calls == 0


@pytest.mark.parametrize("link", ["bad/action", "absent", "other-run"])
def test_invalid_missing_or_cross_run_action_link_cannot_mutate(page, action_app, link):
    app = action_app(action_state="Pending")
    if link == "other-run":
        other = action_app(action_state="Pending")
        action_id = other.action.action_id
    else:
        action_id = "bad/action" if link == "bad/action" else str(uuid4())
    open_run(page, app, action_id=action_id)
    before = app.ledger()
    if link == "bad/action":
        expect(page.locator("#action-error-code")).to_have_text("INVALID_ACTION_REQUEST")
        enter_capability(page, app)
        expect(page.locator("#action-state")).to_have_text("Pending")
    else:
        enter_capability(page, app, verified=False)
        expect(page.locator("#action-error-code")).to_have_text("ACTION_NOT_FOUND")
        expect(page.locator("#action-details")).to_be_hidden()
    assert app.ledger() == before and mutations(app) == [] and app.calls == 0


def test_actual_confirmed_failure_is_not_unknown_or_success(page, action_app):
    app = action_app(action_state="Authorized")
    with closing(sqlite3.connect(app.demo_path)) as connection:
        connection.execute("UPDATE resource SET execution_scope=?", (str(uuid4()),))
        connection.commit()
    open_run(page, app)
    enter_capability(page, app)
    command(page, app, "execute", "Failed")
    expect(page.locator("#action-result-code")).to_have_text("EXECUTION_CONFIRMED_FAILED")
    expect(page.locator("#action-explanation")).to_contain_text("did not occur")
    assert app.calls == 1 and not app.external()["notes"] and not app.external()["receipts"]
    assert len(app.ledger()["action_attempts"]) == 1 and len(mutations(app)) == 1


def test_untrusted_adapter_reason_never_becomes_a_result_message(page, action_app):
    app = action_app(action_state="Authorized", outcome="malicious")
    open_run(page, app)
    enter_capability(page, app)
    command(page, app, "execute", "Unknown")
    expect(page.locator("#action-result-message")).to_have_text(
        "The external effect outcome is unknown."
    )
    assert page.evaluate("window.actionAttack === undefined")
    assert "<img" not in page.locator("#action-result-message").text_content()
    assert app.calls == 1 and len(app.external()["notes"]) == 1
    assert_no_secret(page, app)


def test_store_failure_after_actual_effect_keeps_executing_uncertain(page, action_app, monkeypatch):
    from agentic_intent_gate.domain.action import ActionCode, ActionError

    app = action_app(action_state="Authorized")
    original = app.repo.finish

    def unavailable(*args):
        raise ActionError(ActionCode.DATABASE_UNAVAILABLE)

    monkeypatch.setattr(app.repo, "finish", unavailable)
    open_run(page, app)
    enter_capability(page, app)
    page.locator("#execute-action").click()
    expect(page.locator("#action-error-code")).to_have_text("DATABASE_UNAVAILABLE")
    expect(page.locator("#action-state")).to_have_text("Executing")
    wait_idle(page)
    expect(page.locator("#action-explanation")).to_contain_text("not yet confirmed")
    expect(page.locator("#execute-action")).to_be_hidden()
    monkeypatch.setattr(app.repo, "finish", original)
    page.reload()
    enter_capability(page, app)
    expect(page.locator("#action-state")).to_have_text("Executing")
    assert app.calls == 1 and len(app.ledger()["action_attempts"]) == 1
    assert len(app.external()["notes"]) == len(app.external()["receipts"]) == 1
    assert len(mutations(app)) == 1


def test_new_explicit_run_does_not_reuse_prior_admission_lock(page, action_app):
    app = action_app()
    open_run(page, app)
    enter_capability(page, app)
    command(page, app, "admit", "Pending")
    first_action = page.locator("#action-id").text_content()
    page.locator("#run-intent").fill("Distinct reviewed demo proposal")
    page.locator("#create-run").click()
    expect(page.locator("#run-state")).to_have_text("AwaitingApproval")
    expect(page.locator("#action-details")).to_be_hidden()
    page.locator("#approve-run").click()
    expect(page.locator("#run-state")).to_have_text("Approved")
    wait_idle(page)
    expect(page.locator("#admit-action")).to_be_enabled()
    page.locator("#admit-action").click()
    expect(page.locator("#action-state")).to_have_text("Pending")
    wait_idle(page)
    assert page.locator("#action-id").text_content() != first_action
    assert len(app.ledger()["actions"]) == 2 and app.calls == 0
