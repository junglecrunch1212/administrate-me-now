"""Doctor exercises the production intake policy with only isolated fixtures."""

from pathlib import Path

from bootstrap.doctor import synthetic_round_trip


def test_doctor_proof_uses_member_submission_and_preserves_outcome_checks() -> None:
    proof = synthetic_round_trip(Path(__file__).resolve().parents[2])
    assert proof["explicit_member_submission"] is True
    assert proof["passive_obligation_candidates"] == 0
    assert proof["dashboard_review_visible"] is True
    assert proof["review_suppressed_by_observation"] is True
    assert proof["completion_state"] == proof["rebuild_state"] == "done"
    assert proof["workbook_exists"] is True
    assert proof["reverse_sidecar_count"] > 0
    assert proof["projection_checkpoints_healthy"] is True
    assert proof["external_side_effects"] == 0
