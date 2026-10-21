"""Exact bridge enrollment rebinding through the deployed composition root.

All roots, identities, credentials and Apple providers below are disposable
fixtures. Behavioral execution belongs to hosted CI, never the family runtime.
"""

from __future__ import annotations

import copy
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adminme.bridge_enrollment import BridgeEnrollmentError, capability_set_digest
from adminme_bridge.enrollment_helper import MemberBridgeEnrollmentHelper
from bootstrap import runtime_service
from bootstrap.bridge_enrollment import MemberBridgeInstaller, MemberBridgePaths
from bootstrap.bridge_readiness_transition import (
    BridgeReadinessTransition,
    BridgeReadinessTransitionError,
)
from bootstrap.candidate_readiness import (
    CandidateReadinessError,
    capture_deployment_snapshot,
    deployment_operation_lock,
)
from bootstrap.contracts import DeploymentPaths
from bootstrap.cos_channel_activation import CoSChannelActivator
from tests.acceptance.test_self_service_member_bridge_enrollment import (
    A,
    A_HEADERS,
    A_LOGIN,
    ACCOUNT_COORDINATE,
    B,
    B_HEADERS,
    B_LOGIN,
    CALENDAR,
    KEY,
    PACK,
    REPO_ROOT,
    _calendar_observation,
    _installed_release_settings,
    _provider_effect_count,
)
from tests.unit.bootstrap.test_candidate_readiness import _deployment, _private_write


REQUEST = "readiness-bridge-fixture-request"


def _bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def _binding(paths: DeploymentPaths) -> dict[str, str]:
    snapshot = capture_deployment_snapshot(paths)
    return {
        "release_commit": snapshot.predecessor_commit,
        "instance_fingerprint": snapshot.instance_fingerprint,
        "host_fingerprint": snapshot.host_fingerprint,
        "configuration_fingerprint": snapshot.configuration_fingerprint,
    }


def _fixture(tmp_path: Path) -> tuple[DeploymentPaths, BridgeReadinessTransition]:
    paths = _deployment(tmp_path)
    (paths.instance_dir / "bridges" / "member-mapping.json").unlink()
    _private_write(
        paths.instance_dir / "config" / "member-mapping.json",
        _bytes(
            {
                "schema_version": 2,
                "household_label": "Readiness fixture",  # fixture:tenant_data:ok
                "members": [
                    {
                        "member_id": A,
                        "label": "Fixture A",
                        "role": "principal",
                        "tailscale_identity": A_LOGIN,
                    },
                    {
                        "member_id": B,
                        "label": "Fixture B",
                        "role": "principal",
                        "tailscale_identity": B_LOGIN,
                    },
                ],
                "memberships": [],
                "relationships": [],
            }
        ),
    )
    _private_write(
        paths.instance_dir / "config" / "secret-references.json",
        _bytes(
            {
                "references": {
                    "event_log": "fixture-event-log",
                    "openclaw_shared": "fixture-openclaw",
                },
            }
        ),
    )
    _write_attestation(paths)
    return paths, BridgeReadinessTransition(paths, KEY)


def _write_attestation(paths: DeploymentPaths) -> dict[str, Any]:
    evidence = {
        "contract": "deployment-readiness-v1",
        "deployment_binding": _binding(paths),
        "source": {
            "attestation_id": "original-fixture-attestation",
            "reviewed_at": "2026-08-01T12:00:00Z",
        },
        "recovery": {
            "separate_admin_available": True,
            "filevault_unlock_tested": True,
            "automatic_login_disabled": True,
            "tested_at": "2026-08-01T12:05:00Z",
        },
        "identity_acceptance": {
            "member_ids": [A, B],
            "tested_at": "2026-08-01T12:06:00Z",
        },
        "provider_effect_window": {"started_at": "2026-08-01T12:07:00Z"},
    }
    _private_write(
        paths.instance_dir / "config" / "deployment-readiness.json", _bytes(evidence)
    )
    return evidence


def _record() -> dict[str, Any]:
    return {
        "member_id": A,
        "owner_scope": f"private:{A}",
        "role": "principal",
        "request_id": REQUEST,
        "instance_id": "tenant_fixture_pending",
        "enrollment_id": "fixture-bridge-enrollment",
        "release_commit": "a" * 40,
        "requested_capabilities": ["apple_calendar"],
        "capability_set_sha256": capability_set_digest(["apple_calendar"]),
        "allowed_packs": [PACK],
        "configured_packs": [],
        "enabled_packs": [],
        "authorized_packs": [],
        "adapters_active": [],
        "pack_config": {},
        "status": "package-exported",
        "observation_only": True,
        "expires_at": "2031-01-01T00:00:00Z",
    }


