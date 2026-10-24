"""Feature revocation preserves grants/history and never resumes old authority."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from adminme.request_actions_control import (
    FILENAME,
    read_control,
    request_actions_allowed,
)
from bootstrap.request_actions import set_disabled


def test_recovery_is_atomic_private_and_retains_cutoff_after_enable(
    tmp_path: Path,
) -> None:
    runtime = SimpleNamespace(
        settings=SimpleNamespace(instance=SimpleNamespace(config_dir=tmp_path))
    )
    old = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    grants = tmp_path / "existing-grants.json"
    grants.write_text('{"existing":"unchanged"}')
    assert request_actions_allowed(runtime, issued_at=old)
    disabled = set_disabled(tmp_path, True)
    assert disabled["disabled"] is True
    assert not request_actions_allowed(runtime)
    assert (tmp_path / FILENAME).stat().st_mode & 0o777 == 0o600
    enabled = set_disabled(tmp_path, False)
    assert enabled["revoked_before"] == disabled["revoked_before"]
    assert not request_actions_allowed(runtime, issued_at=old)
    assert request_actions_allowed(
        runtime,
        issued_at=(
            datetime.fromisoformat(enabled["revoked_before"]) + timedelta(seconds=1)
        ).isoformat(),
    )
    assert grants.read_text() == '{"existing":"unchanged"}'
    assert not list(tmp_path.glob(".request-actions-*"))


@pytest.mark.parametrize(
    "value",
    [
        "not json",
        "{}",
        '{"contract":"request-actions-control-v1","disabled":false,"revoked_before":"yesterday"}',
    ],
)
def test_malformed_control_fails_closed(tmp_path: Path, value: str) -> None:
    (tmp_path / FILENAME).write_text(value)
    assert read_control(tmp_path)["disabled"] is True


def test_symlink_never_overwrites_another_configuration(tmp_path: Path) -> None:
    target = tmp_path / "grants.json"
    target.write_text(json.dumps({"existing": True}))
    (tmp_path / FILENAME).symlink_to(target)
    assert read_control(tmp_path)["disabled"] is True
    with pytest.raises(ValueError, match="symlink"):
        set_disabled(tmp_path, True)
    assert json.loads(target.read_text()) == {"existing": True}
