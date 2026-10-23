"""Selected Google reads, manual intake, isolation and restart through production."""

from __future__ import annotations

import asyncio
import base64
import copy
from dataclasses import replace
from pathlib import Path
import os
import shutil
import subprocess
import sys
from typing import Any

from fastapi.testclient import TestClient
import pytest

from adminme.adapters.cos_channel_acquisition import policy_from_config as cos_policy
from adminme.adapters.disposable_test_acquisition import restrict_adapter_config
from adminme.adapters.loader import load_builtin_adapters
from adminme.adapters.member_google_connections import (
    CONFIG_KEY,
    SCOPES,
    WRITE_SCOPES,
    SOURCES,
    GoogleConnectionPolicy,
    binding_digest,
    validate_authorized_change,
)
from adminme.runtime.application import create_application
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
from adminme.settings_control import SettingsControlStore
from adminme.source_policy import policy_for_preset, source_coordinate_for_event
from bootstrap.cos_channel_activation import prepare_configuration
from tests.acceptance.test_cos_channel_acquisition import _configuration
from tests.acceptance.test_forwarded_channel_intake import (
    MEMBER_A,
    MEMBER_B,
    MEMBER_HEADERS,
    OTHER_HEADERS,
    ROOT,
    TEXT,
    _events,
    _settings,
)

PERSONAL_ACCOUNT = "personal-fixture@example.invalid"


def google_configuration(path: Path, *, read_write: bool = False) -> dict[str, Any]:
    config = _configuration(path)
    personal = config["adapters"][0]
    personal["kind"] = "messaging-gmail-api"
    personal["config"].update(
        user_email=PERSONAL_ACCOUNT,
        oauth_credentials_ref="keychain://fixture/personal-google",
        ownership_attested=True,
        binding_provenance="oauth-subject",
        workspace="personal",
        enable_writes=False,
        permission_scope=[SCOPES[personal["kind"]]],
    )
    for kind, suffix, extra in (
        ("calendaring-google-calendar", "calendar", {"calendar_id": "primary"}),
        ("tasks-google-tasks", "tasks", {"tasklist_id": "@default"}),
    ):
        config["adapters"].append(
            {
                "id": f"fixture-personal-{suffix}",
                "kind": kind,
                "enabled": True,
                "config": {
                    **personal["config"],
                    **extra,
                    "permission_scope": [SCOPES[kind].removesuffix(".readonly")],
                    "poll_interval_s": 3600,
                },
            }
        )
    config["adapters"].append(
        {
            "id": "other-member-unselected",
            "kind": "fixture-unavailable-personal-mail",
            "enabled": True,
            "config": {"member_id": MEMBER_B},
        }
    )
    config[CONFIG_KEY] = GoogleConnectionPolicy(
        selections=[
            {
                "adapter_id": entry["id"],
                "member_id": MEMBER_A,
                "provider_account_id": PERSONAL_ACCOUNT,
                "access": (
                    "read_write"
                    if read_write and entry["kind"] in WRITE_SCOPES
                    else "read_only"
                ),
                "binding_sha256": binding_digest(
                    entry,
                    access="read_write"
                    if read_write and entry["kind"] in WRITE_SCOPES
                    else "read_only",
                ),
                "owner_authorization_sha256": "e" * 64,
            }
            for entry in config["adapters"]
            if entry["config"].get("member_id") == MEMBER_A
        ]
    ).model_dump(mode="json")
    return config


