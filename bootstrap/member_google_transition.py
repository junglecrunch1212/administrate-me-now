"""Owner-scoped, crash-safe Google adapter publication without maintenance.

The authenticated member API records its encrypted authorization before invoking
this seam. Only that member's Google rows/selections may change. The existing
publication core binds the exact adapter bytes to unchanged physical readiness,
with an authenticated journal and the updater's lock. Credentials stay in the
secure store; this module neither resolves them nor contacts a provider.
"""

from __future__ import annotations

import copy
import hashlib
from typing import Any, Mapping

import yaml

from adminme.adapters.member_google_connections import (
    CONFIG_KEY,
    SCOPES,
    policy_from_config,
    selected_entries,
    validate_authorized_change,
)
from adminme.provider_bindings import validate_historical_provider_bindings
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
from bootstrap.bridge_readiness_transition import (
    BridgeReadinessTransition,
    BridgeReadinessTransitionError,
)
from bootstrap.candidate_readiness import (
    DeploymentSnapshot,
    _canonical,
    _json_no_duplicates,
    _read_private_file,
    capture_deployment_snapshot,
)
from bootstrap.contracts import DeploymentPaths

CONTRACT = "member-google-readiness-transition-v1"
JOURNAL_FILENAME = "member-google-readiness-transition.json"
_AUTHORITY_FIELDS = {
    "kind",
    "member_id",
    "actor_member_id",
    "tenant_id",
    "request_id",
    "proof_sha256",
}
_IDENTITY_FIELDS = {
    "member_id",
    "provider_account_id",
    "user_email",
    "workspace",
    "binding_provenance",
    "ownership_attested",
}

# Shared recovery failures deliberately retain the same exception hierarchy.
MemberGoogleTransitionError = BridgeReadinessTransitionError


def _owned_google(entry: Any, member_id: str) -> bool:
    return (
        isinstance(entry, dict)
        and str(entry.get("kind") or "").removeprefix("adapter:") in SCOPES
        and isinstance(entry.get("config"), dict)
        and entry["config"].get("member_id") == member_id
        and not entry["config"].get("service_principal_id")
    )


def google_change_digest(config: dict[str, Any], member_id: str) -> str:
    """Bind the full proposed member selection without a self-referential hash.

    Includes read-off rows, all resource selections and read/write ceilings.
    Each changed selection carries this digest as owner_authorization_sha256;
    the authorization event must bind the same proposed configuration.
    """
    policy = policy_from_config(config)
    selections = [
        row.model_dump(mode="json", exclude={"owner_authorization_sha256"})
        for row in (policy.selections if policy is not None else [])
        if row.member_id == member_id
    ]
    entries = [
        row for row in config.get("adapters", []) if _owned_google(row, member_id)
    ]
    return hashlib.sha256(
        _canonical(
            {
                "member_id": member_id,
                "adapters": sorted(entries, key=lambda row: str(row.get("id"))),
                "selections": sorted(selections, key=lambda row: row["adapter_id"]),
            }
        )
    ).hexdigest()


def _entry_index(config: dict[str, Any], member_id: str) -> dict[str, dict[str, Any]]:
    entries = config.get("adapters")
    if not isinstance(entries, list):
        raise MemberGoogleTransitionError("Google adapter inventory is invalid")
    result = {}
    for row in entries:
        if not _owned_google(row, member_id):
            continue
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("id"), str)
            or not row["id"]
            or row["id"] in result
        ):
            raise MemberGoogleTransitionError("Google adapter identity is ambiguous")
        result[row["id"]] = row
    return result


def _unowned_document(config: dict[str, Any], member_id: str) -> dict[str, Any]:
    document = copy.deepcopy(config)
    document["adapters"] = [
        row for row in document["adapters"] if not _owned_google(row, member_id)
    ]
    policy = document.get(CONFIG_KEY)
    if policy is not None:
        # Missing versus empty policy uses the model's identical safe defaults.
        parsed = policy_from_config(document)
        assert parsed is not None
        document[CONFIG_KEY] = parsed.model_dump(mode="json")
        document[CONFIG_KEY]["selections"] = [
            row
            for row in document[CONFIG_KEY]["selections"]
            if row["member_id"] != member_id
        ]
        if not document[CONFIG_KEY]["selections"]:
            document.pop(CONFIG_KEY)
    return document


