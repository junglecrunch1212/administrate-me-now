"""Member Google config commits and crash recovery on disposable hosted roots."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from adminme.adapters.member_google_connections import (
    CONFIG_KEY,
    binding_digest,
    connection_config,
)
from bootstrap.candidate_readiness import (
    CandidateReadinessError,
    capture_deployment_snapshot,
    deployment_operation_lock,
)
from bootstrap.contracts import DeploymentPaths
from bootstrap.cos_channel_activation import prepare_configuration
from bootstrap.member_google_transition import (
    MemberGoogleReadinessTransition,
    MemberGoogleTransitionError,
    google_change_digest,
)
from tests.acceptance.test_member_google_connections import (
    MEMBER_A,
    MEMBER_B,
    google_configuration,
)
from tests.unit.bootstrap.test_candidate_readiness import _deployment, _private_write

KEY = b"g" * 32


def _bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def _binding(paths: DeploymentPaths) -> dict[str, str]:
    snapshot = capture_deployment_snapshot(paths)
    return {
        "release_commit": snapshot.predecessor_commit,
        "instance_fingerprint": snapshot.instance_fingerprint,
        "host_fingerprint": snapshot.host_fingerprint,
        "configuration_fingerprint": snapshot.configuration_fingerprint,
    }


def _fixture(tmp_path: Path) -> tuple[DeploymentPaths, MemberGoogleReadinessTransition]:
    paths = _deployment(tmp_path)
    mapping = {
        "members": [
            {
                "member_id": MEMBER_A,
                "role": "principal",
                "tailscale_identity": "member-a.ts.net",
            },
            {
                "member_id": MEMBER_B,
                "role": "principal",
                "tailscale_identity": "member-b.ts.net",
            },
        ]
    }
    requested = google_configuration(tmp_path)
    config = prepare_configuration(requested, requested, enable_live=False)
    _private_write(paths.instance_dir / "config/member-mapping.json", _bytes(mapping))
    _private_write(paths.instance_dir / "config/adapters.json", _bytes(config))
    evidence = {
        "contract": "deployment-readiness-v1",
        "deployment_binding": _binding(paths),
        "recovery": {
            "tested_at": "2026-08-01T12:05:00Z",
            "filevault_unlock_tested": True,
        },
        "identity_acceptance": {
            "tested_at": "2026-08-01T12:06:00Z",
            "member_ids": [MEMBER_A],
        },
        "provider_effect_window": {"started_at": "2026-08-01T12:07:00Z"},
    }
    _private_write(
        paths.instance_dir / "config/deployment-readiness.json", _bytes(evidence)
    )
    return paths, MemberGoogleReadinessTransition(paths, KEY)


def _authorize(config: dict[str, Any], member_id: str = MEMBER_A) -> dict[str, str]:
    for selection in config[CONFIG_KEY]["selections"]:
        if selection["member_id"] != member_id:
            continue
        entry = next(
            row for row in config["adapters"] if row["id"] == selection["adapter_id"]
        )
        entry["config"] = connection_config(entry, access=selection["access"])
        selection["binding_sha256"] = binding_digest(entry, access=selection["access"])
    digest = google_change_digest(config, member_id)
    for selection in config[CONFIG_KEY]["selections"]:
        if selection["member_id"] == member_id:
            selection["owner_authorization_sha256"] = digest
    assert google_change_digest(config, member_id) == digest
    return {
        "kind": "member-google",
        "member_id": member_id,
        "actor_member_id": member_id,
        "tenant_id": "tenant_fixture_pending",
        "request_id": "fixture-google-request",
        "proof_sha256": digest,
    }


def _candidate(
    transition: MemberGoogleReadinessTransition,
) -> tuple[bytes, dict[str, Any]]:
    previous = transition.index_path.read_bytes()
    candidate = json.loads(previous)
    for selection in candidate[CONFIG_KEY]["selections"]:
        if selection["adapter_id"] == "fixture-personal-calendar":
            selection["access"] = "read_write"
    return previous, candidate


def _commit(transition: MemberGoogleReadinessTransition) -> dict[str, Any]:
    previous, candidate = _candidate(transition)
    authority = _authorize(candidate)
    return transition.commit(
        previous_payload=previous, next_payload=_bytes(candidate), authority=authority
    )


def test_owner_google_write_upgrade_preserves_physical_proof_and_other_services(
    tmp_path: Path,
) -> None:
    paths, transition = _fixture(tmp_path)
    previous = json.loads(transition.index_path.read_bytes())
    original = json.loads(transition.readiness_path.read_bytes())
    committed = _commit(transition)
    current = json.loads(transition.index_path.read_bytes())
    assert committed == {**original, "deployment_binding": _binding(paths)}
    assert current["service_identities"] == previous["service_identities"]
    assert current["cos_channel_acquisition"] == previous["cos_channel_acquisition"]
    assert (
        current["disposable_test_acquisition"]
        == previous["disposable_test_acquisition"]
    )
    assert next(
        row for row in current["adapters"] if row["id"] == "fixture-cos-gmail"
    ) == next(row for row in previous["adapters"] if row["id"] == "fixture-cos-gmail")
    assert not transition.pending
    payload = transition.index_path.read_bytes()
    authority = _authorize(current)
    assert (
        transition.commit(
            previous_payload=payload, next_payload=_bytes(current), authority=authority
        )
        == committed
    )


def test_read_off_retains_prior_history_and_other_account_can_be_added(
    tmp_path: Path,
) -> None:
    paths, transition = _fixture(tmp_path)
    before, candidate = _candidate(transition)
    for row in candidate["adapters"]:
        if row["id"] == "fixture-personal-calendar":
            row["enabled"] = False
    authority = _authorize(candidate)
    transition.commit(
        previous_payload=before, next_payload=_bytes(candidate), authority=authority
    )
    previous = transition.index_path.read_bytes()
    candidate = json.loads(previous)
    template = next(
        row for row in candidate["adapters"] if row["id"] == "fixture-personal-calendar"
    )
    extra = copy.deepcopy(template)
    extra.update(id="fixture-second-calendar", enabled=True)
    extra["config"].update(
        provider_account_id="second-fixture@example.invalid",
        user_email="second-fixture@example.invalid",
        oauth_credentials_ref=f"keychain://{paths.keychain_account()}/google-second-grant",
    )
    candidate["adapters"].append(extra)
    selection = copy.deepcopy(
        next(
            row
            for row in candidate[CONFIG_KEY]["selections"]
            if row["adapter_id"] == template["id"]
        )
    )
    selection.update(
        adapter_id=extra["id"],
        provider_account_id=extra["config"]["provider_account_id"],
    )
    candidate[CONFIG_KEY]["selections"].append(selection)
    authority = _authorize(candidate)
    transition.commit(
        previous_payload=previous, next_payload=_bytes(candidate), authority=authority
    )
    result = json.loads(transition.index_path.read_bytes())
    assert (
        next(row for row in result["adapters"] if row["id"] == template["id"])[
            "enabled"
        ]
        is False
    )
    assert len(result["adapters"]) == len(json.loads(before)["adapters"]) + 1


@pytest.mark.parametrize(
    "tamper",
    [
        "actor",
        "tenant",
        "digest",
        "other_owner",
        "service",
        "topology",
        "history_delete",
        "history_account",
        "credential_instance",
        "unselected",
        "foreign_selection",
        "cross_member_account",
    ],
)
def test_unauthorized_delta_never_changes_durable_config(
    tmp_path: Path, tamper: str
) -> None:
    paths, transition = _fixture(tmp_path)
    previous, candidate = _candidate(transition)
    authority = _authorize(candidate)
    calendar = next(
        row for row in candidate["adapters"] if row["id"] == "fixture-personal-calendar"
    )
    if tamper == "actor":
        authority["actor_member_id"] = MEMBER_B
    elif tamper == "tenant":
        authority["tenant_id"] = "tenant_other_fixture"
    elif tamper == "digest":
        authority["proof_sha256"] = "0" * 64
    elif tamper == "other_owner":
        candidate["adapters"][-1]["enabled"] = True
    elif tamper == "service":
        candidate["adapters"][1]["enabled"] = False
    elif tamper == "topology":
        candidate["member_chat_enabled_members"] = [MEMBER_B]
    elif tamper == "history_delete":
        candidate["adapters"].remove(calendar)
        candidate[CONFIG_KEY]["selections"] = [
            row
            for row in candidate[CONFIG_KEY]["selections"]
            if row["adapter_id"] != calendar["id"]
        ]
        authority = _authorize(candidate)
    elif tamper == "history_account":
        calendar["config"].update(
            provider_account_id="swapped-fixture@example.invalid",
            user_email="swapped-fixture@example.invalid",
        )
        next(
            row
            for row in candidate[CONFIG_KEY]["selections"]
            if row["adapter_id"] == calendar["id"]
        )["provider_account_id"] = "swapped-fixture@example.invalid"
        authority = _authorize(candidate)
    elif tamper == "credential_instance":
        calendar["config"]["oauth_credentials_ref"] = (
            "keychain://other-instance/new-grant"
        )
        authority = _authorize(candidate)
    elif tamper == "unselected":
        candidate[CONFIG_KEY]["selections"] = [
            row
            for row in candidate[CONFIG_KEY]["selections"]
            if row["adapter_id"] != calendar["id"]
        ]
        authority = _authorize(candidate)
    elif tamper == "foreign_selection":
        candidate[CONFIG_KEY]["selections"][0]["member_id"] = MEMBER_B
        authority["proof_sha256"] = google_change_digest(candidate, MEMBER_A)
    else:
        foreign = copy.deepcopy(calendar)
        foreign["id"] = "fixture-foreign-calendar"
        foreign["config"]["member_id"] = MEMBER_B
        candidate["adapters"].append(foreign)
        authority = _authorize(candidate)
    old_readiness = transition.readiness_path.read_bytes()
    with pytest.raises((MemberGoogleTransitionError, ValueError)):
        transition.commit(
            previous_payload=previous,
            next_payload=_bytes(candidate),
            authority=authority,
        )
    assert transition.index_path.read_bytes() == previous
    assert transition.readiness_path.read_bytes() == old_readiness
    assert not transition.pending


class _Crash(BaseException):
    pass


@pytest.mark.parametrize("stage", ["journal", "adapters", "readiness"])
def test_process_crash_recovers_only_exact_durable_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    paths, transition = _fixture(tmp_path)
    original = transition.index_path.read_bytes()
    old_readiness = transition.readiness_path.read_bytes()
    target = {
        "journal": transition.journal_path,
        "adapters": transition.index_path,
        "readiness": transition.readiness_path,
    }[stage]
    write = transition._write

    def crash(path: Path, payload: bytes) -> None:
        write(path, payload)
        if path == target:
            raise _Crash()

    monkeypatch.setattr(transition, "_write", crash)
    with pytest.raises(_Crash):
        _commit(transition)
    with pytest.raises(CandidateReadinessError, match="recovery is pending"):
        capture_deployment_snapshot(paths)
    recovered = MemberGoogleReadinessTransition(paths, KEY)
    recovered.recover()
    assert not recovered.pending
    if stage == "readiness":
        assert recovered.index_path.read_bytes() != original
        assert json.loads(recovered.readiness_path.read_bytes())[
            "deployment_binding"
        ] == _binding(paths)
    else:
        assert recovered.index_path.read_bytes() == original
        assert recovered.readiness_path.read_bytes() == old_readiness


def test_tampered_journal_and_shared_updater_lock_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, transition = _fixture(tmp_path)
    with deployment_operation_lock(paths):
        with pytest.raises(CandidateReadinessError):
            _commit(transition)
    write = transition._write

    def crash(path: Path, payload: bytes) -> None:
        write(path, payload)
        if path == transition.index_path:
            raise _Crash()

    monkeypatch.setattr(transition, "_write", crash)
    with pytest.raises(_Crash):
        _commit(transition)
    wrapper = json.loads(transition.journal_path.read_bytes())
    wrapper["hmac_sha256"] = "0" * 64
    _private_write(transition.journal_path, _bytes(wrapper))
    current = transition.index_path.read_bytes()
    with pytest.raises(MemberGoogleTransitionError, match="authentication failed"):
        MemberGoogleReadinessTransition(paths, KEY).recover()
    assert transition.index_path.read_bytes() == current
    assert transition.pending


def _add_account(
    config: dict[str, Any], *, member_id: str, account: str, reference: str
) -> list[dict[str, Any]]:
    originals = [
        row
        for row in config["adapters"]
        if row["config"].get("member_id") == MEMBER_A
        and row["kind"]
        in {"messaging-gmail-api", "calendaring-google-calendar", "tasks-google-tasks"}
    ]
    added = []
    for original in originals:
        row = copy.deepcopy(original)
        row["id"] += "-second"
        row["config"].update(
            member_id=member_id,
            provider_account_id=account,
            user_email=account,
            oauth_credentials_ref=reference,
        )
        selection = copy.deepcopy(
            next(
                item
                for item in config[CONFIG_KEY]["selections"]
                if item["adapter_id"] == original["id"]
            )
        )
        selection.update(
            adapter_id=row["id"], member_id=member_id, provider_account_id=account
        )
        config["adapters"].append(row)
        config[CONFIG_KEY]["selections"].append(selection)
        added.append(row)
    return added


@pytest.mark.parametrize("duplicate_account", [False, True])
def test_new_member_cannot_reuse_a_retired_other_member_account(
    tmp_path: Path, duplicate_account: bool
) -> None:
    paths, transition = _fixture(tmp_path)
    previous = json.loads(transition.index_path.read_bytes())
    for row in previous["adapters"]:
        if row["config"].get("member_id") == MEMBER_A:
            row["enabled"] = False
    _private_write(transition.index_path, _bytes(previous))
    evidence = json.loads(transition.readiness_path.read_bytes())
    evidence["deployment_binding"] = _binding(paths)
    _private_write(transition.readiness_path, _bytes(evidence))
    candidate = copy.deepcopy(previous)
    account = (
        "personal-fixture@example.invalid"
        if duplicate_account
        else "new-member@example.invalid"
    )
    _add_account(
        candidate,
        member_id=MEMBER_B,
        account=account,
        reference=f"keychain://{paths.keychain_account()}/new-member-google",
    )
    authority = _authorize(candidate, MEMBER_B)

    def call() -> dict[str, Any]:
        return transition.commit(
            previous_payload=_bytes(previous),
            next_payload=_bytes(candidate),
            authority=authority,
        )

    if duplicate_account:
        with pytest.raises(MemberGoogleTransitionError, match="another owner"):
            call()
        assert transition.index_path.read_bytes() == _bytes(previous)
    else:
        call()
        assert json.loads(transition.readiness_path.read_bytes())[
            "deployment_binding"
        ] == _binding(paths)
        assert all(
            row["enabled"] is False
            for row in json.loads(transition.index_path.read_bytes())["adapters"]
            if row["config"].get("member_id") == MEMBER_A
        )


@pytest.mark.parametrize("all_disabled", [False, True])
def test_updater_discovery_retains_multiple_self_service_accounts_without_credential_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, all_disabled: bool
) -> None:
    from bootstrap import provider_binding_transition as binding_transition

    paths, transition = _fixture(tmp_path)
    source = json.loads(transition.index_path.read_bytes())
    _add_account(
        source,
        member_id=MEMBER_A,
        account="second-fixture@example.invalid",
        reference=f"keychain://{paths.keychain_account()}/second-google",
    )
    _authorize(source)
    if all_disabled:
        source[CONFIG_KEY]["selections"] = []
        for row in source["adapters"]:
            if row["config"].get("member_id") == MEMBER_A:
                row["enabled"] = False
    else:
        # A Calendar-only account still retains its inert mail/tasks history.
        unselected = {"fixture-personal-gmail-second", "fixture-personal-tasks-second"}
        source[CONFIG_KEY]["selections"] = [
            row
            for row in source[CONFIG_KEY]["selections"]
            if row["adapter_id"] not in unselected
        ]
        for row in source["adapters"]:
            if row["id"] in unselected:
                row["enabled"] = False

    class NoCredentials:
        def get(self, _reference: str) -> bytes:
            raise AssertionError(
                "existing accounts must not rediscover legacy credentials"
            )

    monkeypatch.setattr(
        binding_transition, "secret_store", lambda *_args, **_kwargs: NoCredentials()
    )
    before = copy.deepcopy(source)
    assert (
        binding_transition.discover_readonly_google_enrollment(
            paths,
            source,
            household_mapping={
                "members": [{"member_id": MEMBER_A, "role": "principal"}]
            },
        )
        is None
    )
    assert source == before


@pytest.mark.parametrize(
    "tamper",
    [
        "unselected_enabled",
        "incomplete",
        "duplicate_kind",
        "legacy_unselected",
        "binding",
    ],
)
def test_updater_discovery_rejects_ambiguous_multiple_google_accounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    from bootstrap import provider_binding_transition as binding_transition

    paths, transition = _fixture(tmp_path)
    source = json.loads(transition.index_path.read_bytes())
    added = _add_account(
        source,
        member_id=MEMBER_A,
        account="second-fixture@example.invalid",
        reference=f"keychain://{paths.keychain_account()}/second-google",
    )
    _authorize(source)
    if tamper == "unselected_enabled":
        source[CONFIG_KEY]["selections"] = [
            row
            for row in source[CONFIG_KEY]["selections"]
            if row["adapter_id"] != added[0]["id"]
        ]
    elif tamper == "incomplete":
        source["adapters"].remove(added[0])
    elif tamper == "duplicate_kind":
        added[0]["kind"] = added[1]["kind"]
    elif tamper == "legacy_unselected":
        source.pop(CONFIG_KEY)
    else:
        added[0]["config"]["ownership_attested"] = False
    monkeypatch.setattr(
        binding_transition, "secret_store", lambda *_args, **_kwargs: None
    )
    with pytest.raises(
        binding_transition.ProviderBindingTransitionError,
        match="incomplete or ambiguous",
    ):
        binding_transition.discover_readonly_google_enrollment(
            paths,
            source,
            household_mapping={
                "members": [{"member_id": MEMBER_A, "role": "principal"}]
            },
        )
