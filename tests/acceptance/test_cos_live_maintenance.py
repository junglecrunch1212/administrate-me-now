"""Hosted-only live/observation transitions through production composition.

The OS/source/backup boundaries use existing disposable operation fixtures.
Readiness, provider-effect auditing, reply governance, encrypted truth, restart,
CLI dispatch and observation control are real. The guarded updater's separate
process suite covers the intervening release switch and preserved windows.
"""

from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

from fastapi.testclient import TestClient
import pytest

from adminme.adapters.loader import load_builtin_adapters
from adminme.cos_channel_operation import require_cos_response_activation
from adminme.lib.instance_config import load_instance_config
from adminme.openclaw_approvals import sign_approval_receipt
from adminme.openclaw_topology import (
    binding_fingerprints,
    member_agent_id,
    member_workspace_path,
)
from adminme.products.openclaw.constants import OPENCLAW_COMMIT, OPENCLAW_VERSION
from adminme.runtime.application import create_application
from bootstrap import cos_channel_activation as operation
from bootstrap.contracts import atomic_write
from tests.acceptance.test_cos_channel_acquisition import _ReadOnlyGmail
from tests.acceptance.test_forwarded_channel_intake import EMAIL_ACCOUNT, MEMBER_A, ROOT
from tests.integration.test_runtime_application import _strict_settings
from tests.unit.bootstrap.test_cos_channel_activation import _audit, _live_controller


