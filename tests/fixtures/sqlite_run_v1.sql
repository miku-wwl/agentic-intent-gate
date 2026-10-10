CREATE TABLE runs (
    run_id TEXT PRIMARY KEY NOT NULL CHECK(length(run_id) = 36),
    intent TEXT NOT NULL CHECK(length(intent) BETWEEN 1 AND 8192
        AND length(CAST(intent AS BLOB)) BETWEEN 1 AND 8192),
    state TEXT NOT NULL CHECK(state IN (
        'IntentReceived', 'PlanGenerating', 'PlanValidated', 'Ready',
        'AwaitingApproval', 'Approved', 'Rejected', 'PlanRejected')),
    validated_plan_json TEXT,
    risk_class TEXT CHECK(risk_class IS NULL OR risk_class IN (
        'READ_ONLY', 'SIDE_EFFECT', 'APPROVAL_REQUIRED')),
    version INTEGER NOT NULL CHECK(version BETWEEN 1 AND 9007199254740991),
    created_at TEXT NOT NULL CHECK(length(created_at) = 27),
    updated_at TEXT NOT NULL CHECK(length(updated_at) = 27 AND updated_at >= created_at),
    last_error_code TEXT,
    last_error_message TEXT CHECK(last_error_message IS NULL
        OR length(last_error_message) BETWEEN 1 AND 256),
    CHECK(validated_plan_json IS NULL OR CASE WHEN json_valid(validated_plan_json)
        THEN json_type(validated_plan_json) = 'object'
            AND length(CAST(validated_plan_json AS BLOB)) <= 16384
            AND json_extract(validated_plan_json, '$.risk_class') IS risk_class
        ELSE 0 END),
    CHECK(
        (state IN ('IntentReceived', 'PlanGenerating')
            AND validated_plan_json IS NULL AND risk_class IS NULL
            AND last_error_code IS NULL AND last_error_message IS NULL)
        OR (state = 'PlanRejected' AND validated_plan_json IS NULL AND risk_class IS NULL
            AND last_error_code IS NOT NULL AND last_error_code IN (
                'PLAN_VALIDATION_ERROR', 'MODEL_UNAVAILABLE', 'MODEL_TIMEOUT',
                'MODEL_RESPONSE_ERROR', 'INTERNAL_ERROR', 'RECOVERY_INTERRUPTED')
            AND last_error_message IS NOT NULL)
        OR (state IN ('PlanValidated', 'Ready', 'AwaitingApproval', 'Approved', 'Rejected')
            AND validated_plan_json IS NOT NULL AND risk_class IS NOT NULL
            AND last_error_code IS NULL AND last_error_message IS NULL)),
    CHECK(state <> 'Ready' OR risk_class = 'READ_ONLY'),
    CHECK(state NOT IN ('AwaitingApproval', 'Approved', 'Rejected')
        OR risk_class IN ('SIDE_EFFECT', 'APPROVAL_REQUIRED'))
) STRICT;

CREATE TABLE approval_decisions (
    decision_id TEXT PRIMARY KEY NOT NULL CHECK(length(decision_id) = 36),
    run_id TEXT NOT NULL UNIQUE REFERENCES runs(run_id),
    decision TEXT NOT NULL CHECK(decision IN ('APPROVE', 'REJECT')),
    reason TEXT CHECK(reason IS NULL OR (length(reason) <= 1024
        AND length(CAST(reason AS BLOB)) <= 4096)),
    actor_type TEXT NOT NULL CHECK(actor_type = 'LOCAL_USER'),
    created_at TEXT NOT NULL CHECK(length(created_at) = 27),
    run_version_before INTEGER NOT NULL CHECK(run_version_before BETWEEN 1 AND 9007199254740990),
    run_version_after INTEGER NOT NULL CHECK(run_version_after = run_version_before + 1)
) STRICT;

CREATE TABLE audit_events (
    event_id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    run_version INTEGER NOT NULL CHECK(run_version BETWEEN 1 AND 9007199254740991),
    event_type TEXT NOT NULL CHECK(event_type IN (
        'RUN_CREATED', 'PLAN_GENERATION_STARTED', 'PLAN_VALIDATED', 'PLAN_REJECTED',
        'RUN_READY', 'APPROVAL_REQUIRED', 'APPROVED', 'REJECTED',
        'RECOVERY_REJECTED', 'RECOVERY_ROUTED')),
    created_at TEXT NOT NULL CHECK(length(created_at) = 27),
    safe_payload_json TEXT NOT NULL CHECK(CASE WHEN json_valid(safe_payload_json)
        THEN json_type(safe_payload_json) = 'object'
            AND length(CAST(safe_payload_json AS BLOB)) <= 1024
            AND json_extract(safe_payload_json, '$.version_after') IS run_version
        ELSE 0 END),
    UNIQUE(run_id, run_version)
) STRICT;