def _authority(operation: str = "prepare") -> dict[str, str]:
    return {
        "kind": "member",
        "actor_member_id": A,
        "member_id": A,
        "request_id": REQUEST,
        "proof_sha256": "d" * 64,
        "operation": operation,
        "tenant_id": "tenant_fixture_pending",
        "identity": A_LOGIN,
        "role": "principal",
    }


def _commit(transition: BridgeReadinessTransition) -> dict[str, Any]:
    return transition.commit(
        previous_payload=None,
        next_payload=_bytes({A_LOGIN: _record()}),
        authority=_authority(),
    )


def test_exact_bridge_delta_preserves_original_physical_proofs(tmp_path: Path) -> None:
    paths, transition = _fixture(tmp_path)
    original = json.loads(transition.readiness_path.read_bytes())
    result = _commit(transition)
    assert result["deployment_binding"] == _binding(paths)
    assert (
        result["deployment_binding"]["configuration_fingerprint"]
        != original["deployment_binding"]["configuration_fingerprint"]
    )
    for field in original.keys() - {"deployment_binding"}:
        assert result[field] == original[field]
    for field in ("release_commit", "instance_fingerprint", "host_fingerprint"):
        assert (
            result["deployment_binding"][field] == original["deployment_binding"][field]
        )
    assert not transition.pending
    # A repeated exact binding write is inert, and cannot refresh proof dates.
    payload = transition.index_path.read_bytes()
    assert (
        transition.commit(
            previous_payload=payload,
            next_payload=payload,
            authority=_authority("revoke"),
        )
        == result
    )


@pytest.mark.parametrize(
    "stage",
    [
        "before_journal",
        "before_index",
        "after_index",
        "before_readiness",
        "after_readiness",
    ],
)
def test_write_failure_recovers_exact_old_or_committed_new_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    paths, transition = _fixture(tmp_path)
    original = transition.readiness_path.read_bytes()
    write = transition._write
    fired = False

    def fail(path: Path, payload: bytes) -> None:
        nonlocal fired
        selected = {
            "before_journal": transition.journal_path,
            "before_index": transition.index_path,
            "after_index": transition.index_path,
            "before_readiness": transition.readiness_path,
            "after_readiness": transition.readiness_path,
        }[stage]
        if path == selected and not fired:
            fired = True
            if stage.startswith("after"):
                write(path, payload)
            raise OSError("fixture interrupted publication")
        write(path, payload)

    monkeypatch.setattr(transition, "_write", fail)
    if stage == "after_readiness":
        assert _commit(transition)["deployment_binding"] == _binding(paths)
        assert transition.index_path.exists()
    else:
        with pytest.raises(OSError):
            _commit(transition)
        assert not transition.index_path.exists()
        assert transition.readiness_path.read_bytes() == original
        # The same exact operation can proceed after its failed publication.
        assert _commit(transition)["deployment_binding"] == _binding(paths)
    assert not transition.pending


class _Crash(BaseException):
    pass


def _crash_after(
    transition: BridgeReadinessTransition, monkeypatch: pytest.MonkeyPatch, path: Path
) -> bytes:
    write = transition._write

    def crash(target: Path, payload: bytes) -> None:
        write(target, payload)
        if target == path:
            raise _Crash()

    monkeypatch.setattr(transition, "_write", crash)
    with pytest.raises(_Crash):
        _commit(transition)
    return transition.journal_path.read_bytes()


@pytest.mark.parametrize("committed", [False, True])
def test_process_crash_recovery_is_bound_to_exact_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, committed: bool
) -> None:
    paths, transition = _fixture(tmp_path)
    original = transition.readiness_path.read_bytes()
    _crash_after(
        transition,
        monkeypatch,
        transition.readiness_path if committed else transition.index_path,
    )
    # The next maintenance must not start on an unresolved bridge transaction.
    with pytest.raises(CandidateReadinessError, match="recovery is pending"):
        capture_deployment_snapshot(paths)
    recovered = BridgeReadinessTransition(paths, KEY)
    recovered.recover()
    assert not recovered.pending
    assert recovered.index_path.exists() is committed
    if committed:
        assert json.loads(recovered.readiness_path.read_bytes())[
            "deployment_binding"
        ] == _binding(paths)
    else:
        assert recovered.readiness_path.read_bytes() == original


