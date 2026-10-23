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

import bootstrap
from bootstrap import contracts, member_bridge_component, source_identity
from bootstrap.contracts import atomic_write, repository_root
from bootstrap.member_bridge_component import (
    HelperComponentInputs,
    PERMISSION_RECOVERY_COMPATIBILITY,
    PERMISSION_RECOVERY_PROTOCOL,
    inspect_helper_component,
    verify_runtime_archive,
)
from bootstrap.source_identity import (
    reviewed_candidate_commit,
    sanitized_git_environment,
)


HELPER_APP_CONTRACT = "administrateme-member-bridge-helper-app-v1"
BUNDLE_IDENTIFIER = "com.administrateme.member-bridge-helper"


def _helper_launcher(
    commit: str,
    archive_sha256: str,
    *,
    permission_controller_sha256: str,
    launch_controller_sha256: str,
    component_commit: str | None = None,
) -> bytes:
    """Render the sealed launcher; no runtime diagnostic or UI override exists."""

    component_commit = component_commit or commit
    if (
        not re.fullmatch(r"[0-9a-f]{40}", commit)
        or not re.fullmatch(r"[0-9a-f]{40}", component_commit)
        or not re.fullmatch(r"[0-9a-f]{64}", archive_sha256)
        or not re.fullmatch(r"[0-9a-f]{64}", permission_controller_sha256)
        or not re.fullmatch(r"[0-9a-f]{64}", launch_controller_sha256)
    ):
        raise ValueError("helper launcher requires exact source coordinates")
    template = r"""#!/bin/bash
set -Eeuo pipefail
umask 077
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

PACKAGE="${1:-}"
CONTENTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
RESOURCES="$CONTENTS_DIR/Resources"
INSTALLER="$RESOURCES/install-member-bridge.sh"
PERMISSION_CONTROLLER="$RESOURCES/member_bridge_permission_controller.py"
PERMISSION_CONTROLLER_SHA256=@CONTROLLER_SHA256@
LAUNCH_CONTROLLER="$RESOURCES/member_bridge_launch.py"
LAUNCH_CONTROLLER_SHA256=@LAUNCH_CONTROLLER_SHA256@
RELEASE_ARCHIVE="$RESOURCES/administrateme-reviewed-release.tar"
EXPECTED_COMMIT=@COMMIT@
HELPER_COMPONENT_COMMIT=@COMPONENT_COMMIT@
RELEASE_ARCHIVE_SHA256=@ARCHIVE_SHA256@
BRIDGE_ROOT="$HOME/Library/Application Support/AdministrateMe Bridge"
CODE_FILE=""
PREVIEW_FILE=""
LOG_FILE=""
PERMISSION_RESULT_FILE=""
ATTEMPT_DIR=""
PHASE="diagnostics_setup"
OPERATION="enroll"
OUTCOME="running"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

remove_temporary_files() {
  [[ -z "$CODE_FILE" ]] || rm -f "$CODE_FILE"
  [[ -z "$PREVIEW_FILE" ]] || rm -f "$PREVIEW_FILE"
  [[ -z "$LOG_FILE" ]] || rm -f "$LOG_FILE"
  [[ -z "$PERMISSION_RESULT_FILE" ]] || rm -f "$PERMISSION_RESULT_FILE"
  return 0
}

read_phase() {
  [[ -n "$ATTEMPT_DIR" ]] || return 0
  while IFS= read -r item; do
    case "$item" in
      preflight|python_setup|release_preparation|native_build_setup|tooling_setup|environment_setup|dependency_install|package_preview|local_acceptance|permission_setup) PHASE="$item" ;;
    esac
  done <"$ATTEMPT_DIR/phases"
}

write_receipt() {
  local exit_code="$1" receipt_temp
  [[ -n "$ATTEMPT_DIR" && -d "$ATTEMPT_DIR" && ! -L "$ATTEMPT_DIR" && -O "$ATTEMPT_DIR" ]] || return 0
  receipt_temp="$(mktemp "$ATTEMPT_DIR/.receipt.XXXXXX")" || return 1
  printf '{"contract":"administrateme-bridge-setup-diagnostic-v1","release_commit":"%s","helper_component_commit":"%s","operation":"%s","phase":"%s","outcome":"%s","exit_code":%s,"started_at":"%s","updated_at":"%s","dependency_log_limit_bytes":262144}\n' \
    "$EXPECTED_COMMIT" "$HELPER_COMPONENT_COMMIT" "$OPERATION" "$PHASE" "$OUTCOME" "$exit_code" "$STARTED_AT" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$receipt_temp"
  mv -f "$receipt_temp" "$ATTEMPT_DIR/receipt.json"
}

finish() {
  local exit_code="$?"
  trap - EXIT
  if [[ "$OUTCOME" == "running" ]]; then
    OUTCOME="failed"
    read_phase || true
  fi
  remove_temporary_files
  write_receipt "$exit_code" || true
  exit "$exit_code"
}
trap finish EXIT

fail_closed() {
  local exit_code="${1:-1}"
  OUTCOME="failed"
  [[ "$PHASE" == "permission_setup" ]] || read_phase
  remove_temporary_files
  write_receipt "$exit_code" || true
  /usr/bin/osascript "$RESOURCES/failed.applescript" "$PHASE" >/dev/null 2>&1 || true
  exit "$exit_code"
}

# Descend only through owned, non-writable-by-others, non-symlink directories.
# Each attempt gets a fresh private directory; no caller-selected output path
# or existing diagnostic file is opened for writing.
safe_directory() {
  local directory="$1" mode
  [[ -d "$directory" && ! -L "$directory" && -O "$directory" ]] || return 1
  mode="$(stat -f '%Lp' "$directory" 2>/dev/null)" || mode="$(stat -c '%a' "$directory")"
  [[ "$mode" =~ ^[0-7]{3,4}$ ]] && (( (8#$mode & 0022) == 0 ))
}
[[ "$HOME" == /* && "$HOME" != "/" && "$(cd "$HOME" && pwd -P)" == "$HOME" ]] || fail_closed
safe_directory "$HOME" || fail_closed
diagnostic_root="$HOME"
for component in Library Logs "AdministrateMe Bridge" Setup; do
  diagnostic_root="$diagnostic_root/$component"
  if [[ ! -e "$diagnostic_root" && ! -L "$diagnostic_root" ]]; then
    mkdir -m 700 "$diagnostic_root" || fail_closed
  fi
  safe_directory "$diagnostic_root" || fail_closed
done
ATTEMPT_DIR="$(mktemp -d "$diagnostic_root/attempt.XXXXXX")" || fail_closed
: >"$ATTEMPT_DIR/phases"
: >"$ATTEMPT_DIR/dependency-install.log"
write_receipt 0
CODE_FILE="$(mktemp "${TMPDIR:-/tmp}/administrateme-bridge-code.XXXXXX")"
PREVIEW_FILE="$(mktemp "${TMPDIR:-/tmp}/administrateme-bridge-preview.XXXXXX")"
LOG_FILE="$(mktemp "${TMPDIR:-/tmp}/administrateme-bridge-helper.XXXXXX")"
PERMISSION_RESULT_FILE="$(mktemp "${TMPDIR:-/tmp}/administrateme-bridge-permissions.XXXXXX")"

prepare_retained_runtime() {
  release_root="$BRIDGE_ROOT/app/releases/$EXPECTED_COMMIT"
  release_python="$release_root/.venv/bin/python"
  [[ -d "$release_root" && ! -L "$release_root" && -x "$release_python" ]] || fail_closed
  export ADMINME_REPOSITORY_ROOT="$release_root"
  export ADMINME_REVIEWED_COMMIT="$EXPECTED_COMMIT"
  cd "$release_root"
}

verify_permission_controller() {
  local actual_hash
  [[ -f "$PERMISSION_CONTROLLER" && ! -L "$PERMISSION_CONTROLLER" && -O "$PERMISSION_CONTROLLER" ]] || return 1
  actual_hash="$(/usr/bin/shasum -a 256 "$PERMISSION_CONTROLLER")" || return 1
  [[ "${actual_hash%% *}" == "$PERMISSION_CONTROLLER_SHA256" ]] || return 1
  [[ -f "$LAUNCH_CONTROLLER" && ! -L "$LAUNCH_CONTROLLER" && -O "$LAUNCH_CONTROLLER" ]] || return 1
  actual_hash="$(/usr/bin/shasum -a 256 "$LAUNCH_CONTROLLER")" || return 1
  [[ "${actual_hash%% *}" == "$LAUNCH_CONTROLLER_SHA256" ]]
}

run_permissions() {
  local operation="$1" controller_exit=0 result_outcome
  PHASE="permission_setup"
  verify_permission_controller || fail_closed
  "$release_python" -I "$PERMISSION_CONTROLLER" --runtime-root "$release_root" --expected-runtime-commit "$EXPECTED_COMMIT" --expected-central-commit "$HELPER_COMPONENT_COMMIT" --bridge-root "$BRIDGE_ROOT" --launch-controller "$LAUNCH_CONTROLLER" --launch-controller-sha256 "$LAUNCH_CONTROLLER_SHA256" --operation "$operation" >"$PERMISSION_RESULT_FILE" 2>"$LOG_FILE" || controller_exit="$?"
  # The exact sealed controller validates its bounded, content-free result.
  # Neither exception text nor unvalidated controller output is retained.
  verify_permission_controller || fail_closed
  if result_outcome="$("$release_python" -I - "$PERMISSION_CONTROLLER" "$PERMISSION_RESULT_FILE" "$operation" "$controller_exit" "$ATTEMPT_DIR/permission-result.json" 2>"$LOG_FILE" <<'PY_RESULT'
import json
from pathlib import Path
import runpy
import sys

controller, result_path, operation, exit_code, destination = sys.argv[1:]
with Path(result_path).open("rb") as stream:
    raw = stream.read(8193)
if len(raw) > 8192:
    raise ValueError("oversized permission result")
def unique_fields(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate permission result fields")
    return result

validator = runpy.run_path(controller)["validate_result"]
result = validator(json.loads(raw, object_pairs_hook=unique_fields), operation=operation, exit_code=int(exit_code))
with Path(destination).open("x", encoding="utf-8") as stream:
    stream.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
print(result["outcome"])
PY_RESULT
  )"; then :; else
    fail_closed 1
  fi
  remove_temporary_files
  case "$result_outcome" in
    ready) [[ "$controller_exit" == 0 ]] || fail_closed; PHASE="complete"; OUTCOME="succeeded"; write_receipt 0; /usr/bin/osascript "$RESOURCES/complete.applescript" >/dev/null || true; exit 0 ;;
    cancelled) OUTCOME="cancelled" ;;
    blocked) OUTCOME="blocked" ;;
    unknown) OUTCOME="unknown" ;;
    *) OUTCOME="failed" ;;
  esac
  write_receipt "$controller_exit" || true
  /usr/bin/osascript "$RESOURCES/permission-stopped.applescript" "$result_outcome" >/dev/null 2>&1 || true
  exit "$controller_exit"
}

if [[ "$PACKAGE" == "--manage" ]]; then
  OPERATION="manage"
  PHASE="manage"
  if action="$(/usr/bin/osascript "$RESOURCES/manage.applescript")"; then :; else
    OUTCOME="cancelled"
    exit 0
  fi
  prepare_retained_runtime
  case "$action" in
    "Continue Enrollment") OPERATION="continue"; run_permissions continue ;;
    "Uninstall Locally") OPERATION="uninstall"; PHASE="uninstall" ;;
    *) OUTCOME="cancelled"; exit 0 ;;
  esac
  if "$release_python" -m bootstrap.cli bridge-uninstall --bridge-root "$BRIDGE_ROOT" >"$LOG_FILE" 2>&1; then
    OUTCOME="succeeded"
    exit 0
  else
    fail_closed "$?"
  fi
fi

PHASE="helper_integrity"
verify_permission_controller || fail_closed
PHASE="handoff_selection"
[[ -n "$PACKAGE" && "$PACKAGE" == *.adminmebridge && -f "$PACKAGE" && ! -L "$PACKAGE" ]] || fail_closed
PHASE="transfer_code"
if TRANSFER_CODE="$(/usr/bin/osascript "$RESOURCES/secure-code.applescript")"; then :; else
  OUTCOME="cancelled"
  exit 0
fi
[[ -n "$TRANSFER_CODE" && "$TRANSFER_CODE" != *$'\n'* && "$TRANSFER_CODE" != *$'\r'* ]] || fail_closed
/usr/bin/printf '%s' "$TRANSFER_CODE" >"$CODE_FILE"
/bin/chmod 600 "$CODE_FILE"
unset TRANSFER_CODE

if ADMINME_BRIDGE_DIAGNOSTICS_FD=3 "$INSTALLER" --expected-commit "$EXPECTED_COMMIT" --release-archive "$RELEASE_ARCHIVE" --release-sha256 "$RELEASE_ARCHIVE_SHA256" --preview-only --transfer-code-file "$CODE_FILE" "$PACKAGE" >"$PREVIEW_FILE" 2>"$LOG_FILE" 3>"$ATTEMPT_DIR/phases" 4<>"$ATTEMPT_DIR/dependency-install.log"; then :; else
  fail_closed "$?"
fi
PHASE="review"
if /usr/bin/osascript "$RESOURCES/review.applescript" "$PREVIEW_FILE" >/dev/null; then :; else
  OUTCOME="cancelled"
  exit 0
fi
if ADMINME_BRIDGE_DIAGNOSTICS_FD=3 "$INSTALLER" --expected-commit "$EXPECTED_COMMIT" --release-archive "$RELEASE_ARCHIVE" --release-sha256 "$RELEASE_ARCHIVE_SHA256" --transfer-code-file "$CODE_FILE" "$PACKAGE" >"$LOG_FILE" 2>&1 3>"$ATTEMPT_DIR/phases" 4<>"$ATTEMPT_DIR/dependency-install.log"; then :; else
  fail_closed "$?"
fi
prepare_retained_runtime
run_permissions initial
"""
    return (
        template.replace("@COMMIT@", shlex.quote(commit))
        .replace("@COMPONENT_COMMIT@", shlex.quote(component_commit))
        .replace("@ARCHIVE_SHA256@", shlex.quote(archive_sha256))
        .replace("@CONTROLLER_SHA256@", shlex.quote(permission_controller_sha256))
        .replace("@LAUNCH_CONTROLLER_SHA256@", shlex.quote(launch_controller_sha256))
        .encode("utf-8")
    )


