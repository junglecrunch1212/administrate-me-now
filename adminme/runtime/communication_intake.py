"""Immutable release intent for the staged communication-intake transition.

The prerequisite runtime already refuses passive mining, but does not select a
historical retirement watermark. Only a cutover release creates that durable
epoch. Once present, the epoch takes precedence over either release role.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from adminme.communication_intake_policy import POLICY_ID


@dataclass(frozen=True)
class CommunicationIntakeRelease:
    role: Literal["cutover", "rollback-prerequisite"]
    default_policy: str | None
    supported_policies: frozenset[str]

    @property
    def activates_cutover(self) -> bool:
        return self.role == "cutover"


def resolve_communication_intake_release(
    versions: dict[str, Any],
) -> CommunicationIntakeRelease:
    """Reject an ambiguous precursor or claims this runtime cannot enforce."""
    application = versions.get("administrateme")
    if not isinstance(application, dict):
        raise ValueError("communication intake release manifest is invalid")
    policies = application.get("supported_communication_intake_policies")
    default = application.get("default_communication_intake_policy")
    role = application.get("communication_intake_release_role")
    if not isinstance(policies, list) or policies != [POLICY_ID]:
        raise ValueError("communication intake release support is invalid")
    if default == POLICY_ID and role in (None, "cutover"):
        return CommunicationIntakeRelease("cutover", POLICY_ID, frozenset(policies))
    if default is None and role == "rollback-prerequisite":
        return CommunicationIntakeRelease(
            "rollback-prerequisite", None, frozenset(policies)
        )
    raise ValueError("communication intake release role contradicts its default")


def load_communication_intake_release(root: Path) -> CommunicationIntakeRelease:
    """Read only the reviewed source manifest, never mutable instance settings."""
    try:
        document = json.loads((root / "bootstrap" / "versions.json").read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(
            "communication intake release manifest is unavailable"
        ) from exc
    if not isinstance(document, dict):
        raise ValueError("communication intake release manifest is invalid")
    return resolve_communication_intake_release(document)
