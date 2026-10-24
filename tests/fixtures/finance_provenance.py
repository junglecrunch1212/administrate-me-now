"""Synthetic draft proof closure. Native count predicate is explicit and typed."""

from copy import deepcopy
import hashlib

from adminme.finance.publication import canonical_bytes, content_hash
from adminme.finance.publication_provenance import (
    ClosedPackage,
    FIELDS,
    REPORT_ORDER,
    digest,
    committed_formula,
)
from tests.acceptance.test_finance_publications import publication


def native_rows(count=4):
    bases = [
        "accepted_historical",
        "accepted_manual",
        "accepted_reversal",
        "committed_generated",
    ]
    rows = []
    for n in range(count):
        row = {field: "" for field in FIELDS}
        row.update(
            line_id=f"line-{n}",
            journal_id=f"journal-{n}",
            canonical_event_id=f"event-{n}",
            import_run_id="run-accepted",
            source_revision_id=f"raw-{n}" if n > 1 else None if n == 0 else "",
            committed_flag=committed_formula(n + 1),
            debit=1,
            credit=0,
            signed_dr=1,
            cash_equivalent_flag=True,
            currency="USD",
        )
        rows.append((row, bases[min(n, 3)]))
    return rows


def package_fixture(
    tenant="tenant-synthetic", owner="member-synthetic", rows=None, evaluated=1
):
    p = publication(tenant)
    p["owner_id"] = owner
    p["excluded_pending_revision_ids"] = ["revision-pending"]
    source = "a" * 64
    rows = deepcopy(rows if rows is not None else native_rows())
    readback_lines, acceptance_lines, records = [], [], []
    for number, (row, basis) in enumerate(rows, 1):
        readback_lines.append(
            {
                "native_line_id": row["line_id"],
                "native_row_sha256": digest(row),
                "committed_run_id": row["import_run_id"],
                "effective_value": {"numberValue": evaluated},
                "source_row": number,
            }
        )
        acceptance_lines.append(
            {
                "native_line_id": row["line_id"],
                "native_row_sha256": digest(row),
                "committed_run_id": row["import_run_id"],
                "acceptance_basis": basis,
            }
        )
    docs = {
        "readback": {
            "contract": "books-line-readback.v1",
            "source_snapshot_sha256": source,
            "captured_at": "2026-01-30T00:00:00Z",
            "lines": readback_lines,
        },
        "acceptance": {
            "contract": "books-line-acceptance.v1",
            "source_snapshot_sha256": source,
            "captured_at": "2026-01-30T00:00:00Z",
            "lines": acceptance_lines,
        },
        "held_observations": {
            "contract": "books-held-observations.v1",
            "source_snapshot_sha256": source,
            "observations": [
                {
                    "source_revision_id": "revision-pending",
                    "canonical_event_id": "held-event",
                }
            ],
        },
    }
    docs["committed_runs"] = {
        "contract": "books-committed-runs.v1",
        "source_snapshot_sha256": source,
        "runs": [{"run_id": "run-accepted", "status": "COMMITTED"}],
    }
    blobs, refs, hashes = {}, [], {}
    for kind, doc in docs.items():
        raw = canonical_bytes(doc)
        sha = hashlib.sha256(raw).hexdigest()
        blobs[sha] = raw
        hashes[kind] = sha
        refs.append({"kind": kind, "sha256": sha, "bytes": len(raw)})
    for number, (row, basis) in enumerate(rows, 1):
        body = {
            "unit_contract": "books-journal-unit.v1",
            "authoritative_book_id": p["book_id"],
            "native_journal_row": row,
        }
        records.append(
            {
                "unit_id": "hbjr1:" + digest(body),
                "body": body,
                "source_row": number,
                "acceptance": {
                    "native_line_id": row["line_id"],
                    "committed_run_id": row["import_run_id"],
                    "effective_value": {"numberValue": evaluated},
                    "acceptance_predicate": "books-native-committed-count.v1",
                    "committed_runs_sha256": hashes["committed_runs"],
                    "frozen_readback_sha256": hashes["readback"],
                    "acceptance_artifact_sha256": hashes["acceptance"],
                    "acceptance_basis": basis,
                },
            }
        )
    records.sort(key=lambda r: r["unit_id"])
    chunk = canonical_bytes(
        {"contract": "books-provenance-chunk.v1", "ordinal": 0, "units": records}
    )
    sha = hashlib.sha256(chunk).hexdigest()
    blobs[sha] = chunk
    units = [r["unit_id"] for r in records]
    index = canonical_bytes(
        {
            "contract": "books-provenance-index.v1",
            "unit_contract": "books-journal-unit.v1",
            "tenant_id": tenant,
            "owner_id": owner,
            "book_id": p["book_id"],
            "source_snapshot_sha256": source,
            "accepted_unit_set_sha256": digest(units),
            "chunks": [
                {"ordinal": 0, "sha256": sha, "bytes": len(chunk), "units": len(units)}
            ],
            "evidence": sorted(refs, key=lambda r: r["sha256"]),
        }
    )
    p["accepted_journal_revision_ids"] = units
    p["source_references"].append(
        {
            "evidence_id": "hbprov1:" + hashlib.sha256(index).hexdigest(),
            "source_kind": "accepted_books",
            "access": "owner_only",
        }
    )
    p["source_references"].sort(key=lambda r: r["evidence_id"])
    p["reports"].sort(key=lambda r: REPORT_ORDER.index(r["report_id"]))
    for report in p["reports"]:
        for metric in report["metrics"]:
            metric["owner_id"] = owner
            metric["revision_ids"] = [units[0]]
    seal_publication(p)
    return p, ClosedPackage(index, blobs, frozenset(hashes.values()))


def seal_publication(p):
    p["publication_id"] = "hbap1:" + digest(
        {k: v for k, v in p.items() if k not in {"publication_id", "content_sha256"}}
    )
    p["content_sha256"] = content_hash(p)
    return p


def rewrite_evidence(p, package, kind, mutate):
    """Synthetic new reviewed evidence, with every dependent hash recomputed."""
    import json

    p = deepcopy(p)
    index = json.loads(package.index)
    blobs = dict(package.blobs)
    ref = next(r for r in index["evidence"] if r["kind"] == kind)
    old = ref["sha256"]
    doc = json.loads(blobs.pop(old))
    mutate(doc)
    raw = canonical_bytes(doc)
    new = hashlib.sha256(raw).hexdigest()
    blobs[new] = raw
    ref.update(sha256=new, bytes=len(raw))
    for chunk_ref in index["chunks"]:
        chunk = json.loads(blobs.pop(chunk_ref["sha256"]))
        for record in chunk["units"]:
            for key, value in record["acceptance"].items():
                if value == old:
                    record["acceptance"][key] = new
        raw = canonical_bytes(chunk)
        sha = hashlib.sha256(raw).hexdigest()
        blobs[sha] = raw
        chunk_ref.update(sha256=sha, bytes=len(raw))
    index["evidence"].sort(key=lambda r: r["sha256"])
    raw_index = canonical_bytes(index)
    for ref in p["source_references"]:
        if ref["evidence_id"].startswith("hbprov1:"):
            ref["evidence_id"] = "hbprov1:" + hashlib.sha256(raw_index).hexdigest()
    p["source_references"].sort(key=lambda r: r["evidence_id"])
    seal_publication(p)
    return p, ClosedPackage(
        raw_index, blobs, frozenset(r["sha256"] for r in index["evidence"])
    )
