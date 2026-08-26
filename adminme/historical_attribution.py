"""Content-free historical member/provider/workspace attribution audit."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

from adminme.events.log import EventLog
from adminme.provider_bindings import ProviderBinding
from adminme.source_assurance import load_source_obligations


def _digest(key: bytes, value: str) -> str:
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()[:20]


async def audit_historical_attribution(
    event_log: EventLog,
    *,
    repository_root: Any,
    provider_bindings: tuple[ProviderBinding, ...],
    hash_key: bytes,
) -> dict[str, Any]:
    event_types = sorted(
        {
            obligation.event_type
            for obligation in load_source_obligations(repository_root)
        }
    )
    watermark = await event_log.latest_event_id()
    bindings_by_account: dict[str, list[ProviderBinding]] = {}
    for binding in provider_bindings:
        bindings_by_account.setdefault(
            binding.provider_account_id.casefold(), []
        ).append(binding)
    groups: dict[tuple[str, str, str, str, str], int] = {}
    total = 0
    attributed = 0
    async for event in event_log.read_attribution_metadata_since(
        through=watermark or "",
        types=event_types,
    ):
        total += 1
        owner_scope = str(event.get("owner_scope") or "")
        account = str(event.get("source_account_id") or "")
        adapter = str(event.get("source_adapter") or "")
        event_type = str(event.get("type") or "")
        bindings = bindings_by_account.get(account.casefold(), [])
        matching_bindings = [
            binding
            for binding in bindings
            if binding.owner_kind == "member"
            and owner_scope == f"private:{binding.member_id}"
        ]
        matching_coordinates = {
            (binding.owner_kind, binding.owner_id, binding.workspace)
            for binding in matching_bindings
        }
        binding_state = "unattributed"
        workspace = "unattributed"
        if len(matching_coordinates) == 1:
            binding_state = "matched"
            workspace = next(iter(matching_coordinates))[2]
            attributed += 1
        elif bindings:
            binding_state = "ambiguous_or_owner_mismatch"
        key = (
            _digest(hash_key, owner_scope),
            _digest(hash_key, account.casefold()),
            workspace,
            _digest(hash_key, f"{adapter}|{event_type}"),
            binding_state,
        )
        groups[key] = groups.get(key, 0) + 1
    rows = [
        {
            "member_scope_hash": key[0],
            "provider_account_hash": key[1],
            "workspace": key[2],
            "provenance_hash": key[3],
            "binding_state": key[4],
            "event_count": count,
        }
        for key, count in sorted(groups.items())
    ]
    return {
        "contract": "historical-attribution-v1",
        "state": "healthy" if total == attributed else "degraded",
        "watermark_event_id": watermark,
        "source_event_count": total,
        "attributed_event_count": attributed,
        "unattributed_event_count": total - attributed,
        "groups": rows,
        "content_exposed": False,
        "raw_identifiers_exposed": False,
    }


__all__ = ["audit_historical_attribution"]