def test_live_observation_cli_restart_and_fresh_reactivation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    controller, activate, pause = _live_controller(tmp_path, monkeypatch)
    seed = tmp_path / "strict-host"
    seed.mkdir()
    settings = _strict_settings(seed)
    roles = {MEMBER_A: "principal"}
    actors = {"discord|fixture-account|fixture-actor": MEMBER_A}
    members = frozenset({MEMBER_A})
    root = controller.paths.instance_dir / "openclaw"
    workspace = member_workspace_path(root, MEMBER_A)
    workspace.mkdir(parents=True)
    gateway = root / "runtime/openclaw.json"
    atomic_write(
        gateway,
        json.dumps(
            {
                "agents": {
                    "list": [
                        {"id": member_agent_id(MEMBER_A), "workspace": str(workspace)}
                    ]
                },
                "bindings": [
                    {
                        "agentId": member_agent_id(MEMBER_A),
                        "match": {
                            "channel": "discord",
                            "accountId": "fixture-account",
                            "peer": {"kind": "direct", "id": "fixture-actor"},
                        },
                    }
                ],
                "session": {"dmScope": "per-account-channel-peer"},
                "plugins": {
                    "entries": {
                        "administrateme": {
                            "config": {
                                "memberAgentIds": [member_agent_id(MEMBER_A)],
                                "bindingFingerprints": list(
                                    binding_fingerprints(actors, members)
                                ),
                            }
                        }
                    }
                },
            }
        ).encode(),
    )
    config = controller.config_dir
    evidence = copy.deepcopy(settings.deployment_evidence)
    evidence["identity_acceptance"]["member_ids"] = [MEMBER_A]
    evidence["provider_effect_window"] = json.loads(
        (config / "deployment-readiness.json").read_bytes()
    )["provider_effect_window"]
    # The selected bounded dataset is an accepted predecessor of an ordinary
    # journaled deployment, with synthetic authenticated backup/restore proof.
    evidence["activation_evidence"] = {
        "contract": "deployment-activation-evidence-v1",
        "crash_recoverable_replacement": True,
        "candidate_live_doctor": True,
        "candidate_pending_attestation_sha256": "1" * 64,
        "fresh_backup_artifact_sha256": "2" * 64,
        "fresh_rehearsal_receipt_sha256": "3" * 64,
        "predecessor_rollback_eligibility_sha256": "4" * 64,
    }
    atomic_write(config / "deployment-readiness.json", json.dumps(evidence).encode())
    settings = replace(
        settings,
        instance=load_instance_config(controller.paths.instance_dir),
        encryption_key=b"o" * 32,
        adapter_config=json.loads((config / "adapters.json").read_bytes()),
        deployment_evidence=evidence,
        member_identities={"maintenance.example.test": MEMBER_A},
        member_roles=roles,
        fixture_member_id=MEMBER_A,
        member_chat_enabled_members=members,
        openclaw_actor_identities=actors,
        openclaw_runtime_config_path=gateway,
        openclaw_model_configured=True,
        deterministic_member_chat_fixture=True,
    )

    class Gmail(_ReadOnlyGmail):
        calls = 0

        def messages(self):
            return self

        def send(self, **_kwargs):
            self.mode = "send"
            self.calls += 1
            return self

        def execute(self):
            if self.mode == "send":
                return {
                    "id": "maintenance-synthetic-send",
                    "threadId": "maintenance-thread",
                }
            return super().execute()

    gmail = Gmail()
    adapters = json.loads((config / "adapters.json").read_bytes())
    adapter = load_builtin_adapters(adapters, repository_root=ROOT)[0]
    module = sys.modules[type(adapter).__module__]
    monkeypatch.setattr(module, "_default_credentials_resolver", lambda _ref: {})
    monkeypatch.setattr(module, "_default_service_factory", lambda *_args: gmail)
    monkeypatch.setattr(type(adapter), "POLL_INTERVAL_S", 3600)

    class Services:
        client = None
        starts = 0
        stops = 0

        def start(self):
            current = replace(
                settings,
                adapter_config=json.loads((config / "adapters.json").read_bytes()),
                deployment_evidence=json.loads(
                    (config / "deployment-readiness.json").read_bytes()
                ),
            )
            self.client = TestClient(create_application(current))
            self.client.__enter__()
            self.starts += 1
            health = self.client.post(
                "/api/openclaw/v1/health/report",
                headers={"Authorization": f"Bearer {settings.openclaw_shared_secret}"},
                json={
                    "state": "healthy",
                    "reason": "isolated maintenance fixture",
                    "openclaw_version": OPENCLAW_VERSION,
                    "openclaw_commit": OPENCLAW_COMMIT,
                    "dm_scope": "per_account_channel_peer",
                    "member_agent_ids": [member_agent_id(MEMBER_A)],
                    "binding_fingerprints": list(binding_fingerprints(actors, members)),
                    "unique_agents": True,
                    "unique_accounts": True,
                },
            )
            assert health.status_code == 200, health.text
            self.settle()

        def stop(self):
            if self.client is not None:
                self.client.__exit__(None, None, None)
                self.client = None
                self.stops += 1

        def runtime_diagnostics(self):
            assert self.client is not None
            return self.client.get("/api/system/ready").json()

        def settle(self):
            for _ in range(500):
                diagnostics = self.runtime_diagnostics()
                source = diagnostics["capabilities"]["policy:source-assurance"][
                    "details"
                ]
                if (
                    diagnostics["ready"]
                    and source["freshness"]["revision_current"]
                    and not source["freshness"]["refresh_in_progress"]
                ):
                    return diagnostics
                assert self.client is not None
                self.client.portal.call(asyncio.sleep, 0.01)
            raise AssertionError(diagnostics)

    services = Services()
    controller.services = services  # type: ignore[assignment]
    monkeypatch.setattr(
        controller,
        "_settle",
        lambda _release: {"status": "healthy", "runtime": services.settle()},
    )
    services.start()
    try:
        client = services.client
        assert client is not None
        headers = {"Tailscale-User-Login": "maintenance.example.test"}
        auth = {"Authorization": f"Bearer {settings.openclaw_shared_secret}"}
        delegated = client.post(
            "/api/member/v1/cos/delegations",
            headers=headers,
            json={
                "request_id": "maintenance-delegation",
                "context_scope": "member_private",
                "permissions": ["read_context", "prepare_external_actions"],
            },
        )
        assert delegated.status_code in {200, 201}, delegated.text
        approved = client.post(
            "/api/member/v1/cos/responses/approvals",
            headers=headers,
            json={
                "request_id": "maintenance-response",
                "channel": "email",
                "service_channel_id": "channel:chief-of-staff:gmail",
                "provider_account_id": EMAIL_ACCOUNT,
                "recipient": "member-a@example.invalid",
                "subject": "Synthetic maintenance fixture",
                "body": "Selected obligation is ready for review.",
            },
        )
        assert approved.status_code == 201, approved.text
        params = {
            "request_id": "maintenance-execute",
            "approval_event_id": approved.json()["approval_event_id"],
        }
        issued = int(time.time() * 1000)
        receipt = {
            "receipt_id": "maintenance-approval",
            "tool_name": "administrateme_cos_response",
            "request_fingerprint": hashlib.sha256(
                json.dumps(params, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "issued_at_ms": issued,
            "expires_at_ms": issued + 120000,
            "openclaw_version": OPENCLAW_VERSION,
            "openclaw_commit": OPENCLAW_COMMIT,
        }
        resolved = client.post(
            "/api/openclaw/v1/approvals/resolve",
            headers=auth,
            json={
                **receipt,
                "decision": "allow-once",
                "signature": sign_approval_receipt(
                    settings.openclaw_shared_secret, **receipt
                ),
            },
        )
        assert resolved.status_code == 200, resolved.text
        command = {
            **params,
            "openclaw_approval_receipt_id": receipt["receipt_id"],
            "provenance": {
                "actor_id": "fixture-actor",
                "channel_id": "discord",
                "account_id": "fixture-account",
                "correlation_id": "maintenance-response",
                "call_kind": "tool",
                "openclaw_version": OPENCLAW_VERSION,
                "openclaw_commit": OPENCLAW_COMMIT,
            },
        }
        sent = client.post(
            "/api/openclaw/v1/tools/cos-response", headers=auth, json=command
        )
        assert sent.status_code == 200, sent.text
        assert sent.json()["status"] == "sent", sent.text
        assert gmail.calls == 1
        assert (
            services.settle()["deployment_readiness"]["dimensions"][
                "observation_effects"
            ]["evidence"]["provider_called_true"]
            == 1
        )
        policy_before = asyncio.run(_audit(controller))[0]
        original_window = json.loads(
            (config / "deployment-readiness.json").read_bytes()
        )["provider_effect_window"]
        original_enrollment = (config / "adapters.json").read_bytes()
        # Invoke the real CLI parser and dispatch with only its host-controller
        # construction substituted by this isolated production-composition host.
        monkeypatch.setattr(
            operation, "CoSChannelActivator", lambda *_args, **_kwargs: controller
        )
        request_path = tmp_path / "private-observation-request.json"
        atomic_write(request_path, pause.model_dump_json().encode())
        actual_diagnostics = services.runtime_diagnostics
        first = True

        def stale_initial_diagnostic():
            nonlocal first
            value = actual_diagnostics()
            if first:
                first = False
                value["deployment_readiness"]["dimensions"]["observation_effects"][
                    "evidence"
                ]["provider_called_true"] = 0
            return value

        monkeypatch.setattr(services, "runtime_diagnostics", stale_initial_diagnostic)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "cos-channel-activation",
                "--instance-root",
                str(controller.paths.root),
                "--request",
                str(request_path),
                "--enter-observation",
            ],
        )
        operation.main()
        assert json.loads(capsys.readouterr().out)["status"] == "observation_entered"
        observed = services.settle()
        assert observed["observation_active"] is True
        current = json.loads((config / "deployment-readiness.json").read_bytes())
        assert current["prior_provider_effect_windows"] == [original_window]
        assert (config / "adapters.json").read_bytes() == original_enrollment
        assert asyncio.run(_audit(controller))[0] == policy_before
        assert services.client is not None
        runtime = services.client.app.state.runtime

        async def authority():
            return await require_cos_response_activation(
                runtime,
                channel="email",
                provider_account_id=EMAIL_ACCOUNT,
                requested_at="2099-01-01T00:00:00+00:00",
            )

        assert services.client.portal.call(authority)["authorized"] is False
        request_path = tmp_path / "private-reactivation-request.json"
        atomic_write(
            request_path,
            activate.model_copy(update={"recipient_entries": None})
            .model_dump_json()
            .encode(),
        )
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "cos-channel-activation",
                "--instance-root",
                str(controller.paths.root),
                "--request",
                str(request_path),
                "--enable-live",
            ],
        )
        operation.main()
        assert json.loads(capsys.readouterr().out)["observation_active"] is False
        assert services.settle()["ready"] is True
        assert gmail.calls == 1
        assert asyncio.run(_audit(controller))[0] == policy_before
        assert services.client is not None
        replay = services.client.post(
            "/api/openclaw/v1/tools/cos-response", headers=auth, json=command
        )
        assert replay.status_code in {200, 403, 409}
        assert gmail.calls == 1
    finally:
        services.stop()


def test_public_cli_rejects_combined_enable_and_observe_without_mutation(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "bootstrap.cos_channel_activation",
            "--instance-root",
            str(tmp_path / "untouched"),
            "--request",
            str(tmp_path / "absent.json"),
            "--enter-observation",
            "--enable-live",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
    assert "separate private-request operation" in result.stderr
    assert not (tmp_path / "untouched").exists()