@pytest.mark.parametrize("read_write", [False, True])
def test_exact_connections_are_idempotent_and_preserve_cos_and_retained_selection(
    tmp_path: Path,
    read_write: bool,
) -> None:
    requested = google_configuration(tmp_path, read_write=read_write)
    before = copy.deepcopy(requested)
    previous = copy.deepcopy(requested)
    previous.pop(CONFIG_KEY)
    prepared = prepare_configuration(previous, requested, enable_live=False)
    assert requested == before
    assert (
        prepared["disposable_test_acquisition"] == before["disposable_test_acquisition"]
    )
    assert cos_policy(prepared).digest() == cos_policy(previous).digest()
    assert restrict_adapter_config(prepared) == prepared
    adapters = load_builtin_adapters(
        prepared,
        repository_root=ROOT,
        member_roles={MEMBER_A: "principal", MEMBER_B: "principal"},
        household_mapping={},
        service_principal_ids={COS_SERVICE_PRINCIPAL_ID},
        validate_bindings=True,
    )
    assert len(adapters) == 4
    for adapter in adapters:
        writable = (
            read_write
            and adapter._config.get("member_id") == MEMBER_A
            and adapter.name in WRITE_SCOPES
        )
        if adapter.name == "messaging-gmail-api":
            assert adapter._scopes == (
                [
                    "https://www.googleapis.com/auth/gmail.modify",
                    "https://www.googleapis.com/auth/gmail.send",
                ]
                if writable
                else [SCOPES[adapter.name]]
            )
            assert adapter._config.get("enable_writes") is writable
        else:
            scope = WRITE_SCOPES[adapter.name] if writable else SCOPES[adapter.name]
            assert adapter._scopes == [scope]
            assert bool(adapter.write_capabilities) is writable
            assert adapter._config.get("enable_writes") is not True
    assert prepared["adapters"][-1]["enabled"] is False
    gmail = prepared["adapters"][0]["config"]
    assert gmail["initial_backfill_max_messages"] == 0
    assert gmail["historical_seed_queries"] == []
    assert gmail["historical_related_max_participants"] == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("member_id", MEMBER_B),
        ("provider_account_id", "different@example.invalid"),
        ("oauth_credentials_ref", "op://fixture/other/credential"),
        ("service_principal_id", COS_SERVICE_PRINCIPAL_ID),
        ("workspace", "work"),
        ("ownership_attested", False),
    ],
)
def test_changed_binding_cannot_reuse_observation_authority(
    tmp_path: Path, field: str, value: Any
) -> None:
    config = google_configuration(tmp_path)
    config["adapters"][0]["config"][field] = value
    with pytest.raises(ValueError, match="binding mismatch"):
        restrict_adapter_config(config)


@pytest.mark.parametrize("kind", list(SCOPES))
def test_access_cannot_widen_under_a_read_only_binding(
    tmp_path: Path, kind: str
) -> None:
    config = google_configuration(tmp_path)
    entry = next(row for row in config["adapters"] if row["kind"] == kind)
    selection = next(
        row
        for row in config[CONFIG_KEY]["selections"]
        if row["adapter_id"] == entry["id"]
    )
    selection["access"] = "read_write"
    with pytest.raises(ValueError, match="binding mismatch"):
        restrict_adapter_config(config)


def test_permission_changes_require_the_owning_members_operation(
    tmp_path: Path,
) -> None:
    previous = google_configuration(tmp_path)
    requested = google_configuration(tmp_path, read_write=True)
    for owner, receipt in [(MEMBER_B, "e" * 64), (MEMBER_A, "f" * 64)]:
        with pytest.raises(ValueError, match="another member|exact authorization"):
            validate_authorized_change(
                previous,
                requested,
                owner_member_id=owner,
                owner_authorization_sha256=receipt,
            )
    validate_authorized_change(
        previous,
        requested,
        owner_member_id=MEMBER_A,
        owner_authorization_sha256="e" * 64,
    )
    # Revocation and disablement remain effective after retaining write-capable config.
    requested.pop(CONFIG_KEY)
    disabled = restrict_adapter_config(requested)
    assert not any(
        row["enabled"] for row in disabled["adapters"] if row["config"].get("member_id")
    )


def test_authority_is_member_specific_and_orphaned_grants_fail_closed(
    tmp_path: Path,
) -> None:
    config = google_configuration(tmp_path)
    previous = copy.deepcopy(config)
    previous.pop(CONFIG_KEY)
    for owner, receipt in [(MEMBER_B, "e" * 64), (MEMBER_A, "f" * 64)]:
        with pytest.raises(ValueError, match="exact authorization"):
            validate_authorized_change(
                previous,
                config,
                owner_member_id=owner,
                owner_authorization_sha256=receipt,
            )
    validate_authorized_change(
        previous, config, owner_member_id=MEMBER_A, owner_authorization_sha256="e" * 64
    )
    with pytest.raises(ValueError, match="another member"):
        validate_authorized_change(
            config,
            previous,
            owner_member_id=MEMBER_B,
            owner_authorization_sha256="e" * 64,
        )
    paused = copy.deepcopy(config)
    paused["adapters"][0]["enabled"] = False
    with pytest.raises(ValueError, match="another member"):
        validate_authorized_change(
            paused,
            config,
            owner_member_id=MEMBER_B,
            owner_authorization_sha256="e" * 64,
        )
    orphan = copy.deepcopy(config)
    orphan.pop("cos_channel_acquisition")
    with pytest.raises(ValueError, match="requires the CoS"):
        restrict_adapter_config(orphan)
    revoked = restrict_adapter_config(previous)
    assert not any(
        e["enabled"] for e in revoked["adapters"] if e["config"].get("member_id")
    )