@dataclass(frozen=True)
class HelperAppBuild:
    path: str
    bundle_identifier: str
    release_commit: str
    helper_component_commit: str
    installer_sha256: str
    permission_controller_sha256: str
    launch_controller_sha256: str
    release_archive_sha256: str
    document_extension: str
    provider_effects: bool = False


def _verify_loaded_build_sources(inputs: HelperComponentInputs) -> None:
    # A clean Git index can hide assume-unchanged files. Imported code can also
    # come from another checkout, so verify its bytes against the fixed objects.
    for path, loaded_file in (
        ("bootstrap/__init__.py", bootstrap.__file__),
        ("bootstrap/member_bridge_helper_app.py", __file__),
        ("bootstrap/member_bridge_component.py", member_bridge_component.__file__),
        ("bootstrap/contracts.py", contracts.__file__),
        ("bootstrap/source_identity.py", source_identity.__file__),
    ):
        if loaded_file is None:
            raise RuntimeError("loaded helper build code has no fixed source")
        source = Path(loaded_file)
        coordinate = (
            inputs.component_sources.get(path) or inputs.runtime_inventory[path]
        )
        if (
            source.is_symlink()
            or source.read_bytes() != inputs.component_payloads[path]
            or bool(source.stat().st_mode & 0o111) != (coordinate["mode"] == "100755")
        ):
            raise RuntimeError(
                "loaded helper build code differs from its component commit"
            )


