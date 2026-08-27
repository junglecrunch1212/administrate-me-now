"""Provider-neutral Cat-B contact mirror used by the four contact packs.

The provider clients are deliberately narrow ports.  OAuth, Contacts
permission, and HTTP/CardDAV libraries stay in the pack-specific client; this
base owns loss-aware normalization, private event emission, revision guards,
and observation-mode write suppression.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from typing import Any, ClassVar, Literal, Protocol

from adminme.adapters.base import AdapterContext, AdapterStatus, SensitivityFloor
from adminme.adapters.emission import write_adapter_event
from adminme.lib.adapters.external_state_mirror_base import (
    ChangeEnvelope,
    ExternalStateMirrorAdapter,
    ProviderConflictError,
    WriteOperation,
    WriteResult,
)
from adminme.lib.observation import outbound as observation_outbound


class ContactProviderClient(Protocol):
    async def changes(self, cursor: str | None) -> dict[str, Any]: ...

    async def write(
        self, kind: str, target_external_id: str | None, payload: dict[str, Any]
    ) -> dict[str, Any]: ...


ClientFactory = Callable[[dict[str, Any]], ContactProviderClient]


def _stable_id(prefix: str, *parts: str) -> str:
    source = "\x1f".join(parts)
    return f"{prefix}_{hashlib.sha256(source.encode()).hexdigest()[:22]}"


def normalize_identifier(kind: str, value: str) -> str:
    """Conservative endpoint normalization; no fuzzy identity decisions."""

    stripped = value.strip()
    if kind == "phone":
        prefix = "+" if stripped.startswith("+") else ""
        return prefix + "".join(
            character for character in stripped if character.isdigit()
        )
    if kind in {"email", "imessage_handle"}:
        return stripped.casefold()
    return stripped


class ContactMirrorAdapter(ExternalStateMirrorAdapter):
    """Common implementation for provider contact mirrors."""

    family: ClassVar[Literal["reminders", "calendaring", "contacts", "tasks"]] = (
        "contacts"
    )
    version: ClassVar[str] = "2.0.0"
    sensitivity_floor: ClassVar[SensitivityFloor] = "normal"
    sharing_model_discriminator: ClassVar[str] = "member_id"
    capabilities: ClassVar[list[Any]] = [
        {
            "kind": "Cat-B",
            "runtime": "central",
            "event_families": [
                "contact.record.observed",
                "contact.record.deleted",
                "contact.group.observed",
            ],
        }
    ]
    write_capabilities: ClassVar[list[str]] = [
        "create_contact",
        "update_contact",
        "delete_contact",
    ]
    provider: ClassVar[str]
    runtime_shape: ClassVar[str] = "central"

    def __init__(
        self,
        *,
        config: dict[str, Any],
        client_factory: ClientFactory,
        max_poll_cycles: int | None = None,
        observation_session: Any = None,
        observation_manager: Any = None,
        observation_event_sink: Any = None,
    ) -> None:
        self._config = dict(config)
        self._client_factory = client_factory
        self._client: ContactProviderClient | None = None
        self._ctx: AdapterContext | None = None
        self._cursor: str | None = None
        self._stop_requested = False
        self._max_poll_cycles = max_poll_cycles
        self._status = AdapterStatus()
        self._observation_session = observation_session
        self._observation_manager = observation_manager
        self._observation_event_sink = observation_event_sink

    async def start(self, *, ctx: AdapterContext) -> None:
        member_id = str(self._config.get("member_id") or "")
        account_id = str(self._config.get("provider_account_id") or "")
        container_id = str(self._config.get("container_external_id") or "default")
        if not member_id or not account_id:
            raise ValueError(
                "contact adapters require member_id and provider_account_id"
            )
        self._ctx = ctx
        self._client = self._client_factory(self._config)
        self._status.state = "running"
        self._status.started_at = ctx.now_factory()
        bridge_enrollment_id = self._config.get("_bridge_enrollment_id")
        bridge_configuration_sha256 = self._config.get("_bridge_configuration_sha256")
        auth_payload = {
            "adapter_name": self.name,
            "adapter_family": self.family,
            "token_storage_ref": str(
                self._config.get("credentials_ref") or "device-permission"
            ),
            "authenticated_at": ctx.now_factory().isoformat().replace("+00:00", "Z"),
            "scopes": list(self._config.get("scopes") or []),
        }
        auth_schema_version = 1
        if isinstance(bridge_enrollment_id, str) and isinstance(
            bridge_configuration_sha256, str
        ):
            auth_schema_version = 2
            auth_payload.update(
                {
                    "bridge_enrollment_id": bridge_enrollment_id,
                    "configuration_sha256": bridge_configuration_sha256,
                    "provider_account_id": account_id,
                }
            )
        await write_adapter_event(
            ctx,
            adapter_name=self.name,
            sensitivity_floor=self.sensitivity_floor,
            config=self._config,
            event_type="adapter.authenticated",
            schema_version=auth_schema_version,
            payload=auth_payload,
        )
        cycles = 0
        while not self._stop_requested:
            result = await self._client.changes(self._cursor)
            observed_at = ctx.now_factory().isoformat().replace("+00:00", "Z")
            for group in result.get("groups") or []:
                await self._emit_group(
                    ctx, group, member_id, account_id, container_id, observed_at
                )
            for record in result.get("records") or []:
                await self._emit_record(
                    ctx, record, member_id, account_id, container_id, observed_at
                )
            for deletion in result.get("deletions") or []:
                await self._emit_deletion(
                    ctx, deletion, member_id, account_id, container_id, observed_at
                )
            self._cursor = str(result.get("next_cursor") or self._cursor or "") or None
            cycles += 1
            if self._max_poll_cycles is not None and cycles >= self._max_poll_cycles:
                break
            await asyncio.sleep(
                max(10, int(self._config.get("poll_interval_s") or 300))
            )

    async def stop(self) -> None:
        self._stop_requested = True
        self._status.state = "stopped"

    def status(self) -> AdapterStatus:
        return self._status

    async def _emit_record(
        self,
        ctx: AdapterContext,
        record: dict[str, Any],
        member_id: str,
        account_id: str,
        container_id: str,
        observed_at: str,
    ) -> None:
        external_id = str(record["external_id"])
        identifiers = []
        for value in record.get("identifiers") or []:
            kind = str(value.get("kind") or "other")
            raw = str(value.get("value") or "")
            if not raw:
                continue
            identifiers.append(
                {
                    "kind": kind,
                    "value": raw,
                    "value_normalized": normalize_identifier(kind, raw),
                    "label": value.get("label"),
                    "primary": bool(value.get("primary")),
                    "verified": bool(value.get("verified")),
                }
            )
        record_id = _stable_id(
            "contact", member_id, self.provider, account_id, container_id, external_id
        )
        await write_adapter_event(
            ctx,
            adapter_name=self.name,
            sensitivity_floor=self.sensitivity_floor,
            config=self._config,
            event_type="contact.record.observed",
            idempotency_key=f"{self.name}:record:{record_id}:{record.get('revision') or observed_at}",
            raw_ref=record.get("raw_ref"),
            payload={
                "contact_record_id": record_id,
                "member_id": member_id,
                "provider": self.provider,
                "provider_account_id": account_id,
                "container_external_id": container_id,
                "external_id": external_id,
                "provider_revision": record.get("revision"),
                "kind": record.get("kind") or "person",
                "display_name": record.get("display_name") or "Unnamed contact",
                "sort_name": record.get("sort_name"),
                "given_name": record.get("given_name"),
                "family_name": record.get("family_name"),
                "nickname": record.get("nickname"),
                "organization_name": record.get("organization_name"),
                "job_title": record.get("job_title"),
                "identifiers": identifiers,
                "fields": dict(record.get("fields") or {}),
                "group_external_ids": list(record.get("group_external_ids") or []),
                "source_url": record.get("source_url"),
                "observed_at": observed_at,
                "capability_warnings": list(record.get("capability_warnings") or []),
            },
        )

    async def _emit_deletion(
        self,
        ctx: AdapterContext,
        deletion: dict[str, Any],
        member_id: str,
        account_id: str,
        container_id: str,
        observed_at: str,
    ) -> None:
        external_id = str(deletion["external_id"])
        record_id = _stable_id(
            "contact", member_id, self.provider, account_id, container_id, external_id
        )
        await write_adapter_event(
            ctx,
            adapter_name=self.name,
            sensitivity_floor=self.sensitivity_floor,
            config=self._config,
            event_type="contact.record.deleted",
            idempotency_key=f"{self.name}:delete:{record_id}:{deletion.get('revision') or observed_at}",
            payload={
                "contact_record_id": record_id,
                "member_id": member_id,
                "provider": self.provider,
                "provider_account_id": account_id,
                "container_external_id": container_id,
                "external_id": external_id,
                "provider_revision": deletion.get("revision"),
                "deleted_at": observed_at,
            },
        )

    async def _emit_group(
        self,
        ctx: AdapterContext,
        group: dict[str, Any],
        member_id: str,
        account_id: str,
        container_id: str,
        observed_at: str,
    ) -> None:
        external_id = str(group["external_id"])
        group_id = _stable_id(
            "contact_group",
            member_id,
            self.provider,
            account_id,
            container_id,
            external_id,
        )
        await write_adapter_event(
            ctx,
            adapter_name=self.name,
            sensitivity_floor=self.sensitivity_floor,
            config=self._config,
            event_type="contact.group.observed",
            idempotency_key=f"{self.name}:group:{group_id}:{group.get('revision') or observed_at}",
            payload={
                "group_record_id": group_id,
                "member_id": member_id,
                "provider": self.provider,
                "provider_account_id": account_id,
                "container_external_id": container_id,
                "external_id": external_id,
                "name": str(group.get("name") or "Unnamed group"),
                "provider_revision": group.get("revision"),
                "observed_at": observed_at,
            },
        )

    async def _apply_write_impl(self, operation: WriteOperation) -> WriteResult:
        if self._client is None:
            raise RuntimeError("contact adapter is not started")
        payload = dict(operation.payload)
        if operation.kind in {
            "update_contact",
            "update_party_identifier",
            "delete_contact",
        } and not payload.get("expected_provider_revision"):
            raise ProviderConflictError(
                "contact write requires expected provider revision"
            )

        async def apply() -> dict[str, Any]:
            assert self._client is not None
            return await self._client.write(
                operation.kind, operation.target_external_id, payload
            )

        if (
            self._observation_session is not None
            and self._observation_manager is not None
            and self._observation_event_sink is not None
        ):
            outcome = await observation_outbound(
                self._observation_session,
                f"contacts.{operation.kind}",
                {
                    "provider": self.provider,
                    "target_external_id": operation.target_external_id,
                    "fields": sorted(payload),
                },
                apply,
                manager=self._observation_manager,
                event_sink=self._observation_event_sink,
                target_channel=self.name,
                target_identifier=operation.target_external_id or "new-contact",
            )
            if outcome.suppressed:
                return WriteResult(applied=False, suppressed=True)
            result = outcome.result or {}
        else:
            result = await apply()
        return WriteResult(
            applied=True,
            external_id=str(
                result.get("external_id") or operation.target_external_id or ""
            )
            or None,
        )

    async def _on_external_change(self, envelope: ChangeEnvelope) -> None:
        del envelope


def unsupported_client_factory(provider: str) -> ClientFactory:
    """Fail clearly until a pack supplies its authenticated provider client."""

    def factory(config: dict[str, Any]) -> ContactProviderClient:
        del config
        raise RuntimeError(f"{provider} client binding is not configured")

    return factory


__all__ = [
    "ClientFactory",
    "ContactMirrorAdapter",
    "ContactProviderClient",
    "normalize_identifier",
    "unsupported_client_factory",
]
