"""
Adapter framework — abstract base classes per epistemic-role category.

Five Cat-A through Cat-E ABCs that extend the simple ``Adapter`` Protocol
from ``adminme/adapters/base.py`` with category-specific contract shape.
Reference: ``docs/05-architecture-amendment-personal-data-layer.md`` §3.2,
[D19] / [PM-34].

This is the **contract layer** shipped in prompt 11-α. The install-time
validator that ENFORCES these contracts ships in 11-β; the CLI + authoring
guide that wraps the validator ships in 11-γ. Adapter packs (Cohort C / D)
import a base class from here and subclass it to declare their epistemic
role explicitly.

Commit 1 ships three of the five ABCs:

- :class:`CommunicationAdapter` — Cat-A bidirectional messaging.
- :class:`ExternalStateMirrorAdapter` — Cat-B bidirectional state mirror.
- :class:`InboundDataAdapter` — Cat-C read-only ingestion.

Commit 2 adds :class:`PersonalKnowledgeAdapter` and
:class:`OutboundActionAdapter`.
"""

from __future__ import annotations

from adminme.lib.adapters.communication_base import (
    CommunicationAdapter,
    InboundEnvelope,
    OutboundMessage,
    SendResult,
)
from adminme.lib.adapters.external_state_mirror_base import (
    ChangeEnvelope,
    ExternalStateMirrorAdapter,
    WriteCapabilityError,
    WriteOperation,
    WriteResult,
)
from adminme.lib.adapters.inbound_data_base import InboundDataAdapter

__all__ = [
    "ChangeEnvelope",
    "CommunicationAdapter",
    "ExternalStateMirrorAdapter",
    "InboundDataAdapter",
    "InboundEnvelope",
    "OutboundMessage",
    "SendResult",
    "WriteCapabilityError",
    "WriteOperation",
    "WriteResult",
]
