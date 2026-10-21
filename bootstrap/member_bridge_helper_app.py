"""Build the reviewed document-opening macOS bridge helper bundle.

The bundle embeds the exact reviewed source archive and installer.  It contains
no updater, remote shell, or arbitrary-code path, and passes the transfer secret
through an owner-only temporary file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from bootstrap.contracts import atomic_write, repository_root


HELPER_APP_CONTRACT = "administrateme-member-bridge-helper-app-v1"
BUNDLE_IDENTIFIER = "com.administrateme.member-bridge-helper"


@dataclass(frozen=True)
class HelperAppBuild:
    path: str
    bundle_identifier: str
    release_commit: str
    installer_sha256: str
    release_archive_sha256: str
    document_extension: str
    provider_effects: bool = False


def _git_commit(root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    value = completed.stdout.strip()
    if completed.returncode or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise RuntimeError("the helper app requires a reviewed Git commit")
    return value


def build_member_bridge_helper_app(
    destination: Path,
    *,
    root: Path | None = None,
    expected_commit: str | None = None,
) -> HelperAppBuild:
    """Create a fail-closed `.app` bundle without replacing an existing app."""

    release_root = (root or repository_root()).resolve()
    commit = expected_commit or _git_commit(release_root)
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or commit != _git_commit(release_root):
        raise RuntimeError("helper app release commit does not match the checkout")
    installer = release_root / "bootstrap" / "install-member-bridge.sh"
    if not installer.is_file() or installer.is_symlink():
        raise RuntimeError("reviewed member-bridge installer is unavailable")

    app = destination.expanduser().resolve()
    if app.suffix != ".app" or app == Path("/") or app.exists() or app.is_symlink():
        raise RuntimeError("choose a new, narrow .app destination")
    compiler = Path("/usr/bin/osacompile")
    if not compiler.is_file():
        raise RuntimeError("the document-opening helper app must be built on macOS")
    handler_source = """on helperLauncher()
  set appRoot to POSIX path of (path to me)
  return appRoot & "Contents/Resources/bridge-helper-launcher.sh"
end helperLauncher

on open enrollmentFiles
  set launcherPath to helperLauncher()
  repeat with enrollmentFile in enrollmentFiles
    set packagePath to POSIX path of enrollmentFile
    do shell script quoted form of launcherPath & space & quoted form of packagePath
  end repeat
end open

on run
  set launcherPath to helperLauncher()
  do shell script quoted form of launcherPath & " --manage"
