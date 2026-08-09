"""Install-time source contracts prevent new silent adapter paths."""

from adminme.lib.pack_registry.models import AdapterManifest
from adminme.lib.pack_registry.validator.source_assurance_contract import (
    check_source_assurance_contract,
)


def test_candidate_source_without_terminal_assessment_fails_closed() -> None:
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-A",
                    "runtime": "central",
                    "event_families": ["messaging.received"],
                }
            ]
        }
    )
    [finding] = check_source_assurance_contract(manifest)
    assert finding.severity == "error"
    assert "no source_assurance disposition" in finding.message


def test_candidate_source_requires_named_assessor_and_features_terminal() -> None:
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-A",
                    "runtime": "central",
                    "event_families": ["messaging.received"],
                }
            ],
            "source_assurance": [
                {
                    "source_events": ["messaging.received"],
                    "mode": "candidate_assessment",
                }
            ],
        }
    )
    findings = check_source_assurance_contract(manifest)
    assert {finding.field_path for finding in findings} == {
        "source_assurance[0].terminal_events",
        "source_assurance[0].assessor",
    }


def test_complete_candidate_contract_passes() -> None:
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-A",
                    "runtime": "central",
                    "event_families": ["messaging.received"],
                }
            ],
            "source_assurance": [
                {
                    "source_events": ["messaging.received"],
                    "mode": "candidate_assessment",
                    "terminal_events": ["triage.intent.features_extracted"],
                    "assessor": "pipeline:noise_filtering",
                }
            ],
        }
    )
    assert check_source_assurance_contract(manifest) == []


def test_candidate_bearing_knowledge_requires_projection_and_assessment() -> None:
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-D",
                    "runtime": "bridge",
                    "event_families": ["member_knowledge.note_added"],
                }
            ],
            "source_assurance": [
                {
                    "source_events": ["member_knowledge.note_added"],
                    "mode": "canonical_projection",
                    "projections": ["member_knowledge"],
                }
            ],
        }
    )
    findings = check_source_assurance_contract(manifest)
    assert any(
        "candidate_assessment or candidate_not_applicable" in item.message
        for item in findings
    )


def test_knowledge_metadata_must_explicitly_declare_no_candidate_semantics() -> None:
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-D",
                    "runtime": "bridge",
                    "event_families": ["member_knowledge.folder_created"],
                }
            ],
            "source_assurance": [
                {
                    "source_events": ["member_knowledge.folder_created"],
                    "mode": "canonical_projection",
                    "projections": ["member_knowledge"],
                },
                {
                    "source_events": ["member_knowledge.folder_created"],
                    "mode": "candidate_not_applicable",
                },
            ],
        }
    )
    assert check_source_assurance_contract(manifest) == []


def test_source_cannot_be_assessed_and_candidate_not_applicable() -> None:
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-D",
                    "runtime": "bridge",
                    "event_families": ["member_knowledge.note_added"],
                }
            ],
            "source_assurance": [
                {
                    "source_events": ["member_knowledge.note_added"],
                    "mode": "canonical_projection",
                    "projections": ["member_knowledge"],
                },
                {
                    "source_events": ["member_knowledge.note_added"],
                    "mode": "candidate_assessment",
                    "terminal_events": ["triage.intent.features_extracted"],
                    "assessor": "pipeline:member_knowledge_indexing",
                },
                {
                    "source_events": ["member_knowledge.note_added"],
                    "mode": "candidate_not_applicable",
                },
            ],
        }
    )
    findings = check_source_assurance_contract(manifest)
    assert any("cannot be both candidate-bearing" in item.message for item in findings)


def test_unknown_projection_and_terminal_schema_fail_closed(tmp_path) -> None:
    class Registry:
        def known_types(self) -> list[str]:
            return ["messaging.received"]

    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-A",
                    "runtime": "central",
                    "event_families": ["messaging.received"],
                }
            ],
            "source_assurance": [
                {
                    "source_events": ["messaging.received"],
                    "mode": "candidate_assessment",
                    "terminal_events": ["triage.intent.features_extracted"],
                    "assessor": "pipeline:noise_filtering",
                }
            ],
        }
    )
    findings = check_source_assurance_contract(
        manifest, registry=Registry(), projections_dir=tmp_path
    )
    assert any("terminal event" in item.message for item in findings)

    projection_manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-B",
                    "runtime": "central",
                    "event_families": ["calendar.event_added"],
                }
            ],
            "source_assurance": [
                {
                    "source_events": ["calendar.event_added"],
                    "mode": "canonical_projection",
                    "projections": ["misspelled_calendar"],
                }
            ],
        }
    )
    projection_findings = check_source_assurance_contract(
        projection_manifest, registry=Registry(), projections_dir=tmp_path
    )
    assert any("unknown projection" in item.message for item in projection_findings)
