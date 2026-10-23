"""Read back an exact CoS Google source and prepare its guarded activation input.

Run only the deployed helper with authorized private inputs. No provider writes,
credential replacement or active-configuration mutation occurs in this command.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import stat
from pathlib import Path
from typing import Any, Callable

from adminme.adapters.cos_google_workspace import (
    CONFIG_KEY,
    PURPOSES,
    CoSGooglePolicy,
    CoSGoogleSelection,
    grant_scopes,
    policy_from_config,
    selected_entries,
    verify_source_resource,
)
from adminme.adapters.credentials import (
    assert_service_oauth_binding,
    resolve_json_credentials,
)
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
from bootstrap.google_oauth import _assert_exact_grant, _resolve_workspace_account_id


def _credentials(document: dict[str, Any], purpose: str) -> Any:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    credentials = Credentials(
        token=None,
        refresh_token=document["refresh_token"],
        token_uri=document.get("token_uri") or "https://oauth2.googleapis.com/token",
        client_id=document["client_id"],
        client_secret=document["client_secret"],
        scopes=list(grant_scopes(purpose)),
    )
    credentials.refresh(Request())
    _assert_exact_grant(credentials, grant_scopes(purpose))
    return credentials


def read_back(
    selection: CoSGoogleSelection,
    *,
    resolver: Callable[[str], dict[str, Any]] = resolve_json_credentials,
    credential_factory: Callable[[dict[str, Any], str], Any] = _credentials,
    account_resolver: Callable[[object], str] = _resolve_workspace_account_id,
    service_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Refresh, prove the account, then read only the selected existing resource."""
    if service_factory is None:
        from googleapiclient.discovery import build

        service_factory = build
    document = resolver(selection.credential_reference)
    assert_service_oauth_binding(
        document,
        service_principal_id=COS_SERVICE_PRINCIPAL_ID,
        provider_account_id=selection.provider_account_id,
        required_scopes=[PURPOSES[selection.purpose][2]],
        purpose=selection.purpose,
    )
    credentials = credential_factory(document, selection.purpose)
    if account_resolver(credentials) != selection.provider_account_id:
        raise ValueError("current Google account does not match the CoS source")
    if selection.purpose == "calendar-read":
        service = service_factory(
            "calendar", "v3", credentials=credentials, cache_discovery=False
        )
        verify_source_resource(
            service, purpose=selection.purpose, resource_id=selection.resource_id
        )
        sample = (
            service.events()
            .list(calendarId=selection.resource_id, maxResults=1)
            .execute()
        )
    else:
        service = service_factory(
            "tasks", "v1", credentials=credentials, cache_discovery=False
        )
        verify_source_resource(
            service, purpose=selection.purpose, resource_id=selection.resource_id
        )
        sample = (
            service.tasks().list(tasklist=selection.resource_id, maxResults=1).execute()
        )
    if not isinstance(sample, dict) or not isinstance(sample.get("items", []), list):
        raise ValueError("Google did not return a valid source read-back")
    return {
        "purpose": selection.purpose,
        "account_verified": True,
        "resource_verified": True,
        "source_read_back": True,
        "sample_item_count": len(sample.get("items", [])),
        "provider_write_count": 0,
    }


def prepare_configuration(
    previous: dict[str, Any], selection: CoSGoogleSelection
) -> dict[str, Any]:
    """Add one exact source; preserve every existing member/mail entry and grant."""
    requested = copy.deepcopy(previous)
    current = policy_from_config(previous)
    rows = list(current.selections) if current else []
    matches = [row for row in rows if row.purpose == selection.purpose]
    if matches and matches != [selection]:
        raise ValueError(
            "existing CoS Google purpose requires an explicit change request"
        )
    if not matches:
        rows.append(selection)
    entry = {
        "id": selection.adapter_id,
        "kind": PURPOSES[selection.purpose][0],
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
    requested[CONFIG_KEY] = CoSGooglePolicy(selections=rows).model_dump(mode="json")
    selected_entries(requested)
    return requested


def _private_json(path: Path) -> dict[str, Any]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError("setup inputs must be private regular files")
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("setup inputs must be JSON objects")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--previous-config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    selection = CoSGoogleSelection.model_validate(_private_json(args.selection))
    prepared = prepare_configuration(_private_json(args.previous_config), selection)
    if args.output.exists() or args.output.is_symlink():
        raise ValueError("output must be a new private preparation file")
    evidence = read_back(selection)
    descriptor = os.open(
        args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(prepared, handle, sort_keys=True)
        handle.write("\n")
    print(json.dumps({"status": "prepared_not_activated", **evidence}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Provider exceptions and validation inputs can contain private coordinates.
        print(
            json.dumps(
                {"status": "failed_not_activated", "error_type": type(exc).__name__}
            )
        )
        raise SystemExit(1) from None