class MemberGoogleReadinessTransition(BridgeReadinessTransition):
    """Commit only fixed adapters.json and its corresponding readiness binding."""

    contract = CONTRACT

    def __init__(self, paths: DeploymentPaths, encryption_key: bytes) -> None:
        super().__init__(paths, encryption_key)
        self.index_path = paths.instance_dir / "config" / "adapters.json"
        self.journal_path = paths.app_dir / JOURNAL_FILENAME

    def _snapshot(self, payload: bytes | None) -> DeploymentSnapshot:
        if payload is None:
            raise MemberGoogleTransitionError("Google adapter inventory is missing")
        return capture_deployment_snapshot(
            self.paths, adapters_payload_override=payload
        )

    def _validate_delta(
        self, previous: bytes | None, next_payload: bytes, authority: Mapping[str, Any]
    ) -> None:
        if (
            previous is None
            or set(authority) != _AUTHORITY_FIELDS
            or any(
                not isinstance(value, str) or not value for value in authority.values()
            )
            or authority.get("kind") != "member-google"
            or authority.get("actor_member_id") != authority.get("member_id")
        ):
            raise MemberGoogleTransitionError("invalid member Google authority")
        member_id = authority["member_id"]
        mapping = _json_no_duplicates(
            _read_private_file(
                self.paths.instance_dir / "config" / "member-mapping.json"
            )[0],
            label="member mapping",
        )
        members = mapping.get("members")
        matches = (
            [
                row
                for row in members
                if isinstance(row, dict) and row.get("member_id") == member_id
            ]
            if isinstance(members, list)
            else []
        )
        instance = yaml.safe_load(
            _read_private_file(self.paths.instance_dir / "config" / "instance.yaml")[0]
        )
        if (
            len(matches) != 1
            or matches[0].get("role") != "principal"
            or not isinstance(matches[0].get("tailscale_identity"), str)
            or not matches[0]["tailscale_identity"].strip()
            or not isinstance(instance, dict)
            or instance.get("tenant_id") != authority["tenant_id"]
        ):
            raise MemberGoogleTransitionError(
                "Google authority disagrees with household binding"
            )
        assert isinstance(members, list)
        before = _json_no_duplicates(previous, label="previous Google adapters")
        after = _json_no_duplicates(next_payload, label="next Google adapters")
        old, new = _entry_index(before, member_id), _entry_index(after, member_id)
        if old.keys() - new.keys():
            raise MemberGoogleTransitionError("Google history rows must be retained")
        if _unowned_document(before, member_id) != _unowned_document(after, member_id):
            raise MemberGoogleTransitionError(
                "Google mutation changes another owner or unrelated configuration"
            )
        if google_change_digest(after, member_id) != authority["proof_sha256"]:
            raise MemberGoogleTransitionError("Google authorization digest mismatch")
        try:
            validate_authorized_change(
                before,
                after,
                owner_member_id=member_id,
                owner_authorization_sha256=authority["proof_sha256"],
            )
            selected = selected_entries(after)
            # Retired rows retain ownership; disabling an account never makes it
            # claimable by a different member or the CoS service principal.
            accounts: dict[str, tuple[str, str, str]] = {}
            credentials: dict[str, tuple[str, str, str, str]] = {}
            for entry in after["adapters"]:
                if not isinstance(entry, dict):
                    raise ValueError("Google adapter inventory is invalid")
                cfg = entry.get("config") or {}
                if not isinstance(cfg, dict):
                    raise ValueError("Google adapter configuration is invalid")
                if (
                    str(entry.get("kind") or "").removeprefix("adapter:") not in SCOPES
                    or cfg.get("ownership_attested") is not True
                ):
                    continue
                account = str(cfg.get("provider_account_id") or "").casefold()
                owner = (
                    "member" if cfg.get("member_id") else "service_principal",
                    str(cfg.get("member_id") or cfg.get("service_principal_id") or ""),
                    str(cfg.get("workspace") or ""),
                )
                if account and accounts.setdefault(account, owner) != owner:
                    raise ValueError("Google account already has another owner")
                reference = cfg.get("oauth_credentials_ref")
                coordinate = (*owner, account)
                if (
                    isinstance(reference, str)
                    and credentials.setdefault(reference, coordinate) != coordinate
                ):
                    raise ValueError("Google credential is assigned to another account")
            for adapter_id, row in new.items():
                prior = old.get(adapter_id)
                if prior == row:
                    continue
                if not _owned_google(row, member_id):
                    raise ValueError("Google mutation exceeds member ownership")
                config = row["config"]
                if prior is not None and (
                    not _owned_google(prior, member_id)
                    or prior["kind"] != row["kind"]
                    or any(
                        prior["config"].get(key) != config.get(key)
                        for key in _IDENTITY_FIELDS
                    )
                ):
                    raise ValueError("Google historical ownership must be retained")
                if row.get("enabled") is not False and adapter_id not in selected:
                    raise ValueError("Google acquisition requires its exact selection")
                if adapter_id in selected and config != selected[adapter_id]["config"]:
                    raise ValueError("Google adapter differs from selected permission")
                reference = config.get("oauth_credentials_ref")
                if (
                    prior is None
                    or reference != prior["config"].get("oauth_credentials_ref")
                ) and (
                    not isinstance(reference, str)
                    or not reference.startswith(
                        f"keychain://{self.paths.keychain_account()}/"
                    )
                    or not reference.rsplit("/", 1)[-1]
                    or reference.count("/") != 3
                ):
                    raise ValueError("Google credential belongs to another instance")
            validate_historical_provider_bindings(
                after,
                member_roles={
                    row["member_id"]: row.get("role", "")
                    for row in members
                    if isinstance(row, dict) and isinstance(row.get("member_id"), str)
                },
                household_mapping=mapping,
                service_principal_ids={COS_SERVICE_PRINCIPAL_ID},
            )
        except (ValueError, TypeError, KeyError) as exc:
            raise MemberGoogleTransitionError(str(exc)) from exc