end run
"""
    compiled = subprocess.run(
        [str(compiler), "-o", str(app), "-e", handler_source],
        check=False,
        capture_output=True,
        text=True,
    )
    if compiled.returncode:
        raise RuntimeError("the document-opening helper app could not be compiled")
    resources = app / "Contents" / "Resources"
    info_path = app / "Contents" / "Info.plist"
    info = plistlib.loads(info_path.read_bytes())
    info.update(
        {
            "CFBundleDevelopmentRegion": "en",
            "CFBundleDisplayName": "AdministrateMe Bridge Helper",
            "CFBundleDocumentTypes": [
                {
                    "CFBundleTypeExtensions": ["adminmebridge"],
                    "CFBundleTypeName": "AdministrateMe Bridge Enrollment",
                    "CFBundleTypeRole": "Viewer",
                    "LSHandlerRank": "Owner",
                }
            ],
            "CFBundleIdentifier": BUNDLE_IDENTIFIER,
            "CFBundleInfoDictionaryVersion": "6.0",
            "CFBundleName": "AdministrateMe Bridge Helper",
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "1.0",
            "CFBundleVersion": "1",
            "LSMinimumSystemVersion": "15.0",
            "NSHighResolutionCapable": True,
            "NSSupportsAutomaticTermination": False,
        }
    )
    atomic_write(
        info_path,
        plistlib.dumps(info, fmt=plistlib.FMT_XML, sort_keys=True),
    )
    atomic_write(
        resources / "handler-source.applescript", handler_source.encode("utf-8")
    )
    bundled_installer = resources / "install-member-bridge.sh"
    atomic_write(bundled_installer, installer.read_bytes())
    os.chmod(bundled_installer, 0o700)
    release_archive = resources / "administrateme-reviewed-release.tar"
    archived = subprocess.run(
        [
            "git",
            "-C",
            str(release_root),
            "archive",
            "--format=tar",
            f"--output={release_archive}",
            commit,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if archived.returncode:
        raise RuntimeError("the exact reviewed release could not be archived")
    os.chmod(release_archive, 0o600)

    secure_code_script = b"""set response to display dialog "Paste the one-time code copied by AdministrateMe Settings. The code stays hidden and is never placed in a URL or command argument." default answer "" hidden answer true buttons {"Cancel", "Continue"} default button "Continue" cancel button "Cancel" with title "AdministrateMe Bridge Helper"\nreturn text returned of response\n"""
    review_script = b"""on run argv\n  set summaryFile to POSIX file (item 1 of argv)\n  set summaryText to read summaryFile\n  display dialog summaryText & return & return & "The helper will verify Tailscale, ask macOS - not the helper - for each selected permission, and start no connector until central attestation." buttons {"Cancel", "Accept on this Mac"} default button "Accept on this Mac" cancel button "Cancel" with title "Review Bridge Enrollment"\nend run\n"""
    failed_script = b"""display dialog "Enrollment did not finish. No provider write was authorized. Return to AdministrateMe Settings to repair, retry, regenerate, or revoke this request." buttons {"OK"} default button "OK" with icon caution with title "Bridge Enrollment Stopped"\n"""
    complete_script = b"""display dialog "The reviewed bridge enrollment flow finished. AdministrateMe Settings shows each source independently while observations settle. No provider write was authorized." buttons {"OK"} default button "OK" with title "Bridge Enrollment Complete"\n"""
    manage_script = b"""set answer to display dialog "Continue selected local permission steps, or uninstall bridge-owned files from this Mac. Revoke the bridge in AdministrateMe Settings to stop central ingestion." buttons {"Cancel", "Uninstall Locally", "Continue Enrollment"} default button "Continue Enrollment" cancel button "Cancel" with title "Manage AdministrateMe Bridge"\nreturn button returned of answer\n"""
    for name, value in (
        ("secure-code.applescript", secure_code_script),
        ("review.applescript", review_script),
        ("failed.applescript", failed_script),
        ("complete.applescript", complete_script),
        ("manage.applescript", manage_script),
    ):
        atomic_write(resources / name, value)

    quoted_commit = shlex.quote(commit)
    installer_sha256 = hashlib.sha256(bundled_installer.read_bytes()).hexdigest()
    release_archive_sha256 = hashlib.sha256(release_archive.read_bytes()).hexdigest()
    quoted_archive_sha256 = shlex.quote(release_archive_sha256)
    launcher = f"""#!/bin/bash
set -Eeuo pipefail
umask 077
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

PACKAGE="${{1:-}}"
CONTENTS_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")/.." && pwd -P)"
RESOURCES="$CONTENTS_DIR/Resources"
INSTALLER="$RESOURCES/install-member-bridge.sh"
RELEASE_ARCHIVE="$RESOURCES/administrateme-reviewed-release.tar"
EXPECTED_COMMIT={quoted_commit}
RELEASE_ARCHIVE_SHA256={quoted_archive_sha256}
BRIDGE_ROOT="$HOME/Library/Application Support/AdministrateMe Bridge"
CODE_FILE="$(mktemp "${{TMPDIR:-/tmp}}/administrateme-bridge-code.XXXXXX")"
PREVIEW_FILE="$(mktemp "${{TMPDIR:-/tmp}}/administrateme-bridge-preview.XXXXXX")"
LOG_FILE="$(mktemp "${{TMPDIR:-/tmp}}/administrateme-bridge-helper.XXXXXX")"
cleanup() {{ rm -f "$CODE_FILE" "$PREVIEW_FILE" "$LOG_FILE"; }}
trap cleanup EXIT

fail_closed() {{
  /usr/bin/osascript "$RESOURCES/failed.applescript" >/dev/null 2>&1 || true
  exit 1
}}

if [[ "$PACKAGE" == "--manage" ]]; then
  action="$(/usr/bin/osascript "$RESOURCES/manage.applescript")" || exit 0
  release_python="$BRIDGE_ROOT/app/releases/$EXPECTED_COMMIT/.venv/bin/python"
  [[ -x "$release_python" ]] || fail_closed
  if [[ "$action" == "Continue Enrollment" ]]; then
    exec "$release_python" -m bootstrap.cli bridge-complete --bridge-root "$BRIDGE_ROOT"
  fi
  if [[ "$action" == "Uninstall Locally" ]]; then
    exec "$release_python" -m bootstrap.cli bridge-uninstall --bridge-root "$BRIDGE_ROOT"
  fi
  exit 0
