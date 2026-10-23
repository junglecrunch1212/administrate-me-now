"""Prepare one reviewed CoS Reminders list for existing guarded activation.

Run the protected setup controller with private exact source/list/device evidence.
This reads the selected source. Only --request-permission opens OS consent;
no command changes provider data.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import stat
from pathlib import Path
from typing import Any, Callable

from adminme.adapters.cos_apple_reminders import (
    ADAPTER_KIND,
    CONFIG_KEY,
    CoSRemindersLookup,
    CoSRemindersPolicy,
    CoSRemindersSelection,
    NativeHelperBinding,
    local_device_identity,
    policy_from_config,
    selected_entries,
    verify_device,
)
from adminme.adapters.cos_reminders_helper import HelperRemindersReader, invoke


def prepare_configuration(
    previous: dict[str, Any], selection: CoSRemindersSelection
) -> dict[str, Any]:
    current = policy_from_config(previous)
    if current and current.selection and current.selection != selection:
        raise ValueError("source migration requires an explicit reviewed replacement")
    requested = copy.deepcopy(previous)
    entry = {
        "id": selection.adapter_id,
        "kind": ADAPTER_KIND,
        "enabled": True,
        "config": selection.adapter_config(),
    }
    existing = [
        row
        for row in requested.get("adapters", [])
        if row.get("id") == selection.adapter_id
    ]
    if existing and existing != [entry]:
        raise ValueError("cannot replace an existing adapter or override its pause")
    if not existing:
        requested["adapters"].append(entry)
    requested[CONFIG_KEY] = CoSRemindersPolicy(selection=selection).model_dump(
        mode="json"
    )
    selected_entries(requested)
    return requested


def read_back(
    selection: CoSRemindersSelection,
    *,
    reader_factory: Callable[[], Any] = HelperRemindersReader,
    identity: Callable[[], tuple[str, int]] = local_device_identity,
) -> dict[str, Any]:
    verify_device(selection, identity())
    snapshot = reader_factory().read(selection)
    if (
        snapshot.source_id != selection.source_id
        or snapshot.list_id != selection.list_id
        or snapshot.list_name is None
        or any(item.list_identifier != selection.list_id for item in snapshot.items)
    ):
        raise ValueError("selected existing Reminders list could not be verified")
    return {
        "device_verified": True,
        "native_source_verified": True,
        "selected_list_verified": True,
        "permission_verified_for_native_helper": True,
        "native_helper_artifact_sha256": selection.native_helper.artifact_sha256,
        "item_count": len(snapshot.items),
        "provider_write_count": 0,
    }


def inspect_selection(
    lookup: CoSRemindersLookup,
    *,
    native_helper: NativeHelperBinding | None = None,
    release_root: Path | None = None,
    reader_factory: Callable[[], Any] | None = None,
    identity: Callable[[], tuple[str, int]] = local_device_identity,
) -> dict[str, Any]:
    device_digest, uid = identity()
    if reader_factory is not None:
        resource = reader_factory().inspect_selection(lookup)
    else:
        if native_helper is None:
            raise ValueError("inspection requires the reviewed native helper receipt")
        resource = HelperRemindersReader(release_root=release_root).inspect_selection(
            lookup, native_helper
        )
    return {
        "contract": "cos-apple-reminders-inspection-v1",
        "requires_source_ownership_review": True,
        "resource": resource,
        "native_helper": native_helper.model_dump() if native_helper else None,
        "device_identity_sha256": device_digest,
        "os_uid": uid,
        "provider_write_count": 0,
    }


def _private_json(path: Path) -> dict[str, Any]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
        info = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & 0o077
            or info.st_size > 4 * 1024 * 1024
        ):
            raise ValueError("inputs must be owner-only regular files")
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("inputs must be JSON objects")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--selection", type=Path)
    mode.add_argument(
        "--status",
        action="store_true",
        help="Native app permission status; never prompts",
    )
    mode.add_argument(
        "--inspect",
        type=Path,
        help="Private exact-title/type metadata request; no content or activation",
    )
    parser.add_argument("--previous-config", type=Path)
    parser.add_argument("--helper-receipt", type=Path)
    parser.add_argument(
        "--runtime-release",
        type=Path,
        help="Compatible protected release used by the native app before central activation",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--request-permission",
        action="store_true",
        help="Explicitly request Reminders access for the installed native app",
    )
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        raise ValueError("output must be a new private preparation file")
    binding: NativeHelperBinding | None = None
    if args.status or args.inspect:
        if args.helper_receipt is None:
            raise ValueError("native setup requires --helper-receipt")
        binding = NativeHelperBinding.model_validate(_private_json(args.helper_receipt))
    if args.status:
        assert binding is not None
        if args.request_permission or args.previous_config:
            raise ValueError("status never prompts or prepares configuration")
        prepared = invoke(binding, "status", {}, release_root=args.runtime_release)
        evidence = {"status": "native_permission_status", **prepared}
    elif args.inspect:
        assert binding is not None
        if args.previous_config:
            raise ValueError(
                "metadata inspection does not prepare adapter configuration"
            )
        lookup = CoSRemindersLookup.model_validate(_private_json(args.inspect))
        local_device_identity()  # Confirm this is a macOS work-user process before consent.
        if args.request_permission:
            invoke(binding, "request", {}, release_root=args.runtime_release)
        prepared = inspect_selection(
            lookup, native_helper=binding, release_root=args.runtime_release
        )
        evidence = {
            "status": "inspected_not_authorized",
            "matched_list_count": 1,
            "provider_write_count": 0,
        }
    else:
        if args.previous_config is None:
            raise ValueError("preparation requires previous configuration")
        selection = CoSRemindersSelection.model_validate(_private_json(args.selection))
        prepared = prepare_configuration(_private_json(args.previous_config), selection)
        if args.request_permission:
            verify_device(selection, local_device_identity())
            invoke(
                selection.native_helper,
                "request",
                {},
                release_root=args.runtime_release,
            )
        evidence = {
            "status": "prepared_not_activated",
            **read_back(
                selection,
                reader_factory=lambda: HelperRemindersReader(
                    release_root=args.runtime_release
                ),
            ),
        }
    descriptor = os.open(
        args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(prepared, handle, sort_keys=True)
        handle.write("\n")
    print(json.dumps(evidence, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            json.dumps(
                {"status": "failed_not_activated", "error_type": type(exc).__name__}
            )
        )
        raise SystemExit(1) from None
