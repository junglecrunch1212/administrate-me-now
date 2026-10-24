"""Own-source writes are separate from the retained read and member bridges."""

from __future__ import annotations

import copy

import pytest

from adminme.adapters import cos_reminders_write_policy as policy
from adminme.adapters.cos_reminders_write import selected_source_sha256
from bootstrap.cos_apple_reminders import prepare_configuration
from tests.acceptance.test_cos_channel_acquisition import _configuration
from tests.unit.bootstrap.test_cos_apple_reminders import _selection


def _configuration_pair(tmp_path):
    selected = _selection()
    previous = prepare_configuration(_configuration(tmp_path), selected)
    requested = copy.deepcopy(previous)
    requested[policy.CONFIG_KEY] = {
        "contract": "cos-apple-reminders-writes-v1",
        "enabled": True,
        "native_helper": {
            **selected.native_helper.model_dump(),
            "app_path": "/fixture/CoSRemindersWrite.app",
            "artifact_sha256": "e" * 64,
        },
        "selection_sha256": selected_source_sha256(selected),
        "owner_authorization_sha256": "f" * 64,
    }
    return previous, requested


def test_write_selection_keeps_read_account_source_and_grants_unchanged(tmp_path):
    previous, requested = _configuration_pair(tmp_path)
    before = copy.deepcopy(previous)
    policy.validate_authorized_change(
        previous, requested, owner_authorization_sha256="f" * 64
    )
    selected = policy.write_selection(requested)
    assert selected.model_dump(exclude={"native_helper"}) == _selection().model_dump(
        exclude={"native_helper"}
    )
    assert selected.native_helper.app_path == "/fixture/CoSRemindersWrite.app"
    assert selected.provider_account_id == _selection().provider_account_id
    assert previous == before
    assert {k: v for k, v in requested.items() if k != policy.CONFIG_KEY} == previous


@pytest.mark.parametrize("fault", ["disabled", "absent", "paused"])
def test_no_implicit_write_authority(tmp_path, fault):
    previous, requested = _configuration_pair(tmp_path)
    if fault == "disabled":
        requested[policy.CONFIG_KEY]["enabled"] = False
    elif fault == "absent":
        requested = previous
    else:
        next(
            row for row in requested["adapters"] if row["id"] == _selection().adapter_id
        )["enabled"] = False
    with pytest.raises(PermissionError):
        policy.write_selection(requested)


@pytest.mark.parametrize("fault", ["digest", "old_helper", "owner", "read_changed"])
def test_activation_cannot_repurpose_source_or_old_read_component(tmp_path, fault):
    previous, requested = _configuration_pair(tmp_path)
    if fault == "digest":
        requested[policy.CONFIG_KEY]["selection_sha256"] = "0" * 64
    elif fault == "old_helper":
        requested[policy.CONFIG_KEY]["native_helper"] = (
            _selection().native_helper.model_dump()
        )
    elif fault == "owner":
        requested[policy.CONFIG_KEY]["owner_authorization_sha256"] = "0" * 64
    else:
        next(
            row for row in requested["adapters"] if row["id"] == _selection().adapter_id
        )["enabled"] = False
    with pytest.raises(ValueError):
        policy.validate_authorized_change(
            previous, requested, owner_authorization_sha256="f" * 64
        )


def test_revocation_does_not_remove_or_rewrite_source_history(tmp_path):
    previous, requested = _configuration_pair(tmp_path)
    policy.validate_authorized_change(
        requested, previous, owner_authorization_sha256="0" * 64
    )
    assert previous["cos_apple_reminders"] == requested["cos_apple_reminders"]
    assert previous["adapters"] == requested["adapters"]