fi

[[ -n "$PACKAGE" && "$PACKAGE" == *.adminmebridge && -f "$PACKAGE" && ! -L "$PACKAGE" ]] || fail_closed
TRANSFER_CODE="$(/usr/bin/osascript "$RESOURCES/secure-code.applescript")" || exit 0
[[ -n "$TRANSFER_CODE" && "$TRANSFER_CODE" != *$'\\n'* && "$TRANSFER_CODE" != *$'\\r'* ]] || fail_closed
/usr/bin/printf '%s' "$TRANSFER_CODE" >"$CODE_FILE"
/bin/chmod 600 "$CODE_FILE"
unset TRANSFER_CODE

if ! "$INSTALLER" --expected-commit "$EXPECTED_COMMIT" --release-archive "$RELEASE_ARCHIVE" --release-sha256 "$RELEASE_ARCHIVE_SHA256" --preview-only --transfer-code-file "$CODE_FILE" "$PACKAGE" >"$PREVIEW_FILE" 2>"$LOG_FILE"; then
  fail_closed
fi
/usr/bin/osascript "$RESOURCES/review.applescript" "$PREVIEW_FILE" >/dev/null || exit 0
if ! "$INSTALLER" --expected-commit "$EXPECTED_COMMIT" --release-archive "$RELEASE_ARCHIVE" --release-sha256 "$RELEASE_ARCHIVE_SHA256" --complete-wizard --transfer-code-file "$CODE_FILE" "$PACKAGE" >"$LOG_FILE" 2>&1; then
  fail_closed
fi
/usr/bin/osascript "$RESOURCES/complete.applescript" >/dev/null
""".encode("utf-8")
    executable = resources / "bridge-helper-launcher.sh"
    atomic_write(executable, launcher)
    os.chmod(executable, 0o700)

    manifest = {
        "contract": HELPER_APP_CONTRACT,
        "bundle_identifier": BUNDLE_IDENTIFIER,
        "release_commit": commit,
        "installer_path": "Contents/Resources/install-member-bridge.sh",
        "installer_sha256": installer_sha256,
        "release_archive_path": "Contents/Resources/administrateme-reviewed-release.tar",
        "release_archive_sha256": release_archive_sha256,
        "document_extension": "adminmebridge",
        "signed_package_required": True,
        "transfer_secret_in_argv": False,
        "transfer_secret_in_url": False,
        "provider_writes": False,
        "auto_updater": False,
        "remote_shell": False,
        "arbitrary_central_code": False,
        "tcc_dialog_automation": False,
    }
    atomic_write(
        resources / "helper-manifest.json",
        (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    # osacompile seals the initial droplet before we add the reviewed resources
    # and document metadata. Seal the completed bundle, then verify it before
    # returning a distributable helper. This local ad-hoc signature makes no
    # Developer ID or notarization claim and does not change Gatekeeper policy.
    for arguments in (
        ["--force", "--sign", "-", "--timestamp=none"],
        ["--verify", "--deep", "--strict"],
    ):
        sealed = subprocess.run(
            ["/usr/bin/codesign", *arguments, str(app)],
            check=False,
            capture_output=True,
            text=True,
        )
        if sealed.returncode:
            raise RuntimeError("the completed bridge helper signature is invalid")
    return HelperAppBuild(
        path=str(app),
        bundle_identifier=BUNDLE_IDENTIFIER,
        release_commit=commit,
        installer_sha256=installer_sha256,
        release_archive_sha256=release_archive_sha256,
        document_extension="adminmebridge",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build-member-bridge-helper-app")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--expected-commit", required=True)
    args = parser.parse_args(argv)
    result = build_member_bridge_helper_app(
        args.destination, expected_commit=args.expected_commit
    )
    print(json.dumps(result.__dict__, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BUNDLE_IDENTIFIER",
    "HELPER_APP_CONTRACT",
    "HelperAppBuild",
    "build_member_bridge_helper_app",
]
