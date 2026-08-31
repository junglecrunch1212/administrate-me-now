"""Secret-redacted human and machine diagnostics for the deployed instance."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import plistlib
import shutil
import sqlite3
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import sqlcipher3
import yaml
from fastapi.testclient import TestClient

from adminme.demo import FIXTURE_MEMBER_ID
from adminme.openclaw_approvals import sign_approval_receipt
from adminme.products.openclaw.constants import OPENCLAW_COMMIT, OPENCLAW_VERSION
from adminme.runtime.application import RuntimeSettings, create_application
from bootstrap.contracts import (
    ADAPTER_CATEGORIES,
    OPENCLAW_LABEL,
    PROJECTION_NAMES,
    RUNTIME_LABEL,
    DeploymentPaths,
    atomic_write,
    load_versions,
    repository_root,
    sha256_file,
)
from bootstrap.openclaw_activation import model_provider_configured
from bootstrap.openclaw_cron import OpenClawCronReconciler
from bootstrap.openclaw_install import reviewed_source_status
from bootstrap.openclaw_tools import (
    administrateme_plugin_status,
    probe_required_llm_tool,
    required_llm_tool_status,
)
from bootstrap.secrets import secret_store
from bootstrap.services import ServiceManager, source_assurance_terminal_evidence


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    ok: bool
    detail: str
    remediation: str | None = None
    evidence: dict[str, Any] | None = None


def _command_output(command: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode:
        return None
    return (completed.stdout or completed.stderr).strip()


def _version_parts(value: str) -> tuple[int, int, int]:
    cleaned = value.strip().lstrip("v").split()[0]
    pieces = cleaned.split(".")
    try:
        numbers = [int(piece) for piece in pieces[:3]]
    except ValueError:
        return (-1, -1, -1)
    return tuple((numbers + [0, 0, 0])[:3])  # type: ignore[return-value]


def _event_log_check(path: Path, key: bytes) -> tuple[bool, dict[str, Any]]:
    if not path.is_file():
        return False, {"reason": "event log is missing"}
    try:
        connection = sqlcipher3.connect(str(path))
        connection.row_factory = sqlcipher3.Row
        connection.execute(f"PRAGMA key = \"x'{key.hex()}'\"")
        cipher_version = connection.execute("PRAGMA cipher_version").fetchone()[0]
        event_count = connection.execute("SELECT count(*) FROM events").fetchone()[0]
        migrations = connection.execute(
            "SELECT version FROM _schema_version ORDER BY version"
        ).fetchall()
        connection.close()
    except Exception as exc:
        return False, {"reason": type(exc).__name__}
    raw = path.read_bytes()
    plaintext_probe = b"Could you reply today with the fixture household update?"
    return plaintext_probe not in raw, {
        "cipher_version": str(cipher_version),
        "event_count": int(event_count),
        "migrations": [int(row[0]) for row in migrations],
        "writable": os.access(path, os.W_OK),
        "plaintext_probe_absent": plaintext_probe not in raw,
    }


def _required_reactive_pipeline_ids(root: Path) -> set[str]:
    """Discover reviewed built-in consumers from their executable manifests."""

    required: set[str] = set()
    for manifest in sorted((root / "packs" / "pipelines").glob("*/pipeline.yaml")):
        try:
            document = yaml.safe_load(manifest.read_text("utf-8")) or {}
            pack_id = str((document.get("pack") or {}).get("id") or "")
            events = (document.get("triggers") or {}).get("events") or []
        except (OSError, TypeError, yaml.YAMLError):
            continue
        if pack_id and isinstance(events, list) and events:
            required.add(f"pipeline:{pack_id}")
    return required


def synthetic_round_trip(root: Path | None = None) -> dict[str, Any]:
    """Exercise message -> review -> workbook -> completion -> what-now."""
    repo = (root or repository_root()).resolve()
    with tempfile.TemporaryDirectory(prefix="adminme-doctor-proof-") as temp_dir:
        instance = Path(temp_dir) / "instance"
        instance.mkdir()
        settings = RuntimeSettings.credential_free(
            instance,
            encryption_key=b"d" * 32,
            repository_root=repo,
            member_identities={"fixture-principal.ts.net": FIXTURE_MEMBER_ID},
            member_roles={FIXTURE_MEMBER_ID: "principal"},
            deterministic_openclaw_fixture=True,
            fixture_member_id=FIXTURE_MEMBER_ID,
            openclaw_enabled=True,
            openclaw_shared_secret="doctor-openclaw-secret-32-characters",
            openclaw_actor_identities={
                "doctor|fixture-account|doctor-actor": FIXTURE_MEMBER_ID
            },
            openclaw_model_configured=True,
            member_chat_enabled_members=frozenset({FIXTURE_MEMBER_ID}),
            deterministic_member_chat_fixture=True,
        )
        app = create_application(settings)
        headers = {
            "Tailscale-User-Login": "fixture-principal.ts.net",
            "X-Correlation-Id": "doctor:canonical-proof",
        }
        with TestClient(app) as client:
            assert client.portal is not None
            message = client.post(
                "/api/comms/v1/demo/canonical-message",
                headers=headers,
                json={
                    "request_id": "doctor-message",
                    "native_message_id": "doctor-native-001",
                    "body": "Could you reply today with the fixture household update?",
                    "received_at": "2026-07-30T12:05:00Z",
                },
            )
            if message.status_code != 200:
                raise RuntimeError("canonical message fixture failed")
            commitment_id = message.json()["output"]["commitment_id"]
            pending = client.get(
                "/api/comms/v1/commitments/pending", headers=headers
            ).json()["items"]
            proposal = next(
                row for row in pending if row["commitment_id"] == commitment_id
            )
            dashboard = client.get("/api/core/v1/triage", headers=headers).json()
            dashboard_visible = any(
                item.get("id") == commitment_id for item in dashboard["items"]
            )
            before = client.get("/api/core/v1/what-now", headers=headers).json()
            delegation = client.post(
                "/api/member/v1/cos/delegations",
                headers=headers,
                json={
                    "request_id": "doctor-cos-delegation",
                    "context_scope": "member_private",
                    "permissions": ["read_context", "manage_internal_work"],
                },
            )
            if delegation.status_code != 201:
                raise RuntimeError("canonical CoS delegation fixture failed")
            review_params = {
                "request_id": "doctor-approve",
                "action": "approve",
                "commitment_id": commitment_id,
                "expected_last_event_id": proposal["last_event_id"],
                "send_acknowledgement": True,
            }
            request_fingerprint = hashlib.sha256(
                json.dumps(
                    review_params, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
            issued_at_ms = int(time.time() * 1000)
            expires_at_ms = issued_at_ms + 120_000
            receipt_id = "doctor-openclaw-allow-once"
            shared_secret = "doctor-openclaw-secret-32-characters"
            signature = sign_approval_receipt(
                shared_secret,
                receipt_id=receipt_id,
                tool_name="administrateme_review",
                request_fingerprint=request_fingerprint,
                issued_at_ms=issued_at_ms,
                expires_at_ms=expires_at_ms,
                openclaw_version=OPENCLAW_VERSION,
                openclaw_commit=OPENCLAW_COMMIT,
            )
            openclaw_headers = {"Authorization": f"Bearer {shared_secret}"}
            resolved_receipt = client.post(
                "/api/openclaw/v1/approvals/resolve",
                headers=openclaw_headers,
                json={
                    "receipt_id": receipt_id,
                    "tool_name": "administrateme_review",
                    "request_fingerprint": request_fingerprint,
                    "issued_at_ms": issued_at_ms,
                    "expires_at_ms": expires_at_ms,
                    "decision": "allow-once",
                    "openclaw_version": OPENCLAW_VERSION,
                    "openclaw_commit": OPENCLAW_COMMIT,
                    "signature": signature,
                },
            )
            if resolved_receipt.status_code != 200:
                raise RuntimeError("OpenClaw approval receipt fixture failed")
            approved_response = client.post(
                "/api/openclaw/v1/tools/review",
                headers=openclaw_headers,
                json={
                    "provenance": {
                        "actor_id": "doctor-actor",
                        "agent_id": "doctor-agent",
                        "session_key": "adminme:doctor:fixture",
                        "session_id": "doctor-session",
                        "channel_id": "doctor",
                        "account_id": "fixture-account",
                        "message_id": "doctor-message-approval",
                        "thread_id": "doctor-thread",
                        "correlation_id": "doctor:openclaw-approval",
                        "call_kind": "tool",
                        "openclaw_version": OPENCLAW_VERSION,
                        "openclaw_commit": OPENCLAW_COMMIT,
                    },
                    **review_params,
                    "openclaw_approval_receipt_id": receipt_id,
                },
            )
            if approved_response.status_code != 200:
                raise RuntimeError("canonical review fixture failed")
            approved = approved_response.json()
            command_id = f"commitment-ack:{commitment_id}:doctor-approve"
            terminal = None
            for _ in range(100):
                terminal = client.portal.call(
                    app.state.runtime.event_log.get_by_idempotency,
                    app.state.runtime.settings.instance.tenant_id,
                    f"external-sync-result:{command_id}",
                )
                if terminal is not None:
                    break
                time.sleep(0.01)
            if terminal is None:
                raise RuntimeError("canonical provider outcome fixture failed")
            after_approval = client.get("/api/core/v1/what-now", headers=headers).json()
            completion_response = client.post(
                f"/api/core/v1/commitments/{commitment_id}/complete",
                headers=headers,
                json={
                    "request_id": "doctor-complete",
                    "expected_last_event_id": approved["commitment"]["last_event_id"],
                    "note": "Completed by credential-free Doctor proof",
                },
            )
            if completion_response.status_code != 200:
                raise RuntimeError("canonical completion fixture failed")
            completed = completion_response.json()
            runtime = app.state.runtime
            client.portal.call(runtime.projections.rebuild, "commitments")
            rebuilt = client.get(
                f"/api/core/v1/commitments/{commitment_id}", headers=headers
            ).json()
            workbook = settings.instance.xlsx_workbooks_dir / "adminme-ops.xlsx"
            finance_workbook = (
                settings.instance.xlsx_workbooks_dir / "adminme-finance.xlsx"
            )
            sidecar_root = settings.instance.xlsx_workbooks_dir.parent / ".xlsx-state"
            sidecar_count = (
                len(list(sidecar_root.rglob("*.json"))) if sidecar_root.is_dir() else 0
            )
            projection_names = tuple(runtime.projections.projection_names())
            projection_status = client.portal.call(runtime.projections.status)
            event_count = client.portal.call(runtime.event_log.count)
            portal = client.get("/api/member/v1/bootstrap", headers=headers)
            portal_today = client.get("/api/member/v1/surfaces/today", headers=headers)
            chat_status = client.get(
                "/api/member/v1/chat/status", headers=headers
            ).json()
            return {
                "message_state": message.json()["output"]["state"],
                "dashboard_review_visible": dashboard_visible,
                "review_suppressed_by_observation": bool(
                    terminal["payload"].get("suppressed")
                ),
                "what_now_changed_on_approval": before != after_approval,
                "what_now_recalculated_on_completion": completed["what_now"]
                != after_approval,
                "completion_state": completed["commitment"]["status"],
                "rebuild_state": rebuilt["status"],
                "workbook_exists": workbook.is_file(),
                "finance_workbook_exists": finance_workbook.is_file(),
                "workbook_sha256": sha256_file(workbook),
                "reverse_sidecar_count": sidecar_count,
                "projection_names": list(projection_names),
                "projection_checkpoints_healthy": all(
                    not status.get("degraded") for status in projection_status.values()
                ),
                "event_count": event_count,
                "external_side_effects": len(runtime.fixture_adapter.delivered),
                "member_portal_health": "healthy"
                if portal.status_code == 200
                else "degraded",
                "deterministic_domain_views": portal_today.status_code == 200,
                "chat_transport": chat_status.get("state"),
                "model_provider_configured": settings.openclaw_model_configured,
            }


class Doctor:
    def __init__(
        self,
        paths: DeploymentPaths,
        *,
        root: Path | None = None,
        test_mode: bool = False,
    ) -> None:
        self.paths = paths
        self.root = (root or repository_root()).resolve()
        self.test_mode = test_mode
        self.store = secret_store(paths, test_mode=test_mode)

    def run(self, *, include_synthetic_proof: bool = True) -> dict[str, Any]:
        checks: list[DoctorCheck] = []
        versions = load_versions(self.root)
        system = "Darwin" if self.test_mode else platform.system()
        machine = "arm64" if self.test_mode else platform.machine()
        checks.append(
            DoctorCheck(
                "platform",
                system == "Darwin" and machine == "arm64",
                f"{system}/{machine}",
                "Use the supported Apple-silicon macOS host.",
            )
        )
        if self.test_mode:
            observed_versions = {
                "python": versions["python"]["preferred"],
                "node": versions["node"]["preferred"],
                "npm": "11.17.0",
                "homebrew": "6.0.0",
                "openclaw": versions["openclaw"]["version"],
            }
        else:
            observed_versions = {
                "python": platform.python_version(),
                "node": _command_output(["node", "-p", "process.versions.node"]),
                "npm": _command_output(["npm", "--version"]),
                "homebrew": _command_output(["brew", "--version"]),
                "openclaw": _command_output(["openclaw", "--version"]),
            }
        python_version = _version_parts(str(observed_versions["python"]))
        node_version = _version_parts(str(observed_versions["node"]))
        npm_version = _version_parts(str(observed_versions["npm"]))
        brew_text = str(observed_versions["homebrew"])
        brew_version = _version_parts(
            brew_text.splitlines()[0].replace("Homebrew", "").strip()
        )
        version_ok = (
            python_version[:2] == (3, 14)
            and node_version[0] == 24
            and node_version >= (24, 15, 0)
            and npm_version[0] == 11
            and npm_version >= (11, 6, 0)
            and 6 <= brew_version[0] < 7
            and versions["openclaw"]["version"] in str(observed_versions["openclaw"])
        )
        checks.append(
            DoctorCheck(
                "runtime_and_package_versions",
                version_ok,
                "runtime versions satisfy the tested matrix"
                if version_ok
                else "one or more runtime versions are incompatible",
                "Run bootstrap install; do not upgrade OpenClaw independently.",
                observed_versions,
            )
        )
        source_status = reviewed_source_status(
            self.paths,
            test_mode=self.test_mode,
        )
        checks.append(
            DoctorCheck(
                "reviewed_openclaw_source",
                bool(source_status["ok"]),
                str(source_status["detail"]),
                "Rerun the reviewed source installer; never substitute an npm or floating OpenClaw build.",
                source_status,
            )
        )
        config_path = self.paths.instance_dir / "config" / "instance.yaml"
        checks.append(
            DoctorCheck(
                "validated_configuration",
                config_path.is_file(),
                "instance configuration present"
                if config_path.is_file()
                else "missing",
            )
        )
        report_path = self.paths.public_config_path
        report = (
            json.loads(report_path.read_text("utf-8")) if report_path.exists() else {}
        )
        sections = report.get("sections", {})
        checks.append(
            DoctorCheck(
                "bootstrap_sections",
                len(sections) == 10
                and set(sections.values()) == {"complete"}
                and report.get("bootstrap_complete") is True,
                f"{sum(value == 'complete' for value in sections.values())}/10 complete",
                "Resume bootstrap or finish/revoke each selected member-bridge enrollment.",
            )
        )
        references_path = self.paths.instance_dir / "config" / "secret-references.json"
        references: dict[str, str] = {}
        if references_path.exists():
            references = json.loads(references_path.read_text("utf-8"))["references"]
        secrets_ok = bool(references) and all(
            self.store.has(reference) for reference in references.values()
        )
        checks.append(
            DoctorCheck(
                "secure_secret_references",
                secrets_ok,
                f"{len(references)} references resolved via {self.store.provider_name}",
                "Repair the missing Keychain or 1Password item; never put its value in .env.",
            )
        )
        event_path = self.paths.instance_dir / "events" / "events.db"
        event_ok = False
        event_evidence: dict[str, Any] = {"reason": "secret unavailable"}
        if secrets_ok:
            event_ok, event_evidence = _event_log_check(
                event_path,
                self.store.get(references["event_log"]),
            )
        checks.append(
            DoctorCheck(
                "encrypted_event_log_and_migrations",
                event_ok and bool(event_evidence.get("migrations")),
                "SQLCipher log opens, is writable, and has migration history"
                if event_ok
                else "event-log validation failed",
                "Stop services and restore the last verified encrypted backup.",
                event_evidence,
            )
        )
        projection_files = {
            name: (self.paths.instance_dir / "projections" / f"{name}.db").is_file()
            for name in PROJECTION_NAMES
        }
        checkpoint_path = self.paths.instance_dir / "events" / "bus_checkpoints.db"
        checkpoint_count = 0
        degraded_count = 0
        if checkpoint_path.is_file():
            connection = sqlite3.connect(str(checkpoint_path))
            try:
                row = connection.execute(
                    "SELECT count(*), coalesce(sum(degraded), 0) "
                    "FROM bus_consumer_offsets WHERE subscriber_id LIKE 'projection:%'"
                ).fetchone()
                checkpoint_count, degraded_count = int(row[0]), int(row[1])
            finally:
                connection.close()
        checks.append(
            DoctorCheck(
                "fifteen_projections_and_checkpoints",
                len(projection_files) == len(PROJECTION_NAMES)
                and all(projection_files.values())
                and checkpoint_count == len(PROJECTION_NAMES)
                and degraded_count == 0,
                f"{sum(projection_files.values())}/{len(PROJECTION_NAMES)} stores; "
                f"{checkpoint_count}/{len(PROJECTION_NAMES)} healthy checkpoints",
                "Run Doctor rebuild from a stopped instance before enabling writes.",
                {
                    "projections": projection_files,
                    "checkpoint_count": checkpoint_count,
                    "degraded_count": degraded_count,
                },
            )
        )
        required_pipeline_ids = _required_reactive_pipeline_ids(self.root)
        pipeline_rows: dict[str, dict[str, Any]] = {}
        if checkpoint_path.is_file():
            connection = sqlite3.connect(str(checkpoint_path))
            connection.row_factory = sqlite3.Row
            try:
                for row in connection.execute(
                    "SELECT subscriber_id, checkpoint_event_id, last_success_at_ms, "
                    "last_failure_at_ms, consecutive_failures, degraded, "
                    "poison_event_id, consumer_version "
                    "FROM bus_consumer_offsets "
                    "WHERE subscriber_id LIKE 'pipeline:%'"
                ):
                    pipeline_rows[str(row["subscriber_id"])] = {
                        "checkpoint_event_id": row["checkpoint_event_id"],
                        "last_success_at_ms": row["last_success_at_ms"],
                        "last_failure_at_ms": row["last_failure_at_ms"],
                        "consecutive_failures": int(row["consecutive_failures"] or 0),
                        "degraded": bool(row["degraded"]),
                        "poison_event_id": row["poison_event_id"],
                        "consumer_version": row["consumer_version"],
                    }
            except sqlite3.OperationalError:
                # A pre-migration checkpoint DB has no consumer_version yet.
                pipeline_rows = {}
            finally:
                connection.close()
        missing_pipelines = sorted(required_pipeline_ids - set(pipeline_rows))
        unhealthy_pipelines = sorted(
            subscriber_id
            for subscriber_id in required_pipeline_ids & set(pipeline_rows)
            if pipeline_rows[subscriber_id]["degraded"]
            or pipeline_rows[subscriber_id]["poison_event_id"] is not None
            or pipeline_rows[subscriber_id]["consecutive_failures"] > 0
        )
        pipeline_delivery_ok = (
            bool(required_pipeline_ids)
            and not missing_pipelines
            and not unhealthy_pipelines
        )
        checks.append(
            DoctorCheck(
                "required_reactive_pipeline_delivery",
                pipeline_delivery_ok,
                (
                    f"{len(required_pipeline_ids)}/{len(required_pipeline_ids)} "
                    "required pipeline subscribers healthy"
                    if pipeline_delivery_ok
                    else (
                        f"{len(missing_pipelines)} missing; "
                        f"{len(unhealthy_pipelines)} degraded or poisoned"
                    )
                ),
                "Deploy a reviewed pipeline repair and replay from each last good checkpoint.",
                {
                    "required": sorted(required_pipeline_ids),
                    "missing": missing_pipelines,
                    "unhealthy": unhealthy_pipelines,
                    "subscribers": pipeline_rows,
                },
            )
        )
        workbook_dir = self.paths.instance_dir / "projections" / "xlsx_workbooks"
        workbook_files = {
            "ops": (workbook_dir / "adminme-ops.xlsx").is_file(),
            "finance": (workbook_dir / "adminme-finance.xlsx").is_file(),
        }
        sidecar_dir = workbook_dir.parent / ".xlsx-state"
        sidecar_count = (
            len(list(sidecar_dir.rglob("*.json"))) if sidecar_dir.is_dir() else 0
        )
        has_workbooks = any(workbook_files.values())
        workbook_ok = projection_files["xlsx_workbooks"] and (
            (not has_workbooks and sidecar_count == 0)
            or (has_workbooks and sidecar_count > 0)
        )
        workbook_state = (
            "not_generated_no_domain_rows"
            if not has_workbooks and sidecar_count == 0
            else "forward_and_reverse_state_present"
        )
        checks.append(
            DoctorCheck(
                "workbook_forward_and_reverse_state",
                workbook_ok,
                workbook_state,
                "Rebuild xlsx_workbooks and repair sidecar state before enabling reverse sync.",
                {"workbooks": workbook_files, "sidecar_files": sidecar_count},
            )
        )
        adapters_path = self.paths.instance_dir / "config" / "adapters.json"
        adapter_categories: dict[str, str] = {}
        if adapters_path.exists():
            adapter_categories = json.loads(adapters_path.read_text("utf-8")).get(
                "adapter_categories", {}
            )
        checks.append(
            DoctorCheck(
                "adapter_categories_and_capabilities",
                set(adapter_categories) == set(ADAPTER_CATEGORIES),
                f"{len(adapter_categories)}/5 categories explicitly classified",
                "Rerun channel and adapter pairing; disabled is valid, absent is not.",
                {"states": adapter_categories},
            )
        )
        model_configured = model_provider_configured(self.paths)
        shared_reference = references.get("openclaw_shared")
        gateway_token = (
            self.store.get(shared_reference).decode("ascii")
            if isinstance(shared_reference, str) and self.store.has(shared_reference)
            else None
        )
        plugin_manifest = (
            self.root / "openclaw" / "administrateme" / "openclaw.plugin.json"
        )
        package_path = self.root / "openclaw" / "administrateme" / "package.json"
        plugin_ok = False
        plugin_evidence: dict[str, Any] = {}
        if plugin_manifest.exists() and package_path.exists():
            package = json.loads(package_path.read_text("utf-8"))
            compat = package.get("openclaw", {}).get("compat", {})
            build = package.get("openclaw", {}).get("build", {})
            plugin_evidence = {
                "plugin_api": compat.get("pluginApi"),
                "gateway": compat.get("minGatewayVersion"),
                "commit": build.get("openclawCommit"),
            }
            release_status = administrateme_plugin_status(
                self.paths,
                self.root,
                test_mode=self.test_mode,
            )
            plugin_evidence.update(release_status)
            llm_tool = required_llm_tool_status(self.paths, test_mode=self.test_mode)
            plugin_evidence["required_llm_tool"] = llm_tool
            if model_configured and include_synthetic_proof:
                execution_probe = probe_required_llm_tool(
                    gateway_token, test_mode=self.test_mode
                )
            elif model_configured:
                execution_probe = {
                    "ok": True,
                    "state": "deferred_until_full_proof",
                    "credentials_in_report": False,
                }
            else:
                execution_probe = {
                    "ok": True,
                    "state": "model_provider_optional_unconfigured",
                    "credentials_in_report": False,
                }
            plugin_evidence["required_llm_tool_execution"] = execution_probe
            plugin_ok = (
                compat.get("pluginApi") == versions["openclaw"]["plugin_api"]
                and build.get("openclawCommit") == versions["openclaw"]["commit"]
                and release_status["ok"]
                and llm_tool["ok"]
                and execution_probe["ok"]
            )
        checks.append(
            DoctorCheck(
                "openclaw_compatibility_and_plugin_discovery",
                plugin_ok,
                "manifest, plugin discovery, and required llm-task execution agree"
                if plugin_ok
                else "plugin compatibility or required llm-task registration mismatch",
                "Restore the tested exact OpenClaw build, reconcile llm-task, and pass its authenticated synthetic execution probe.",
                plugin_evidence,
            )
        )
        try:
            instance_config = yaml.safe_load(config_path.read_text("utf-8")) or {}
            timezone_name = instance_config.get("timezone", "")
            cron_status = OpenClawCronReconciler(
                self.paths,
                root=self.root,
                gateway_token=gateway_token,
                test_mode=self.test_mode,
            ).status(timezone_name)
        except Exception as exc:
            cron_status = {
                "ok": False,
                "detail": f"cron validation failed: {type(exc).__name__}",
            }
        checks.append(
            DoctorCheck(
                "openclaw_cron_reconciliation",
                bool(cron_status.get("ok")),
                str(cron_status.get("detail", "cron status unavailable")),
                "Run bootstrap/start.sh; it safely reconciles only AdministrateMe-owned schedules.",
                cron_status,
            )
        )
        portal_assets = self.root / "adminme" / "products" / "member" / "assets"
        portal_ok = all(
            (portal_assets / name).is_file()
            for name in ("index.html", "member.css", "member.js")
        )
        references_path = self.paths.instance_dir / "config" / "secret-references.json"
        try:
            references = json.loads(references_path.read_text("utf-8")).get(
                "references", {}
            )
        except (OSError, json.JSONDecodeError):
            references = {}
        chat_transport_configured = bool(references.get("openclaw_shared"))
        checks.append(
            DoctorCheck(
                "member_portal_domain_and_chat_health",
                portal_ok,
                "member portal contract is installed; optional chat state is explicit"
                if portal_ok
                else "member portal assets are incomplete",
                "Reinstall the exact AdministrateMe package; configure chat only for opted-in members.",
                {
                    "portal_health": "installed" if portal_ok else "degraded",
                    "deterministic_domain_views": "covered_by_fifteen_projection_check",
                    "openclaw_chat_transport": "configured"
                    if chat_transport_configured
                    else "optional_unconfigured",
                    "model_provider": "configured"
                    if model_configured
                    else "optional_unconfigured",
                    "credentials_in_report": False,
                },
            )
        )
        launchd: dict[str, bool] = {}
        for label in (RUNTIME_LABEL, OPENCLAW_LABEL):
            path = self.paths.launch_agent_dir / f"{label}.plist"
            try:
                data = plistlib.loads(path.read_bytes())
                launchd[label] = data.get("Label") == label
            except Exception:
                launchd[label] = False
        service_manager = ServiceManager(self.paths, test_mode=self.test_mode)
        service_status = service_manager.status().public()
        checks.append(
            DoctorCheck(
                "launchd_and_process_state",
                all(launchd.values()) and service_status["status"] == "healthy",
                service_status["status"],
                "Run bootstrap/start.sh, then inspect the named service log.",
                {"definitions": launchd, "services": service_status},
            )
        )
        runtime_diagnostics = service_manager.runtime_diagnostics() or {}
        assurance_ok, assurance_evidence = source_assurance_terminal_evidence(
            runtime_diagnostics
        )
        checks.append(
            DoctorCheck(
                "source_assurance_terminal_dispositions",
                assurance_ok,
                (
                    "every source obligation has a fresh audited terminal disposition"
                    if assurance_ok
                    else "source accounting is pending, stale, failed, or incomplete"
                ),
                "Keep observation active; repair or replay the named source assessor before deployment.",
                assurance_evidence,
            )
        )
        deployment_readiness = runtime_diagnostics.get("deployment_readiness") or {}
        readiness_ok = deployment_readiness.get("ready") is True
        if self.test_mode and not deployment_readiness:
            readiness_ok = True
            deployment_readiness = {
                "contract": "deployment-readiness-v1",
                "state": "not_required_in_disposable_test",
                "ready": True,
                "content_exposed": False,
            }
        checks.append(
            DoctorCheck(
                "deployment_topology_and_recovery_readiness",
                readiness_ok,
                (
                    "topology, identity, provider, OpenClaw, bridge, backup, and effect gates are ready"
                    if readiness_ok
                    else "one or more deployment readiness dimensions are blocked"
                ),
                "Complete the owner Human Task Register; do not weaken or bypass a missing readiness predicate.",
                deployment_readiness,
            )
        )
        host = "127.0.0.1"
        ports = {
            "runtime": versions["administrateme"]["runtime_port"],
            "openclaw": versions["openclaw"]["gateway_port"],
        }
        listeners_ok = True
        listener_evidence: dict[str, Any] = {"mode": "fixture-config"}
        if not self.test_mode and service_status["status"] == "healthy":
            listener_evidence = {}
            for name, port in ports.items():
                output = _command_output(
                    ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"]
                )
                listener_evidence[name] = output or "missing"
                listeners_ok = listeners_ok and bool(
                    output
                    and (f"127.0.0.1:{port}" in output or f"[::1]:{port}" in output)
                    and f"*:{port}" not in output
                )
        checks.append(
            DoctorCheck(
                "ports_and_loopback_binding",
                host == "127.0.0.1" and len(set(ports.values())) == 2 and listeners_ok,
                f"configured loopback ports: {ports}",
                "Correct configuration before starting either service.",
                {"host": host, "ports": ports, "listeners": listener_evidence},
            )
        )
        mapping_path = self.paths.instance_dir / "config" / "member-mapping.json"
        member_count = 0
        portal_bridge_bindings: dict[str, tuple[str, str]] = {}
        if mapping_path.exists():
            mapping_value = json.loads(mapping_path.read_text("utf-8"))
            members_value = mapping_value["members"]
            member_count = len(members_value)
            portal_bridge_bindings = {
                member["tailscale_identity"].casefold(): (
                    member["member_id"],
                    member["role"],
                )
                for member in members_value
                if member.get("tailscale_identity")
            }
        bridge_path = self.paths.instance_dir / "bridges" / "member-mapping.json"
        bridge_records = (
            json.loads(bridge_path.read_text("utf-8")) if bridge_path.exists() else {}
        )
        bridge_count = len(bridge_records)
        bridge_statuses = [
            record.get("status")
            for record in bridge_records.values()
            if isinstance(record, dict)
        ]
        bridge_terminal = all(
            status in {"active", "revoked"} for status in bridge_statuses
        )
        bridge_bindings_ok = all(
            portal_bridge_bindings.get(identity.casefold())
            == (record.get("member_id"), record.get("role"))
            and record.get("owner_scope") == f"private:{record.get('member_id')}"
            for identity, record in bridge_records.items()
            if isinstance(identity, str) and isinstance(record, dict)
        ) and all(isinstance(record, dict) for record in bridge_records.values())
        bridge_ok = (
            bridge_count <= member_count
            and bridge_bindings_ok
            and (bridge_terminal or self.test_mode)
        )
        checks.append(
            DoctorCheck(
                "member_mapping_and_bridge_status",
                member_count > 0 and bridge_ok,
                f"{member_count} members; {bridge_count} selected bridges; bindings={bridge_bindings_ok}; statuses={bridge_statuses}",
                "Export, accept, and finalize each selected bridge, or explicitly revoke it; never reuse another member's package.",
            )
        )
        observation_path = self.paths.instance_dir / "config" / "observation.json"
        observation = (
            json.loads(observation_path.read_text("utf-8"))
            if observation_path.exists()
            else {}
        )
        observation_ok = (
            observation.get("active") is True
            and observation.get("briefing_complete") is True
            and observation.get("trusted") is False
            and bool(observation.get("trust_not_before"))
        )
        checks.append(
            DoctorCheck(
                "observation_mode_and_trust_workflow",
                observation_ok,
                "observation is active; trust cannot be declared yet"
                if observation_ok
                else "observation trust workflow is incomplete",
                "Repeat the observation briefing; do not disable observation manually.",
            )
        )
        free = (
            shutil.disk_usage(self.paths.root).free if self.paths.root.exists() else 0
        )
        backup_writable = self.paths.backup_dir.is_dir() and os.access(
            self.paths.backup_dir, os.W_OK
        )
        checks.append(
            DoctorCheck(
                "disk_space_and_backup_target",
                free >= 5 * 1024**3 and backup_writable,
                f"{free // 1024**3} GiB free; backup target writable={backup_writable}",
                "Free 5 GiB and repair the configured encrypted backup target.",
            )
        )
        if include_synthetic_proof:
            try:
                proof = synthetic_round_trip(self.root)
                proof_ok = (
                    proof["message_state"] == "proposal_only"
                    and proof["dashboard_review_visible"]
                    and proof["review_suppressed_by_observation"]
                    and proof["completion_state"] == "done"
                    and proof["rebuild_state"] == "done"
                    and proof["workbook_exists"]
                    and proof["reverse_sidecar_count"] > 0
                    and set(proof["projection_names"]) == set(PROJECTION_NAMES)
                    and proof["projection_checkpoints_healthy"]
                    and proof["external_side_effects"] == 0
                    and proof["member_portal_health"] == "healthy"
                    and proof["deterministic_domain_views"]
                    and proof["chat_transport"] == "healthy"
                    and proof["model_provider_configured"]
                )
            except Exception as exc:
                proof = {"failure": type(exc).__name__}
                proof_ok = False
            checks.append(
                DoctorCheck(
                    "canonical_synthetic_round_trip",
                    proof_ok,
                    "message, review, portal, chat seam, dashboard, workbook, completion, recalculation, and rebuild passed"
                    if proof_ok
                    else "canonical proof failed",
                    "Keep observation on and run the focused acceptance test.",
                    proof,
                )
            )
        result = {
            "schema_version": 1,
            "status": "healthy" if all(check.ok for check in checks) else "failed",
            "mode": "observation",
            "checks": [asdict(check) for check in checks],
            "safe_to_attach": True,
            "live_provider_claims": [],
        }
        self._assert_secret_free(result, references)
        return result

    def export(
        self, path: Path, *, include_synthetic_proof: bool = True
    ) -> dict[str, Any]:
        result = self.run(include_synthetic_proof=include_synthetic_proof)
        safe = _redact_paths(result, self.paths.root)
        atomic_write(path, json.dumps(safe, indent=2, sort_keys=True).encode("utf-8"))
        return safe

    def _assert_secret_free(
        self,
        result: dict[str, Any],
        references: dict[str, str],
    ) -> None:
        rendered = json.dumps(result, sort_keys=True).encode("utf-8")
        for reference in references.values():
            if not self.store.has(reference):
                continue
            value = self.store.get(reference)
            variants = (value, value.hex().encode("ascii"))
            if any(variant and variant in rendered for variant in variants):
                raise RuntimeError("Doctor output contained a secret value")


def _redact_paths(value: Any, root: Path) -> Any:
    if isinstance(value, dict):
        return {key: _redact_paths(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_paths(item, root) for item in value]
    if isinstance(value, str):
        return value.replace(str(root), "<instance-root>")
    return value


__all__ = ["Doctor", "DoctorCheck", "synthetic_round_trip"]