@pytest.mark.parametrize(
    "tamper",
    ["signature", "schema", "path", "index", "configuration", "readiness", "release"],
)
def test_recovery_rejects_tamper_without_overwriting_current_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    paths, transition = _fixture(tmp_path)
    journal = json.loads(_crash_after(transition, monkeypatch, transition.index_path))
    if tamper in {"signature", "schema", "path"}:
        if tamper == "signature":
            journal["hmac_sha256"] = "0" * 64
        elif tamper == "schema":
            journal["document"]["contract"] = "different-contract"
        else:
            journal["document"]["index_path"] = "outside-root"
        _private_write(transition.journal_path, _bytes(journal))
    else:
        target = {
            "index": transition.index_path,
            "configuration": paths.instance_dir / "config" / "adapters.json",
            "readiness": transition.readiness_path,
            "release": paths.app_dir / "current-release.json",
        }[tamper]
        document = json.loads(target.read_bytes())
        document["fixture_unrelated_change"] = True
        _private_write(target, _bytes(document))
    before = (
        transition.index_path.read_bytes(),
        transition.readiness_path.read_bytes(),
    )
    with pytest.raises((BridgeReadinessTransitionError, CandidateReadinessError)):
        BridgeReadinessTransition(paths, KEY).recover()
    assert transition.pending
    assert (
        transition.index_path.read_bytes(),
        transition.readiness_path.read_bytes(),
    ) == before


def test_unrelated_drift_cross_member_and_stale_index_are_rejected(
    tmp_path: Path,
) -> None:
    paths, transition = _fixture(tmp_path)
    next_index = {A_LOGIN: _record(), B_LOGIN: {"member_id": B}}
    with pytest.raises(BridgeReadinessTransitionError, match="another identity"):
        transition.commit(
            previous_payload=None,
            next_payload=_bytes(next_index),
            authority=_authority(),
        )
    _private_write(transition.index_path, _bytes({}))
    with pytest.raises(BridgeReadinessTransitionError, match="changed since"):
        _commit(transition)
    transition.index_path.unlink()
    _private_write(
        paths.instance_dir / "config" / "adapters.json",
        _bytes({"adapters": [], "fixture_drift": True}),
    )
    with pytest.raises(
        BridgeReadinessTransitionError, match="physical readiness binding is stale"
    ):
        _commit(transition)
    assert not transition.pending
    assert not transition.index_path.exists()


def test_stale_journal_cannot_overwrite_later_legitimate_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, transition = _fixture(tmp_path)
    stale = _crash_after(transition, monkeypatch, transition.readiness_path)
    current = BridgeReadinessTransition(paths, KEY)
    current.recover()
    before = current.index_path.read_bytes()
    revoked = json.loads(before)
    revoked[A_LOGIN].update(status="revoked", revoked_at="2031-01-01T00:00:00Z")
    current.commit(
        previous_payload=before,
        next_payload=_bytes(revoked),
        authority=_authority("revoke"),
    )
    final = current.index_path.read_bytes()
    _private_write(current.journal_path, stale)
    with pytest.raises(BridgeReadinessTransitionError, match="changed independently"):
        current.recover()
    assert current.index_path.read_bytes() == final


def test_cleanup_failure_retains_commit_and_blocks_new_maintenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, transition = _fixture(tmp_path)

    def fail() -> None:
        raise OSError("fixture journal cleanup unavailable")

    monkeypatch.setattr(transition, "_remove_journal", fail)
    committed = _commit(transition)
    assert transition.pending
    assert json.loads(transition.readiness_path.read_bytes()) == committed
    with pytest.raises(CandidateReadinessError, match="recovery is pending"):
        capture_deployment_snapshot(paths)
    BridgeReadinessTransition(paths, KEY).recover()
    assert _binding(paths) == committed["deployment_binding"]


