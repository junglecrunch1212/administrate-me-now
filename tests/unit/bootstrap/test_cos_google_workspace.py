"""CoS purpose-bound Google preparation, read-back and activation boundaries."""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from adminme.adapters.cos_google_workspace import (
    CONFIG_KEY,
    PURPOSES,
    CoSGoogleSelection,
    grant_scopes,
    selected_entries,
    validate_authorized_change,
)
from adminme.adapters.credentials import (
    assert_service_oauth_binding,
    credential_service_digest,
)
from adminme.adapters.disposable_test_acquisition import restrict_adapter_config
from adminme.adapters.loader import load_builtin_adapters
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
from bootstrap.cos_channel_activation import (
    CoSActivationError,
    prepare_configuration as activate_configuration,
)
from bootstrap.cos_google_workspace import prepare_configuration, read_back
from tests.acceptance.test_cos_channel_acquisition import _configuration
from tests.acceptance.test_forwarded_channel_intake import EMAIL_ACCOUNT, MEMBER_A, ROOT


def _selection(purpose: str) -> CoSGoogleSelection:
    return CoSGoogleSelection.model_validate(
        {
            "adapter_id": "cos-" + purpose,
            "purpose": purpose,
            "provider_account_id": EMAIL_ACCOUNT,
            "resource_id": "fixture-" + purpose,
            "credential_reference": "keychain://fixture/cos-" + purpose,
            "owner_authorization_sha256": "e" * 64,
        }
    )


def _document(purpose: str) -> dict[str, Any]:
    return {
        "owner_kind": "service_principal",
        "owner_service_principal_digest": credential_service_digest(
            COS_SERVICE_PRINCIPAL_ID
        ),
        "provider_account_id": EMAIL_ACCOUNT,
        "grant_purpose": purpose,
        "client_id": "fixture-client",
        "client_secret": "fixture-secret",
        "refresh_token": "fixture-refresh",
        "scopes": list(grant_scopes(purpose)),
    }


class _Service:
    def __init__(self, selection: CoSGoogleSelection) -> None:
        self.selection = selection
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.resource: dict[str, Any] = {
            "id": selection.resource_id,
            "accessRole": "owner",
        }

    def calendarList(self) -> _Service:
        return self

    def tasklists(self) -> _Service:
        return self

    def events(self) -> _Service:
        return self

    def tasks(self) -> _Service:
        return self

    def get(self, **kwargs: Any) -> Any:
        self.calls.append(("get", kwargs))
        return SimpleNamespace(execute=lambda: self.resource)

    def list(self, **kwargs: Any) -> Any:
        self.calls.append(("list", kwargs))
        return SimpleNamespace(
            execute=lambda: {"items": [{"id": "private-fixture-item"}]}
        )


def _read_back(
    selection: CoSGoogleSelection, service: _Service, **kwargs: Any
) -> dict[str, Any]:
    return read_back(
        selection,
        resolver=kwargs.pop("resolver", lambda _ref: _document(selection.purpose)),
        credential_factory=lambda _document, _purpose: object(),
        account_resolver=kwargs.pop(
            "account_resolver", lambda _credentials: EMAIL_ACCOUNT
        ),
        service_factory=lambda *_args, **_kwargs: service,
        **kwargs,
    )


@pytest.mark.parametrize("purpose", list(PURPOSES))
def test_read_back_proves_current_account_and_only_exact_source(purpose: str) -> None:
    selection = _selection(purpose)
    service = _Service(selection)
    result = _read_back(selection, service)
    assert result["source_read_back"] is True
    assert result["provider_write_count"] == 0
    assert result["sample_item_count"] == 1
    assert "private-fixture-item" not in str(result)
    key = "calendarId" if purpose == "calendar-read" else "tasklist"
    assert service.calls == [
        ("get", {key: selection.resource_id}),
        ("list", {key: selection.resource_id, "maxResults": 1}),
    ]


@pytest.mark.parametrize("purpose", list(PURPOSES))
def test_wrong_account_is_rejected_before_source_read(purpose: str) -> None:
    selection = _selection(purpose)
    service = _Service(selection)
    with pytest.raises(ValueError, match="current Google account"):
        _read_back(
            selection,
            service,
            account_resolver=lambda _credentials: "other@example.invalid",
        )
    assert service.calls == []


@pytest.mark.parametrize("purpose", list(PURPOSES))
def test_wrong_resource_is_rejected_before_contents(purpose: str) -> None:
    selection = _selection(purpose)
    service = _Service(selection)
    service.resource["id"] = "different-resource"
    with pytest.raises(ValueError, match="different"):
        _read_back(selection, service)
    assert len(service.calls) == 1


