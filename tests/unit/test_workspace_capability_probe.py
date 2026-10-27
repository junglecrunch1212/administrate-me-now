"""Synthetic contract tests only; no native app, browser, member, or network probe."""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "workspace_capability_probe.py"
SPEC = importlib.util.spec_from_file_location("workspace_capability_probe", SCRIPT)
assert SPEC and SPEC.loader
probe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = probe
SPEC.loader.exec_module(probe)

NOW = datetime(2026, 10, 27, 16, 0, tzinfo=timezone.utc)
DEVICE = "device:synthetic-family-mac"
PRINCIPAL = "service-principal:chief-of-staff"
RESOURCES = {
    "shell_files": "/approved/synthetic/proposal",
    "native_app_control": "native-service:synthetic-reminders",
    "browser": "browser-resource:synthetic-planning",
    "authorized_member_route": "member-route:synthetic-member-a",
}


def request() -> dict:
    return {
        "contract": "workspace-capability-receipts-v1",
        "schema_version": 1,
        "intent": {
            "device_id": DEVICE,
            "principal_id": PRINCIPAL,
            "resources": deepcopy(RESOURCES),
        },
        "receipts": [],
        "traffic": {
            "existing_approved_connection_used": True,
            "new_outbound_setup_requested": False,
        },
    }


def receipt(
    surface: str,
    *,
    claim: str = "verified",
    support: str = "supported",
    outcome: str = "success",
    terminal: bool = True,
    disposition: str = "completed",
    evidence_kind: str = "actual_terminal_result",
) -> dict:
    return {
        "surface": surface,
        "claim": claim,
        "support": support,
        "outcome": outcome,
        "terminal": terminal,
        "disposition": disposition,
        "evidence_kind": evidence_kind,
        "device_id": DEVICE,
        "principal_id": PRINCIPAL,
        "resource_id": RESOURCES[surface],
        "observed_at": (NOW - timedelta(minutes=2)).isoformat(),
        "valid_until": (NOW + timedelta(minutes=20)).isoformat(),
        "evidence_id": f"synthetic:{surface}:receipt-1",
        "evidence_sha256": "a" * 64,
        "checks": sorted(probe.REQUIRED_CHECKS[probe.Surface(surface)]),
    }


def evaluate(value: dict) -> dict:
    return probe.evaluate_bytes(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode(), now=NOW
    )


def surfaces(report: dict) -> dict[str, dict]:
    return {row["surface"]: row for row in report.get("surfaces", [])}


