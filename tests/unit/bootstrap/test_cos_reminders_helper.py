"""Real client/worker/native-reader composition with isolated OS transport fakes."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from adminme.adapters import cos_reminders_helper as client
from bootstrap import cos_reminders_helper_app as package
from bootstrap import cos_reminders_native_worker as worker
from tests.unit.bootstrap import test_cos_apple_reminders as acquisition


def _transport(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    helper_contract: str | None = None,
):
    selected = acquisition._selection().model_copy(update={"os_uid": os.getuid()})
    if helper_contract:
        app = tmp_path / "Fixture Reminders.app"
        metadata = app / "Contents/Resources/helper-manifest.json"
        metadata.parent.mkdir(parents=True)
        metadata.write_text(json.dumps({"contract": helper_contract}))
        selected = selected.model_copy(
            update={
                "native_helper": selected.native_helper.model_copy(
                    update={"app_path": str(app)}
                )
            }
        )
    coordinates = {
        "release_root": "/fixture/protected",
        "release_commit": "e" * 40,
        "python_executable": "/fixture/protected/.venv/bin/python",
        "python_library": "/fixture/Python",
        "python_library_sha256": "f" * 64,
    }
    state: Any = SimpleNamespace(
        calls=[], request=None, output=None, permission=3, mutation=None
    )
    monkeypatch.setattr(client.sys, "platform", "darwin")
    monkeypatch.setattr(client, "release_coordinates", lambda root=None: coordinates)
    monkeypatch.setattr(
        client, "local_device_identity", lambda: ("a" * 64, os.getuid())
    )
    monkeypatch.setattr(
        package, "verify_helper_app", lambda path: selected.native_helper.model_dump()
    )
    monkeypatch.setattr(worker, "permission_status", lambda: state.permission)
    monkeypatch.setattr(worker, "run_with_main_loop", worker.perform)

    def identity(request):
        return {
            **selected.native_helper.model_dump(),
            "native_executable": selected.native_helper.app_path
            + "/Contents/MacOS/"
            + package.EXECUTABLE,
            "native_pid": 123,
            **{
                key: request[key]
                for key in (
                    "python_executable",
                    "release_root",
                    "release_commit",
                    "python_library_sha256",
                    "device_identity_sha256",
                    "os_uid",
                )
            },
        }

    monkeypatch.setattr(worker, "native_identity", identity)

    def launch(args, **kwargs):
        assert args[:6] == [
            "/usr/bin/open",
            "-W",
            "-n",
            "-a",
            selected.native_helper.app_path,
            "--args",
        ]
        assert kwargs["timeout"] == 115
        path = Path(args[6])
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700
        request = json.loads(path.read_text())
        state.calls.append(request["operation"])
        state.request = request
        claimed = path.with_name("claimed.json")
        path.rename(
            claimed
        )  # native claim is separately proved by the macOS process test.
        code = worker.run(claimed)
        output = path.with_name("result.json")
        state.output = json.loads(output.read_text())
        if state.mutation:
            state.mutation(state.output)
            output.write_text(json.dumps(state.output))
        return SimpleNamespace(returncode=code)

    monkeypatch.setattr(client.subprocess, "run", launch)
    return selected, state


def test_status_never_requests_permission_or_reads_provider(monkeypatch, tmp_path):
    selected, state = _transport(monkeypatch, tmp_path)
    monkeypatch.setattr(acquisition, "_selection", lambda: selected)
    native, os_state = acquisition._native_reader(monkeypatch)
    monkeypatch.setattr(
        native,
        "request_reminders_permission",
        lambda: pytest.fail("status requested permission"),
    )
    state.permission = 0
    assert client.invoke(selected.native_helper, "status", {}) == {
        "authorization_status": 0
    }
    assert os_state.allocated == 0 and os_state.calls == []
    with pytest.raises(PermissionError):
        client.HelperRemindersReader().read(selected)
    assert os_state.allocated == 0


def test_same_helper_exact_read_and_empty_snapshot_are_proved(monkeypatch, tmp_path):
    selected, state = _transport(monkeypatch, tmp_path)
    native, os_state = acquisition._native_reader(monkeypatch)
    reader = client.HelperRemindersReader()
    snapshot = reader.read(selected)
    assert snapshot.list_id == selected.list_id and len(snapshot.items) == 1
    assert snapshot.items[0].last_modified_at.isoformat() == "2026-10-01T00:00:00+00:00"
    monkeypatch.setattr(
        native.NativeRemindersReader,
        "read",
        lambda self, selection: acquisition.SelectedSnapshot(
            selection.source_id, selection.list_id, "Fixture list", ()
        ),
    )
    evidence = acquisition.read_back(
        selected,
        reader_factory=lambda: reader,
        identity=lambda: ("a" * 64, os.getuid()),
    )
    assert evidence["item_count"] == 0
    assert evidence["permission_verified_for_native_helper"] is True
    assert state.calls == ["read", "read"] and os_state.converted == 1


@pytest.mark.parametrize(
    "fault",
    [
        "nonce",
        "operation",
        "bundle",
        "executable",
        "artifact",
        "release",
        "uid",
        "status",
        "extra",
    ],
)
def test_other_process_and_unexpected_replies_fail_closed(monkeypatch, tmp_path, fault):
    selected, state = _transport(monkeypatch, tmp_path)
    acquisition._native_reader(monkeypatch)

    def mutate(output):
        if fault == "nonce":
            output["nonce"] = "0" * 32
        elif fault == "operation":
            output["operation"] = "request"
        elif fault == "bundle":
            output["identity"]["bundle_identifier"] = "org.python.python"
        elif fault == "executable":
            output["identity"]["native_executable"] = (
                "/fixture/Other.app/Contents/MacOS/main"
            )
        elif fault == "artifact":
            output["identity"]["artifact_sha256"] = "0" * 64
        elif fault == "release":
            output["identity"]["release_commit"] = "0" * 40
        elif fault == "uid":
            output["identity"]["os_uid"] += 1
        elif fault == "status":
            output["result"]["authorization_status"] = 0
        else:
            output["unexpected"] = True

    state.mutation = mutate
    with pytest.raises((PermissionError, ValidationError)):
        client.HelperRemindersReader().read(selected)


@pytest.mark.parametrize("fault", ["permission", "device", "source", "foreign-item"])
def test_request_does_not_emit_unverified_snapshot(monkeypatch, tmp_path, fault):
    selected, state = _transport(monkeypatch, tmp_path)
    native, os_state = acquisition._native_reader(monkeypatch)
    if fault == "permission":
        os_state.permission = 0
    elif fault == "device":
        selected = selected.model_copy(update={"device_identity_sha256": "0" * 64})
    elif fault == "source":
        os_state.source_id = "unreviewed-source"
    else:
        monkeypatch.setattr(
            native.NativeRemindersReader,
            "read",
            lambda self, selected: acquisition.SelectedSnapshot(
                selected.source_id,
                selected.list_id,
                "Fixture",
                (acquisition._item(list_identifier="unreviewed-list"),),
            ),
        )
    with pytest.raises(PermissionError):
        client.HelperRemindersReader().read(selected)


def test_explicit_request_alone_never_counts_as_exact_source_read(
    monkeypatch, tmp_path
):
    selected, state = _transport(monkeypatch, tmp_path)
    native, os_state = acquisition._native_reader(monkeypatch)
    state.permission = 0
    calls = []
    monkeypatch.setattr(
        native, "request_reminders_permission", lambda: calls.append("requested")
    )
    with pytest.raises(PermissionError):
        client.invoke(selected.native_helper, "request", {})
    assert calls == ["requested"] and os_state.allocated == 0
    state.permission = 3
    assert client.invoke(selected.native_helper, "request", {}) == {
        "authorization_status": 3
    }
    assert os_state.allocated == 0


def _request():
    now = int(time.time() * 1000)
    return {
        "contract": "cos-reminders-helper-request-v1",
        "nonce": "a" * 32,
        "issued_at_ms": now,
        "expires_at_ms": now + 100000,
        "operation": "status",
        "release_root": "/fixture/protected",
        "release_commit": "b" * 40,
        "python_executable": "/fixture/protected/.venv/bin/python",
        "python_library": "/fixture/Python",
        "python_library_sha256": "c" * 64,
        "device_identity_sha256": "d" * 64,
        "os_uid": os.getuid(),
        "payload": {},
    }


@pytest.mark.parametrize(
    "fault",
    ["stale", "future", "ttl", "uid", "type", "route", "payload", "extra", "release"],
)
def test_worker_rejects_invalid_requests_before_identity_or_provider(fault):
    request = _request()
    if fault == "stale":
        request["expires_at_ms"] = request["issued_at_ms"] - 1
    elif fault == "future":
        request["issued_at_ms"] += 10000
    elif fault == "ttl":
        request["expires_at_ms"] += 120000
    elif fault == "uid":
        request["os_uid"] += 1
    elif fault == "type":
        request["os_uid"] = True
    elif fault == "route":
        request["operation"] = "execute"
    elif fault == "payload":
        request["payload"] = {"script": "unreviewed"}
    elif fault == "extra":
        request["command"] = "unreviewed"
    else:
        request["python_executable"] = "/fixture/other/.venv/bin/python"
    with pytest.raises((ValueError, PermissionError)):
        worker.validate_request(request)


def test_worker_claim_prevents_replay_and_redacts_failure(monkeypatch, tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / "claimed.json"
    path.write_text(json.dumps(_request()))
    path.chmod(0o600)
    calls = []

    def wrong_identity(request):
        calls.append("identity")
        raise PermissionError("private provider detail that must not leave the helper")

    monkeypatch.setattr(worker, "native_identity", wrong_identity)
    assert worker.run(path) == 1
    first = (tmp_path / "result.json").read_text()
    assert "private provider" not in first and "PermissionError" in first
    (tmp_path / "result.json").unlink()
    assert worker.run(path) == 1
    assert calls == ["identity"]
    assert "FileExistsError" in (tmp_path / "result.json").read_text()


def test_missing_helper_configuration_never_falls_back_to_generic_python():
    value = acquisition._selection().model_dump()
    del value["native_helper"]
    with pytest.raises(ValidationError):
        acquisition.CoSRemindersSelection.model_validate(value)


@pytest.mark.asyncio
async def test_actual_helper_client_adapter_persists_facts_then_denial_preserves_history(
    monkeypatch, tmp_path, runtime
):
    selected, state = _transport(monkeypatch, tmp_path)
    monkeypatch.setattr(acquisition, "_selection", lambda: selected)
    native, os_state = acquisition._native_reader(monkeypatch)
    ctx, log = runtime
    adapter = acquisition._adapter(tmp_path, acquisition.Reader())
    adapter._device_identity = lambda: ("a" * 64, os.getuid())
    adapter._reader_factory = client.HelperRemindersReader
    try:
        await acquisition._start(adapter, ctx, log)
        facts = await acquisition._facts(log)
        assert [row["type"] for row in facts] == ["list.created", "list_item.added"]
        assert facts[-1]["payload"]["member_id"] is None
        state.permission = 0
        with pytest.raises(PermissionError):
            await adapter._poll_once(ctx=ctx)
        assert await acquisition._facts(log) == facts
    finally:
        await adapter.stop()


# Reuse the production encrypted-log composition; no alternate persistence fixture.
runtime = acquisition.runtime


def test_native_string_subclasses_preserve_notes():
    class NativeString(str):
        pass

    result = worker._wire({"notes": NativeString("Private fixture text")})
    assert result == {"notes": "Private fixture text"}
    assert type(result["notes"]) is str


def test_worker_supports_pre_client_protected_selection_contract(monkeypatch, tmp_path):
    from pydantic import create_model
    from adminme.adapters import cos_apple_reminders as binding

    selected, state = _transport(monkeypatch, tmp_path)
    acquisition._native_reader(monkeypatch)
    legacy = create_model(
        "LegacySelection",
        __config__={"extra": "forbid", "strict": True},
        **{
            key: (field.annotation, field)
            for key, field in binding.CoSRemindersSelection.model_fields.items()
            if key != "native_helper"
        },
    )
    monkeypatch.setattr(binding, "CoSRemindersSelection", legacy)
    result = client.HelperRemindersReader().read(selected)
    assert result.list_id == selected.list_id and len(result.items) == 1
    assert state.calls == ["read"]


@pytest.mark.parametrize(
    "error,extra,expected",
    [
        ("python_initialization_failed", False, "python_initialization_failed"),
        ("PermissionError", False, "PermissionError"),
        ("TimeoutError", False, "TimeoutError"),
        *[
            (code, False, code)
            for code in (
                "python_library_dlopen_signature_rejected",
                "python_library_dlopen_dependency_missing",
                "python_library_dlopen_architecture_mismatch",
                "python_library_dlopen_symbol_missing",
                "python_library_dlopen_failed",
                "python_library_changed_during_load",
            )
        ],
        ("private provider detail", False, "unexpected_native_response"),
        ("PermissionError", True, "unexpected_native_response"),
    ],
)
def test_native_error_diagnostics_are_fixed_and_redacted(
    monkeypatch, tmp_path, error, extra, expected
):
    selected, state = _transport(monkeypatch, tmp_path)
    acquisition._native_reader(monkeypatch)

    def mutation(output):
        output.clear()
        output.update(
            contract="cos-reminders-helper-error-v1", status="failed", error=error
        )
        if extra:
            output["private"] = "private provider detail"

    state.mutation = mutation
    with pytest.raises(client.NativeHelperError) as failure:
        client.invoke(selected.native_helper, "status", {})
    assert failure.value.code == expected
    assert "private provider" not in client.redacted_error(failure.value)


def test_every_fixed_native_failure_remains_diagnosable():
    import re

    root = Path(__file__).parents[3]
    source = (root / "bootstrap/native/cos_reminders_launcher.m").read_text()
    codes = set(re.findall(r'failure\(parent, @"([a-z_]+)"', source))
    codes.update(re.findall(r'return @"(python_library_[a-z_]+)"', source))
    assert codes and codes <= client.NATIVE_ERROR_CODES
    for code in codes:
        assert client.NativeHelperError(code).code == code


def _write_transport(monkeypatch, tmp_path, *, contract="cos-reminders-native-app-v2"):
    from adminme.adapters import cos_reminders_write as writes
    from tests.unit.adapters.reminders.test_cos_reminders_write import _command

    selected, state = _transport(monkeypatch, tmp_path, helper_contract=contract)
    command = _command(
        selection_sha256=writes.selected_source_sha256(selected),
        provider_account_id=selected.provider_account_id,
    )
    # The fixture transport's selected protected release contains this exact module.
    monkeypatch.setattr(
        writes, "__file__", "/fixture/protected/adminme/adapters/cos_reminders_write.py"
    )
    effects = []

    class Writer:
        def inspect(self, selection):
            assert selection == selected
            return {
                "writable": True,
                "provider_account_id": selection.provider_account_id,
                "source_id": selection.source_id,
                "list_id": selection.list_id,
            }

        def create(self, selection, supplied):
            assert selection == selected and supplied == command
            writes.validate_native_create(supplied, selection)
            effects.append(supplied.command_id)
            due = supplied.due_at.astimezone(timezone.utc)
            return {
                "applied": True,
                "suppressed": False,
                "external_id": "fixture-created-reminder",
                "provider_account_id": selection.provider_account_id,
                "source_id": selection.source_id,
                "list_id": selection.list_id,
                "requested_due_at": supplied.due_at.isoformat(),
                "observed_due": {
                    **{
                        key: getattr(due, key)
                        for key in ("year", "month", "day", "hour", "minute", "second")
                    },
                    "time_zone": "UTC",
                    "all_day": False,
                    "floating": False,
                },
                "observed_at": datetime.now(timezone.utc).isoformat(),
            }

    monkeypatch.setattr(writes, "NativeRemindersWriter", Writer)
    return selected, command, state, effects


def test_write_transport_proves_native_identity_exact_source_and_result(
    monkeypatch, tmp_path
):
    selected, command, state, effects = _write_transport(monkeypatch, tmp_path)
    result = client.HelperRemindersWriter().create(selected, command)
    assert result.applied is True and result.suppressed is False
    assert result.list_id == selected.list_id
    assert result.requested_due_at == command.due_at
    assert effects == [command.command_id]
    assert state.calls == ["create_reminder"]
    assert state.request["payload"]["command"] == command.model_dump(mode="json")


def test_write_inspection_proves_exact_destination_without_save(monkeypatch, tmp_path):
    selected, _command, state, effects = _write_transport(monkeypatch, tmp_path)
    result = client.HelperRemindersWriter().inspect(selected)
    assert result.writable is True
    assert result.source_id == selected.source_id and result.list_id == selected.list_id
    assert state.calls == ["inspect_write"] and effects == []


@pytest.mark.parametrize("side", ["client", "native"])
def test_read_component_never_accepts_create(monkeypatch, tmp_path, side):
    selected, command, state, effects = _write_transport(
        monkeypatch, tmp_path, contract="cos-reminders-native-app-v1"
    )
    if side == "client":
        with pytest.raises(PermissionError, match="write-capable helper"):
            client.HelperRemindersWriter().create(selected, command)
        assert state.calls == []
    else:
        request = _request()
        request["operation"] = "create_reminder"
        request["payload"] = {
            "selection": selected.model_dump(mode="json", exclude={"native_helper"}),
            "command": command.model_dump(mode="json"),
        }
        request["device_identity_sha256"] = selected.device_identity_sha256
        with pytest.raises(PermissionError, match="only supports reads"):
            worker.perform(request, selected.native_helper.model_dump())
    assert effects == []


@pytest.mark.parametrize(
    "fault", ["account", "source", "list", "time", "suppressed", "extra"]
)
def test_unverified_native_write_receipt_never_claims_success_or_retries(
    monkeypatch, tmp_path, fault
):
    selected, command, state, effects = _write_transport(monkeypatch, tmp_path)

    def mutate(output):
        receipt = output["result"]["write_result"]
        if fault == "account":
            receipt["provider_account_id"] = "other-account"
        elif fault == "source":
            receipt["source_id"] = "other-source"
        elif fault == "list":
            receipt["list_id"] = "other-list"
        elif fault == "time":
            receipt["observed_due"]["hour"] += 1
        elif fault == "suppressed":
            receipt["suppressed"] = True
        else:
            receipt["extra"] = True

    state.mutation = mutate
    with pytest.raises((PermissionError, ValidationError)):
        client.HelperRemindersWriter().create(selected, command)
    assert effects == [command.command_id] and state.calls == ["create_reminder"]


def test_write_does_not_prompt_when_native_permission_is_missing(monkeypatch, tmp_path):
    selected, command, state, effects = _write_transport(monkeypatch, tmp_path)
    state.permission = 0
    from adminme.adapters import cos_apple_reminders_native as native

    monkeypatch.setattr(
        native,
        "request_reminders_permission",
        lambda: pytest.fail("Runtime attempted to prompt for consent"),
    )
    with pytest.raises(client.NativeHelperError):
        client.HelperRemindersWriter().create(selected, command)
    assert effects == []