def test_legacy_preferences_cannot_reenable_extraction_and_pause_survives_restart(
    tmp_path: Path,
) -> None:
    config = google_configuration(tmp_path)
    path = tmp_path / "settings.json"
    old = SettingsControlStore(path)
    for source in SOURCES.values():
        old.set_candidate_policy(
            MEMBER_A,
            source,
            mode="review",
            language_enabled=True,
            candidate_kinds=["commitment"],
            expected_revision=old.read()["revision"],
        )
    for _restart in range(2):
        store = SettingsControlStore(path, adapter_config=config)
        for source in SOURCES.values():
            assert (
                store.effective_candidate_policy(MEMBER_A, source)["mode"] == "manual"
            )
            assert (
                store.effective_candidate_policy(MEMBER_A, source)["language_enabled"]
                is False
            )
            with pytest.raises(ValueError, match="manual intake only"):
                store.set_candidate_policy(
                    MEMBER_A,
                    source,
                    mode="review",
                    language_enabled=True,
                    candidate_kinds=["commitment"],
                    expected_revision=store.read()["revision"],
                )
        candidate = store.preview_connection(
            "fixture-personal-gmail",
            "pause",
            expected_revision=store.read()["revision"],
            actor_member_id=MEMBER_A,
        )
        store.save_candidate(candidate)
        effective = store.effective_adapter_config(config)
        assert effective["adapters"][0]["enabled"] is False
        assert effective["adapters"][2]["enabled"] is True


@pytest.mark.parametrize(
    "kind,event_type",
    [
        ("messaging-gmail-api", "messaging.received"),
        ("calendaring-google-calendar", "calendar.event_added"),
        ("tasks-google-tasks", "list_item.added"),
    ],
)
def test_retained_exact_attention_preferences_remain_manual(
    tmp_path: Path, kind: str, event_type: str
) -> None:
    config = google_configuration(tmp_path)
    entry = next(e for e in config["adapters"] if e["kind"] == kind)
    path = tmp_path / "attention.json"
    prior = SettingsControlStore(path)
    coordinate = source_coordinate_for_event(
        {
            "type": event_type,
            "tenant_id": "fixture-tenant",
            "owner_scope": f"private:{MEMBER_A}",
            "source_adapter": kind,
            "source_account_id": PERSONAL_ACCOUNT,
            "payload": {"member_id": MEMBER_A},
        }
    )
    for index, instance in enumerate((kind, entry["id"]), 1):
        exact = coordinate.model_copy(update={"adapter_instance_id": instance})
        prior.apply_source_policy_v2_event(
            {
                "owner_principal_id": MEMBER_A,
                "coordinate": exact.model_dump(),
                "policy_key": exact.key,
                "revision": index,
                "action": "set",
                "policy": policy_for_preset("balanced").model_dump(),
            }
        )
    for _restart in range(2):
        current = SettingsControlStore(path, adapter_config=config)
        for instance in (kind, entry["id"]):
            exact = coordinate.model_copy(update={"adapter_instance_id": instance})
            effective = current.effective_source_policy_v2(MEMBER_A, exact)
            assert effective["policy"]["triage_mode"] == "manual_only"
            assert effective["policy"]["attention_mode"] == "manual"
            assert effective["origin"] == "exact_override"
        for changes in (
            {"provider_account_id": "other@example.invalid"},
            {"adapter_instance_id": "another-instance"},
        ):
            other = coordinate.model_copy(update=changes)
            assert (
                current.effective_source_policy_v2(MEMBER_A, other)["policy"][
                    "triage_mode"
                ]
                != "manual_only"
            )


