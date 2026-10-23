"""Run the real Notes script against disposable application objects in CI.

No Apple application, credentials, permissions or household data are accessed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

import pytest

from adminme_bridge.connector_packs.apple_notes.provider import (
    AppleNotesScriptingProvider,
)


_FAKE_NOTES = r"""
let effects = 0;
let nativeBody = fixture.body;
const note = {
  id: () => "native-a", name: () => "Ordinary note",
  modificationDate: () => new Date(fixture.revision),
  passwordProtected: () => fixture.locked,
  shared: () => fixture.shared,
  attachments: () => fixture.attachments,
};
Object.defineProperty(note, "body", {get: () => () => nativeBody, set: value => {effects++; nativeBody = value;}});
const folder = {id: () => fixture.folder, name: () => "Selected folder", shared: () => fixture.folder_shared, folders: () => [], notes: () => [note]};
const account = {id: () => fixture.account, name: () => "Selected account", folders: () => [folder]};
function Application() { return {
  accounts: () => [account],
  make: spec => {effects++; nativeBody = spec.withProperties.body; return note;},
  delete: value => {if (value !== note) throw new Error("wrong target"); effects++;},
}; }
"""


def _native(**changes: Any) -> dict[str, Any]:
    return {
        "account": "account-a",
        "folder": "folder-a",
        "revision": "2026-01-01T00:00:00.000Z",
        "body": "<div>Existing note</div>",
        "locked": False,
        "shared": False,
        "folder_shared": False,
        "attachments": [],
        **changes,
    }


def _payload(**changes: Any) -> dict[str, Any]:
    return {
        "account_external_id": "account-a",
        "folder_external_id": "folder-a",
        "allowed_container_ids": ["folder-a"],
        "apple_service_permission": {
            "event_id": "grant-a",
            "revision": 1,
            "coordinates_sha256": "a" * 64,
        },
        "apple_service_allowed_container_ids": ["folder-a"],
        "apple_service_authorization_expires_at": "2099-01-01T00:00:00.000Z",
        "expected_provider_revision": "2026-01-01T00:00:00.000Z",
        "body": "<div>Approved text</div>",
        **changes,
    }


def _provider(
    native: dict[str, Any],
) -> tuple[AppleNotesScriptingProvider, dict[str, Any]]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for isolated native Notes script execution")
    captured: dict[str, Any] = {}

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        source = (
            "const fixture = "
            + json.dumps(native)
            + ";\n"
            + _FAKE_NOTES
            + "\n"
            + command[4]
            + "\ntry { const response = JSON.parse(run(["
            + json.dumps(command[-1])
            + "])); console.log(JSON.stringify({response, effects, body:nativeBody})); }"
            + " catch (error) { console.log(JSON.stringify({error:String(error), effects, body:nativeBody})); }"
        )
        completed = subprocess.run(
            [node, "--input-type=module", "-e", source],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        captured.update(json.loads(completed.stdout))
        return subprocess.CompletedProcess(
            command,
            1 if captured.get("error") else 0,
            stdout=json.dumps(captured.get("response")),
            stderr=str(captured.get("error") or ""),
        )

    return AppleNotesScriptingProvider(runner=runner), captured


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation", ["create_note", "update_note", "append_note", "delete_note"]
)
async def test_real_script_mutates_only_selected_ordinary_note(operation: str) -> None:
    provider, observed = _provider(_native())
    result = await provider.apply_write(
        operation, _payload(), None if operation == "create_note" else "native-a"
    )
    assert result == {"applied": True, "suppressed": False, "external_id": "native-a"}
    assert observed["effects"] == 1
    if operation == "append_note":
        assert observed["body"] == "<div>Existing note</div><div>Approved text</div>"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"account": "account-b"},
        {"folder": "folder-b"},
        {"revision": "2026-01-02T00:00:00.000Z"},
        {"locked": True},
        {"shared": True},
        {"shared": None},
        {"folder_shared": True},
        {"folder_shared": None},
        {"attachments": [{"id": "attachment-a"}]},
        {"attachments": None},
        {"body": '<div><img src="cid:attachment"></div>'},
        {"body": '<div style="color:blue">formatted</div>'},
    ],
)
async def test_native_changes_after_approval_fail_before_effect(
    changes: dict[str, Any],
) -> None:
    provider, observed = _provider(_native(**changes))
    with pytest.raises(RuntimeError):
        await provider.apply_write("update_note", _payload(), "native-a")
    assert observed["effects"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload,target",
    [
        (_payload(allowed_container_ids=["folder-b"]), "native-a"),
        (_payload(account_external_id=""), "native-a"),
        (_payload(expected_provider_revision=None), "native-a"),
        (_payload(body='<img src="https://invalid.invalid/image">'), "native-a"),
        (_payload(), None),
    ],
)
async def test_incomplete_or_unsafe_command_never_starts_native_script(
    payload: dict[str, Any], target: str | None
) -> None:
    provider, observed = _provider(_native())
    with pytest.raises(ValueError):
        await provider.apply_write("update_note", payload, target)
    assert observed == {}


@pytest.mark.asyncio
async def test_expired_proof_cannot_cross_native_effect_boundary() -> None:
    provider, observed = _provider(_native())
    with pytest.raises(RuntimeError, match="expired"):
        await provider.apply_write(
            "create_note",
            _payload(apple_service_authorization_expires_at="2000-01-01T00:00:00.000Z"),
            None,
        )
    assert observed["effects"] == 0
