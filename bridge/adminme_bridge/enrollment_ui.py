"""Resumable macOS UI for per-capability bridge enrollment.

The UI uses native AppleScript dialogs only as member-facing controls.  It
never clicks a permission prompt: the member opens macOS Settings, makes the
choice there, then reports that choice before a content-free local probe runs.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from adminme.bridge_enrollment import CAPABILITIES
from bootstrap.bridge_enrollment import BridgeEnrollmentError

from adminme_bridge.enrollment_helper import MemberBridgeEnrollmentHelper


@dataclass(frozen=True)
class LocalSourceChoice:
    source_coordinate: str | None = None
    account_coordinate: str | None = None


class EnrollmentInteraction(Protocol):
    def confirm_permission(
        self, capability_id: str, label: str, requirement: str
    ) -> bool: ...

    def choose_source(self, capability_id: str, label: str) -> LocalSourceChoice: ...

    def content_free_probe(
        self, capability_id: str, choice: LocalSourceChoice
    ) -> dict[str, Any]: ...

    def show_result(self, result: dict[str, Any]) -> None: ...


class MacOSEnrollmentInteraction:
    """Native, local-only dialog driver used by the packaged helper app."""

    @staticmethod
    def _osascript(script: str, *arguments: str) -> str:
        completed = subprocess.run(
            ["/usr/bin/osascript", "-e", script, *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if completed.returncode:
            raise BridgeEnrollmentError("the local enrollment step was cancelled")
        return completed.stdout.strip()

    @staticmethod
    def _jxa(script: str) -> Any:
        completed = subprocess.run(
            ["/usr/bin/osascript", "-l", "JavaScript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if completed.returncode:
            raise BridgeEnrollmentError("the selected local source is unavailable")
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise BridgeEnrollmentError(
                "local source discovery returned invalid metadata"
            ) from exc

    def confirm_permission(
        self, capability_id: str, label: str, requirement: str
    ) -> bool:
        del capability_id
        result = self._osascript(
            """on run argv
set sourceLabel to item 1 of argv
set requirementText to item 2 of argv
set answer to display dialog "macOS controls " & sourceLabel & ". " & requirementText & return & return & "Make the choice in System Settings, then continue here. The helper cannot click or bypass the macOS permission dialog." buttons {"Not Now", "Permission Granted"} default button "Permission Granted" cancel button "Not Now" with title "AdministrateMe Bridge Helper"
return button returned of answer
end run""",
            label,
            requirement,
        )
        return result == "Permission Granted"

    def _containers(self, capability_id: str) -> list[dict[str, str]]:
        scripts = {
            "apple_calendar": """var a=Application("Calendar"); JSON.stringify(a.calendars().map(function(x){return {label:x.name(),coordinate:x.uid()};}))""",
            "apple_reminders": """var a=Application("Reminders"); JSON.stringify(a.lists().map(function(x){return {label:x.name(),coordinate:x.id()};}))""",
        }
        rows = self._jxa(scripts[capability_id])
        if not isinstance(rows, list):
            raise BridgeEnrollmentError(
                "local source discovery returned invalid metadata"
            )
        return [
            {"label": str(row["label"]), "coordinate": str(row["coordinate"])}
            for row in rows
            if isinstance(row, dict) and row.get("label") and row.get("coordinate")
        ]

    def _choose_container(self, capability_id: str, label: str) -> LocalSourceChoice:
        rows = self._containers(capability_id)
        if not rows:
            raise BridgeEnrollmentError(
                "no compatible local source container was found"
            )
        displayed = [f"{row['label']} — {row['coordinate']}" for row in rows]
        selected = self._osascript(
            """on run argv
set sourceLabel to item 1 of argv
set sourceChoices to items 2 thru -1 of argv
set selectedItem to choose from list sourceChoices with title "Choose " & sourceLabel & " source" with prompt "Choose the one local container AdministrateMe may observe." without multiple selections allowed
if selectedItem is false then error number -128
return item 1 of selectedItem
end run""",
            label,
            *displayed,
        )
        index = displayed.index(selected)
        coordinate = rows[index]["coordinate"]
        return LocalSourceChoice(
            source_coordinate=coordinate,
            account_coordinate=f"{capability_id}:{coordinate}",
        )

    def _choose_folder(self, label: str) -> LocalSourceChoice:
        value = self._osascript(
            """on run argv