class ReadOnlyGoogle:
    """Only the provider read seam is synthetic; production adapters still run."""

    def __init__(self, account: str) -> None:
        self.account = account

    def users(self) -> ReadOnlyGoogle:
        return self

    def history(self) -> ReadOnlyGoogle:
        return self

    def events(self) -> ReadOnlyGoogle:
        return self

    def calendars(self) -> ReadOnlyGoogle:
        return self

    def tasklists(self) -> ReadOnlyGoogle:
        return self

    def tasks(self) -> ReadOnlyGoogle:
        return self

    def getProfile(self, **_kwargs: Any) -> ReadOnlyGoogle:
        return self

    def get(self, **_kwargs: Any) -> ReadOnlyGoogle:
        return self

    def list(self, **_kwargs: Any) -> ReadOnlyGoogle:
        return self

    def execute(self) -> dict[str, Any]:
        return {
            "id": "@default",
            "title": "Fixture list",
            "summary": "Fixture calendar",
            "historyId": "100",
            "emailAddress": self.account,
            "items": [],
            "nextSyncToken": "fixture-sync",
        }


@pytest.mark.parametrize("read_write", [False, True])
def test_production_reads_project_private_facts_without_candidates_across_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, read_write: bool
) -> None:
    config = google_configuration(tmp_path, read_write=read_write)
    adapters = load_builtin_adapters(config, repository_root=ROOT)
    calls: list[tuple[str, list[str]]] = []
    for adapter in adapters:
        module = sys.modules[type(adapter).__module__]
        monkeypatch.setattr(module, "_default_credentials_resolver", lambda _ref: {})

        def service(
            _credentials: Any, scopes: list[str], subject: str
        ) -> ReadOnlyGoogle:
            assert (
                len(scopes) == 1
                and scopes[0]
                in {
                    *SCOPES.values(),
                    *WRITE_SCOPES.values(),
                }
            ) or scopes == [
                "https://www.googleapis.com/auth/gmail.modify",
                "https://www.googleapis.com/auth/gmail.send",
            ]
            calls.append((subject, scopes))
            return ReadOnlyGoogle(subject)

        monkeypatch.setattr(module, "_default_service_factory", service)
        monkeypatch.setattr(type(adapter), "POLL_INTERVAL_S", 3600)
    settings = replace(_settings(tmp_path), adapter_config=config)
    for run in range(2):
        app = create_application(settings)
        with TestClient(app) as client:
            runtime = app.state.runtime
            assert client.portal is not None
            assert set(runtime.adapters.inventory()) == {
                "fixture-personal-gmail",
                "fixture-personal-calendar",
                "fixture-personal-tasks",
                "fixture-cos-gmail",
            }
            managed = runtime.adapters._managed

            async def wait_adapters() -> None:
                for _attempt in range(1000):
                    if all(row.adapter._ctx is not None for row in managed.values()):
                        return
                    await asyncio.sleep(0.01)
                raise AssertionError("selected Google adapters did not start")

            client.portal.call(wait_adapters)
            if run == 0:

                async def ingest() -> None:
                    gmail = managed["fixture-personal-gmail"].adapter
                    await gmail._emit_messaging_received(
                        ctx=gmail._ctx,
                        message={
                            "id": "personal-fixture-mail",
                            "threadId": "personal-fixture-thread",
                            "internalDate": "1925035200000",
                            "labelIds": ["INBOX"],
                            "payload": {
                                "mimeType": "text/plain",
                                "headers": [
                                    {"name": "From", "value": "sender@example.invalid"},
                                    {"name": "To", "value": PERSONAL_ACCOUNT},
                                    {
                                        "name": "Subject",
                                        "value": "Please submit the form",
                                    },
                                ],
                                "body": {
                                    "data": base64.urlsafe_b64encode(
                                        TEXT.encode()
                                    ).decode()
                                },
                            },
                        },
                    )
                    calendar = managed["fixture-personal-calendar"].adapter
                    await calendar._emit_calendar_snapshot(
                        ctx=calendar._ctx,
                        event_type="calendar.event_added",
                        calendar_id="primary",
                        calendar_name="Fixture calendar",
                        event={
                            "id": "personal-fixture-calendar",
                            "summary": TEXT,
                            "status": "confirmed",
                            "start": {"dateTime": "2031-01-02T12:00:00Z"},
                            "end": {"dateTime": "2031-01-02T13:00:00Z"},
                            "updated": "2030-12-01T12:00:00Z",
                            "etag": "fixture-1",
                        },
                    )
                    tasks = managed["fixture-personal-tasks"].adapter
                    await tasks._emit_item(
                        ctx=tasks._ctx,
                        event_type="list_item.added",
                        tasklist={"id": "@default", "title": "Fixture list"},
                        task={
                            "id": "personal-fixture-task",
                            "title": TEXT,
                            "status": "needsAction",
                            "updated": "2030-12-01T12:00:00Z",
                            "etag": "fixture-1",
                        },
                    )

                client.portal.call(ingest)
            observed = {
                event_type: client.portal.call(_events, runtime, event_type)
                for event_type in (
                    "messaging.received",
                    "calendar.event_added",
                    "list_item.added",
                )
            }
            for rows in observed.values():
                assert len(rows) == 1
                client.portal.call(
                    runtime.wait_for_subscriber,
                    "source-policy-v2:shadow",
                    rows[0]["event_id"],
                )
            client.portal.call(
                runtime.wait_for_subscriber,
                "pipeline:pipeline:noise_filtering",
                observed["messaging.received"][0]["event_id"],
            )
            audits = client.portal.call(
                _events, runtime, "source.policy_v2.shadow_evaluated"
            )
            assert {a["payload"]["source_event_id"] for a in audits} >= {
                rows[0]["event_id"] for rows in observed.values()
            }
            assert all(
                a["payload"]["policy"]["triage_mode"] == "manual_only" for a in audits
            )
            # Read surfaces after production event consumers settle, not merely
            # after successful connector construction.
            for _attempt in range(200):
                calendar_surface = client.get(
                    "/api/member/v1/surfaces/calendar?date=2031-01-02",
                    headers=MEMBER_HEADERS,
                )
                if "personal-fixture-calendar" in calendar_surface.text:
                    break
                client.portal.call(asyncio.sleep, 0.05)
            assert calendar_surface.status_code == 200
            assert "personal-fixture-calendar" in calendar_surface.text
            other = client.get(
                "/api/member/v1/surfaces/calendar?date=2031-01-02",
                headers=OTHER_HEADERS,
            )
            assert "personal-fixture-calendar" not in other.text
            for event_type in (
                "triage.item.proposed",
                "triage.intent.submitted",
                "external_sync.write_requested",
                "skill.call.recorded",
                "skill.call.failed",
                "skill.call.suppressed",
                "messaging.semantic_features_extracted",
            ):
                assert client.portal.call(_events, runtime, event_type) == []
            skipped = client.portal.call(
                _events, runtime, "communication_intake.source_skipped"
            )
            assert len(skipped) == 1
            assert skipped[0]["payload"]["language_called"] is False
            assert skipped[0]["payload"]["candidate_count"] == 0
            for event_type in (
                "messaging.received",
                "calendar.event_added",
                "list_item.added",
            ):
                rows = client.portal.call(_events, runtime, event_type)
                assert len(rows) == 1
                assert rows[0]["owner_scope"] == f"private:{MEMBER_A}"
            control = client.get(
                "/api/member/v1/surfaces/settings", headers=MEMBER_HEADERS
            ).json()["control"]
            assert len(control["connections"]) == 3
            for row in control["connections"]:
                writable = read_write and row["id"] in {
                    "fixture-personal-gmail",
                    "fixture-personal-calendar",
                    "fixture-personal-tasks",
                }
                assert bool(row["permissions"]["write"]) is writable
            sources = control["source_policy_v2"]["sources"]
            selected = [
                s
                for s in sources
                if str(s.get("adapter_instance_id") or "").startswith(
                    "fixture-personal-"
                )
            ]
            assert len(selected) >= 3
            assert all(
                s["acquisition"]["coverage"] == "selected_google_sources"
                for s in selected
            )
            assert all(s["policy"]["triage_mode"] == "manual_only" for s in selected)
            for source in selected:
                writable = read_write and source["adapter_instance_id"] in {
                    "fixture-personal-gmail",
                    "fixture-personal-calendar",
                    "fixture-personal-tasks",
                }
                assert source["acquisition"]["provider_writes"] is writable
                assert source["acquisition"]["writes_require_existing_governance"]
    assert len(calls) == 8


def test_packaged_google_connections_without_git_metadata(tmp_path: Path) -> None:
    """Run the real composition scenario from an installed-style source tree."""
    packaged = tmp_path / "release"
    shutil.copytree(
        ROOT,
        packaged,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            "node_modules",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
        ),
    )
    assert not (packaged / ".git").exists()
    environment = dict(
        os.environ, PYTHONPATH=str(packaged / "bridge"), PYTHONDONTWRITEBYTECODE="1"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "--runxfail",
            "-q",
            "tests/acceptance/test_member_google_connections.py::test_production_reads_project_private_facts_without_candidates_across_restart",
        ],
        cwd=packaged,
        env=environment,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert result.returncode == 0, result.stdout + result.stderr
