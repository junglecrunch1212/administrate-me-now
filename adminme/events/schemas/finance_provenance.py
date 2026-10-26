"""Owner-approved immutable proof closure; no grants or provider acquisition."""

from adminme.events.registry import registry
from adminme.finance.provenance_store import ProvenanceBlobArchivedV1

registry.register("finance.provenance_archived", 1, ProvenanceBlobArchivedV1)
