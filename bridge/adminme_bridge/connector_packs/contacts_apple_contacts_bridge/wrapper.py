"""Bridge-only composition wrapper for the Apple Contacts adapter."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any, ClassVar, Literal

from adminme.adapters.base import AdapterContext
from adminme.lib.adapters import WriteOperation
from adminme_bridge.adapters.base import AdapterStatus, BridgeContext
from adminme_bridge.adapters.sinks import (
    BridgeTransportEventSink,
    IngestClientBridgeSink,
)


_REPO_ROOT = Path(__file__).resolve().parents[4]
_CENTRAL_ADAPTER_PATH = (
    _REPO_ROOT / "packs" / "adapters" / "contacts" / "apple-contacts" / "adapter.py"
)


def _load_central_adapter_class() -> Any:
    import sys

    module_name = "_adminme_bridge_central_apple_contacts"
    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached.AppleContactsAdapter
    spec = importlib.util.spec_from_file_location(module_name, _CENTRAL_ADAPTER_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"failed to load Apple Contacts at {_CENTRAL_ADAPTER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module.AppleContactsAdapter


class AppleContactsBridgeAdapter:
    family: ClassVar[Literal["contacts"]] = "contacts"
    provider: ClassVar[str] = "apple_contacts"

    def __init__(self, *, internal_config: dict[str, Any] | None = None) -> None:
        if not internal_config or not internal_config.get("container_external_id"):
            raise ValueError("Contacts bridge requires container_external_id")
        account = internal_config.get("provider_account_id")
        if (
            not isinstance(account, str)
            or re.fullmatch(r"provider-account:[0-9a-f]{32,64}", account) is None
        ):
            raise ValueError("Contacts bridge requires a content-free provider account")
        self._internal_config = dict(internal_config)
        self._internal: Any = None
        self._status = AdapterStatus()

    async def start(self, *, ctx: BridgeContext) -> None:
        config = {
            **self._internal_config,
            "member_id": ctx.member_id,
            "tenant_id": ctx.tenant_id,
            "deployment_target": "bridge",
            "credentials_ref": "keychain://device-permissions/contacts",
            "scopes": ["contacts.read", "contacts.write"],
            "_bridge_enrollment_id": ctx.enrollment_id,
            "_bridge_configuration_sha256": ctx.configuration_sha256,
        }
        adapter_class = _load_central_adapter_class()
        self._internal = adapter_class(config=config)
        sink = ctx.sink or IngestClientBridgeSink(ctx.ingest_client)
        adapter_context = AdapterContext(
            event_sink=BridgeTransportEventSink(sink, tenant_id=ctx.tenant_id),
            config=config,
            correlation_id_factory=ctx.correlation_id_factory,
            now_factory=ctx.now_factory,
        )
        await self._internal.start(ctx=adapter_context)

    async def stop(self) -> None:
        if self._internal is not None:
            await self._internal.stop()

    async def apply_write(
        self,
        operation: str,
        payload: dict[str, Any],
        target_external_id: str | None = None,
    ) -> dict[str, Any]:
        if self._internal is None:
            raise RuntimeError("Apple Contacts adapter has not finished starting")
        result = await self._internal.apply_write(
            WriteOperation(operation, target_external_id, payload)
        )
        return {
            "applied": result.applied,
            "external_id": result.external_id,
            "suppressed": result.suppressed,
        }

    def status(self) -> AdapterStatus:
        if self._internal is None:
            return self._status
        central = self._internal.status()
        return AdapterStatus(
            state=central.state,
            last_error=central.last_error,
            last_cursor_advance_at=central.last_cursor_advance_at,
            started_at=central.started_at,
            restart_count=central.restart_count,
            extra=dict(central.extra),
        )


__all__ = ["AppleContactsBridgeAdapter"]
