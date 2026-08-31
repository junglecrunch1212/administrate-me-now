from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import sqlcipher3
import sqlite_vec  # type: ignore[import-untyped]
from fastapi.testclient import TestClient

from adminme.events.registry import ensure_autoloaded, registry
from adminme.runtime.application import create_application
from adminme.sandbox.contracts import (
    FIDELITY_CONTRACT,
    MINIMUM_CARDINALITIES,
    MINIMUM_TAILS,
    REQUIRED_CATEGORIES,
    ManifestError,
    dominance_report,
    validate_manifest,
)
from adminme.sandbox.generator import (
    expected_coverage,
    generated_domain_events,
    generated_events,
    generated_history_events,
    measure_generated_coverage,
)
from adminme.sandbox.manager import (
    SandboxError,
    _baseline_identity,
    _network_contained_command,
    _network_contained_serve_command,
    load_artifact,
    run_test,
    sign_artifact,
)
from adminme.sandbox.manager import (
    create_disposable,
    discard_disposable,
    inspect_current,
    reconcile,
    tree_fingerprint,
)
from adminme.sandbox.profile import privacy_compare
from adminme.synthetic_member import (
    _live_fidelity_settlement_timeout_s,
    build_synthetic_member_settings,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_live_fidelity_settlement_deadline_scales_and_stays_bounded() -> None:
    assert _live_fidelity_settlement_timeout_s(0) == 180.0
    assert _live_fidelity_settlement_timeout_s(1_000) > 180.0
    assert _live_fidelity_settlement_timeout_s(1_000_000) == 900.0
    with pytest.raises(ValueError, match="cannot be negative"):
        _live_fidelity_settlement_timeout_s(-1)


def _projection_state_digest(
    instance_dir: Path, encryption_key: bytes
) -> tuple[str, int]:
    """Hash logical projection rows, independent of SQLite page layout."""

    digest = hashlib.sha256()
    database_count = 0
    for database in sorted((instance_dir / "projections").glob("*.db")):
        if database.name == "_checkpoints.db":
            continue
        database_count += 1
        digest.update(database.name.encode("utf-8"))
        connection = sqlcipher3.connect(str(database))
        try:
            key_pragma = f"x'{encryption_key.hex()}'"
            connection.execute(f'PRAGMA key = "{key_pragma}"')
            connection.enable_load_extension(True)
            sqlite_vec.load(connection)
            connection.enable_load_extension(False)
            connection.execute("PRAGMA query_only=ON")
            tables = [
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            for table in tables:
                digest.update(table.encode("utf-8"))
                quoted = '"' + table.replace('"', '""') + '"'
                columns = list(connection.execute(f"PRAGMA table_info({quoted})"))
                order = ", ".join(str(index) for index in range(1, len(columns) + 1))
                query = f"SELECT * FROM {quoted}"
                if order:
                    query += f" ORDER BY {order}"
                for row in connection.execute(query):
                    normalized = [
                        value.hex() if isinstance(value, bytes) else value
                        for value in row
                    ]
                    digest.update(
                        json.dumps(
                            normalized,
                            ensure_ascii=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    )
        finally:
            connection.close()
    return digest.hexdigest(), database_count


def _manifest(
    *,
    commit: str = "a" * 40,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "contract": FIDELITY_CONTRACT,
        "created_at": (created_at or datetime.now(timezone.utc)).isoformat(),
        "active_release": {"commit": commit, "immutable": True},
        "runtime": {
            "python": "3.14.6",
            "node": "24.19.0",
            "openclaw_gateway": f"2026.7.2@{'b' * 40}",
            "plugins": {"administrateme": "2.0.0-gate7"},
        },
        "event_schemas": {"messaging.received": 5},
        "projections": {"interactions": 4},
        "pipelines": {"pipeline:noise_filtering": "7.0.0"},
        "topology": {
            field: []
            for field in (
                "product_surfaces",
                "capabilities",
                "adapter_categories",
                "provider_families",
                "account_shapes",
                "workspace_shapes",
                "permission_capabilities",
            )
        },
        "observation": {
            "active": True,
            "governance_state": "configured",
            "external_writes_enabled": False,
        },
        "cardinalities": {name: 0 for name in MINIMUM_CARDINALITIES},
        "tails": {
            dimension: {quantile: 0 for quantile in MINIMUM_TAILS[dimension]}
            for dimension in MINIMUM_TAILS
        },
        "categories": {dimension: [] for dimension in REQUIRED_CATEGORIES},
        "personas": {
            persona: {
                "cardinalities": {"source_facts": 0, "messages": 0},
                "tails": {
                    "body_chars": {
                        "p50": 0,
                        "p90": 0,
                        "p95": 0,
                        "p99": 0,
                        "maximum": 0,
                    },
                    "thread_depth": {
                        "p50": 0,
                        "p90": 0,
                        "p95": 0,
                        "p99": 0,
                        "maximum": 0,
                    },
                },
                "categories": {
                    "message_directions": [],
                    "source_shapes": [],
                },
            }
            for persona in (
                "adult_primary",
                "adult_secondary",
                "dependents",
                "cos_service",
            )
        },
        "scale": {
            "event_log_events": 0,
            "event_log_bytes": 0,
            "replay_depth": 0,
        },
        "privacy": {
            "small_cell_threshold": 5,
            "small_cells_suppressed": 0,
            "content_persisted": False,
            "reversible_hashes_persisted": False,
        },
    }


def test_live_fidelity_contract_dominates_every_floor_and_fails_under_complexity() -> (
    None
):
    manifest = _manifest()
    target = expected_coverage(manifest)
    report = dominance_report(manifest, target)
    assert report["passes"] is True
    assert report["cardinalities"]["source_facts"] == {
        "required": 67_930,
        "actual": 67_930,
        "passes": True,
    }
    assert all(row["strict_superset"] for row in report["categories"].values())

    target["cardinalities"]["source_facts"] = 67_929
    failed = dominance_report(manifest, target)
    assert failed["passes"] is False
    assert failed["cardinalities"]["source_facts"]["passes"] is False

    stale = _manifest(created_at=datetime.now(timezone.utc) - timedelta(hours=1))
    with pytest.raises(ManifestError, match="not current"):
        validate_manifest(stale)
    unsafe = _manifest()
    unsafe["observation"]["active"] = False
    with pytest.raises(ManifestError, match="must be active"):
        validate_manifest(unsafe)


def test_full_floor_generator_is_typed_varied_and_deterministic() -> None:
    manifest = _manifest()
    ensure_autoloaded()
    first: Counter[str] = Counter()
    second: Counter[str] = Counter()
    first_keys: list[str] = []
    for event in generated_events("tenant-synthetic-acceptance", manifest):
        model = registry.get(event.type, event.schema_version)
        assert model is not None
        model.model_validate(event.payload)
        first[event.type] += 1
        if len(first_keys) < 10:
            first_keys.append(str(event.idempotency_key))
    for event in generated_events("tenant-synthetic-acceptance", manifest):
        second[event.type] += 1
    assert first == second
    assert sum(first.values()) == 67_930
    assert len(first) >= 10
    assert first["messaging.received"] >= 951
    assert first["messaging.sent"] > 0
    assert first["contact.record.observed"] >= 184
    assert first["adapter.cursor_advanced"] > 60_000
    assert first_keys == [
        f"live-fidelity:v2:message:received:{index:06d}" for index in range(10)
    ]
    proposals = list(
        generated_domain_events(
            "tenant-synthetic-acceptance",
            manifest,
            source_event_ids=[f"ev_fictional_{index:06d}" for index in range(600)],
        )
    )
    assert (
        len(proposals)
        == expected_coverage(manifest)["cardinalities"]["triage_candidates"]
    )
    assert len(proposals) >= 951
    assert (
        len({event.payload["parent_intent_id"] for event in proposals})
        == (expected_coverage(manifest)["cardinalities"]["triage_matters"])
    )
    assert {event.payload["target_kind"] for event in proposals} == set(
        REQUIRED_CATEGORIES["candidate_kinds"]
    )
    for event in proposals:
        model = registry.get(event.type, event.schema_version)
        assert model is not None
        model.model_validate(event.payload)
    history = list(
        generated_history_events(
            "tenant-synthetic-acceptance",
            manifest,
            source_event_ids=[f"ev_fictional_{index:06d}" for index in range(600)],
        )
    )
    assert len(history) >= 100
    assert {event.type for event in history} >= {
        "triage.item.enriched",
        "triage.item.confirmed",
        "triage.item.dismissed",
        "triage.item.superseded",
        "triage.feedback.recorded",
        "triage.policy_rule.approved",
        "triage.policy_rule.revoked",
    }
    for event in history:
        model = registry.get(event.type, event.schema_version)
        assert model is not None
        model.model_validate(event.payload)
    measured = measure_generated_coverage(manifest)
    measured_report = dominance_report(manifest, measured)
    assert measured_report["passes"] is True
    assert measured["cardinalities"]["members"] == 4
    assert measured["cardinalities"]["service_principals"] == 1
    assert measured["cardinalities"]["feedback_facts"] >= 30
    assert measured["cardinalities"]["rule_history_facts"] >= 12
    assert all(
        profile["cardinalities"]["source_facts"] > 0
        for profile in measured["personas"].values()
    )


def test_signed_fidelity_artifact_is_private_authenticated_and_current(
    tmp_path: Path,
) -> None:
    state = tmp_path / "sandbox-state"
    state.mkdir(mode=0o700)
    artifact = sign_artifact(
        _manifest(),
        {
            "contract": "administrateme-in-memory-privacy-comparison-v1",
            "passes": True,
            "exact_matches": 0,
            "suspicious_phrase_matches": 0,
            "content_persisted": False,
            "reversible_hashes_persisted": False,
        },
        state_root=state,
    )
    path = state / "profiles" / "active-fidelity.json"
    path.parent.mkdir(mode=0o700)
    path.write_text(json.dumps(artifact), encoding="utf-8")
    path.chmod(0o600)
    assert load_artifact(path, state_root=state)["manifest"]["active_release"] == {
        "commit": "a" * 40,
        "immutable": True,
    }
    outside = tmp_path / "outside-artifact.json"
    outside.write_bytes(path.read_bytes())
    outside.chmod(0o600)
    with pytest.raises(SandboxError, match="inside the state root"):
        load_artifact(outside, state_root=state)

    artifact["manifest"]["cardinalities"]["source_facts"] = 999_999
    path.write_text(json.dumps(artifact), encoding="utf-8")
    with pytest.raises(SandboxError, match="authentication failed"):
        load_artifact(path, state_root=state)
    path.chmod(0o644)
    with pytest.raises(SandboxError, match="broad permissions"):
        load_artifact(path, state_root=state)


def test_privacy_comparison_returns_counts_only() -> None:
    result = privacy_compare(
        [
            "Please review fictional case copper finch by Tuesday and record the next step",
            "fictional-person@fictional.invalid",
        ],
        [
            "A wholly unrelated live phrase with enough words to exercise the overlap detector safely",
            "live-person@provider.invalid",
        ],
    )
    assert result["passes"] is True
    assert "matches" not in result
    overlap = privacy_compare(
        [
            "A repeated live phrase with enough words to trigger the exact overlap detector now"
        ],
        [
            "A repeated live phrase with enough words to trigger the exact overlap detector now"
        ],
    )
    assert overlap["passes"] is False
    assert overlap["exact_matches"] == 1
    assert "phrase" not in overlap


def test_test_commands_receive_enforced_network_containment() -> None:
    denied = _network_contained_command(["python", "-V"], require_gateway=False)
    if sys.platform == "darwin":
        assert denied[0].endswith("sandbox-exec")
        assert "(deny network*)" in denied[2]
        gateway = _network_contained_command(["python", "-V"], require_gateway=True)
        assert 'tcp "localhost:18789"' in gateway[2]
        assert "python" in gateway
        server = _network_contained_serve_command(
            ["python", "-m", "adminme.cli.main"],
            port=8321,
            require_gateway=False,
        )
        assert "(deny network*)" in server[2]
        assert 'local tcp "localhost:8321"' in server[2]
        assert "localhost:18789" not in server[2]
    elif sys.platform.startswith("linux"):
        assert "--net" in denied
        with pytest.raises(SandboxError, match="cannot enforce"):
            _network_contained_command(["python", "-V"], require_gateway=True)


def test_baseline_identity_covers_profile_candidate_contract_and_generator() -> None:
    first_id, identity = _baseline_identity(_manifest(), REPOSITORY_ROOT)
    changed = _manifest()
    changed["cardinalities"]["source_facts"] = 123
    second_id, _second_identity = _baseline_identity(changed, REPOSITORY_ROOT)
    assert first_id != second_id
    assert identity["candidate_contract"]["generator_version"] == 4
    assert identity["candidate_contract"]["source_tree_sha256"]
    assert identity["candidate_contract"]["event_schemas"]
    assert identity["candidate_contract"]["projections"]
    assert identity["candidate_contract"]["pipelines"]
    assert identity["candidate_contract"]["runtime_versions"]


def test_gateway_required_mode_fails_before_reconcile_without_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ADMINME_OPENCLAW_GATEWAY_TOKEN", raising=False)
    monkeypatch.delenv("ADMINME_OPENCLAW_SHARED_SECRET", raising=False)
    with pytest.raises(SandboxError, match="Gateway-required test cannot run"):
        run_test(
            ["true"],
            artifact_path=tmp_path / "missing-artifact",
            active_release_pointer=tmp_path / "missing-pointer",
            state_root=tmp_path / "state",
            repository_root=REPOSITORY_ROOT,
            require_gateway=True,
        )


def test_active_runtime_profiles_its_exact_release_without_content(
    tmp_path: Path,
) -> None:
    settings = build_synthetic_member_settings(
        tmp_path / "profile-source",
        REPOSITORY_ROOT,
    )
    settings = replace(
        settings,
        host_topology={"deployment_binding": {"release_commit": "c" * 40}},
    )
    app = create_application(settings)
    with TestClient(app) as client:
        response = client.get("/api/system/sandbox-fidelity")
        assert response.status_code == 200, response.text
        manifest = validate_manifest(response.json())
        serialized = json.dumps(manifest)
        assert manifest["active_release"]["commit"] == "c" * 40
        assert manifest["privacy"]["content_persisted"] is False
        assert manifest["personas"]["adult_primary"]["cardinalities"]["messages"] > 0
        assert "Signed form needed" not in serialized
        assert "close-contact@member-fixture.invalid" not in serialized

        comparison = client.post(
            "/api/system/sandbox-privacy-compare",
            json={"synthetic_values": ["Signed form needed"]},
        )
        assert comparison.status_code == 200
        assert comparison.json()["passes"] is False
        assert "Signed form needed" not in comparison.text
        assert client.portal is not None
        assert (
            client.portal.call(
                _external_write_count,
                app.state.runtime,
            )
            == 0
        )


async def _external_write_count(runtime: Any) -> int:
    assert runtime.event_log is not None
    count = 0
    async for _event in runtime.event_log.read_since(
        types=["external_sync.write_requested"]
    ):
        count += 1
    return count


async def _rebuild_all_projections(runtime: Any) -> int:
    assert runtime.projections is not None
    names = runtime.projections.projection_names()
    for name in names:
        await runtime.projections.rebuild(name)
    return len(names)


def test_full_volume_canonical_baseline_is_composed_cloned_and_immutable(
    tmp_path: Path,
) -> None:
    state = tmp_path / "sandbox-state"
    state.mkdir(mode=0o700)
    release = tmp_path / "deployment" / "app" / "releases" / ("a" * 40)
    release.mkdir(parents=True)
    pointer = tmp_path / "deployment" / "app" / "current-release.json"
    pointer.write_text(
        json.dumps({"commit": "a" * 40, "path": str(release)}),
        encoding="utf-8",
    )
    pointer.chmod(0o600)
    artifact = sign_artifact(
        _manifest(),
        {
            "contract": "administrateme-in-memory-privacy-comparison-v1",
            "passes": True,
            "exact_matches": 0,
            "suspicious_phrase_matches": 0,
            "content_persisted": False,
            "reversible_hashes_persisted": False,
        },
        state_root=state,
    )
    artifact_path = state / "profiles" / "active-fidelity.json"
    artifact_path.parent.mkdir(mode=0o700)
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    artifact_path.chmod(0o600)

    metadata = reconcile(
        artifact_path=artifact_path,
        active_release_pointer=pointer,
        state_root=state,
        repository_root=REPOSITORY_ROOT,
    )
    assert metadata["dominance"]["passes"] is True
    assert metadata["emitted_workload_dominance"]["passes"] is True
    assert metadata["privacy_proof"]["passes"] is True
    assert metadata["production_composition"]["passes"] is True
    assert metadata["production_composition"]["projection_count"] == 15
    assert metadata["production_composition"]["pipeline_count"] == 8
    assert metadata["production_composition"]["real_adapter_instances"] == 0
    assert metadata["production_composition"]["external_effect_events"] == 0
    assert metadata["production_composition"]["measured"]["triage_candidates"] >= 951
    assert metadata["production_composition"]["measured"]["triage_matters"] >= 522
    assert (
        metadata["production_composition"]["measured"][
            "enrichment_or_destination_required"
        ]
        >= 591
    )
    assert set(
        metadata["production_composition"]["measured"]["candidate_kinds"]
    ) >= set(REQUIRED_CATEGORIES["candidate_kinds"])
    inspection = inspect_current(
        state_root=state,
        repository_root=REPOSITORY_ROOT,
    )
    assert inspection["inspection"]["passes"] is True

    first = create_disposable(state_root=state, repository_root=REPOSITORY_ROOT)
    second = create_disposable(state_root=state, repository_root=REPOSITORY_ROOT)
    canonical = state / "baselines" / metadata["baseline_id"] / "instance"
    canonical_before = tree_fingerprint(canonical)

    encryption_key = first.encryption_key_path.read_bytes()
    replay_settings = build_synthetic_member_settings(
        first.path,
        REPOSITORY_ROOT,
        encryption_key=encryption_key,
    )
    replay_app = create_application(replay_settings)
    with TestClient(replay_app) as client:
        projection_before, projection_count = _projection_state_digest(
            first.path, encryption_key
        )
        assert projection_count == 15
        assert client.portal is not None
        assert (
            client.portal.call(_rebuild_all_projections, replay_app.state.runtime) == 15
        )
        projection_after, replayed_projection_count = _projection_state_digest(
            first.path, encryption_key
        )
        assert replayed_projection_count == 15
        assert projection_after == projection_before
        response = client.get(
            "/api/member/v1/surfaces/triage",
            params={"triage_lens": "all_candidates", "triage_limit": 100},
        )
        assert response.status_code == 200, response.text
        assert len(response.json()["items"]) >= 100

    first_marker = first.path / "config" / "disposable-only.json"
    first_marker.write_text('{"isolated": true}\n', encoding="utf-8")
    assert not (second.path / "config" / "disposable-only.json").exists()
    assert tree_fingerprint(canonical) == canonical_before
    discard_disposable(first)
    discard_disposable(second)

    reused = reconcile(
        artifact_path=artifact_path,
        active_release_pointer=pointer,
        state_root=state,
        repository_root=REPOSITORY_ROOT,
    )
    assert reused["baseline_id"] == metadata["baseline_id"]
    assert reused["instance_fingerprint"] == canonical_before
