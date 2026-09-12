"""Read-only lab guards and frozen input manifest; never invoke a model."""

from datetime import date
from pathlib import Path

import pytest

from tools.triage_model_lab import (
    CASE_IDS,
    FIXTURE_HASHES,
    FIXTURES,
    SEMANTIC_IDS,
    LabRefused,
    _case_evidence,
    _retry_policy,
    load_frozen_cases,
    validate_environment,
)


def _environment(tmp_path: Path) -> dict[str, str]:
    return {
        "GITHUB_ACTIONS": "true",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "ADMINME_MODEL_LAB_APPROVED_SHA": "a" * 40,
        "OPENCLAW_GATEWAY_TOKEN": "fixture-token-never-used",
        "RUNNER_TEMP": str(tmp_path),
    }


@pytest.mark.parametrize(
    ("change", "system"),
    [
        ({}, "Darwin"),
        ({"GITHUB_ACTIONS": "false"}, "Linux"),
        ({"RUNNER_ENVIRONMENT": "self-hosted"}, "Linux"),
        ({"ADMINME_MODEL_LAB_APPROVED_SHA": "b" * 40}, "Linux"),
        ({"OPENCLAW_GATEWAY_TOKEN": ""}, "Linux"),
        ({"ADMINME_TEST_MODE": "1"}, "Linux"),
        ({"ADMINME_INSTANCE_DIR": "/fixture-forbidden-instance"}, "Linux"),
        ({"ADMINME_DETERMINISTIC_OPENCLAW_FIXTURE": "1"}, "Linux"),
        ({"OPENCLAW_GATEWAY_URL": "https://example.invalid"}, "Linux"),
        ({"RUNNER_TEMP": "relative-path"}, "Linux"),
    ],
)
def test_model_lab_refuses_non_hosted_or_unapproved_execution(tmp_path, change, system):
    with pytest.raises(LabRefused):
        validate_environment(
            {**_environment(tmp_path), **change},
            system=system,
            head="a" * 40,
            as_of=date(2026, 9, 12),
        )


def test_model_lab_allows_only_current_frozen_deadline_window(tmp_path):
    assert (
        validate_environment(
            _environment(tmp_path),
            system="Linux",
            head="a" * 40,
            as_of=date(2026, 9, 12),
        )
        == tmp_path.resolve()
    )
    with pytest.raises(LabRefused, match="deadline"):
        validate_environment(
            _environment(tmp_path),
            system="Linux",
            head="a" * 40,
            as_of=date(2026, 9, 17),
        )


def test_model_lab_manifest_has_all_nineteen_sources_without_expected_answers():
    cases = load_frozen_cases()
    assert tuple(case["case_id"] for case in cases) == (*CASE_IDS, *SEMANTIC_IDS)
    assert len({case["message_id"] for case in cases}) == 19
    assert cases[11]["thread_id"] == cases[12]["thread_id"]
    assert cases[11]["occurred_at"] < cases[12]["occurred_at"]
    forbidden = {"actions", "forbidden", "expected", "source_context", "scoring"}
    assert all(not forbidden.intersection(case) for case in cases)


def test_model_lab_rejects_modified_or_added_frozen_input(tmp_path):
    for name in FIXTURE_HASHES:
        (tmp_path / name).write_bytes((FIXTURES / name).read_bytes())
    source = tmp_path / "golden-sources-frozen.json"
    source.write_bytes(source.read_bytes() + b"\n")
    with pytest.raises(LabRefused, match="digest"):
        load_frozen_cases(tmp_path)
    source.write_bytes((FIXTURES / source.name).read_bytes())
    (tmp_path / "unexpected.json").write_text("{}")
    with pytest.raises(LabRefused, match="four files"):
        load_frozen_cases(tmp_path)


def test_model_lab_attempt_ceiling_is_bound_to_current_production_retry_constant():
    policy = _retry_policy()
    assert policy["delay_seconds"] == [0.5, 2.0]
    assert policy["max_gateway_skill_attempts_per_source"] == 3
    assert policy["max_source_gateway_skill_attempts"] == 57


def test_model_lab_never_scores_missing_or_failed_inference_as_no_action():
    source_id = "fixture-source-event"
    assessment = {
        "event_id": "fixture-assessment-event",
        "type": "triage.intent.features_extracted",
        "causation_id": source_id,
        "payload": {
            "source_event_id": source_id,
            "extraction_state": "complete",
            "assessment_outcome": "no_candidate",
        },
    }
    result = _case_evidence([assessment], source_id)
    assert result["failures"] == ["no_source_correlated_actual_classifier_receipt"]
    receipt = {
        "event_id": "fixture-skill-event",
        "type": "skill.call.recorded",
        "causation_id": source_id,
        "payload": {
            "openclaw_invocation_id": "fixture-never-invoked",
            "provider": "fixture",
            "outputs": {
                "assessment_contract": "personal-obligation-assessment-v1",
            },
        },
    }
    assert _case_evidence([receipt, assessment], source_id)["failures"] == [
        "incomplete_actual_classifier_receipt"
    ]
    receipt["payload"]["provider"] = "openclaw-routed"
    receipt["payload"]["outputs"]["reasons"] = ["skill_failure_defensive_default"]
    assert _case_evidence([receipt, assessment], source_id)["failures"] == [
        "incomplete_actual_classifier_receipt"
    ]
    receipt["payload"]["outputs"]["reasons"] = []
    failure = {
        "event_id": "fixture-failed-event",
        "type": "skill.call.failed",
        "causation_id": source_id,
        "payload": {},
    }
    assert _case_evidence([failure, receipt, assessment], source_id)["failures"] == [
        "failed_or_suppressed_classifier_attempt"
    ]
