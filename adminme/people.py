"""Deterministic composition and provider truth for the People cockpit.

People is relationship memory, not an inferred address book.  Contact records
and message endpoints are evidence; only qualified Parties appear here.  The
rules in this module are pure and replayable.  A model may propose a fact for
Triage, but cannot promote a sender, merge identities, expose private fields,
or decide who deserves attention.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from adminme.surface_contracts import attention_explanation, surface_link


PROVIDER_CONTRACTS: dict[str, dict[str, Any]] = {
    "apple_contacts": {
        "label": "Apple Contacts",
        "runtime": "member_bridge",
        "operations": ["read", "create", "update", "delete", "groups"],
        "limitations": [
            "Requires Contacts permission on the member's signed-in Apple device.",
            "Provider groups and labels are preserved without granting household access.",
        ],
    },
    "google_people": {
        "label": "Google Contacts",
        "runtime": "central_oauth",
        "operations": ["read", "create", "update", "delete", "groups"],
        "limitations": [
            "Write-back requires the latest provider etag and explicit approval.",
            "Directory-only profiles are evidence, not writable personal contacts.",
        ],
    },
    "microsoft_graph": {
        "label": "Microsoft Contacts",
        "runtime": "central_oauth",
        "operations": ["read", "create", "update", "delete", "folders"],
        "limitations": [
            "Personal contacts and organization directory identities stay distinct.",
            "Write-back requires the latest provider change key and explicit approval.",
        ],
    },
    "carddav": {
        "label": "CardDAV",
        "runtime": "central_or_bridge",
        "operations": ["read", "create", "update", "delete", "address_books"],
        "limitations": [
            "Exact field and group support depends on the CardDAV server.",
            "Write-back requires the latest ETag and explicit approval.",
        ],
    },
}


def _iso_timestamp(value: Any) -> float:
    if not isinstance(value, str) or not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _name(row: dict[str, Any]) -> str:
    return str(row.get("display_name") or row.get("sort_name") or "Unnamed person")


def _relationship_label(row: dict[str, Any]) -> str:
    profile = row.get("profile") if isinstance(row.get("profile"), dict) else {}
    circle = profile.get("circle") if profile else None
    labels = [str(value) for value in row.get("relationship_labels") or []]
    if circle:
        return str(circle)
    if labels:
        return " · ".join(labels[:2])
    if row.get("kind") == "organization":
        return "Organization"
    return "Contact"


def _contact_gap_days(last_contact: Any, now: datetime) -> int | None:
    timestamp = _iso_timestamp(last_contact)
    if not timestamp:
        return None
    return max(0, int((now.timestamp() - timestamp) // 86400))


def _cue(row: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    profile = row.get("profile") if isinstance(row.get("profile"), dict) else {}
    if not profile or not profile.get("cues_enabled") or profile.get("suppressed"):
        return None
    cadence = profile.get("desired_cadence_days")
    if not isinstance(cadence, int) or cadence < 1:
        return None
    gap = _contact_gap_days(row.get("last_contact_at"), now)
    if gap is None or gap < cadence:
        return None
    overdue = gap - cadence
    score = 300 + min(overdue, 90)
    return {
        "party_id": row["party_id"],
        "title": f"Reconnect with {_name(row)}",
        "reason": (
            f"You chose a {cadence}-day cadence; the last visible contact was "
            f"{gap} days ago."
        ),
        "score": score,
        "overdue_days": overdue,
        "href": surface_link(
            "people", object_kind="item", object_id=str(row["party_id"])
        ),
        "explanation": attention_explanation(
            rule_id="people.explicit_cadence_overdue",
            reasons=["member-enabled cadence", "visible last-contact date"],
            signals={
                "score": score,
                "cadence_days": cadence,
                "contact_gap_days": gap,
                "excluded": ["suppressed profiles", "unconfirmed endpoints"],
            },
        ),
    }


def compose_people_cockpit(
    *,
    member_id: str,
    people: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    configured_sources: list[dict[str, Any]],
    unconfirmed_endpoint_count: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the private relationship workspace from authorized rows only."""

    current = now or datetime.now(timezone.utc)
    source_index: dict[tuple[str, str], dict[str, Any]] = {}
    for source in configured_sources:
        key = (str(source["provider"]), str(source["provider_account_id"]))
        source_index[key] = {**source, "record_count": 0, "latest_at": None}
    for source in sources:
        key = (str(source["provider"]), str(source["provider_account_id"]))
        contract = PROVIDER_CONTRACTS.get(key[0], {})
        merged = source_index.setdefault(
            key,
            {
                "provider": key[0],
                "provider_account_id": key[1],
                "label": contract.get("label") or key[0].replace("_", " ").title(),
                "runtime": contract.get("runtime") or "unknown",
                "operations": contract.get("operations") or ["read"],
                "limitations": contract.get("limitations") or [],
                "state": "projected",
            },
        )
        merged["record_count"] = int(source.get("record_count") or 0)
        merged["latest_at"] = source.get("latest_at")
        merged["deleted_count"] = int(source.get("deleted_count") or 0)

    directory: list[dict[str, Any]] = []
    cues: list[dict[str, Any]] = []
    for row in people:
        raw_attributes = row.get("attributes")
        attributes: dict[str, Any] = (
            raw_attributes if isinstance(raw_attributes, dict) else {}
        )
        raw_profile = row.get("profile")
        profile: dict[str, Any] = raw_profile if isinstance(raw_profile, dict) else {}
        entry = {
            "party_id": str(row["party_id"]),
            "kind": row.get("kind") or "person",
            "display_name": _name(row),
            "sort_name": row.get("sort_name") or _name(row),
            "nickname": row.get("nickname"),
            "relationship_label": (
                "You" if str(row["party_id"]) == member_id else _relationship_label(row)
            ),
            "organization": attributes.get("organization_name"),
            "job_title": attributes.get("job_title"),
            "favorite": bool(profile.get("favorite")),
            "suppressed": bool(profile.get("suppressed")),
            "tags": list(profile.get("tags") or []),
            "last_contact_at": row.get("last_contact_at"),
            "contact_gap_days": _contact_gap_days(row.get("last_contact_at"), current),
            "contact_record_count": int(row.get("contact_record_count") or 0),
            "access_mode": row.get("access_mode"),
            "href": surface_link(
                "people", object_kind="item", object_id=str(row["party_id"])
            ),
            "last_event_id": row.get("last_event_id"),
        }
        directory.append(entry)
        cue = _cue(row, current)
        if cue is not None:
            cues.append(cue)

    directory.sort(
        key=lambda item: (
            not bool(item["favorite"]),
            str(item["sort_name"]).casefold(),
            str(item["party_id"]),
        )
    )
    cues.sort(key=lambda item: (-int(item["score"]), str(item["party_id"])))
    lens_counts = {
        "all": len(directory),
        "household": sum(
            1
            for item in directory
            if item["relationship_label"] not in {"Contact", "Organization"}
        ),
        "favorites": sum(1 for item in directory if item["favorite"]),
        "organizations": sum(1 for item in directory if item["kind"] == "organization"),
        "suppressed": sum(1 for item in directory if item["suppressed"]),
    }
    return {
        "contract": "deterministic-people-cockpit-v1",
        "member_id": member_id,
        "directory": directory,
        "cues": cues[:8],
        "lens_counts": lens_counts,
        "sources": sorted(
            source_index.values(),
            key=lambda item: (
                str(item.get("label") or ""),
                str(item.get("provider_account_id") or ""),
            ),
        ),
        "needs_review": {
            "unconfirmed_endpoint_count": unconfirmed_endpoint_count,
            "href": surface_link("triage", object_kind="view", object_id="ready"),
            "copy": "Observed senders stay out of People until identity is confirmed.",
        },
        "generated_at": current.isoformat().replace("+00:00", "Z"),
        "rules": {
            "ranking": "favorite, then alphabetical",
            "cue_policy": "member-enabled explicit cadence only",
            "identity_policy": "qualified Parties only; ambiguity fails closed",
            "privacy_policy": "owner scope, household scope, then explicit field-group grant",
        },
    }


__all__ = ["PROVIDER_CONTRACTS", "compose_people_cockpit"]
