"""Valid synthetic 98-blob proof and independently sized dated import payload."""

from __future__ import annotations

import hashlib
import math

from adminme.finance.publication import canonical_bytes, content_hash
from adminme.finance.publication_provenance import ClosedPackage, digest
from tests.fixtures.finance_provenance import (
    native_rows,
    package_fixture,
    seal_publication,
)
from tests.fixtures.finance_planning import projection
from tests.fixtures.finance_activation import witness_projection

PROVENANCE_TARGET = 18 * 1024 * 1024
DATED_TARGET = 6_020_614
UNIT_COUNT = 8022


def _proof(tenant, owner, padding):
    publication, small = package_fixture(tenant, owner)
    import json

    original = json.loads(small.index)
    common = [
        r
        for r in original["evidence"]
        if r["kind"] in {"held_observations", "committed_runs"}
    ]
    blobs = {r["sha256"]: small.blobs[r["sha256"]] for r in common}
    run_sha = next(r["sha256"] for r in common if r["kind"] == "committed_runs")
    records = []
    for number, (row, basis) in enumerate(native_rows(UNIT_COUNT), 1):
        row["description"] = "Synthetic resource fixture. " + "x" * padding
        body = {
            "unit_contract": "books-journal-unit.v1",
            "authoritative_book_id": publication["book_id"],
            "native_journal_row": row,
        }
        records.append(
            {
                "unit_id": "hbjr1:" + digest(body),
                "body": body,
                "source_row": number,
                "basis": basis,
            }
        )
    records.sort(key=lambda r: r["unit_id"])
    refs, chunks = list(common), []
    for offset in range(0, len(records), 256):
        batch = records[offset : offset + 256]
        evidence_hashes = {}
        for kind in ("readback", "acceptance"):
            lines = []
            for record in batch:
                row = record["body"]["native_journal_row"]
                line = {
                    "native_line_id": row["line_id"],
                    "native_row_sha256": digest(row),
                    "committed_run_id": row["import_run_id"],
                }
                if kind == "readback":
                    line.update(
                        effective_value={"numberValue": 1},
                        source_row=record["source_row"],
                    )
                else:
                    line["acceptance_basis"] = record["basis"]
                lines.append(line)
            raw = canonical_bytes(
                {
                    "contract": f"books-line-{kind}.v1",
                    "source_snapshot_sha256": original["source_snapshot_sha256"],
                    "captured_at": "2026-01-30T00:00:00Z",
                    "lines": lines,
                }
            )
            sha = hashlib.sha256(raw).hexdigest()
            blobs[sha] = raw
            refs.append({"kind": kind, "sha256": sha, "bytes": len(raw)})
            evidence_hashes[kind] = sha
        units = []
        for record in batch:
            row = record["body"]["native_journal_row"]
            units.append(
                {k: v for k, v in record.items() if k != "basis"}
                | {
                    "acceptance": {
                        "native_line_id": row["line_id"],
                        "committed_run_id": row["import_run_id"],
                        "effective_value": {"numberValue": 1},
                        "acceptance_predicate": "books-native-committed-count.v1",
                        "committed_runs_sha256": run_sha,
                        "frozen_readback_sha256": evidence_hashes["readback"],
                        "acceptance_artifact_sha256": evidence_hashes["acceptance"],
                        "acceptance_basis": record["basis"],
                    }
                }
            )
        raw = canonical_bytes(
            {
                "contract": "books-provenance-chunk.v1",
                "ordinal": len(chunks),
                "units": units,
            }
        )
        sha = hashlib.sha256(raw).hexdigest()
        blobs[sha] = raw
        chunks.append(
            {
                "ordinal": len(chunks),
                "sha256": sha,
                "bytes": len(raw),
                "units": len(units),
            }
        )
    unit_ids = [r["unit_id"] for r in records]
    index = canonical_bytes(
        {
            **original,
            "accepted_unit_set_sha256": digest(unit_ids),
            "chunks": chunks,
            "evidence": sorted(refs, key=lambda r: r["sha256"]),
        }
    )
    publication["accepted_journal_revision_ids"] = unit_ids
    publication["source_references"] = [
        r
        for r in publication["source_references"]
        if not r["evidence_id"].startswith("hbprov1:")
    ]
    publication["source_references"].append(
        {
            "evidence_id": "hbprov1:" + hashlib.sha256(index).hexdigest(),
            "source_kind": "accepted_books",
            "access": "owner_only",
        }
    )
    publication["source_references"].sort(key=lambda r: r["evidence_id"])
    for report in publication["reports"]:
        for metric in report["metrics"]:
            metric["revision_ids"] = [unit_ids[0]]
    seal_publication(publication)
    return publication, ClosedPackage(
        index, blobs, frozenset(r["sha256"] for r in refs)
    )


def sized_package(tenant, owner):
    publication, package = _proof(tenant, owner, 0)
    raw_size = len(package.index) + sum(map(len, package.blobs.values()))
    padding = max(0, math.ceil((PROVENANCE_TARGET + 2048 - raw_size) / UNIT_COUNT))
    publication, package = _proof(tenant, owner, padding)
    raw_size = len(package.index) + sum(map(len, package.blobs.values()))
    assert len(package.blobs) == 98
    assert PROVENANCE_TARGET <= raw_size < PROVENANCE_TARGET + 2 * UNIT_COUNT
    assert max(map(len, package.blobs.values())) <= 1024 * 1024
    # Account for the accepted unit anchor separately from dated event bodies.
    base = len(canonical_bytes(witness_projection(projection(publication))))
    sample = len(canonical_bytes(witness_projection(projection(publication, 1200))))
    count = math.ceil((DATED_TARGET - base) / ((sample - base) / 1200)) + 24
    assert 0 < count <= 10000
    dated = witness_projection(projection(publication, count))
    dated["content_sha256"] = content_hash(dated)
    assert DATED_TARGET <= len(canonical_bytes(dated)) <= 8 * 1024 * 1024
    return publication, dated, package