class WorkspaceCapabilityProbeTests(unittest.TestCase):
    def test_valid_terminal_matching_receipt_is_verified(self) -> None:
        value = request()
        value["receipts"] = [receipt("shell_files")]

        report = evaluate(value)

        self.assertEqual(surfaces(report)["shell_files"]["state"], "verified")
        self.assertEqual(
            surfaces(report)["shell_files"]["basis"],
            "current_matching_operator_attested_terminal_success",
        )
        self.assertEqual(report["bounded_acceptance"], "not_accepted")
        self.assertFalse(report["full_workspace_ready"])

    def test_stale_or_mismatched_identity_and_resource_never_verify(self) -> None:
        cases: list[tuple[str, object]] = [
            ("device_id", "device:other"),
            ("principal_id", "service-principal:other"),
            ("resource_id", "/approved/synthetic/other"),
        ]
        for field, replacement in cases:
            with self.subTest(field=field):
                value = request()
                row = receipt("shell_files")
                row[field] = replacement
                value["receipts"] = [row]
                result = surfaces(evaluate(value))["shell_files"]
                self.assertEqual(result["state"], "unverified")
                self.assertIn("mismatch", result["basis"])

        stale = request()
        row = receipt("shell_files")
        row["observed_at"] = (NOW - timedelta(minutes=30)).isoformat()
        row["valid_until"] = (NOW + timedelta(minutes=5)).isoformat()
        stale["receipts"] = [row]
        result = surfaces(evaluate(stale))["shell_files"]
        self.assertEqual(result["state"], "unverified")
        self.assertEqual(result["basis"], "observation_missing_current_validity")

    def test_denied_and_unavailable_are_distinct_terminal_results(self) -> None:
        value = request()
        value["receipts"] = [
            receipt("native_app_control", claim="denied", outcome="denied"),
            receipt(
                "browser",
                claim="unavailable",
                support="unknown",
                outcome="unavailable",
            ),
        ]

        rows = surfaces(evaluate(value))

        self.assertEqual(rows["native_app_control"]["state"], "denied")
        self.assertEqual(rows["browser"]["state"], "unavailable")

    def test_pending_and_missing_receipts_are_not_success(self) -> None:
        value = request()
        value["receipts"] = [
            receipt(
                "authorized_member_route",
                claim="pending_unknown",
                support="unknown",
                outcome="pending",
                terminal=False,
                disposition="queued",
                evidence_kind="queued_result",
            )
        ]

        rows = surfaces(evaluate(value))

        self.assertEqual(rows["authorized_member_route"]["state"], "pending_unknown")
        self.assertEqual(rows["shell_files"]["state"], "pending_unknown")
        self.assertNotEqual(rows["authorized_member_route"]["state"], "verified")

    def test_contradictory_claims_and_nonproof_artifacts_are_rejected(self) -> None:
        contradictions = [
            {"outcome": "denied"},
            {
                "terminal": False,
                "disposition": "queued",
                "evidence_kind": "queued_result",
            },
            {"disposition": "prompted", "evidence_kind": "prompt"},
            {"disposition": "registered", "evidence_kind": "registration"},
            {"disposition": "configured", "evidence_kind": "enabled_setting"},
        ]
        for change in contradictions:
            with self.subTest(change=change):
                value = request()
                row = receipt("shell_files")
                row.update(change)
                value["receipts"] = [row]
                result = surfaces(evaluate(value))["shell_files"]
                self.assertEqual(result["state"], "unverified")
                self.assertEqual(
                    result["basis"], "contradictory_or_nonqualifying_receipt"
                )

        relative = request()
        relative["intent"]["resources"]["shell_files"] = "relative/proposal"
        row = receipt("shell_files")
        row["resource_id"] = "relative/proposal"
        relative["receipts"] = [row]
        result = surfaces(evaluate(relative))["shell_files"]
        self.assertEqual(result["state"], "unverified")
        self.assertIn("absolute", result["basis"])

    def test_unknown_schema_extra_fields_and_oversized_inputs_refuse_safely(
        self,
    ) -> None:
        unknown = request()
        unknown["schema_version"] = 2
        self.assertEqual(
            evaluate(unknown)["bounded_acceptance"], "refused_invalid_input"
        )

        extra = request()
        extra["unexpected"] = "synthetic"
        self.assertEqual(evaluate(extra)["bounded_acceptance"], "refused_invalid_input")

        oversized = b"{" + b" " * (probe.MAX_INPUT_BYTES + 1) + b"}"
        result = probe.evaluate_bytes(oversized, now=NOW)
        self.assertEqual(result["bounded_acceptance"], "refused_invalid_input")

    def test_secret_shaped_and_raw_content_fields_are_never_reflected(self) -> None:
        secret_marker = "synthetic-secret-must-not-appear"
        for field in ("password", "raw_content", "credential", "message_body"):
            with self.subTest(field=field):
                value = request()
                row = receipt("shell_files")
                row[field] = secret_marker
                value["receipts"] = [row]
                report_text = json.dumps(evaluate(value), sort_keys=True)
                self.assertNotIn(secret_marker, report_text)
                self.assertNotIn(field, report_text)
                self.assertIn("refused_invalid_input", report_text)

    def test_control_map_does_not_inflate_shell_or_existing_traffic(self) -> None:
        value = request()
        value["receipts"] = [receipt("shell_files")]

        report = evaluate(value)
        controls = {row["path"]: row for row in report["control_coverage"]}

        self.assertEqual(
            controls["shell_child_processes_and_curl"]["evidence"],
            "not_evidenced_by_capability_receipts",
        )
        self.assertFalse(
            controls["shell_child_processes_and_curl"]["approval_enforcement_evidenced"]
        )
        self.assertEqual(
            controls["plugins_and_mcp"]["evidence"],
            "not_evidenced_by_capability_receipts",
        )
        self.assertEqual(
            controls["delegated_agents"]["evidence"],
            "not_evidenced_by_capability_receipts",
        )
        self.assertEqual(
            controls["background_processes"]["evidence"],
            "not_evidenced_by_capability_receipts",
        )
        self.assertEqual(
            report["traffic"]["existing_approved_connection"],
            "declared_context_only_not_reverified",
        )
        self.assertEqual(report["traffic"]["new_outbound_setup_request"], "none")
        self.assertEqual(report["traffic"]["runner_network_activity"], "none")

    def test_routing_failure_and_unsupported_integration_are_actionable(self) -> None:
        value = request()
        value["receipts"] = [
            receipt(
                "authorized_member_route",
                claim="routing_failure",
                outcome="routing_failure",
            ),
            receipt(
                "native_app_control",
                claim="unsupported_integration",
                outcome="unavailable",
                support="unsupported",
            ),
        ]
        rows = surfaces(evaluate(value))
        self.assertEqual(rows["authorized_member_route"]["state"], "routing_failure")
        self.assertEqual(rows["native_app_control"]["state"], "unsupported_integration")
        self.assertTrue(rows["native_app_control"]["next_handoff"])

    def test_wrong_surface_or_incomplete_operations_cannot_verify(self) -> None:
        for surface in RESOURCES:
            with self.subTest(surface=surface):
                value = request()
                row = receipt(surface)
                row["checks"] = row["checks"][:-1]
                value["receipts"] = [row]
                self.assertEqual(
                    surfaces(evaluate(value))[surface]["state"], "unverified"
                )
        value = request()
        row = receipt("browser")
        row["checks"] = receipt("shell_files")["checks"]
        value["receipts"] = [row]
        self.assertEqual(surfaces(evaluate(value))["browser"]["state"], "unverified")

    def test_service_turn_does_not_establish_member_channel_acceptance(self) -> None:
        value = request()
        row = receipt("authorized_member_route")
        row["checks"] = ["native_turn_completed"]
        value["receipts"] = [row]
        self.assertEqual(
            surfaces(evaluate(value))["authorized_member_route"]["state"], "unverified"
        )

    def test_expired_future_duplicate_or_missing_proof_does_not_verify(self) -> None:
        for changes in (
            {
                "observed_at": (NOW - timedelta(minutes=10)).isoformat(),
                "valid_until": (NOW - timedelta(minutes=1)).isoformat(),
            },
            {"observed_at": (NOW + timedelta(minutes=1)).isoformat()},
        ):
            value = request()
            row = receipt("shell_files")
            row.update(changes)
            value["receipts"] = [row]
            self.assertEqual(
                surfaces(evaluate(value))["shell_files"]["state"], "unverified"
            )
        value = request()
        value["receipts"] = [receipt("shell_files"), receipt("shell_files")]
        self.assertEqual(
            surfaces(evaluate(value))["shell_files"]["state"], "unverified"
        )
        value["receipts"] = [receipt("shell_files")]
        del value["receipts"][0]["evidence_sha256"]
        self.assertEqual(evaluate(value)["bounded_acceptance"], "refused_invalid_input")

    def test_browser_success_does_not_prove_outbound_enforcement(self) -> None:
        value = request()
        value["receipts"] = [receipt(surface) for surface in RESOURCES]
        report = evaluate(value)
        self.assertEqual(report["bounded_acceptance"], "attestations_complete")
        self.assertFalse(report["full_workspace_ready"])
        self.assertTrue(
            all(
                not row["approval_enforcement_evidenced"]
                for row in report["control_coverage"]
            )
        )
        self.assertIn("operator-attested", report["limitations"][0])

    def test_catalog_only_has_supported_unverified_state(self) -> None:
        value = request()
        row = receipt(
            "native_app_control",
            claim="supported_but_unverified",
            outcome="unknown",
            terminal=False,
            disposition="registered",
            evidence_kind="registration",
        )
        row["checks"] = []
        value["receipts"] = [row]
        self.assertEqual(
            surfaces(evaluate(value))["native_app_control"]["state"],
            "supported_but_unverified",
        )

    def test_malformed_input_is_bounded_and_never_echoed(self) -> None:
        for raw in (
            b'{"contract":1,"contract":2}',
            b"[" * 2000,
            b"\xff",
            b'{"schema_version":' + b"9" * 5000 + b"}",
        ):
            with self.subTest(length=len(raw)):
                self.assertEqual(
                    probe.evaluate_bytes(raw, now=NOW)["bounded_acceptance"],
                    "refused_invalid_input",
                )
        for field, replacement in (
            ("checks", ["invented"]),
            ("checks", [1]),
            ("evidence_sha256", "invalid"),
        ):
            value = request()
            row = receipt("shell_files")
            row[field] = replacement
            value["receipts"] = [row]
            self.assertEqual(
                evaluate(value)["bounded_acceptance"], "refused_invalid_input"
            )


if __name__ == "__main__":
    unittest.main()
