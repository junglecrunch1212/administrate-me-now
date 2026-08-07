"""Google People API contact adapter."""

from __future__ import annotations

from typing import Any, ClassVar

from adminme.lib.adapters import WriteOperation, WriteResult
from adminme.lib.adapters.contacts_base import (
    ClientFactory,
    ContactMirrorAdapter as ExternalStateMirrorAdapter,
    unsupported_client_factory,
)


class GooglePeopleAdapter(ExternalStateMirrorAdapter):
    name: ClassVar[str] = "contacts-google-people"
    provider: ClassVar[str] = "google_people"

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


__all__ = ["GooglePeopleAdapter"]
