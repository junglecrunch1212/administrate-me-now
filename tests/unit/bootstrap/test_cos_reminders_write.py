"""Preparation and revocation never mutate the read source or create a reminder."""

from __future__ import annotations

import json
import sys

import pytest

from adminme.adapters.cos_apple_reminders import NativeHelperBinding
from adminme.adapters.cos_reminders_helper import NativeWriteSource
from adminme.adapters.cos_reminders_write_policy import CONFIG_KEY, write_selection
from bootstrap import cos_reminders_write as setup
from tests.unit.adapters.reminders.test_cos_reminders_write_policy import (
    _configuration_pair,
)


def test_prepare_enable_and_disable_preserve_every_existing_grant(tmp_path):
    previous, requested = _configuration_pair(tmp_path)
    binding = NativeHelperBinding.model_validate(requested[CONFIG_KEY]["native_helper"])
    actual = setup.prepare_configuration(
        previous,
        native_helper=binding,
        owner_authorization_sha256="f" * 64,
        enabled=True,
    )
    assert actual == requested
    disabled = setup.prepare_configuration(
        actual,
        native_helper=None,
        owner_authorization_sha256="a" * 64,
        enabled=False,
    )
    assert disabled[CONFIG_KEY]["enabled"] is False
    assert {k: v for k, v in disabled.items() if k != CONFIG_KEY} == previous
    with pytest.raises(PermissionError):
        write_selection(disabled)


@pytest.mark.parametrize("mode", ["inspect", "enable", "disable"])
def test_supported_setup_cli_only_inspects_or_prepares(
    monkeypatch, tmp_path, capsys, mode
):
    previous, requested = _configuration_pair(tmp_path)
    config_path = tmp_path / "configuration.json"
    config_path.write_text(json.dumps(requested if mode == "disable" else previous))
    config_path.chmod(0o600)
    receipt_path = tmp_path / "helper.json"
    receipt_path.write_text(json.dumps(requested[CONFIG_KEY]["native_helper"]))
    receipt_path.chmod(0o600)
    output = tmp_path / "prepared.json"
    argv = [
        "cos-reminders-write",
        "--" + mode,
        "--previous-config",
        str(config_path),
        "--output",
        str(output),
    ]
    if mode != "disable":
        argv += ["--helper-receipt", str(receipt_path)]
    if mode != "inspect":
        argv += ["--owner-authorization-sha256", "f" * 64]
    monkeypatch.setattr(sys, "argv", argv)
    calls = []
    monkeypatch.setattr(
        setup, "verify_write_component", lambda binding: calls.append("verify")
    )
    selected = write_selection(requested)
    monkeypatch.setattr(
        setup,
        "local_device_identity",
        lambda: (selected.device_identity_sha256, selected.os_uid),
    )
    monkeypatch.setattr(
        setup,
        "invoke",
        lambda *args, **kwargs: pytest.fail("Unrequested OS permission operation"),
    )

    class Inspector:
        def __init__(self, **kwargs):
            pass

        def inspect(self, selection):
            assert selection == selected
            calls.append("inspect")
            return NativeWriteSource(
                writable=True,
                source_id=selected.source_id,
                list_id=selected.list_id,
                provider_account_id=selected.provider_account_id,
            )

        def create(self, *args):
            pytest.fail("Preparation attempted a provider save")

    monkeypatch.setattr(setup, "HelperRemindersWriter", Inspector)
    setup.main()
    proof = json.loads(capsys.readouterr().out)
    prepared = json.loads(output.read_text())
    assert output.stat().st_mode & 0o777 == 0o600
    assert proof["provider_write_count"] == 0
    assert calls == ([] if mode == "disable" else ["verify", "inspect"])
    if mode == "inspect":
        assert prepared["source"]["list_id"] == selected.list_id
    else:
        assert {k: v for k, v in prepared.items() if k != CONFIG_KEY} == previous
        assert prepared[CONFIG_KEY]["enabled"] is (mode == "enable")
