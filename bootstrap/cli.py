"""Non-programmer-safe command surface behind the stable shell entrypoints."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any

from bootstrap.backup_restore import BackupManager
from bootstrap.contracts import DeploymentPaths, repository_root
from bootstrap.doctor import Doctor
from bootstrap.services import ServiceManager
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
    for name in ("start", "stop", "status", "backup", "restore", "upgrade", "rollback"):
        child = subparsers.add_parser(name)
        if name == "backup":
            child.add_argument("--output", type=Path)
        elif name == "restore":
            child.add_argument("backup", type=Path)
        elif name == "upgrade":
            child.add_argument("candidate", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = _paths(args.instance_root)
    test_mode = os.environ.get("ADMINME_TEST_MODE") == "1"
    root = Path(os.environ.get("ADMINME_REPOSITORY_ROOT", repository_root())).resolve()
    try:
        if args.command == "bootstrap":
            fixture = args.fixture
            answers = None
            if args.answers:
                answers = json.loads(args.answers.read_text("utf-8"))
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
            service = ServiceManager(paths, test_mode=test_mode).start()
            diagnostics = Doctor(paths, root=root, test_mode=test_mode).run()
            _print(
                {
                    "status": diagnostics["status"],
                    "mode": "observation",
                    "services": service.public(),
                    "doctor": diagnostics,
                }
            )
            return 0 if diagnostics["status"] == "healthy" else 1
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
            manager = ServiceManager(paths, test_mode=test_mode)
            result = getattr(manager, args.command)()
            _print(result.public())
            return 0 if result.status not in {"failed", "degraded"} else 1
        if args.command == "backup":
            _print(
                BackupManager(paths, root=root, test_mode=test_mode).create(args.output)
            )
            return 0
        if args.command == "restore":
            _print(
                BackupManager(paths, root=root, test_mode=test_mode).restore(
                    args.backup
                )
            )
            return 0
        upgrades = UpgradeManager(paths, root=root, test_mode=test_mode)
        if args.command == "upgrade":
            _print(upgrades.activate(args.candidate))
        else:
            _print(upgrades.rollback())
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


def _interactive_answers() -> dict[str, Any]:
    print("Do not paste passwords, tokens, or OAuth codes into this wizard.")
    print("Provider sign-in happens later in the provider's own window.")
    assistant_name = input("What should the household call the assistant? ").strip()
    assistant_name = assistant_name or "Household Assistant"
    apple = _yes_no("Does this household use Apple member devices?", default=True)
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
        role = input("Role: principal, child, helper, or ambient? ").strip().lower()
        if role not in {"principal", "child", "helper", "ambient"}:
            raise RuntimeError(f"unsupported role: {role}")
        member_id = f"member_{secrets.token_hex(6)}"
        member: dict[str, Any] = {"member_id": member_id, "role": role, "label": label}
        if apple:
            identity = input(
                "Verified Tailscale login for this member (for example member@domain): "
            ).strip()
            if not identity:
                raise RuntimeError(
                    "Apple bridge enrollment requires a Tailscale identity"
                )
            member["tailscale_identity"] = identity
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
    print("Map OpenClaw actors explicitly to the household members shown by label.")
    for member in members:
        if member["role"] not in {"principal", "child", "device"}:
            continue
        if not _yes_no(
            f"Pair an OpenClaw actor for {member['label']}?",
            default=member["role"] == "principal",
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
    if not openclaw_actor_identities:
        raise RuntimeError("at least one OpenClaw actor mapping is required")
    return {
        "assistant": {"persona": "quiet_assistant", "identity": assistant_name},
        "household": {
            "kind": "apple" if apple else "non_apple",
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
            "selected": _yes_no("Prepare Plaid sandbox Link?"),
            "environment": "sandbox",
            "fake_link": False,
        },
        "seed": {
            name: _yes_no(f"Seed {name.replace('_', ' ')} now?") for name in seed_names
        },
        "integrations": {
            **{
                name: _yes_no(f"Prepare {name.replace('_', ' ')} pairing?")
                for name in integration_names
            },
            "fixture_channel": False,
            "openclaw_actor_identities": openclaw_actor_identities,
        },
        "bridges": {
            "apple": apple,
            "obsidian": apple and _yes_no("Allow optional Obsidian for adult bridges?"),
        },
    }


if __name__ == "__main__":
    sys.exit(main())
