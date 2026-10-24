"""Draft closed-package consumer for the pinned books-journal-unit.v1 proposal.

The package never resolves paths or URLs. Independently approved evidence pins
are a separate owner-ingress input, not a boolean supplied by a journal row.
Only bounded scalar manifests survive verification; raw rows stay out of CoS.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
)

from adminme.finance.publication import PublicationError, canonical_bytes

CONTRACT_PATH = Path(__file__).parent / "contracts/books-journal-unit.v1.proposal.json"
CONTRACT_SHA256 = "ce2fdc0d7c291486b71921a4353e4fc53ff16074869a6f07560d35c4a53f3f31"
CONTRACT = json.loads(CONTRACT_PATH.read_bytes())
if hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest() != CONTRACT_SHA256:
    raise RuntimeError("journal unit contract bytes changed")
FIELDS = frozenset(CONTRACT["native_field_names"])
LIMITS = CONTRACT["size_limits"]
REPORT_ORDER = [
    "actuals",
    "cash",
    "budget",
    "observed_net",
    "balance_sheet",
    "scenarios",
    "quality",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class BlobRef(StrictModel):
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bytes: int = Field(ge=1, le=1048576)


class ChunkRef(BlobRef):
    ordinal: int = Field(ge=0, lt=128)
    units: int = Field(ge=1, le=256)


class EvidenceRef(BlobRef):
    kind: Literal["readback", "acceptance", "held_observations", "committed_runs"]


class ProvenanceIndex(StrictModel):
    contract: Literal["books-provenance-index.v1"]
    unit_contract: Literal["books-journal-unit.v1"]
    tenant_id: str = Field(min_length=1, max_length=96)
    owner_id: str = Field(min_length=1, max_length=96)
    book_id: str = Field(min_length=1, max_length=96)
    source_snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    accepted_unit_set_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    chunks: list[ChunkRef] = Field(min_length=1, max_length=128)
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=128)


class NativeCommittedCell(StrictModel):
    numberValue: StrictInt | StrictFloat


def committed_formula(row: int) -> str:
    return f'=IF(AF{row}="","",COUNTIFS(\'Refresh Runs\'!A:A,AF{row},\'Refresh Runs\'!D:D,"COMMITTED"))'


class LineProof(StrictModel):
    native_line_id: str = Field(min_length=1, max_length=240)
    committed_run_id: str = Field(min_length=1, max_length=240)
    acceptance_predicate: Literal["books-native-committed-count.v1"]
    effective_value: NativeCommittedCell
    committed_runs_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    frozen_readback_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    acceptance_artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    acceptance_basis: Literal[
        "accepted_historical",
        "accepted_manual",
        "accepted_reversal",
        "committed_generated",
    ]


class UnitBody(StrictModel):
    unit_contract: Literal["books-journal-unit.v1"]
    authoritative_book_id: str = Field(min_length=1, max_length=96)
    native_journal_row: dict[str, StrictStr | StrictInt | float | StrictBool | None]


class UnitRecord(StrictModel):
    unit_id: str = Field(pattern=r"^hbjr1:[a-f0-9]{64}$")
    body: UnitBody
    source_row: int = Field(ge=1)
    acceptance: LineProof


class ProvenanceChunk(StrictModel):
    contract: Literal["books-provenance-chunk.v1"]
    ordinal: int = Field(ge=0, lt=128)
    units: list[UnitRecord] = Field(min_length=1, max_length=256)


@dataclass(frozen=True)
class ClosedPackage:
    index: bytes
    blobs: Mapping[str, bytes]
    # Exact hashes obtained from independent, reviewed source evidence. These
    # are deliberately not read from the package's self-declared index.
    approved_evidence: frozenset[str]


@dataclass(frozen=True)
class VerifiedProvenance:
    root: str
    source_snapshot_sha256: str
    line_units: dict[str, str]
    unit_ids: frozenset[str]
    held_events_by_revision: dict[str, str]


def freeze_package(package: ClosedPackage) -> ClosedPackage:
    if (
        not isinstance(package.index, bytes)
        or len(package.index) > LIMITS["provenance_index_bytes"]
        or len(package.blobs) > 128
        or len(package.approved_evidence) > 128
    ):
        raise PublicationError("provenance_size_limit")
    blobs = dict(package.blobs)
    if any(
        not isinstance(raw, bytes) or len(raw) > LIMITS["chunk_bytes"]
        for raw in blobs.values()
    ):
        raise PublicationError("provenance_size_limit")
    if (
        len(package.index) + sum(map(len, blobs.values()))
        > LIMITS["aggregate_uncompressed_provenance_bytes"]
    ):
        raise PublicationError("provenance_size_limit")
    return ClosedPackage(
        package.index, MappingProxyType(blobs), frozenset(package.approved_evidence)
    )


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PublicationError("provenance_duplicate_json_key")
        result[key] = value
    return result


def parse_blob(raw: bytes, maximum: int) -> dict[str, Any]:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= maximum:
        raise PublicationError("provenance_size_limit")
    try:
        value = json.loads(raw, object_pairs_hook=_object)
        if not isinstance(value, dict) or canonical_bytes(value) != raw:
            raise ValueError("canonical object required")
        return value
    except PublicationError:
        raise
    except (ValueError, TypeError):
        raise PublicationError("provenance_noncanonical_json") from None


def namespace_root(publication: dict[str, Any]) -> str | None:
    units = publication["accepted_journal_revision_ids"]
    refs = [
        r
        for r in publication["source_references"]
        if r["evidence_id"].startswith("hbprov1:")
    ]
    active = (
        any(u.startswith("hbjr1:") for u in units)
        or publication["publication_id"].startswith("hbap1:")
        or bool(refs)
    )
    if not active:
        return None
    import re

    if not units or any(not re.fullmatch(r"hbjr1:[a-f0-9]{64}", u) for u in units):
        raise PublicationError("provenance_mixed_or_invalid_unit_namespace")
    if units != sorted(set(units)) or len(refs) != 1:
        raise PublicationError("provenance_order_or_root_required")
    root = refs[0]
    if (
        root["source_kind"] != "accepted_books"
        or root["access"] != "owner_only"
        or not re.fullmatch(r"hbprov1:[a-f0-9]{64}", root["evidence_id"])
    ):
        raise PublicationError("provenance_root_invalid")
    if publication["excluded_pending_revision_ids"] != sorted(
        set(publication["excluded_pending_revision_ids"])
    ):
        raise PublicationError("provenance_exclusions_order")
    if publication["source_references"] != sorted(
        publication["source_references"], key=lambda r: r["evidence_id"]
    ):
        raise PublicationError("provenance_references_order")
    reports = [r["report_id"] for r in publication["reports"]]
    if reports != [r for r in REPORT_ORDER if r in reports]:
        raise PublicationError("provenance_reports_order")
    expected = "hbap1:" + digest(
        {
            k: v
            for k, v in publication.items()
            if k not in {"publication_id", "content_sha256"}
        }
    )
    if publication["publication_id"] != expected:
        raise PublicationError("provenance_publication_identity")
    return str(root["evidence_id"])


def verify_package(
    publication: dict[str, Any],
    package: ClosedPackage,
    previous: VerifiedProvenance | None = None,
) -> VerifiedProvenance:
    """Verify one bounded chunk at a time; no implicit acquisition or trust."""
    try:
        return _verify_package(publication, package, previous)
    except PublicationError:
        raise
    except (ValueError, KeyError, TypeError, OverflowError):
        raise PublicationError("provenance_invalid") from None


def _verify_package(
    publication: dict[str, Any],
    package: ClosedPackage,
    previous: VerifiedProvenance | None,
) -> VerifiedProvenance:
    root = namespace_root(publication)
    if root is None:
        raise PublicationError("provenance_namespace_required")
    data = parse_blob(package.index, LIMITS["provenance_index_bytes"])
    index = ProvenanceIndex.model_validate(data)
    if root != "hbprov1:" + hashlib.sha256(package.index).hexdigest():
        raise PublicationError("provenance_root_hash")
    if any(
        getattr(index, k) != publication[k]
        for k in ("tenant_id", "owner_id", "book_id")
    ):
        raise PublicationError("provenance_owner_binding")
    refs = [*index.chunks, *index.evidence]
    if len(refs) > LIMITS["chunk_count"]:
        raise PublicationError("provenance_size_limit")
    hashes = [r.sha256 for r in refs]
    if len(set(hashes)) != len(hashes) or set(package.blobs) != set(hashes):
        raise PublicationError("provenance_not_closed")
    if (
        sum(r.bytes for r in refs) + len(package.index)
        > LIMITS["aggregate_uncompressed_provenance_bytes"]
    ):
        raise PublicationError("provenance_size_limit")
    if [r.ordinal for r in index.chunks] != list(range(len(index.chunks))):
        raise PublicationError("provenance_chunk_order")
    if [r.sha256 for r in index.evidence] != sorted(r.sha256 for r in index.evidence):
        raise PublicationError("provenance_evidence_order")
    evidence = {r.sha256: r for r in index.evidence}
    if set(evidence) != set(package.approved_evidence):
        raise PublicationError("provenance_independent_evidence_required")

    def blob(ref: BlobRef) -> dict[str, Any]:
        raw = package.blobs[ref.sha256]
        if len(raw) != ref.bytes or hashlib.sha256(raw).hexdigest() != ref.sha256:
            raise PublicationError("provenance_blob_hash_or_size")
        return parse_blob(raw, LIMITS["chunk_bytes"])

    # Every evidence document is independently pinned and has an exact shape.
    # At most two line-evidence documents are retained while checking a chunk.
    def evidence_rows(sha: str, kind: str) -> dict[str, dict[str, Any]]:
        ref = evidence[sha]
        if ref.kind != kind:
            raise PublicationError("provenance_evidence_kind")
        doc = blob(ref)
        expected = {"contract", "source_snapshot_sha256", "lines", "captured_at"}
        if (
            set(doc) != expected
            or doc["contract"] != f"books-line-{kind}.v1"
            or doc["source_snapshot_sha256"] != index.source_snapshot_sha256
        ):
            raise PublicationError("provenance_evidence_binding")
        if datetime.fromisoformat(doc["captured_at"]) > datetime.fromisoformat(
            publication["published_at"]
        ):
            raise PublicationError("provenance_evidence_clock")
        if not isinstance(doc["lines"], list) or not 1 <= len(doc["lines"]) <= 256:
            raise PublicationError("provenance_evidence_count")
        rows = {}
        for row in doc["lines"]:
            fields = {
                "native_line_id",
                "native_row_sha256",
                "committed_run_id",
                "effective_value" if kind == "readback" else "acceptance_basis",
            }
            if kind == "readback":
                fields.add("source_row")
            if set(row) != fields or row["native_line_id"] in rows:
                raise PublicationError("provenance_evidence_identity")
            rows[row["native_line_id"]] = row
        return rows

    held_refs = [r for r in index.evidence if r.kind == "held_observations"]
    if len(held_refs) != 1:
        raise PublicationError("provenance_held_evidence_required")
    held_doc = blob(held_refs[0])
    if (
        set(held_doc) != {"contract", "source_snapshot_sha256", "observations"}
        or held_doc["contract"] != "books-held-observations.v1"
        or held_doc["source_snapshot_sha256"] != index.source_snapshot_sha256
    ):
        raise PublicationError("provenance_held_binding")
    held: dict[str, str] = {}
    for row in held_doc["observations"]:
        if (
            set(row) != {"source_revision_id", "canonical_event_id"}
            or not all(isinstance(v, str) and v for v in row.values())
            or row["source_revision_id"] in held
        ):
            raise PublicationError("provenance_held_identity")
        held[row["source_revision_id"]] = row["canonical_event_id"]
    if sorted(held) != publication["excluded_pending_revision_ids"]:
        raise PublicationError("provenance_held_set")
    held_events = set(held.values())
    run_refs = [r for r in index.evidence if r.kind == "committed_runs"]
    if len(run_refs) != 1:
        raise PublicationError("provenance_committed_run_evidence_required")
    run_doc = blob(run_refs[0])
    if (
        set(run_doc) != {"contract", "source_snapshot_sha256", "runs"}
        or run_doc["contract"] != "books-committed-runs.v1"
        or run_doc["source_snapshot_sha256"] != index.source_snapshot_sha256
        or not isinstance(run_doc["runs"], list)
        or len(run_doc["runs"]) > 2048
    ):
        raise PublicationError("provenance_committed_run_evidence_invalid")
    committed_counts: dict[str, int] = {}
    for run in run_doc["runs"]:
        if set(run) != {"run_id", "status"} or not all(
            isinstance(v, str) for v in run.values()
        ):
            raise PublicationError("provenance_committed_run_evidence_invalid")
        if run["status"].casefold() == "committed":
            key = run["run_id"].casefold()
            committed_counts[key] = committed_counts.get(key, 0) + 1
    line_units: dict[str, str] = {}
    units: list[str] = []
    used_evidence = {held_refs[0].sha256, run_refs[0].sha256}
    used_lines: dict[str, set[str]] = {}
    for ref in index.chunks:
        chunk = ProvenanceChunk.model_validate(blob(ref))
        if chunk.ordinal != ref.ordinal or len(chunk.units) != ref.units:
            raise PublicationError("provenance_chunk_count_or_ordinal")
        cache: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
        for record in chunk.units:
            body = record.body.model_dump()
            row = body["native_journal_row"]
            proof = record.acceptance
            count = proof.effective_value.numberValue
            if (
                not math.isfinite(count)
                or count != 1
                or any(c in proof.committed_run_id for c in "*?~")
                or committed_counts.get(proof.committed_run_id.casefold()) != 1
                or proof.committed_runs_sha256 != run_refs[0].sha256
                or row["committed_flag"] != committed_formula(record.source_row)
            ):
                raise PublicationError("provenance_native_committed_predicate")
            if set(row) != FIELDS or body["authoritative_book_id"] != index.book_id:
                raise PublicationError("provenance_native_fields_or_book")
            if record.unit_id != "hbjr1:" + digest(body):
                raise PublicationError("provenance_unit_hash")
            line = row["line_id"]
            if (
                not isinstance(line, str)
                or not line
                or line in line_units
                or proof.native_line_id != line
            ):
                raise PublicationError("provenance_native_line_identity")
            if (
                row["source_revision_id"] in held
                or row["canonical_event_id"] in held_events
            ):
                raise PublicationError("provenance_held_unit")
            if (
                row["source_revision_id"] in (None, "")
                and proof.acceptance_basis == "committed_generated"
            ):
                raise PublicationError("provenance_blank_source_basis")
            if row["import_run_id"] != proof.committed_run_id:
                raise PublicationError("provenance_committed_run")
            for sha, kind in (
                (proof.frozen_readback_sha256, "readback"),
                (proof.acceptance_artifact_sha256, "acceptance"),
            ):
                key = (sha, kind)
                if key not in cache:
                    if len(cache) >= 2:
                        cache.clear()
                    cache[key] = evidence_rows(sha, kind)
                witness = cache[key].get(line)
                if (
                    witness is None
                    or witness["native_row_sha256"] != digest(row)
                    or witness["committed_run_id"] != proof.committed_run_id
                ):
                    raise PublicationError("provenance_line_witness")
                if kind == "readback" and (
                    canonical_bytes(witness["effective_value"])
                    != canonical_bytes(proof.effective_value.model_dump())
                    or type(witness["source_row"]) is not int
                    or witness["source_row"] != record.source_row
                ):
                    raise PublicationError("provenance_evaluated_acceptance_required")
                if (
                    kind == "acceptance"
                    and witness["acceptance_basis"] != proof.acceptance_basis
                ):
                    raise PublicationError("provenance_acceptance_basis")
                used_evidence.add(sha)
                used_lines.setdefault(sha, set()).add(line)
            line_units[line] = record.unit_id
            units.append(record.unit_id)
    if (
        units != sorted(set(units))
        or units != publication["accepted_journal_revision_ids"]
        or digest(units) != index.accepted_unit_set_sha256
    ):
        raise PublicationError("provenance_unit_coverage_or_order")
    if used_evidence != set(evidence):
        raise PublicationError("provenance_unreferenced_evidence")
    for sha, lines in used_lines.items():
        if set(evidence_rows(sha, evidence[sha].kind)) != lines:
            raise PublicationError("provenance_unreferenced_evidence_line")
    if previous and (
        not previous.unit_ids <= set(units)
        or any(line_units.get(k) != v for k, v in previous.line_units.items())
        or any(held.get(k) != v for k, v in previous.held_events_by_revision.items())
    ):
        raise PublicationError("provenance_historical_line_changed")
    return VerifiedProvenance(
        root, index.source_snapshot_sha256, line_units, frozenset(units), held
    )
