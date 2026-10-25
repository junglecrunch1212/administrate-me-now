"""Operator-staged, exact CoS source policy for expressly requested browser reads."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, Field

from adminme.workstation_inventory import StrictRecord

PROVISION_FILE = "workstation-browser-source-request.json"


class BrowserSourceProvision(StrictRecord):
    schema_version: Literal[1] = 1
    request_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    resource_id: str = Field(min_length=1, max_length=300)
    policy_key: str = Field(pattern=r"^spv2_[0-9a-f]{64}$")
    expected_source_revision: int = Field(ge=0)
    expected_binding_revision: int = Field(ge=1)
    operator_principal_id: str = Field(pattern=r"^operator:[A-Za-z0-9:._-]{1,240}$")
    owner_authorization_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expires_at: AwareDatetime
    context_mode: Literal["content_on_explicit_authorized_demand"]

    def current(self, now: datetime) -> bool:
        remaining = (self.expires_at - now).total_seconds()
        return 0 < remaining <= 3600