set chosenFolder to choose folder with prompt "Choose the local " & (item 1 of argv) & " folder AdministrateMe may observe."
return POSIX path of chosenFolder
end run""",
            label,
        )
        return LocalSourceChoice(source_coordinate=value)

    def choose_source(self, capability_id: str, label: str) -> LocalSourceChoice:
        if capability_id in {"apple_calendar", "apple_reminders"}:
            return self._choose_container(capability_id, label)
        if capability_id == "apple_contacts":
            return LocalSourceChoice(
                source_coordinate="all-containers",
                account_coordinate="apple_contacts:all-containers",
            )
        if capability_id in {"apple_voice_memos", "obsidian"}:
            return self._choose_folder(label)
        return LocalSourceChoice()

    def content_free_probe(
        self, capability_id: str, choice: LocalSourceChoice
    ) -> dict[str, Any]:
        passed = False
        if capability_id in {"apple_calendar", "apple_reminders"}:
            passed = any(
                row["coordinate"] == choice.source_coordinate
                for row in self._containers(capability_id)
            )
        elif capability_id == "apple_contacts":
            value = self._jxa(
                'var a=Application("Contacts"); JSON.stringify({available:a.people().length >= 0});'
            )
            passed = isinstance(value, dict) and value.get("available") is True
        elif capability_id == "apple_notes":
            value = self._jxa(
                'var a=Application("Notes"); JSON.stringify({available:a.accounts().length >= 0});'
            )
            passed = isinstance(value, dict) and value.get("available") is True
        else:
            root = Path(str(choice.source_coordinate or "")).expanduser()
            try:
                next(root.iterdir(), None)
                passed = True
            except OSError:
                passed = False
        return {"status": "passed" if passed else "failed", "content_exposed": False}

    def show_result(self, result: dict[str, Any]) -> None:
        active = ", ".join(result.get("observing", [])) or "None yet"
        blocked = ", ".join(result.get("blocked", [])) or "None"
        self._osascript(
            """on run argv
display dialog "Local enrollment steps are complete." & return & return & "Observing or settling: " & (item 1 of argv) & return & "Denied or blocked: " & (item 2 of argv) & return & return & "AdministrateMe Settings will show each source independently. No provider write was authorized." buttons {"Done"} default button "Done" with title "AdministrateMe Bridge Helper"
end run""",
            active,
            blocked,
        )


class MemberBridgeEnrollmentWizard:
    """Drive every selected capability through permission, proof, and start."""

    def __init__(
        self,
        helper: MemberBridgeEnrollmentHelper,
        interaction: EnrollmentInteraction,
    ) -> None:
        self.helper = helper
        self.interaction = interaction

    def run(self) -> dict[str, Any]:
        observing: list[str] = []
        blocked: list[str] = []
        for capability_id in tuple(self.helper.capability_states):
            state = self.helper.capability_states[capability_id]
            if state in {"observing", "settling", "settled"}:
                observing.append(str(CAPABILITIES[capability_id]["label"]))
                continue
            if state in {"permission_denied", "prerequisite_missing", "degraded"}:
                blocked.append(str(CAPABILITIES[capability_id]["label"]))
                continue
            descriptor = CAPABILITIES[capability_id]
            label = str(descriptor["label"])
            self.helper.open_permission_settings(capability_id)
            if not self.interaction.confirm_permission(
                capability_id, label, str(descriptor["local_permission"])
            ):
                self.helper.record_permission(capability_id, granted=False)
                blocked.append(label)
                continue
            try:
                choice = self.interaction.choose_source(capability_id, label)
                config = self.helper.pack_config_for(
                    capability_id,
                    source_coordinate=choice.source_coordinate,
                    account_coordinate=choice.account_coordinate,
                )
                self.helper.record_permission(
                    capability_id, granted=True, pack_config=config
                )

                def content_free_probe(
                    _capability: str,
                    _request: dict[str, Any],
                    *,
                    selected: LocalSourceChoice = choice,
                    current_capability: str = capability_id,
                ) -> dict[str, Any]:
                    return self.interaction.content_free_probe(
                        current_capability, selected
                    )

                self.helper.run_capability_test(
                    capability_id,
                    content_free_probe,
                )
                self.helper.prove_and_start(capability_id)
                self.helper.record_observing(capability_id)
                observing.append(label)
            except BridgeEnrollmentError:
                current = self.helper.capability_states.get(capability_id)
                if current in {"requested", "implementation_available"}:
                    self.helper.record_prerequisite_missing(
                        capability_id, reason="local_prerequisite_unavailable"
                    )
                elif current not in {"permission_denied", "prerequisite_missing"}:
                    self.helper.record_capability_degraded(
                        capability_id, reason="local_capability_test_failed"
                    )
                blocked.append(label)
        completion = self.helper.finish_permission_phase()
        result = {
            "state": completion["state"],
            "observing": observing,
            "blocked": blocked,
            "content_exposed": False,
            "provider_writes_authorized": False,
            "provider_effects": False,
        }
        self.interaction.show_result(result)
        return result


def run_macos_enrollment_wizard(helper: MemberBridgeEnrollmentHelper) -> dict[str, Any]:
    return MemberBridgeEnrollmentWizard(helper, MacOSEnrollmentInteraction()).run()


__all__ = [
    "EnrollmentInteraction",
    "LocalSourceChoice",
    "MacOSEnrollmentInteraction",
    "MemberBridgeEnrollmentWizard",
    "run_macos_enrollment_wizard",
]