def build_member_bridge_helper_app(
    destination: Path,
    *,
    root: Path | None = None,
    expected_commit: str | None = None,
    runtime_commit: str | None = None,
    expected_runtime_archive_sha256: str | None = None,
    runtime_compatibility_contract: str | None = None,
) -> HelperAppBuild:
    """Create a fail-closed `.app` bundle without replacing an existing app."""

    release_root = (root or repository_root()).resolve()
    component_commit = expected_commit or reviewed_candidate_commit(release_root)
    if not re.fullmatch(r"[0-9a-f]{40}", component_commit):
        raise RuntimeError("helper component requires an exact source commit")
    commit = runtime_commit or component_commit
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RuntimeError("helper runtime requires an exact retained commit")
    if expected_runtime_archive_sha256 is not None and not re.fullmatch(
        r"[0-9a-f]{64}", expected_runtime_archive_sha256
    ):
        raise RuntimeError("retained runtime archive requires an exact SHA-256")
    if commit != component_commit and expected_runtime_archive_sha256 is None:
        raise RuntimeError(
            "a distinct runtime requires its previously verified archive SHA-256"
        )
    inputs = inspect_helper_component(
        release_root,
        component_commit,
        commit,
        compatibility_contract=runtime_compatibility_contract,
    )
    _verify_loaded_build_sources(inputs)

    app = destination.expanduser().resolve()
    if (
        app.suffix != ".app"
        or app == Path("/")
        or app.exists()
        or app.is_symlink()
        or app.is_relative_to(release_root)
    ):
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
            "NSCalendarsFullAccessUsageDescription": (
                "Connect the calendars you select for read-only AdministrateMe "
                "bridge setup. macOS grants broader calendar access; this setup "
                "does not authorize calendar changes."
            ),
            "NSRemindersFullAccessUsageDescription": (
                "Connect the reminder lists you select for read-only "
                "AdministrateMe bridge setup. macOS grants broader reminder "
                "access; this setup does not authorize reminder changes."
            ),
            "NSAppleEventsUsageDescription": (
                "Connect the Notes service you select and open permission settings "
                "during AdministrateMe bridge setup. No provider changes are "
                "authorized by this setup."
            ),
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
    atomic_write(
        bundled_installer,
        inputs.component_payloads["bootstrap/install-member-bridge.sh"],
    )
    os.chmod(bundled_installer, 0o700)
    permission_controller = resources / "member_bridge_permission_controller.py"
    atomic_write(
        permission_controller,
        inputs.component_payloads["bootstrap/member_bridge_permission_controller.py"],
    )
    os.chmod(permission_controller, 0o600)
    permission_controller_sha256 = hashlib.sha256(
        permission_controller.read_bytes()
    ).hexdigest()
    launch_controller = resources / "member_bridge_launch.py"
    atomic_write(
        launch_controller,
        inputs.component_payloads["bootstrap/member_bridge_launch.py"],
    )
    os.chmod(launch_controller, 0o600)
    launch_controller_sha256 = hashlib.sha256(
        launch_controller.read_bytes()
    ).hexdigest()
    release_archive = resources / "administrateme-reviewed-release.tar"
    archived = subprocess.run(
        [
            "/usr/bin/git",
            "--no-replace-objects",
            "-c",
            "core.fsmonitor=false",
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
        env=sanitized_git_environment(),
        timeout=120,
    )
    if archived.returncode:
        raise RuntimeError("the exact reviewed release could not be archived")
    os.chmod(release_archive, 0o600)
    verify_runtime_archive(release_archive, inputs.runtime_inventory)
    release_archive_sha256 = hashlib.sha256(release_archive.read_bytes()).hexdigest()
    if (
        expected_runtime_archive_sha256 is not None
        and release_archive_sha256 != expected_runtime_archive_sha256
    ):
        raise RuntimeError(
            "retained runtime archive does not match its verified SHA-256"
        )

    secure_code_script = b"""set response to display dialog "Paste the one-time code copied by AdministrateMe Settings. The code stays hidden and is never placed in a URL or command argument." default answer "" hidden answer true buttons {"Cancel", "Continue"} default button "Continue" cancel button "Cancel" with title "AdministrateMe Bridge Helper"\nreturn text returned of response\n"""
    review_script = b"""on run argv\n  set summaryFile to POSIX file (item 1 of argv)\n  set summaryText to read summaryFile\n  display dialog summaryText & return & return & "The helper will verify Tailscale, ask macOS - not the helper - for each selected permission, and start no connector until central attestation." buttons {"Cancel", "Accept on this Mac"} default button "Accept on this Mac" cancel button "Cancel" with title "Review Bridge Enrollment"\nend run\n"""
    failed_script = b"""on run argv
  set phase to item 1 of argv
  set explanation to "Enrollment did not finish. A private setup diagnostic was saved on this Mac. Return to AdministrateMe Settings to review the request before retrying."
  if phase is "diagnostics_setup" then
    set explanation to "The helper could not safely create its private diagnostic folder. Repair the local setup folder permissions before retrying."
  else if phase is in {"preflight", "python_setup", "release_preparation"} then
    set explanation to "Local installation prerequisites or reviewed release preparation stopped. A private setup diagnostic was saved. Repair the reported local setup problem before retrying this request in AdministrateMe Settings."
  else if phase is "native_build_setup" then
    set explanation to "The selected developer tools and SDK could not be prepared for installation. A private setup diagnostic was saved. Repair the reported developer-tool or explicit SDK setting before retrying."
  else if phase is in {"tooling_setup", "environment_setup", "dependency_install"} then
    set explanation to "Required software installation stopped before enrollment. A private installation log and setup diagnostic were saved. Repair the installation problem before retrying this request in AdministrateMe Settings."
  else if phase is in {"handoff_selection", "transfer_code", "package_preview"} then
    set explanation to "The secure handoff could not be opened. Check this request in AdministrateMe Settings and download a fresh handoff if it has expired. Keep its one-time code private."
  else if phase is in {"local_acceptance", "permission_setup", "continue_enrollment"} then
    set explanation to "Local enrollment or a selected permission step stopped. Review this request in AdministrateMe Settings, then use Continue Enrollment only after the reported problem is repaired."
  else if phase is "uninstall" then
    set explanation to "Local removal did not finish. A private setup diagnostic was saved. Review the bridge in AdministrateMe Settings; revoke it there to stop central ingestion."
  end if
  display dialog explanation & return & return & "No provider write was authorized." buttons {"OK"} default button "OK" with icon caution with title "Bridge Enrollment Stopped"
end run
"""
    complete_script = b"""display dialog "The reviewed bridge enrollment flow finished. AdministrateMe Settings shows each source independently while observations settle. No provider write was authorized." buttons {"OK"} default button "OK" with title "Bridge Enrollment Complete"\n"""
    permission_stopped_script = b"""on run argv
  set outcome to item 1 of argv
  set explanation to "Enrollment has not completed. Review the source statuses in AdministrateMe Settings before continuing."
  if outcome is "blocked" then
    set explanation to "One or more selected sources still need attention. Review their statuses in AdministrateMe Settings, repair the reported permission or source selection, then open this helper and choose Continue Enrollment."
  else if outcome is "cancelled" then
    set explanation to "Enrollment was cancelled before all selected sources finished. Earlier accepted steps are retained. Review the source statuses before using Continue Enrollment."
  else if outcome is "unknown" then
    set explanation to "The final enrollment result could not be confirmed. Review the current request in AdministrateMe Settings before continuing; do not create another request for this attempt."
  end if
  display dialog explanation & return & return & "No provider write was authorized." buttons {"OK"} default button "OK" with icon caution with title "Bridge Enrollment Needs Attention"
end run
"""
    manage_script = b"""set answer to display dialog "Continue selected local permission steps, or uninstall bridge-owned files from this Mac. Revoke the bridge in AdministrateMe Settings to stop central ingestion." buttons {"Cancel", "Uninstall Locally", "Continue Enrollment"} default button "Continue Enrollment" cancel button "Cancel" with title "Manage AdministrateMe Bridge"\nreturn button returned of answer\n"""
    for name, value in (
        ("secure-code.applescript", secure_code_script),
        ("review.applescript", review_script),
        ("failed.applescript", failed_script),
        ("complete.applescript", complete_script),
        ("manage.applescript", manage_script),
        ("permission-stopped.applescript", permission_stopped_script),
    ):
        atomic_write(resources / name, value)

    installer_sha256 = hashlib.sha256(bundled_installer.read_bytes()).hexdigest()
    launcher = _helper_launcher(
        commit,
        release_archive_sha256,
        component_commit=component_commit,
        permission_controller_sha256=permission_controller_sha256,
        launch_controller_sha256=launch_controller_sha256,
    )
    executable = resources / "bridge-helper-launcher.sh"
    atomic_write(executable, launcher)
    os.chmod(executable, 0o700)

    manifest = {
        "contract": HELPER_APP_CONTRACT,
        "bundle_identifier": BUNDLE_IDENTIFIER,
        "release_commit": commit,
        "runtime_commit": commit,
        "helper_component_commit": component_commit,
        "helper_component_sources": inputs.component_sources,
        "runtime_compatibility": {
            **inputs.compatibility,
            "runtime_archive_sha256": release_archive_sha256,
        },
        "required_central_protocol": PERMISSION_RECOVERY_PROTOCOL,
        "required_central_release_commit": component_commit,
        "installer_path": "Contents/Resources/install-member-bridge.sh",
        "installer_sha256": installer_sha256,
        "permission_controller_path": "Contents/Resources/member_bridge_permission_controller.py",
        "permission_controller_sha256": permission_controller_sha256,
        "launch_controller_path": "Contents/Resources/member_bridge_launch.py",
        "launch_controller_sha256": launch_controller_sha256,
        "launcher_sha256": hashlib.sha256(launcher).hexdigest(),
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
        "setup_diagnostics": {
            "contract": "administrateme-bridge-setup-diagnostic-v1",
            "private_terminal_receipts": True,
            "dependency_log_limit_bytes": 262144,
            "retains_enrollment_output": False,
            "retains_transfer_code": False,
            "permission_result_limit_bytes": 8192,
            "retains_validated_permission_result_only": True,
        },
    }
    atomic_write(
        resources / "helper-manifest.json",
        (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    final_inputs = inspect_helper_component(
        release_root,
        component_commit,
        commit,
        compatibility_contract=runtime_compatibility_contract,
    )
    _verify_loaded_build_sources(final_inputs)
    if final_inputs != inputs:
        raise RuntimeError("helper component inputs changed during packaging")
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
        helper_component_commit=component_commit,
        installer_sha256=installer_sha256,
        permission_controller_sha256=permission_controller_sha256,
        launch_controller_sha256=launch_controller_sha256,
        release_archive_sha256=release_archive_sha256,
        document_extension="adminmebridge",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build-member-bridge-helper-app")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--runtime-commit")
    parser.add_argument("--expected-runtime-archive-sha256")
    parser.add_argument(
        "--runtime-compatibility-contract",
        choices=(PERMISSION_RECOVERY_COMPATIBILITY,),
    )
    args = parser.parse_args(argv)
    result = build_member_bridge_helper_app(
        args.destination,
        expected_commit=args.expected_commit,
        runtime_commit=args.runtime_commit,
        expected_runtime_archive_sha256=args.expected_runtime_archive_sha256,
        runtime_compatibility_contract=args.runtime_compatibility_contract,
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
