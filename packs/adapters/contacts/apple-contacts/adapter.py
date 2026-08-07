"""Apple Contacts member-bridge adapter."""

from __future__ import annotations

from typing import Any, ClassVar

from adminme.lib.adapters import WriteOperation, WriteResult
from adminme.lib.adapters.contacts_base import (
    ClientFactory,
    ContactMirrorAdapter as ExternalStateMirrorAdapter,
    unsupported_client_factory,
)


class AppleContactsAdapter(ExternalStateMirrorAdapter):
    name: ClassVar[str] = "contacts-apple-contacts"
    provider: ClassVar[str] = "apple_contacts"
    runtime_shape: ClassVar[str] = "member_bridge"
    capabilities: ClassVar[list[Any]] = [
        {
            "kind": "Cat-B",
            "runtime": "bridge",
            "event_families": [
                "contact.record.observed",
                "contact.record.deleted",
                "contact.group.observed",
            ],
        }
    ]

    def __init__(
        self,
        *,
        config: dict[str, Any],
        client_factory: ClientFactory | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            config=config,
            client_factory=client_factory or unsupported_client_factory(self.provider),
            **kwargs,
        )

    async def _apply_write_impl(self, operation: WriteOperation) -> WriteResult:
        if operation.kind not in self.write_capabilities:
            raise ValueError(f"unsupported contact operation: {operation.kind}")
        return await super()._apply_write_impl(operation)


__all__ = ["AppleContactsAdapter"]
