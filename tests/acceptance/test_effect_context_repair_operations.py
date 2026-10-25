"""Real email history -> contained maintenance -> guarded repair or degraded fallback.

Only immutable release/OS/provider/model boundaries are disposable. The old
reader boundary reproduces its exact selective-context counter delta; corrected
source audit, encrypted history, maintenance journal, updater and recovery are real.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta, timezone
import hashlib
from functools import partial
import json
import os
from types import SimpleNamespace

import pytest

from adminme.cos_response_assurance import AUDIT_TYPES, cos_provider_effect_counts
from adminme.source_assurance import audit_source_assurance
from bootstrap import cos_channel_activation as operation
from bootstrap import effect_context_repair as repair
from bootstrap import upgrade
from bootstrap.contracts import atomic_write
from bootstrap.secrets import secret_store
from bootstrap.upgrade import UpgradeError, UpgradeManager
from tests.acceptance import test_requested_email_action as email
from tests.acceptance.test_cos_live_maintenance import maintenance_runtime
from tests.acceptance.test_forwarded_submission_repair_operations import (
    _doctor as _legacy_doctor,
)
from tests.acceptance.test_forwarded_channel_intake import ROOT
from tests.unit.bootstrap.test_cos_channel_activation import _audit


def _doctor(services):
    report = _legacy_doctor(services)
    report["status"] = "healthy" if all(r["ok"] for r in report["checks"]) else "failed"
    return report


def _old_runtime_reader(monkeypatch):
    state = {"enabled": True}

    async def old(log, **kwargs):
        value = await audit_source_assurance(log, **kwargs)
        if state["enabled"]:
            events = [
                e
                async for e in log.read_since(
                    types=sorted(AUDIT_TYPES), through=value["watermark_event_id"]
                )
            ]
            full = cos_provider_effect_counts(events)
            legacy = cos_provider_effect_counts(repair._legacy_view(events))
            delta = {
                k: legacy.get(k, 0) - full.get(k, 0) for k in set(legacy) | set(full)
            }
            if any(delta.values()):
                value["state"] = "degraded"
                value["provider_effects"]["state"] = "degraded"
                value["provider_effects"]["counts"] = {
                    k: v + delta.get(k, 0)
                    for k, v in value["provider_effects"]["counts"].items()
                }
        return value

    # Only predecessor runtime publication uses its defective selective reader.
    # Candidate offline audit and every unchanged retained validator stay real.
    monkeypatch.setattr("adminme.runtime.application.audit_source_assurance", old)
    return state


def _sent_fixture(fixture, monkeypatch):
    controller, activate, _pause, services, settings, gmail, config = fixture
    client = services.client
    headers = {"Tailscale-User-Login": "maintenance.example.test"}
    granted = client.post(
        "/api/member/v1/cos/delegations",
        headers=headers,
        json={
            "request_id": "effect-repair-delegation",
            "context_scope": "member_private",
            "permissions": ["read_context", "prepare_external_actions"],
        },
    )
    assert granted.status_code in {200, 201}, granted.text
    monkeypatch.setattr(
        "tests.acceptance.test_authenticated_conversation_channels.MEMBER_HEADERS",
        headers,
    )
    monkeypatch.setattr(
        email, "AUTH", {"Authorization": f"Bearer {settings.openclaw_shared_secret}"}
    )
    monkeypatch.setattr(email, "SECRET", settings.openclaw_shared_secret)
    source = email.source_for(client, "portal", "effect-repair-source")
    model, _ = email.model_boundary(client)
    try:
        approved = email.confirm(client, source)
        sent = client.post(
            email.EXECUTE,
            headers=email.AUTH,
            json=email.policy_receipt(client, source, approved),
        )
        assert sent.status_code == 200 and sent.json()["status"] == "sent", sent.text
        assert gmail.calls == 1
    finally:
        client.portal.call(model.aclose)
    # Existing supported feature control is retained byte-for-byte by recovery.
    atomic_write(
        config / "request-actions-control.json",
        json.dumps(
            {
                "contract": "request-actions-control-v1",
                "disabled": True,
                "revoked_before": datetime.now(timezone.utc).isoformat(),
            }
        ).encode(),
    )
    reference = secret_store(controller.paths, test_mode=True).put(
        "event-log", settings.encryption_key
    )
    atomic_write(
        config / "secret-references.json",
        json.dumps({"references": {"event_log": reference}}).encode(),
    )
    monkeypatch.setattr(
        operation, "run_release_doctor", lambda *_a, **_kw: _doctor(services)
    )
    request = operation.MaintenancePreparationRequest(
        owner_member_id=activate.owner_member_id,
        operator_principal_id=activate.operator_principal_id,
        owner_authorization_sha256=activate.owner_authorization_sha256,
        effect_context_repair=repair.EffectContextRepairRequest(
            kind="requested_email_v5_context_hydration",
            approval_event_ids=[approved["approval_event_id"]],
        ),
    )
    services.settle()
    return request


def _prepare_upgrade(
    controller, services, settings, plan, plan_path, monkeypatch, old_audit
):
    paths = controller.paths
    candidate = paths.app_dir / "releases" / plan.target_commit
    predecessor = paths.app_dir / "releases" / plan.predecessor_commit
    for release in (candidate, predecessor):
        (release / "bootstrap").mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic_write(
            release / "bootstrap/versions.json",
            (ROOT / "bootstrap/versions.json").read_bytes(),
        )
        # The updater now compares real generated instructions before maintenance.
        # Both synthetic releases retain the same reviewed program sources.
        for program in (ROOT / "bootstrap/openclaw/programs").glob("*.md"):
            atomic_write(
                release / "bootstrap/openclaw/programs" / program.name,
                program.read_bytes(),
            )
    pointer_path = paths.app_dir / "current-release.json"
    atomic_write(
        pointer_path,
        json.dumps(
            {
                "path": str(predecessor),
                "commit": plan.predecessor_commit,
                "previous": None,
            }
        ).encode(),
    )
    config = paths.instance_dir / "config"
    initial = json.loads((config / "deployment-readiness.json").read_bytes())
    pending_id = "3" * 32
    manager = UpgradeManager(
        paths,
        root=ROOT,
        test_mode=True,
        pending_attestation_id=pending_id,
        effect_context_repair_plan=plan_path,
    )
    manager.services = services
    original_start, original_stop = services.start, services.stop

    def start():
        selected = json.loads(pointer_path.read_bytes())["commit"]
        old_audit["enabled"] = selected == plan.predecessor_commit
        settings.host_topology["deployment_binding"]["release_commit"] = selected
        original_start()
        return SimpleNamespace(status="degraded", runtime="running", openclaw="running")

    monkeypatch.setattr(services, "start", start)
    monkeypatch.setattr(services, "stop", lambda **_kwargs: original_stop())
    monkeypatch.setattr(
        services,
        "status",
        lambda: SimpleNamespace(
            status="degraded" if services.client else "stopped",
            runtime="running",
            openclaw="running",
        ),
        raising=False,
    )
    monkeypatch.setattr(
        services, "runtime_is_live", lambda: services.client is not None, raising=False
    )
    monkeypatch.setattr(manager, "_candidate_commit", lambda _path: plan.target_commit)
    monkeypatch.setattr(manager, "_attest_controller_provenance", lambda *_a: None)
    monkeypatch.setattr(
        manager, "_validate_instance_candidate", lambda *_a, **_kw: {"compatible": True}
    )
    monkeypatch.setattr(manager, "_preflight_keychain_access", lambda *_a: None)
    monkeypatch.setattr(manager, "_publish_release", lambda *_a: candidate)
    monkeypatch.setattr(
        manager,
        "_validate_retained_runtime",
        lambda *_a, **_kw: {
            "source_identity_method": "disposable-source-fixture",
            "tracked_source_tree_matches": True,
        },
    )
    binding = copy.deepcopy(initial["deployment_binding"])

    def snapshot(_paths):
        selected = json.loads(pointer_path.read_bytes())["commit"]
        return SimpleNamespace(
            predecessor_commit=selected,
            instance_fingerprint=binding["instance_fingerprint"],
            host_fingerprint=binding["host_fingerprint"],
            configuration_fingerprint=binding["configuration_fingerprint"],
            public=lambda: copy.deepcopy(plan.deployment_snapshot),
        )

    monkeypatch.setattr(upgrade, "capture_deployment_snapshot", snapshot)
    monkeypatch.setattr(
        "bootstrap.candidate_readiness.capture_deployment_snapshot", snapshot
    )
    pending_snapshot = snapshot(paths)
    pending = {
        "attestation_id": pending_id,
        "deployment_binding": {
            "submission_repair": None,
            "provider_binding_transition": None,
        },
    }

    def load_pending(*_args, **kwargs):
        assert kwargs["submission_repair_manifest"] is None
        return pending, "4" * 64, pending_snapshot

    monkeypatch.setattr(
        upgrade, "load_pending_attestation_for_activation", load_pending
    )

    def promoted(*_args, **_kwargs):
        evidence = copy.deepcopy(initial)
        evidence["deployment_binding"]["release_commit"] = plan.target_commit
        return evidence

    monkeypatch.setattr(upgrade, "build_active_attestation_from_pending", promoted)
    monkeypatch.setattr(
        manager,
        "_prove_predecessor_rollback_eligibility",
        lambda **_kwargs: {
            "contract": "administrateme-predecessor-rollback-eligibility-v1",
            "content_exposed": False,
        },
    )

    class Backup:
        def __init__(self, *_args, **_kwargs):
            pass

        def create(self, target):
            assert not target.exists()
            atomic_write(target, b"authenticated-encrypted-fixture-boundary")
            return {"path": str(target), "encrypted": True, "manifest_sha256": "6" * 64}

        def rehearse(self, backup, *, candidate, receipt):
            atomic_write(receipt, b'{"fixture":"authenticated-restore"}')
            return {
                "status": "healthy",
                "candidate": {"commit": plan.target_commit},
                "artifact": {"sha256": hashlib.sha256(backup.read_bytes()).hexdigest()},
                "deployment_binding": pending_snapshot.public(),
                "receipt_sha256": "7" * 64,
                "tested_at": "2026-10-20T12:00:00Z",
            }

    monkeypatch.setattr(upgrade, "BackupManager", Backup)
    activity = paths.app_dir / "fixture-preparation-lock"
    monkeypatch.setattr(
        manager,
        "_consume_preparation_state",
        lambda *_a: os.open(activity, os.O_CREAT | os.O_RDWR, 0o600),
    )
    monkeypatch.setattr(manager, "_install_reviewed_openclaw", lambda *_a: None)
    monkeypatch.setattr(
        upgrade, "reconcile_administrateme_plugin", lambda *_a, **_kw: None
    )
    monkeypatch.setattr(upgrade, "configure_required_llm_tool", lambda *_a, **_kw: None)
    monkeypatch.setattr(upgrade, "install_launch_agents", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        manager, "_run_release_doctor", lambda *_a, **_kw: _doctor(services)
    )
    return manager, candidate, pointer_path


@pytest.mark.parametrize(
    "outcome",
    [
        "healthy_candidate",
        "failed_candidate",
        "interrupted_containment",
        "failed_original_recovery",
    ],
)
def test_real_email_containment_upgrade_and_schema_aware_fallback(
    tmp_path, monkeypatch, outcome
):
    old = _old_runtime_reader(monkeypatch)
    with maintenance_runtime(tmp_path, monkeypatch, allow_degraded=True) as fixture:
        controller, _activate, _pause, services, settings, gmail, config = fixture
        request = _sent_fixture(fixture, monkeypatch)
        before = services.settle()
        repair.validate_diagnostics(before, observation=False)
        policy_before = asyncio.run(_audit(controller))[0]
        controls = {
            name: (config / name).read_bytes()
            for name in ("adapters.json", "request-actions-control.json")
        }
        # Ordinary maintenance continues to refuse degraded readiness.
        ordinary = request.model_copy(update={"effect_context_repair": None})
        with pytest.raises(operation.CoSActivationError, match="healthy live"):
            controller.prepare_maintenance(ordinary)
        plan = controller.prepare_maintenance(request)
        plan_path = tmp_path / "private-effect-plan.json"
        atomic_write(plan_path, plan.model_dump_json().encode())
        # A new authenticated read/instruction fact is harmless; the final pause
        # uses the current quiesced watermark, not a forever-stale preparation ID.
        email.source_for(
            services.client,
            "portal",
            "read-only-after-plan",
            text="Show my current work.",
        )
        services.settle()
        if outcome == "interrupted_containment":
            validator = repair.validate_diagnostics

            def defective_status(value, *, observation, doctor=None):
                if doctor is not None and doctor["status"] == "failed":
                    raise repair.EffectContextRepairError(
                        "repair Doctor has unrelated failures"
                    )
                return validator(value, observation=observation, doctor=doctor)

            with monkeypatch.context() as patch:
                patch.setattr(repair, "validate_diagnostics", defective_status)
                with pytest.raises(repair.EffectContextRepairError, match="Doctor"):
                    controller.pause_maintenance(plan)
            interrupted = json.loads(controller.journal_path.read_bytes())
            assert interrupted["phase"] == "verifying"
            receipts = list(
                (controller.paths.app_dir / "effect-context-repair").glob(
                    "doctor-*.json"
                )
            )
            assert (
                len(receipts) == 1
                and json.loads(receipts[0].read_bytes())["status"] == "failed"
            )
            assert "doctor" not in interrupted
            controls_at_failure = {
                p: (config / p).read_bytes()
                for p in (
                    "runtime.yaml",
                    "observation.json",
                    "deployment-readiness.json",
                )
            }
            events_at_failure = asyncio.run(_audit(controller))[1]
            monkeypatch.setattr(
                controller,
                "_observation_selection",
                lambda: (plan.predecessor_commit, ROOT, "c" * 40),
            )
            with pytest.raises(operation.CoSActivationError, match="another source"):
                controller.recover()
            lineage = []

            def ancestor(repository, commit, *, retained_by_commit):
                lineage.append((commit, retained_by_commit))
                assert (
                    repository == ROOT
                    and commit == plan.target_commit
                    and retained_by_commit == "c" * 40
                )

            monkeypatch.setattr(operation, "load_git_tree", ancestor)
            # No journal edits or extra side effects are permitted on refusal.
            for field, value in (
                ("phase", "prepared"),
                ("controller_commit", "d" * 40),
                ("owner_member_id", "other-fixture-member"),
                ("maintenance_plan_sha256", "0" * 64),
                ("operation", "activate"),
            ):
                changed = copy.deepcopy(interrupted)
                changed[field] = value
                atomic_write(controller.journal_path, json.dumps(changed).encode())
                with pytest.raises(
                    operation.CoSActivationError, match="exact unexpired"
                ):
                    controller.recover_effect_context_controller(plan)
            atomic_write(controller.journal_path, json.dumps(interrupted).encode())
            expired = plan.model_copy(
                update={
                    "expires_at": (
                        datetime.now(timezone.utc) - timedelta(seconds=1)
                    ).isoformat()
                }
            )
            with pytest.raises(operation.CoSActivationError, match="exact unexpired"):
                controller.recover_effect_context_controller(expired)
            with monkeypatch.context() as patch:
                from bootstrap.source_identity import SourceIdentityError

                def unrelated(*_args, **_kwargs):
                    raise SourceIdentityError("not retained by reviewed candidate")

                patch.setattr(operation, "load_git_tree", unrelated)
                with pytest.raises(SourceIdentityError, match="not retained"):
                    controller.recover_effect_context_controller(plan)
            for filename, field, value, error in (
                (
                    "adapters.json",
                    "unexpected_config",
                    True,
                    "authority or configuration",
                ),
                ("observation.json", "active", False, "observation boundary"),
                ("request-actions-control.json", "disabled", False, "revocation"),
            ):
                path = config / filename
                original_bytes = path.read_bytes()
                changed = json.loads(original_bytes)
                changed[field] = value
                atomic_write(path, json.dumps(changed).encode())
                with pytest.raises(
                    (operation.CoSActivationError, repair.EffectContextRepairError),
                    match=error,
                ):
                    controller.recover_effect_context_controller(plan)
                atomic_write(path, original_bytes)
                assert json.loads(controller.journal_path.read_bytes()) == interrupted
            lineage.clear()
            contained = controller.recover_effect_context_controller(plan)
            assert lineage == [(plan.target_commit, "c" * 40)]
            history = json.loads(
                (controller.directory / "history" / f"{plan.plan_id}.json").read_bytes()
            )
            assert history["controller_commit"] == plan.target_commit
            assert (
                history["effect_context_proof"] == interrupted["effect_context_proof"]
            )
            assert history["maintenance_plan_sha256"] == plan.digest()
            assert (
                history["effect_context_recovery_controller"]["controller_commit"]
                == "c" * 40
            )
            assert all(
                (config / p).read_bytes() == value
                for p, value in controls_at_failure.items()
            )
            old_ids = [
                e["event_id"]
                for e in events_at_failure
                if e["source_adapter"] == "cos-channel-operation"
            ]
            new_ids = [
                e["event_id"]
                for e in asyncio.run(_audit(controller))[1]
                if e["source_adapter"] == "cos-channel-operation"
            ]
            assert new_ids == old_ids
            assert not controller.journal_path.exists()
        else:
            contained = controller.pause_maintenance(plan)
        assert contained["status"] == "contained_degraded"
        diagnostic = services.settle()
        assert diagnostic["observation_active"] is True and diagnostic["ready"] is False
        assert asyncio.run(_audit(controller))[0] == policy_before
        assert all(
            (config / name).read_bytes() == payload
            for name, payload in controls.items()
        )
        manager, _candidate, pointer = _prepare_upgrade(
            controller, services, settings, plan, plan_path, monkeypatch, old
        )
        loaded = repair.load_plan(
            controller.paths,
            ROOT,
            plan_path,
            candidate=plan.target_commit,
            predecessor=plan.predecessor_commit,
            test_mode=True,
        )
        assert loaded == plan
        # The public HTR export must not exempt an extra integrity fault merely
        # because this private repair plan is present. It emits no attestation.
        from bootstrap import htr10_export

        monkeypatch.setattr(htr10_export, "ServiceManager", lambda *_a, **_kw: services)
        monkeypatch.setattr(
            htr10_export,
            "capture_deployment_snapshot",
            upgrade.capture_deployment_snapshot,
        )
        monkeypatch.setattr(
            "bootstrap.source_identity.reviewed_candidate_commit",
            lambda _path: plan.target_commit,
        )
        bad = copy.deepcopy(services.settle())
        bad["capabilities"]["policy:source-assurance"]["details"]["provider_effects"][
            "counts"
        ]["duplicate_terminal"] = 1
        historical = controller.paths.backup_dir / "rejected-historical.json"
        exported = controller.paths.backup_dir / "rejected-source.json"
        with monkeypatch.context() as patch:
            patch.setattr(services, "runtime_diagnostics", lambda: bad)
            with pytest.raises(
                repair.EffectContextRepairError, match="reported failure"
            ):
                asyncio.run(
                    htr10_export.export_htr10_audits(
                        controller.paths,
                        repository_root=ROOT,
                        historical_output=historical,
                        source_output=exported,
                        ownership_reviewed=True,
                        effect_context_repair_plan=plan_path,
                        test_mode=True,
                    )
                )
        assert not historical.exists() and not exported.exists()
        services.settle_on_start = False
        if outcome in {"failed_candidate", "failed_original_recovery"}:
            original = manager._post_switch_doctor

            def fail_candidate(release, **kwargs):
                if release.name == plan.target_commit:
                    raise UpgradeError("synthetic candidate health regression")
                return original(release, **kwargs)

            monkeypatch.setattr(manager, "_post_switch_doctor", fail_candidate)
            if outcome == "failed_original_recovery":
                # The original immutable candidate can fail its automatic fallback
                # at the same old Doctor status check. Explicit later-controller
                # rollback completes that journal without retargeting the plan.
                def defective_wait(*_args, **_kwargs):
                    raise repair.EffectContextRepairError(
                        "repair Doctor has unrelated failures"
                    )

                with monkeypatch.context() as patch:
                    patch.setattr(upgrade, "wait_effect_context", defective_wait)
                    with pytest.raises(UpgradeError):
                        manager.activate(ROOT)
                assert manager.activation_journal.path.exists()
                recovered = manager.rollback(recovery_repository=ROOT)
                assert recovered["status"] == "recovered"
                assert recovered["restored_readiness"] == "contained_degraded"
            else:
                with pytest.raises(UpgradeError, match="regression"):
                    manager.activate(ROOT)
            assert json.loads(pointer.read_bytes())["commit"] == plan.predecessor_commit
            assert services.settle()["ready"] is False
            histories = [
                json.loads(p.read_bytes())
                for p in manager.activation_journal.history.glob("*.json")
            ]
            assert histories[-1]["restored_readiness"] == "contained_degraded"
        else:
            manager.activate(ROOT)
            assert json.loads(pointer.read_bytes())["commit"] == plan.target_commit
            assert services.settle()["ready"] is True
        assert services.runtime_diagnostics()["observation_active"] is True
        assert gmail.calls == 1
        assert all(
            (config / name).read_bytes() == payload
            for name, payload in controls.items()
        )
        assert asyncio.run(_audit(controller))[0] == policy_before
        assert not manager.activation_journal.path.exists()


def test_repair_audit_checks_real_chain_targets_windows_and_original_prefix(
    tmp_path, monkeypatch
):
    _old_runtime_reader(monkeypatch)
    with maintenance_runtime(tmp_path, monkeypatch, allow_degraded=True) as fixture:
        controller, _activate, _pause, services, _settings, gmail, _config = fixture
        request = _sent_fixture(fixture, monkeypatch)
        client = services.client
        runtime = client.app.state.runtime
        report = services.settle()
        source = report["capabilities"]["policy:source-assurance"]["details"]
        through = source["watermark_event_id"]
        actual = repair._windows(controller.paths)
        # Several retained closed zero-contact windows exercise the production
        # interval validators while the whole source scan remains exactly once.
        start = datetime.now(timezone.utc) - timedelta(days=20)
        prior = [
            {
                "started_at": (start + timedelta(hours=i)).isoformat(),
                "ended_at": (start + timedelta(hours=i, minutes=10)).isoformat(),
            }
            for i in range(8)
        ]
        windows = {
            "prior_provider_effect_windows": prior
            + actual["prior_provider_effect_windows"],
            "provider_effect_window": actual["provider_effect_window"],
        }
        scans = []

        async def counted(*args, **kwargs):
            scans.append(kwargs["through_event_id"])
            return await audit_source_assurance(*args, **kwargs)

        monkeypatch.setattr("adminme.source_assurance.audit_source_assurance", counted)

        async def audit(**overrides):
            values = dict(
                repository=ROOT,
                request=request.effect_context_repair,
                through=through,
                capabilities=report["capabilities"],
                windows=windows,
                tenant_id=runtime.settings.instance.tenant_id,
                reported=source,
            )
            values.update(overrides)
            return await repair.audit_context(runtime.event_log, **values)

        good = client.portal.call(audit)
        assert good["provider_called_true"] == 1
        assert scans == [through]
        wrong = request.effect_context_repair.model_copy(
            update={"approval_event_ids": ["not-the-executed-approval"]}
        )
        with pytest.raises(repair.EffectContextRepairError, match="target approvals"):
            client.portal.call(partial(audit, request=wrong))
        with pytest.raises(repair.EffectContextRepairError, match="tenant"):
            client.portal.call(partial(audit, tenant_id="another-fixture-tenant"))
        contact_window = copy.deepcopy(windows)
        contact_window["prior_provider_effect_windows"].append(
            {
                "started_at": (
                    datetime.now(timezone.utc) - timedelta(hours=1)
                ).isoformat(),
                "ended_at": (
                    datetime.now(timezone.utc) + timedelta(minutes=1)
                ).isoformat(),
            }
        )
        with pytest.raises(repair.EffectContextRepairError, match="observation-window"):
            client.portal.call(partial(audit, windows=contact_window))
        plan = controller.prepare_maintenance(request)
        invalid = plan.effect_context_proof.model_copy(
            update={"chain_sha256": "0" * 64}
        )
        with pytest.raises(
            repair.EffectContextRepairError, match="retained repair history"
        ):
            repair.verify_proof(
                controller.paths,
                ROOT,
                invalid,
                test_mode=True,
                diagnostics=services.settle(),
                observation=False,
            )
        # Corrupt only the isolated reader return, never persisted encrypted truth.
        original = runtime.event_log.read_since
        for defect in ("missing", "owner", "fingerprint", "duplicate", "unknown"):

            async def changed(**kwargs):
                async for event in original(**kwargs):
                    row = copy.deepcopy(event)
                    if row["type"] == "cos.response.result":
                        if defect == "missing":
                            continue
                        if defect == "owner":
                            row["owner_scope"] = "private:other-fixture-member"
                        if defect == "fingerprint":
                            row["payload"]["content_fingerprint"] = "0" * 64
                        if defect == "unknown":
                            row["payload"]["provider_effect_count"] = None
                        if defect == "duplicate":
                            yield row
                    yield row

            with monkeypatch.context() as patch:
                patch.setattr(runtime.event_log, "read_since", changed)
                with pytest.raises(
                    repair.EffectContextRepairError, match="retained effect chain"
                ):
                    client.portal.call(audit)
        assert gmail.calls == 1