def test_shared_calendar_is_not_reattributed_to_cos() -> None:
    selection = _selection("calendar-read")
    service = _Service(selection)
    service.resource["accessRole"] = "reader"
    with pytest.raises(ValueError, match="owned"):
        _read_back(selection, service)
    assert len(service.calls) == 1


@pytest.mark.parametrize("purpose", list(PURPOSES))
@pytest.mark.parametrize(
    "update",
    [
        {"owner_kind": "member"},
        {"owner_member_digest": "1" * 24},
        {"provider_account_id": "member@example.invalid"},
        {"grant_purpose": "gmail-read-send"},
        {"scopes": ["https://www.googleapis.com/auth/tasks"]},
        {"scopes": []},
        {"scopes": ["openid", "https://www.googleapis.com/auth/userinfo.email"]},
    ],
)
def test_member_mail_or_write_grants_never_gain_workspace_authority(
    purpose: str, update: dict[str, Any]
) -> None:
    selection = _selection(purpose)
    service = _Service(selection)
    with pytest.raises(RuntimeError, match="exact CoS account"):
        _read_back(
            selection, service, resolver=lambda _ref: {**_document(purpose), **update}
        )
    assert service.calls == []


def test_workspace_grant_cannot_be_used_for_mail() -> None:
    with pytest.raises(RuntimeError, match="exact CoS account"):
        assert_service_oauth_binding(
            _document("calendar-read"),
            service_principal_id=COS_SERVICE_PRINCIPAL_ID,
            provider_account_id=EMAIL_ACCOUNT,
            required_scopes=["https://www.googleapis.com/auth/gmail.send"],
        )


def test_guarded_activation_adds_only_selected_cos_sources_preserves_mail_and_members(
    tmp_path: Path,
) -> None:
    previous = _configuration(tmp_path)
    requested = prepare_configuration(previous, _selection("calendar-read"))
    requested = prepare_configuration(requested, _selection("tasks-read"))
    assert requested["adapters"][:2] == previous["adapters"]
    assert requested["cos_channel_acquisition"] == previous["cos_channel_acquisition"]
    assert (
        requested["disposable_test_acquisition"]
        == previous["disposable_test_acquisition"]
    )
    validate_authorized_change(previous, requested, owner_authorization_sha256="e" * 64)
    with pytest.raises(ValueError, match="exact authorization"):
        validate_authorized_change(
            previous, requested, owner_authorization_sha256="f" * 64
        )
    prepared = activate_configuration(previous, requested, enable_live=False)
    assert restrict_adapter_config(prepared) == prepared
    adapters = load_builtin_adapters(
        prepared,
        repository_root=ROOT,
        member_roles={MEMBER_A: "principal"},
        service_principal_ids={COS_SERVICE_PRINCIPAL_ID},
        validate_bindings=True,
    )
    assert len(adapters) == 3
    assert {adapter.instance_name for adapter in adapters} == {
        "fixture-cos-gmail",
        "cos-calendar-read",
        "cos-tasks-read",
    }
    assert all(
        not adapter.write_capabilities
        for adapter in adapters
        if adapter.name != "messaging-gmail-api"
    )
    paused = copy.deepcopy(prepared)
    paused["adapters"][-1]["enabled"] = False
    assert restrict_adapter_config(paused)["adapters"][-1]["enabled"] is False
    revoked = copy.deepcopy(prepared)
    revoked[CONFIG_KEY]["selections"] = []
    restored = activate_configuration(prepared, revoked, enable_live=False)
    assert all(row["enabled"] is False for row in restored["adapters"][-2:])
    assert len(restored["adapters"]) == len(prepared["adapters"])


@pytest.mark.parametrize(
    "mutation",
    ["member", "account", "resource", "grant", "discovery", "writes", "extra-adapter"],
)
def test_workspace_selection_refuses_scope_drift(tmp_path: Path, mutation: str) -> None:
    previous = _configuration(tmp_path)
    requested = prepare_configuration(previous, _selection("calendar-read"))
    config = requested["adapters"][-1]["config"]
    if mutation == "member":
        config["member_id"] = MEMBER_A
    elif mutation == "account":
        config["provider_account_id"] = "other@example.invalid"
    elif mutation == "resource":
        config["calendar_id"] = "primary"
    elif mutation == "grant":
        config["oauth_credentials_ref"] = "keychain://fixture/mail"
    elif mutation == "discovery":
        config["discover_calendars"] = True
    elif mutation == "writes":
        config["read_only"] = False
    else:
        requested["adapters"].append(
            {"id": "unreviewed", "kind": "unknown", "enabled": True}
        )
    with pytest.raises((ValueError, CoSActivationError)):
        activate_configuration(previous, requested, enable_live=False)