def test_service_composition_enrollment_restart_and_next_maintenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    installed, commit = _installed_release_settings(tmp_path)
    paths, transition = _fixture(tmp_path)
    release = paths.app_dir / "releases" / commit
    shutil.rmtree(release)
    shutil.move(str(installed.repository_root), release)
    original = _write_attestation(paths)
    monkeypatch.setenv("ADMINME_INSTANCE_ROOT", str(paths.root))
    monkeypatch.setenv("ADMINME_REPOSITORY_ROOT", str(release))
    monkeypatch.setenv("ADMINME_REVIEWED_COMMIT", commit)

    class FixtureSecrets:
        def get(self, reference: str) -> bytes:
            return KEY if reference == "fixture-event-log" else b"fixture-openclaw-key"

    monkeypatch.setattr(
        runtime_service, "secret_store", lambda _paths: FixtureSecrets()
    )
    monkeypatch.setattr(
        runtime_service, "sample_host_topology", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        runtime_service, "model_provider_configured", lambda _paths: False
    )
    clients: list[TestClient] = []

    def start_service(app: Any, **_kwargs: Any) -> None:
        client = TestClient(app, base_url="https://central-fixture.ts.net")
        client.__enter__()
        clients.append(client)

    monkeypatch.setattr(runtime_service.uvicorn, "run", start_service)
    # This is the updater's real startup exclusion: clean service recovery must
    # not try to acquire the parent's nonblocking lock a second time.
    with deployment_operation_lock(paths):
        runtime_service.main()
    client = clients[-1]
    try:
        runtime = client.app.state.runtime
        body = {
            "expected_revision": 0,
            "request_id": REQUEST,
            "member_id": A,
            "device_class": "this_mac",
            "capabilities": ["apple_calendar"],
        }
        cross_member = client.post(
            "/api/member/v1/settings/bridges", headers=B_HEADERS, json=body
        )
        assert cross_member.status_code == 403
        response = client.post(
            "/api/member/v1/settings/bridges", headers=A_HEADERS, json=body
        )
        assert response.status_code == 202, response.text
        first_bytes = transition.index_path.read_bytes()
        retry = client.post(
            "/api/member/v1/settings/bridges", headers=A_HEADERS, json=body
        )
        assert retry.status_code == 202 and retry.json()["idempotent"] is True
        assert transition.index_path.read_bytes() == first_bytes
        handoff = client.post(
            f"/api/member/v1/settings/bridges/{REQUEST}/package", headers=A_HEADERS
        )
        assert handoff.status_code == 200, handoff.text
        package = tmp_path / "fixture.adminmebridge"
        package.write_bytes(handoff.content)
        member_paths = MemberBridgePaths.from_root(tmp_path / "member-mac")

        def submit(bound: dict[str, Any], envelope: dict[str, Any]) -> str:
            response = client.post(
                "/api/bridge/ingest",
                headers={
                    "Tailscale-User-Login": bound["tailscale_identity"],
                    "X-AdministrateMe-Bridge-Enrollment": bound["enrollment_id"],
                },
                json=envelope,
            )
            assert response.status_code == 202, response.text
            return str(response.json()["event_id"])

        def report(
            bound: dict[str, Any], payload: dict[str, Any], signature: str
        ) -> dict[str, Any]:
            response = client.post(
                "/api/bridge/enrollment/progress",
                headers={
                    "Tailscale-User-Login": bound["tailscale_identity"],
                    "X-AdministrateMe-Bridge-Enrollment": bound["enrollment_id"],
                },
                json={**payload, "signature": signature},
            )
            assert response.status_code == 202, response.text
            return response.json()

        installer = MemberBridgeInstaller(
            member_paths, root=release, test_mode=True, submitter=submit
        )
        installer._tailscale_identity = lambda: (A_LOGIN, "readiness-fixture-node")  # type: ignore[method-assign]
        helper = MemberBridgeEnrollmentHelper(
            member_paths,
            repository_root=release,
            package_path=package,
            transfer_code=handoff.headers["X-AdministrateMe-Transfer-Code"],
            test_mode=True,
            reporter=report,
            evidence_submitter=submit,
            installer=installer,
        )
        helper.open_and_verify()
        helper.accept_locally(member_confirmed=True)
        config = helper.pack_config_for(
            "apple_calendar",
            source_coordinate=CALENDAR,
            account_coordinate=ACCOUNT_COORDINATE,
        )
        helper.record_permission("apple_calendar", granted=True, pack_config=config)
        helper.run_capability_test(
            "apple_calendar",
            lambda *_args: {"status": "passed", "content_exposed": False},
        )
        helper.prove_and_start("apple_calendar")
        helper.record_observing("apple_calendar")
        record = runtime.settings.bridge_enrollments[A_LOGIN]
        observation = client.post(
            "/api/bridge/ingest",
            headers={
                **A_HEADERS,
                "X-AdministrateMe-Bridge-Enrollment": record["enrollment_id"],
            },
            json=_calendar_observation(tenant_id=runtime.settings.instance.tenant_id),
        )
        assert observation.status_code == 202, observation.text
        surface = client.get("/api/member/v1/surfaces/settings", headers=A_HEADERS)
        assert surface.status_code == 200, surface.text
        assert (
            surface.json()["control"]["bridge_wizard"]["current"]["state"]
            == "active_observation"
        )
        for action in ("pause", "resume"):
            controlled = client.post(
                f"/api/member/v1/settings/bridges/{REQUEST}/control",
                headers=A_HEADERS,
                json={"command_id": f"fixture-{action}", "action": action},
            )
            assert controlled.status_code == 200, controlled.text
        assert (
            runtime._deployment_readiness(observation_active=True)["dimensions"][
                "attestation_binding"
            ]["state"]
            == "healthy"
        )
        final = json.loads(transition.readiness_path.read_bytes())
        assert final == {**original, "deployment_binding": _binding(paths)}
        assert runtime.settings.deployment_evidence == final
        assert (
            runtime.settings.host_topology["deployment_binding"]
            == final["deployment_binding"]
        )
        assert client.portal.call(_provider_effect_count, runtime) == 0
    finally:
        client.__exit__(None, None, None)

    with deployment_operation_lock(paths):
        runtime_service.main()
    restarted = clients[-1]
    try:
        runtime = restarted.app.state.runtime
        assert (
            runtime._deployment_readiness(observation_active=True)["dimensions"][
                "attestation_binding"
            ]["state"]
            == "healthy"
        )
        assert runtime.settings.bridge_enrollments[A_LOGIN]["enabled_packs"] == [PACK]
        assert runtime.settings.deployment_evidence == final
        # The next guarded operational transition uses the exact same binding
        # prerequisite, without minting new physical proof or changing dates.
        activator = CoSChannelActivator(paths, repository=REPO_ROOT, test_mode=True)
        assert (
            activator._bound_snapshot(commit)
            == capture_deployment_snapshot(paths).public()
        )
        assert restarted.portal.call(_provider_effect_count, runtime) == 0

        previous = copy.deepcopy(runtime.settings.bridge_enrollments)
        with pytest.raises(BridgeEnrollmentError, match="authenticated authority"):
            runtime.bridge_enrollment.pause(request_id=REQUEST, member_id=A)
        assert runtime.settings.bridge_enrollments == previous

        def fail_refresh(_evidence: dict[str, Any]) -> None:
            raise RuntimeError("fixture memory refresh failure")

        runtime.bridge_enrollment.on_index_committed = fail_refresh
        result = restarted.post(
            f"/api/member/v1/settings/bridges/{REQUEST}/control",
            headers=A_HEADERS,
            json={"command_id": "fixture-pause-refresh-failure", "action": "pause"},
        )
        assert result.status_code >= 400
        assert runtime.settings.bridge_enrollments == json.loads(
            transition.index_path.read_bytes()
        )
        assert runtime.settings.bridge_enrollments[A_LOGIN]["status"] == "paused"
        assert (
            runtime._deployment_readiness(observation_active=True)["dimensions"][
                "attestation_binding"
            ]["state"]
            == "blocked"
        )
    finally:
        restarted.__exit__(None, None, None)

    runtime_service.main()
    recovered = clients[-1]
    try:
        runtime = recovered.app.state.runtime
        assert (
            runtime._deployment_readiness(observation_active=True)["dimensions"][
                "attestation_binding"
            ]["state"]
            == "healthy"
        )
        revoked = recovered.post(
            f"/api/member/v1/settings/bridges/{REQUEST}/control",
            headers=A_HEADERS,
            json={"command_id": "fixture-final-revoke", "action": "revoke"},
        )
        assert revoked.status_code == 200, revoked.text
        current = recovered.get(
            "/api/member/v1/surfaces/settings", headers=A_HEADERS
        ).json()["control"]
        next_request = "readiness-expiry-fixture"
        prepared = recovered.post(
            "/api/member/v1/settings/bridges",
            headers=A_HEADERS,
            json={
                **body,
                "expected_revision": current["revision"],
                "request_id": next_request,
            },
        )
        assert prepared.status_code == 202, prepared.text
        record = runtime.settings.bridge_enrollments[A_LOGIN]
        after_expiry = datetime.fromisoformat(record["expires_at"]) + timedelta(
            seconds=1
        )
        runtime.bridge_enrollment.now = lambda: after_expiry
        expired = recovered.post(
            f"/api/member/v1/settings/bridges/{next_request}/package", headers=A_HEADERS
        )
        assert expired.status_code == 410, expired.text
        assert runtime.settings.bridge_enrollments[A_LOGIN]["status"] == "revoked"
        assert runtime.settings.deployment_evidence == {
            **original,
            "deployment_binding": _binding(paths),
        }
        assert recovered.portal.call(_provider_effect_count, runtime) == 0
    finally:
        recovered.__exit__(None, None, None)
