"""Read-only evidence collection remains possible while physical attestations expire."""

from __future__ import annotations

import asyncio
import json
import stat
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from adminme.runtime.application import create_application
from bootstrap.candidate_readiness import build_htr10_review
from bootstrap.contracts import DeploymentPaths, atomic_write
from bootstrap.htr10_export import export_htr10_audits
from bootstrap.secrets import secret_store
from bootstrap.services import ServiceManager
from tests.integration.test_runtime_application import (
    REPO_ROOT,
    _report_strict_openclaw,
    _strict_settings,
)


def test_real_runtime_exports_fresh_audits_without_attesting_expired_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    paths.create()
    settings = _strict_settings(paths.root)
    paths = replace(paths, instance_dir=settings.instance.instance_dir)
    original_window = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    stale = (datetime.now(timezone.utc) - timedelta(days=32)).isoformat()
    evidence = settings.deployment_evidence
    evidence["provider_effect_window"]["started_at"] = original_window
    for key in ("identity_acceptance", "recovery", "backup_rollback"):
        evidence[key]["tested_at"] = stale
    evidence["deployment_binding"]["configuration_fingerprint"] = "f" * 64
    settings.host_topology["deployment_binding"] = {
        **settings.host_topology["deployment_binding"],
        "configuration_fingerprint": "d" * 64,
    }
    event_reference = secret_store(paths, test_mode=True).put(
        "event-log", settings.encryption_key
    )
    config = paths.instance_dir / "config"
    atomic_write(
        config / "instance.yaml",
        f"tenant_id: {settings.instance.tenant_id}\ntimezone: Etc/UTC\n".encode(),
    )
    mapping = {
        "members": [
            {
                "member_id": "member-a",
                "role": "principal",
                "tailscale_identity": "member-a.ts.net",
            }
        ]
    }
    for name, value in (
        ("adapters.json", settings.adapter_config),
        ("member-mapping.json", mapping),
        ("deployment-readiness.json", evidence),
        ("secret-references.json", {"references": {"event_log": event_reference}}),
    ):
        atomic_write(config / name, json.dumps(value).encode())
    protected = {
        path: (path.read_bytes(), path.stat().st_ino)
        for path in config.iterdir()
        if path.is_file()
    }
    application = create_application(settings)
    with TestClient(application) as client:
        _report_strict_openclaw(client)
        runtime = application.state.runtime
        assert client.portal is not None
        # Represent the predecessor's current audit schema: its legacy window
        # has no explicit origin field, but all operational/effect proof is live.
        for _ in range(100):
            baseline = client.get("/api/system/ready").json()["capabilities"][
                "policy:source-assurance"
            ]["details"]["freshness"]
            if (
                baseline["revision_current"] is True
                and baseline["refresh_in_progress"] is False
            ):
                break
            client.portal.call(asyncio.sleep, 0.01)
        assert baseline["revision_current"] is True
        assert baseline["refresh_in_progress"] is False
        runtime._source_assurance_cache["provider_effects"]["observation_window"].pop(
            "started_at"
        )
        diagnostics = client.get("/api/system/ready").json()
        assert set(diagnostics["deployment_readiness"]["blocking_dimensions"]) == {
            "attestation_binding",
            "service_account_recovery",
            "identity_acceptance",
            "backup_rollback",
            "observation_effects",
        }
        assert diagnostics["service_ready"] is True
        count_before = client.portal.call(runtime.event_log.count)
        monkeypatch.setattr(
            ServiceManager, "runtime_diagnostics", lambda _self: diagnostics
        )
        historical = paths.backup_dir / "historical.json"
        source = paths.backup_dir / "source.json"
        result = asyncio.run(
            export_htr10_audits(
                paths,
                repository_root=REPO_ROOT,
                historical_output=historical,
                source_output=source,
                ownership_reviewed=True,
                test_mode=True,
            )
        )
        assert result["watermark_bound"] is True
        assert result["source_state"] == result["historical_state"] == "healthy"
        assert result["activation_authorized"] is False
        assert result["physical_checks_attested"] is False
        exported_source = json.loads(source.read_bytes())
        assert (
            exported_source["provider_effects"]["observation_window"]["started_at"]
            == original_window
        )
        review = build_htr10_review(
            paths,
            historical_attribution=historical,
            source_assurance=source,
            observed_at=datetime.now(timezone.utc).isoformat(),
        )
        assert review["provider_effect_window_started_at"] == original_window
        assert client.portal.call(runtime.event_log.count) == count_before
        assert client.get("/api/system/ready").json()["ready"] is False
    for path, before in protected.items():
        assert (path.read_bytes(), path.stat().st_ino) == before
    assert stat.S_IMODE(historical.stat().st_mode) == 0o600
    assert stat.S_IMODE(source.stat().st_mode) == 0o600
    assert not (paths.app_dir / "readiness" / "candidate-pending").exists()
