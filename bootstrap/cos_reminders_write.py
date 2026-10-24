"""Inspect or prepare the separate CoS Reminders writer without provider changes.

Preparation preserves the selected reader and all other configuration. It does
not activate writing. Only --request-permission may open native consent, for the
exact verified version-two app. Disabling does not need live OS access.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
from typing import Any

from adminme.adapters.cos_apple_reminders import (
    NativeHelperBinding,
    local_device_identity,
    policy_from_config as read_policy_from_config,
    verify_device,
)
from adminme.adapters.cos_reminders_helper import (
    HelperRemindersWriter,
    NativeHelperError,
    invoke,
)
from adminme.adapters.cos_reminders_write import selected_source_sha256
from adminme.adapters.cos_reminders_write_policy import (
    CONFIG_KEY,
    CoSRemindersWritePolicy,
    policy_from_config,
    validate_authorized_change,
    write_selection,
)
from bootstrap.cos_apple_reminders import _private_json
from bootstrap.cos_reminders_helper_app import (
    HELPER_WRITE_APP_CONTRACT,
    verify_helper_app,
)


def prepare_configuration(
    previous: dict[str, Any],
    *,
    native_helper: NativeHelperBinding | None,
    owner_authorization_sha256: str,
    enabled: bool,
) -> dict[str, Any]:
    """Pure configuration preparation; guarded activation remains separate."""
    read_policy = read_policy_from_config(previous)
    selected = read_policy.selection if read_policy else None
    if selected is None:
        raise ValueError("An existing reviewed CoS reminder read source is required")
    old = policy_from_config(previous)
    if not enabled:
        if old is None:
            raise ValueError("No configured CoS reminder writer can be disabled")
        native_helper = old.native_helper
    if native_helper is None:
        raise ValueError("The exact write-helper receipt is required")
    requested = copy.deepcopy(previous)
    requested[CONFIG_KEY] = CoSRemindersWritePolicy(
        enabled=enabled,
        native_helper=native_helper,
        selection_sha256=selected_source_sha256(selected),
        owner_authorization_sha256=owner_authorization_sha256,
    ).model_dump(mode="json")
    validate_authorized_change(
        previous,
        requested,
        owner_authorization_sha256=owner_authorization_sha256,
    )
    if enabled:
        write_selection(requested)
    return requested


def verify_write_component(binding: NativeHelperBinding) -> None:
    if verify_helper_app(Path(binding.app_path)) != binding.model_dump():
        raise PermissionError("Write-helper receipt differs from the installed app")
    manifest = json.loads(
        (
            Path(binding.app_path) / "Contents/Resources/helper-manifest.json"
        ).read_bytes()
    )
    if manifest.get("contract") != HELPER_WRITE_APP_CONTRACT:
        raise PermissionError("The selected component only supports reads")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--inspect", action="store_true")
    mode.add_argument("--enable", action="store_true")
    mode.add_argument("--disable", action="store_true")
    parser.add_argument("--previous-config", required=True, type=Path)
    parser.add_argument("--helper-receipt", type=Path)
    parser.add_argument("--owner-authorization-sha256")
    parser.add_argument("--runtime-release", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--request-permission", action="store_true")
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        raise ValueError("Output must be a new private preparation file")
    previous = _private_json(args.previous_config)
    binding = (
        NativeHelperBinding.model_validate(_private_json(args.helper_receipt))
        if args.helper_receipt
        else None
    )
    if args.disable and (binding is not None or args.request_permission):
        raise ValueError("Disabling does not inspect, replace or prompt for a helper")
    if args.inspect:
        read_policy = read_policy_from_config(previous)
        if read_policy is None or read_policy.selection is None or binding is None:
            raise ValueError(
                "Inspection requires an existing source and helper receipt"
            )
        selected = read_policy.selection.model_copy(update={"native_helper": binding})
        prepared: dict[str, Any] = {}
    else:
        prepared = prepare_configuration(
            previous,
            native_helper=binding,
            owner_authorization_sha256=args.owner_authorization_sha256,
            enabled=args.enable,
        )
        selected = write_selection(prepared) if args.enable else None
    evidence: dict[str, Any] = {
        "status": "inspected_not_activated"
        if args.inspect
        else "prepared_not_activated",
        "provider_write_count": 0,
    }
    if selected is not None:
        verify_write_component(selected.native_helper)
        verify_device(selected, local_device_identity())
        if args.request_permission:
            invoke(
                selected.native_helper, "request", {}, release_root=args.runtime_release
            )
        result = HelperRemindersWriter(release_root=args.runtime_release).inspect(
            selected
        )
        evidence.update(
            native_permission_verified=True,
            exact_selected_list_writable=result.writable,
            native_helper_artifact_sha256=selected.native_helper.artifact_sha256,
        )
        if args.inspect:
            prepared = {**evidence, "source": result.model_dump(mode="json")}
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
                {
                    "status": "failed_not_activated",
                    "error_type": type(exc).__name__,
                    **(
                        {"error_code": exc.code}
                        if isinstance(exc, NativeHelperError)
                        else {}
                    ),
                }
            )
        )
        raise SystemExit(1) from None