def test_resource_aliases_and_duplicate_purposes_are_not_exact(tmp_path: Path) -> None:
    for resource in ["primary", "@default", "*", " x "]:
        with pytest.raises(ValueError, match="exact provider resource"):
            CoSGoogleSelection.model_validate(
                {**_selection("calendar-read").model_dump(), "resource_id": resource}
            )
    requested = prepare_configuration(
        _configuration(tmp_path), _selection("calendar-read")
    )
    requested[CONFIG_KEY]["selections"].append(
        copy.deepcopy(requested[CONFIG_KEY]["selections"][0])
    )
    with pytest.raises(ValueError, match="separate named grants"):
        selected_entries(requested)


@pytest.mark.asyncio
async def test_real_oauth_adapters_start_and_persist_service_owned_source_readback(
    tmp_path: Path,
) -> None:
    from datetime import datetime, timezone
    from adminme.adapters.base import AdapterContext
    from adminme.events.bus import EventBus
    from adminme.events.log import EventLog
    from adminme.events.writer import EventWriter
    from adminme.lib.instance_config import load_instance_config

    previous = _configuration(tmp_path / "fixture")
    requested = prepare_configuration(previous, _selection("calendar-read"))
    requested = prepare_configuration(requested, _selection("tasks-read"))
    prepared = activate_configuration(previous, requested, enable_live=False)
    adapters = load_builtin_adapters(
        prepared,
        repository_root=ROOT,
        member_roles={MEMBER_A: "principal"},
        service_principal_ids={COS_SERVICE_PRINCIPAL_ID},
        validate_bindings=True,
    )
    instance_dir = tmp_path / "state"
    instance_dir.mkdir()
    instance = load_instance_config(instance_dir)
    log = EventLog(instance, b"w" * 32)
    bus = EventBus(log, instance.bus_checkpoint_path)
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    ctx = AdapterContext(
        event_sink=EventWriter(log, bus),
        config={},
        correlation_id_factory=lambda: "fixture-cos-google",
        now_factory=lambda: now,
    )
    try:
        for purpose, (kind, _resource_key, _scope) in PURPOSES.items():
            adapter = next(row for row in adapters if row.name == kind)
            service = _Service(_selection(purpose))
            item = (
                {
                    "id": "calendar-item",
                    "summary": "Fixture",
                    "status": "confirmed",
                    "start": {"dateTime": "2026-10-02T12:00:00Z"},
                    "end": {"dateTime": "2026-10-02T13:00:00Z"},
                    "updated": "2026-10-01T12:00:00Z",
                    "etag": "fixture-revision",
                }
                if purpose == "calendar-read"
                else {
                    "id": "task-item",
                    "title": "Fixture",
                    "status": "needsAction",
                    "updated": "2026-10-01T12:00:00Z",
                    "etag": "fixture-revision",
                }
            )
            service.list = lambda **_kwargs: SimpleNamespace(
                execute=lambda: {"items": [item]}
            )  # type: ignore[method-assign]
            adapter._credentials_resolver = lambda _reference: _document(purpose)
            adapter._service_factory = lambda _document, _scopes, _subject: service
            adapter._max_poll_cycles = 0
            await adapter.start(ctx=ctx)
            await adapter._poll_once(ctx=ctx)
            assert adapter.status().state == "running"
            assert adapter.write_capabilities == []
            await adapter.stop()
        facts = [
            event
            async for event in log.read_since()
            if event["type"] in {"calendar.event_added", "list_item.added"}
        ]
        assert {event["type"] for event in facts} == {
            "calendar.event_added",
            "list_item.added",
        }
        for event in facts:
            assert event["owner_scope"] == "service-private:" + COS_SERVICE_PRINCIPAL_ID
            assert event["payload"]["member_id"] is None
            assert event["payload"]["service_principal_id"] == COS_SERVICE_PRINCIPAL_ID
    finally:
        await bus.stop()
        await log.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("purpose", list(PURPOSES))
async def test_adapters_reject_member_token_before_constructing_provider_client(
    tmp_path: Path, purpose: str
) -> None:
    from datetime import datetime, timezone
    from adminme.adapters.base import AdapterContext

    previous = _configuration(tmp_path)
    requested = prepare_configuration(previous, _selection(purpose))
    prepared = activate_configuration(previous, requested, enable_live=False)
    adapters = load_builtin_adapters(prepared, repository_root=ROOT)
    adapter = next(row for row in adapters if row.name == PURPOSES[purpose][0])
    adapter._credentials_resolver = lambda _ref: {
        **_document(purpose),
        "owner_kind": "member",
    }
    adapter._service_factory = lambda *_args: pytest.fail(
        "invalid token reached provider factory"
    )
    ctx = AdapterContext(
        event_sink=object(),
        config={},
        correlation_id_factory=lambda: "fixture",  # type: ignore[arg-type]
        now_factory=lambda: datetime.now(timezone.utc),
    )
    with pytest.raises(RuntimeError, match="exact CoS account"):
        await adapter.start(ctx=ctx)
