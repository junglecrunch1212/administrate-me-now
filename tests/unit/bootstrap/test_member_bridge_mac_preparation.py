"""Hosted prerequisite process tests; native tools are synthetic boundaries."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from bootstrap.member_bridge_helper_app import _mac_prerequisites


def test_pinned_homebrew_installer_is_verified_before_opening():
    script = _mac_prerequisites().decode()
    assert "--proto '=https' --proto-redir '=https'" in script
    assert "releases/download/7.0.10/Homebrew.pkg" in script
    assert script.index("shasum -a 256") < script.index('open -W "$package"')
    assert script.index("--check-signature") < script.index('open -W "$package"')
    assert script.index("--assess --type install") < script.index('open -W "$package"')
    assert "sudo" not in script
    assert "curl |" not in script


@pytest.mark.parametrize(
    "state,expected",
    [
        ("filevault", "FileVault"),
        ("sdk", "developer tools"),
        ("network", "Tailscale"),
        ("ready", None),
    ],
)
def test_prerequisites_are_resumable_without_enrollment(
    tmp_path: Path, state: str, expected: str | None
):
    commands = tmp_path / "bin"
    commands.mkdir()
    ui = tmp_path / "ui.log"
    native = tmp_path / "native"
    native.write_text("""#!/bin/bash
case "${0##*/}" in
uname) if [[ "$1" == -s ]]; then echo Darwin; else echo arm64; fi ;;
sw_vers) echo 15.7 ;;
id) echo 501 ;;
fdesetup) [[ "$FIXTURE_STATE" == filevault ]] && echo Off || echo On ;;
xcrun) [[ "$FIXTURE_STATE" != sdk ]] ;;
osascript) cat >/dev/null; printf '%s\\n' "$*" >>"$FIXTURE_UI" ;;
open|xcode-select) exit 0 ;;
tailscale) [[ "$FIXTURE_STATE" != network ]] ;;
brew) exit 0 ;;
esac
""")
    native.chmod(0o700)
    for name in (
        "uname",
        "sw_vers",
        "id",
        "fdesetup",
        "xcrun",
        "osascript",
        "open",
        "xcode-select",
        "tailscale",
        "brew",
    ):
        (commands / name).symlink_to(native)
    script = (
        _mac_prerequisites()
        .decode()
        .replace(
            'export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"',
            f'export PATH="{commands}:/usr/bin:/bin"',
        )
    )
    for name in ("xcrun", "osascript", "open", "xcode-select"):
        script = script.replace(f"/usr/bin/{name}", str(commands / name))
    path = tmp_path / "prepare.sh"
    path.write_text(script)
    result = subprocess.run(
        ["/bin/bash", str(path)],
        capture_output=True,
        text=True,
        env={**os.environ, "FIXTURE_STATE": state, "FIXTURE_UI": str(ui)},
        timeout=10,
    )
    assert result.returncode == (2 if expected else 0), result.stderr
    if expected:
        assert expected in ui.read_text()
    assert "transfer" not in result.stdout
