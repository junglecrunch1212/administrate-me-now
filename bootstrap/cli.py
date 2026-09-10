"""Non-programmer-safe command surface behind the stable shell entrypoints."""

from __future__ import annotations

import argparse
import dataclasses
import getpass
import json
import os
import secrets
import stat
import sys
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from bootstrap.backup_restore import BackupManager
from bootstrap.bridge_enrollment import (
    BridgeEnrollmentManager,
    MemberBridgeInstaller,
    MemberBridgePaths,
)
from bootstrap.contracts import DeploymentPaths, repository_root
from bootstrap.doctor import Doctor
from bootstrap.openclaw_activation import activate as activate_openclaw
from bootstrap.openclaw_cron import OpenClawCronReconciler
from bootstrap.secrets import reference_for, secret_store
from bootstrap.services import ServiceManager
from bootstrap.state import EncryptedStateStore
from bootstrap.upgrade import UpgradeManager
from bootstrap.wizard import BootstrapWizard


def _paths(value: str | None) -> DeploymentPaths:
    if value:
        production = os.environ.get("ADMINME_TEST_MODE") != "1"
        return DeploymentPaths.from_root(Path(value), production=production)
    environment = os.environ.get("ADMINME_INSTANCE_ROOT")
    if environment:
        production = os.environ.get("ADMINME_TEST_MODE") != "1"
        return DeploymentPaths.from_root(Path(environment), production=production)
    return DeploymentPaths.default()


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def _private_bridge_configuration(path: Path) -> dict[str, Any]:
    candidate = path.expanduser()
    info = candidate.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != int(getattr(os, "getuid", lambda: 0)())
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise RuntimeError("bridge configuration must be an owner-only regular file")
    value = yaml.safe_load(candidate.read_text("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("bridge configuration must contain one mapping")
    return value


def _private_transfer_code(path: Path) -> str:
    """Read a helper-provided code without placing the secret in argv."""

    candidate = path.expanduser()
    info = candidate.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != int(getattr(os, "getuid", lambda: 0)())
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise RuntimeError("bridge transfer code must be an owner-only regular file")
    value = candidate.read_text("utf-8").strip()
    if not value or len(value) > 240 or any(character.isspace() for character in value):
        raise RuntimeError("bridge transfer code file is invalid")
    return value


def _saved_answers(paths: DeploymentPaths, *, test_mode: bool) -> dict[str, Any]:
    """Load the original encrypted answers for an interrupted bootstrap."""

    store = secret_store(paths, test_mode=test_mode)
    reference = reference_for(paths, "bootstrap-state", test_mode=test_mode)
    state = EncryptedStateStore(paths.state_path, store.get(reference)).load()
    answers = state.get("answers")
    if not isinstance(answers, dict):
        raise RuntimeError(
            "the resumable bootstrap journal has no valid answer set; stop and restore the last verified backup"
        )
    return answers


def _reconcile_openclaw_cron(
    paths: DeploymentPaths,
    *,
    root: Path,
    test_mode: bool,
) -> dict[str, Any]:
    instance_path = paths.instance_dir / "config" / "instance.yaml"
    instance = yaml.safe_load(instance_path.read_text("utf-8")) or {}
    timezone_name = instance.get("timezone")
    if not isinstance(timezone_name, str):
        raise RuntimeError("instance configuration has no household timezone")
    references = json.loads(
        (paths.instance_dir / "config" / "secret-references.json").read_text("utf-8")
    )["references"]
    gateway_token = (
        secret_store(paths, test_mode=test_mode)
        .get(references["openclaw_shared"])
        .decode("ascii")
    )
    return OpenClawCronReconciler(
        paths,
        root=root,
        gateway_token=gateway_token,
        test_mode=test_mode,
    ).reconcile(timezone_name)


def _bootstrap_diagnostic_status(diagnostics: dict[str, Any]) -> tuple[str, int]:
    if diagnostics.get("status") == "healthy":
        return "healthy", 0
    failed = {
        check.get("name")
        for check in diagnostics.get("checks", [])
        if isinstance(check, dict) and check.get("ok") is False
    }
    enrollment_only = {
        "bootstrap_sections",
        "member_mapping_and_bridge_status",
    }
    if failed and failed <= enrollment_only:
        return "bridge_enrollment_required", 0
    return "failed", 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="administrateme-bootstrap")
    parser.add_argument("--instance-root", help="Explicit deployment root")
    subparsers = parser.add_subparsers(dest="command", required=True)
    bootstrap = subparsers.add_parser("bootstrap")
    bootstrap.add_argument("--dry-run", action="store_true")
    bootstrap.add_argument("--fixture", choices=("two-adult-one-child",))
    bootstrap.add_argument("--answers", type=Path)
    bootstrap.add_argument("--stop-after-section", type=int)
    bootstrap.add_argument("--fail-section", type=int)
    doctor = subparsers.add_parser("doctor")
    doctor.add_argument("--export", type=Path)
    doctor.add_argument("--no-synthetic-proof", action="store_true")
    reset_plan = subparsers.add_parser("test-reset-plan")
    reset_plan.add_argument("--candidate-commit", required=True)
    reset_plan.add_argument("--acquisition", type=Path, required=True)
    reset_plan.add_argument("--diagnostic-backup", type=Path, required=True)
    reset_plan.add_argument("--output", type=Path, required=True)
    reset_selection = subparsers.add_parser("test-reset-select")
    reset_selection.add_argument("--selection", type=Path, required=True)
    reset_discovery = subparsers.add_parser("test-reset-discover")
    reset_discovery.add_argument("--output", type=Path, required=True)
    for name in (
        "start",
        "stop",
        "status",
        "backup",
        "rehearse-restore",
        "restore",
        "upgrade",
        "rollback",
        "activate-openclaw",
        "bridge-export",
        "bridge-configure",
        "bridge-attest",
        "bridge-finalize",
        "bridge-revoke",
        "bridge-status",
        "bridge-preview",
        "bridge-accept",
        "bridge-complete",
        "bridge-disable",
        "bridge-uninstall",
        "bridge-local-status",
        "bridge-enable-pack",
    ):
        child = subparsers.add_parser(name)
        if name == "backup":
            child.add_argument("--output", type=Path)
        elif name == "rehearse-restore":
            child.add_argument("backup", type=Path)
            child.add_argument("candidate", type=Path)
            child.add_argument("--receipt", type=Path)
        elif name == "restore":
            child.add_argument("backup", type=Path)
        elif name == "upgrade":
            child.add_argument("candidate", type=Path)
            child.add_argument("--test-reset-manifest", type=Path)
            child.add_argument(
                "--pending-attestation-id",
                help=(
                    "consume one exact candidate-pending readiness artifact "
                    "during the journaled activation"
                ),
            )
            child.add_argument(
                "--approve-runtime-migration",
                action="store_true",
                help=("authorize a separately rehearsed Python/Node baseline cutover"),
            )
            child.add_argument(
                "--approve-declared-source-assessment-repair",
                action="store_true",
                help=("activate only the candidate-declared exact degraded baseline"),
            )
            child.add_argument(
                "--approve-provider-binding-migration",
                action="store_true",
                help=(
                    "apply only the provider-binding transition bound by "
                    "candidate-pending evidence"
                ),
            )
        elif name == "rollback":
            child.add_argument(
                "--recovery-repository",
                type=Path,
                help=(
                    "exact clean protected-main checkout used for fail-closed "
                    "rollback recovery verification"
                ),
            )
        elif name == "bridge-export":
            child.add_argument("member_id")
            child.add_argument("output", type=Path)
            child.add_argument("--central-url", required=True)
        elif name == "bridge-configure":
            child.add_argument("member_id")
            child.add_argument("configuration", type=Path)
        elif name == "bridge-attest":
            child.add_argument("member_id")
            child.add_argument("--pack", action="append", dest="packs")
        elif name in {"bridge-finalize", "bridge-revoke"}:
            child.add_argument("member_id")
        elif name in {"bridge-preview", "bridge-accept"}:
            child.add_argument("package", type=Path)
            child.add_argument("--bridge-root", type=Path)
            child.add_argument(
                "--transfer-code-file",
                type=Path,
                help="Owner-only file used by the reviewed macOS helper app",
            )
            if name == "bridge-preview":
                child.add_argument("--text", action="store_true")
        elif name == "bridge-enable-pack":
            child.add_argument("pack")
            child.add_argument("--bridge-root", type=Path)
        elif name in {
            "bridge-complete",
            "bridge-disable",
            "bridge-uninstall",
            "bridge-local-status",
        }:
            child.add_argument("--bridge-root", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = _paths(args.instance_root)
    test_mode = os.environ.get("ADMINME_TEST_MODE") == "1"
    root = Path(os.environ.get("ADMINME_REPOSITORY_ROOT", repository_root())).resolve()
    try:
        if args.command == "test-reset-plan":
            from bootstrap.contracts import atomic_write
            from bootstrap.disposable_test_reset import build_manifest, manifest_digest

            result = build_manifest(
                paths,
                candidate_commit=args.candidate_commit,
                acquisition=_private_bridge_configuration(args.acquisition),
                diagnostic_backup=args.diagnostic_backup,
            )
            if args.output.exists():
                raise RuntimeError("reset plan output already exists")
            atomic_write(args.output, json.dumps(result, indent=2).encode())
            _print(
                {
                    "status": "planned",
                    "manifest_sha256": manifest_digest(result),
                    "output": str(args.output),
                    "operational_changes": False,
                }
            )
            return 0
        if args.command == "bootstrap":
            fixture = args.fixture
            answers = None
            if args.answers:
                answers = json.loads(args.answers.read_text("utf-8"))
            elif paths.state_path.exists() and fixture is None:
                answers = _saved_answers(paths, test_mode=test_mode)
                print(
                    "Resuming the encrypted bootstrap with the original answers; no household questions will be asked again.",
                    file=sys.stderr,
                )
            elif fixture is None and args.dry_run:
                fixture = "two-adult-one-child"
            elif fixture is None:
                if not sys.stdin.isatty():
                    raise RuntimeError(
                        "interactive bootstrap needs a terminal; use --answers with a local non-secret file"
                    )
                answers = _interactive_answers()
            wizard = BootstrapWizard(
                paths,
                root=root,
                fixture=fixture,
                test_mode=test_mode,
                dry_run=args.dry_run,
                preflight_overrides={
                    "system": "Darwin",
                    "machine": "arm64",
                    "macos_version": "26.0",
                }
                if test_mode
                else None,
                answers=answers,
            )
            state = wizard.run(
                stop_after_section=args.stop_after_section,
                fail_section=args.fail_section,
            )
            complete = all(
                section["status"] == "complete"
                for section in state["sections"].values()
            )
            if not complete:
                _print(
                    {
                        "status": "resumable",
                        "sections": {
                            key: value["status"]
                            for key, value in state["sections"].items()
                        },
                    }
                )
                return 0
            manager = ServiceManager(paths, test_mode=test_mode)
            service = manager.start()
            try:
                cron = _reconcile_openclaw_cron(
                    paths,
                    root=root,
                    test_mode=test_mode,
                )
            except Exception:
                manager.stop()
                raise
            diagnostics = Doctor(paths, root=root, test_mode=test_mode).run()
            bootstrap_status, exit_code = _bootstrap_diagnostic_status(diagnostics)
            _print(
                {
                    "status": bootstrap_status,
                    "mode": "observation",
                    "services": service.public(),
                    "openclaw_cron": cron,
                    "doctor": diagnostics,
                }
            )
            return exit_code
        if args.command == "doctor":
            doctor = Doctor(paths, root=root, test_mode=test_mode)
            proof = not args.no_synthetic_proof
            result = (
                doctor.export(args.export, include_synthetic_proof=proof)
                if args.export
                else doctor.run(include_synthetic_proof=proof)
            )
            _print(result)
            return 0 if result["status"] == "healthy" else 1
        if args.command in {"start", "stop", "status"}:
            if (
                args.command == "start"
                and (paths.app_dir / "disposable-test-reset-active.json").exists()
            ):
                raise RuntimeError(
                    "interrupted disposable test reset requires its exact manifest"
                )
            manager = ServiceManager(paths, test_mode=test_mode)
            result = getattr(manager, args.command)()
            output: dict[str, Any] = result.public()
            if args.command == "start" and result.status not in {"failed", "degraded"}:
                output["openclaw_cron"] = _reconcile_openclaw_cron(
                    paths,
                    root=root,
                    test_mode=test_mode,
                )
            _print(output)
            return 0 if result.status not in {"failed", "degraded"} else 1
        if args.command == "activate-openclaw":
            result = activate_openclaw(paths, root=root, test_mode=test_mode)
            manager = ServiceManager(paths, test_mode=test_mode)
            manager.stop()
            service = manager.start()
            _print({"activation": result, "services": service.public()})
            return 0 if service.status == "healthy" else 1
        if args.command == "backup":
            _print(
                BackupManager(paths, root=root, test_mode=test_mode).create(args.output)
            )
            return 0
        if args.command == "rehearse-restore":
            _print(
                BackupManager(paths, root=root, test_mode=test_mode).rehearse(
                    args.backup,
                    candidate=args.candidate,
                    receipt=args.receipt,
                )
            )
            return 0
        if args.command in {
            "bridge-export",
            "bridge-configure",
            "bridge-attest",
            "bridge-finalize",
            "bridge-revoke",
            "bridge-status",
        }:
            enrollments = BridgeEnrollmentManager(
                paths,
                root=root,
                test_mode=test_mode,
            )
            if args.command == "bridge-export":
                exported = enrollments.export(
                    args.member_id,
                    args.output,
                    central_url=args.central_url,
                )
                print(
                    "ONE-TIME BRIDGE TRANSFER CODE (not stored; keep separate from the file):",
                    file=sys.stderr,
                )
                print(exported.transfer_code, file=sys.stderr)
                _print(exported.public)
            elif args.command == "bridge-configure":
                _print(
                    enrollments.configure(
                        args.member_id,
                        _private_bridge_configuration(args.configuration),
                    )
                )
            elif args.command == "bridge-attest":
                _print(enrollments.attest(args.member_id, args.packs))
            elif args.command == "bridge-finalize":
                _print(enrollments.finalize(args.member_id))
            elif args.command == "bridge-revoke":
                _print(enrollments.revoke(args.member_id))
            else:
                _print(enrollments.status())
            return 0
        if args.command in {
            "bridge-preview",
            "bridge-accept",
            "bridge-complete",
            "bridge-disable",
            "bridge-uninstall",
            "bridge-local-status",
            "bridge-enable-pack",
        }:
            bridge_paths = (
                MemberBridgePaths.from_root(
                    args.bridge_root,
                    production=not test_mode,
                )
                if args.bridge_root
                else MemberBridgePaths.default()
            )
            installer = MemberBridgeInstaller(
                bridge_paths,
                root=root,
                test_mode=test_mode,
            )
            if args.command in {"bridge-preview", "bridge-accept"}:
                code = (
                    _private_transfer_code(args.transfer_code_file)
                    if args.transfer_code_file
                    else getpass.getpass(
                        "Paste the one-time bridge transfer code (it will not display): "
                    )
                )
                try:
                    envelope = json.loads(args.package.read_text("utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    envelope = {}
                is_wizard = isinstance(envelope.get("signature"), str)
                if args.command == "bridge-preview":
                    if not is_wizard:
                        raise RuntimeError(
                            "the macOS helper requires a signed self-service package"
                        )
                    from adminme_bridge.enrollment_helper import (  # type: ignore[import-untyped]
                        MemberBridgeEnrollmentHelper,
                    )

                    helper = MemberBridgeEnrollmentHelper(
                        bridge_paths,
                        repository_root=root,
                        package_path=args.package,
                        transfer_code=code,
                        test_mode=test_mode,
                        installer=installer,
                    )
                    preview = helper.inspect()
                    if args.text:
                        print(
                            "\n".join(
                                (
                                    f"Instance: {preview.central_url}",
                                    f"Member: {preview.member_id}",
                                    f"Device: {preview.device_class.replace('_', ' ')}",
                                    "Read-only sources: "
                                    + ", ".join(preview.capability_labels),
                                    f"Reviewed release: {preview.release_commit[:12]}",
                                    f"Expires: {preview.expires_at}",
                                    "Provider writes: none",
                                )
                            )
                        )
                    else:
                        _print(dataclasses.asdict(preview))
                elif is_wizard:
                    from adminme_bridge.enrollment_helper import (  # type: ignore[import-untyped]
                        MemberBridgeEnrollmentHelper,
                    )

                    helper = MemberBridgeEnrollmentHelper(
                        bridge_paths,
                        repository_root=root,
                        package_path=args.package,
                        transfer_code=code,
                        test_mode=test_mode,
                        installer=installer,
                    )
                    helper.open_and_verify()
                    _print(helper.accept_locally(member_confirmed=True))
                else:
                    _print(installer.accept(args.package, code))
            elif args.command == "bridge-enable-pack":
                _print(installer.enable_pack(args.pack))
            elif args.command == "bridge-complete":
                from adminme_bridge.enrollment_helper import (  # type: ignore[import-untyped]
                    MemberBridgeEnrollmentHelper,
                )
                from adminme_bridge.enrollment_ui import run_macos_enrollment_wizard  # type: ignore[import-untyped]

                helper = MemberBridgeEnrollmentHelper.resume(
                    bridge_paths,
                    repository_root=root,
                    test_mode=test_mode,
                    installer=installer,
                )
                _print(run_macos_enrollment_wizard(helper))
            elif args.command == "bridge-uninstall":
                _print(installer.revoke_and_uninstall())
            elif args.command == "bridge-disable":
                _print(installer.disable())
            else:
                _print(installer.status())
            return 0
        if args.command == "restore":
            if (paths.app_dir / "disposable-test-reset-active.json").exists():
                raise RuntimeError(
                    "interrupted disposable test reset requires its exact manifest"
                )
            _print(
                BackupManager(paths, root=root, test_mode=test_mode).restore(
                    args.backup
                )
            )
            return 0
        upgrades = UpgradeManager(
            paths,
            root=root,
            test_mode=test_mode,
            approve_runtime_migration=(
                bool(args.approve_runtime_migration)
                if args.command == "upgrade"
                else False
            ),
            approve_source_assessment_repair=(
                bool(args.approve_declared_source_assessment_repair)
                if args.command == "upgrade"
                else False
            ),
            approve_provider_binding_migration=(
                bool(args.approve_provider_binding_migration)
                if args.command == "upgrade"
                else False
            ),
            pending_attestation_id=(
                args.pending_attestation_id if args.command == "upgrade" else None
            ),
            test_reset_manifest=(
                args.test_reset_manifest if args.command == "upgrade" else None
            ),
        )
        if args.command == "test-reset-select":
            from bootstrap.disposable_test_reset import select_messages

            _print(select_messages(upgrades, args.selection))
        elif args.command == "test-reset-discover":
            from bootstrap.disposable_test_reset import discover_messages

            _print(discover_messages(upgrades, args.output))
        elif args.command == "upgrade":
            _print(upgrades.activate(args.candidate))
        else:
            _print(upgrades.rollback(args.recovery_repository))
        return 0
    except Exception as exc:
        _print(
            {
                "status": "failed",
                "error": type(exc).__name__,
                "message": str(exc),
            }
        )
        return 1


def _yes_no(prompt: str, *, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    answer = input(f"{prompt} {suffix} ").strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes"}


def _choice(prompt: str, choices: set[str]) -> str:
    while True:
        answer = input(prompt).strip().lower()
        if answer in choices:
            return answer
        print(f"Choose one of: {', '.join(sorted(choices))}.")


def _local_timezone() -> str:
    configured = os.environ.get("TZ", "").strip()
    if configured:
        try:
            ZoneInfo(configured)
            return configured
        except ZoneInfoNotFoundError:
            pass
    try:
        resolved = Path("/etc/localtime").resolve()
        parts = resolved.parts
        marker = parts.index("zoneinfo")
        candidate = "/".join(parts[marker + 1 :])
        ZoneInfo(candidate)
        return candidate
    except (OSError, ValueError, ZoneInfoNotFoundError):
        return "Etc/UTC"


def _timezone_answer() -> str:
    detected = _local_timezone()
    while True:
        value = input(f"Household IANA timezone [{detected}]: ").strip() or detected
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            print("Use an IANA name such as Europe/London or America/Chicago.")
            continue
        return value


def _interactive_answers() -> dict[str, Any]:
    print("\nAdministrateMe setup questions")
    print("Keep the setup read-along open beside this Terminal.")
    print("STOP if any prompt asks for a password, API key, token, or OAuth code.")
    print("Provider sign-in happens later in OpenClaw or the provider's own window.")
    print("\n[Questions 1/6] Assistant and household devices")
    assistant_name = input("What should the household call the assistant? ").strip()
    assistant_name = assistant_name or "Household Assistant"
    apple = _yes_no("Does this household use Apple member devices?", default=True)
    household_timezone = _timezone_answer()
    print("\n[Questions 2/6] Household members and roles")
    print("principal = household administrator; child = restricted member;")
    print(
        "helper = limited helper; ambient = context only, with no interactive access."
    )
    while True:
        raw_count = input("How many household members should be enrolled? ").strip()
        try:
            count = int(raw_count)
        except ValueError:
            count = 0
        if 1 <= count <= 20:
            break
        print("Enter a number from 1 to 20.")
    members = []
    profiles: dict[str, str] = {}
    for index in range(1, count + 1):
        label = input(
            f"Member {index} label (stored only in encrypted state): "
        ).strip()
        label = label or f"Member {index}"
        role = _choice(
            "Role: principal, child, helper, or ambient? ",
            {"principal", "child", "helper", "ambient"},
        )
        member_id = f"member_{secrets.token_hex(6)}"
        member: dict[str, Any] = {"member_id": member_id, "role": role, "label": label}
        if apple:
            print(
                "Use the exact Tailscale user login (normally an email address), not a device name or .ts.net server address."
            )
            identity = input(
                "Verified Tailscale login for this member (leave blank to defer): "
            ).strip()
            if identity:
                member["tailscale_identity"] = identity
            else:
                print(
                    "  Tailscale identity deferred; portal chat and bridge enrollment "
                    "stay disabled for this member."
                )
        members.append(member)
        profiles[member_id] = (
            "kid_scoreboard"
            if role == "child"
            else "ambient_entity"
            if role == "ambient"
            else "adhd_executive"
        )
    if not any(member["role"] == "principal" for member in members):
        raise RuntimeError("at least one household member must be a principal")
    principal_ids = [
        member["member_id"] for member in members if member["role"] == "principal"
    ]
    coparents = (
        principal_ids
        if len(principal_ids) > 1
        and _yes_no("Should the enrolled principals be recorded as coparents?")
        else []
    )
    seed_names = (
        "places",
        "assets",
        "accounts",
        "recurrences",
        "providers",
        "schools",
        "projects",
        "vendors",
        "crm_relationships",
    )
    integration_names = (
        "shared_lists",
        "calendars",
        "contacts",
        "home_assistant",
    )
    openclaw_actor_identities = []
    member_chat_enabled_members: list[str] = []
    child_chat_enabled_members: list[str] = []
    print("\n[Questions 3/6] OpenClaw channel actors (optional during first install)")
    print("A channel actor is the exact sender identity shown by OpenClaw.")
    print(
        "If you do not know it yet, answer No. Do not guess; the Tailscale portal still works."
    )
    for member in members:
        if member["role"] not in {"principal", "child", "device"}:
            continue
        if not _yes_no(
            f"Pair an OpenClaw actor for {member['label']}?",
            default=False,
        ):
            continue
        channel_id = input("  Channel ID (for example imessage): ").strip()
        account_id = input("  Account ID (use - when absent): ").strip() or "-"
        actor_id = input("  Actor ID shown by OpenClaw: ").strip()
        if not channel_id or not actor_id:
            raise RuntimeError("channel ID and actor ID are required")
        openclaw_actor_identities.append(
            {
                "channel_id": channel_id,
                "account_id": account_id,
                "actor_id": actor_id,
                "member_id": member["member_id"],
            }
        )
    print("\n[Questions 4/6] Private member-portal chat")
    print(
        "This uses each member's Tailscale login and stays separate from channel pairing."
    )
    for member in members:
        if member["role"] not in {"principal", "child"}:
            continue
        if not member.get("tailscale_identity"):
            print(
                f"Portal chat for {member['label']} is deferred until a verified "
                "Tailscale login is added."
            )
            continue
        enabled = _yes_no(
            f"Enable isolated member-portal chat for {member['label']}?",
            default=member["role"] == "principal",
        )
        if not enabled:
            continue
        member_chat_enabled_members.append(member["member_id"])
        if member["role"] == "child" and _yes_no(
            f"Confirm the independently restricted child chat policy for {member['label']}?"
        ):
            child_chat_enabled_members.append(member["member_id"])
        elif member["role"] == "child":
            member_chat_enabled_members.remove(member["member_id"])
    if not openclaw_actor_identities and not member_chat_enabled_members:
        raise RuntimeError(
            "Enable portal chat for at least one principal or pair one exact "
            "OpenClaw channel actor; rerun the wizard and do not guess an identity"
        )
    print("\n[Questions 5/6] Optional setup follow-ups")
    print(
        "These answers prepare configuration or a follow-up; they do not connect a live provider now."
    )
    plaid_selected = _yes_no("Prepare Plaid sandbox Link?")
    seed = {
        name: _yes_no(
            f"Create an after-bootstrap data-entry follow-up for {name.replace('_', ' ')}?"
        )
        for name in seed_names
    }
    integrations = {
        name: _yes_no(f"Prepare {name.replace('_', ' ')} pairing?")
        for name in integration_names
    }
    print("\n[Questions 6/6] Member bridge options")
    enabled_bridge_members: list[str] = []
    if apple:
        print(
            "A member bridge is a separate daemon on a Mac signed into that member's Apple and Tailscale accounts."
        )
        for member in members:
            if member["role"] not in {"principal", "child", "helper"}:
                continue
            if not member.get("tailscale_identity"):
                print(
                    f"Member bridge setup for {member['label']} is deferred until "
                    "a verified Tailscale login is added."
                )
                continue
            if _yes_no(
                f"Prepare a separate member-bridge package for {member['label']}?"
            ):
                enabled_bridge_members.append(member["member_id"])
    obsidian = bool(enabled_bridge_members) and _yes_no(
        "Allow optional Obsidian for adult bridge packages?"
    )
    print(
        "\nObservation mode will suppress every external side effect during the trust period."
    )
    print(
        "It stays on for at least five full days (seven recommended) and time alone never turns it off."
    )
    observation_confirmed = _yes_no(
        "Confirm that observation mode must remain on until a principal completes the trust review?"
    )
    if not observation_confirmed:
        raise RuntimeError(
            "observation briefing was not confirmed; no installation sections were run"
        )
    return {
        "assistant": {
            "persona": "quiet_assistant",
            "identity": assistant_name,
            "observation_briefing_confirmed": True,
        },
        "household": {
            "kind": "apple" if apple else "non_apple",
            "timezone": household_timezone,
            "members": members,
            "ambient_members": [
                m["member_id"] for m in members if m["role"] == "ambient"
            ],
            "coparents": coparents,
            "helpers": [m["member_id"] for m in members if m["role"] == "helper"],
        },
        "profiles": profiles,
        "providers": {"assistant": "openclaw", "optional": []},
        "plaid": {
            "selected": plaid_selected,
            "environment": "sandbox",
            "fake_link": False,
        },
        "seed": seed,
        "integrations": {
            **integrations,
            "fixture_channel": False,
            "openclaw_actor_identities": openclaw_actor_identities,
            "member_chat_enabled_members": member_chat_enabled_members,
            "child_chat_enabled_members": child_chat_enabled_members,
        },
        "bridges": {
            "apple": apple,
            "obsidian": obsidian,
            "enabled_members": enabled_bridge_members,
        },
    }


if __name__ == "__main__":
    sys.exit(main())
