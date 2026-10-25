"""Synthetic adapter for the existing closed-package validation/emission path.

Preparation below preserves worker_child.prepare's validation ordering and exact
payload encoding. Its two ownership changes are disk-backed components and a
lazy proof mapping during emission. No production module imports this adapter.
"""

from datetime import datetime, timedelta, timezone
import json

from adminme.events.schemas.finance_import import ImportBlobPin
from adminme.finance.closed_import import _canonical_json, _decode
from adminme.finance.dated_projection import _validate_owned_projection
from adminme.finance.publication import PublicationError, validate_publication
from adminme.finance.publication_provenance import ClosedPackage, namespace_root
from adminme.finance import worker_child
from adminme.finance.worker import _sql
from tests.fixtures.finance_staging_store import StagedBlobs, StagedEvents, receipt


class StagedUpload(worker_child.Upload):
    def __init__(self, manifest, store):
        super().__init__(manifest)
        self.store = store

    def object(self, blobs=False):
        self.expect(b"{")
        result = {}
        self.whitespace()
        if self.peek() == b"}":
            self.take()
            return result
        while True:
            key = self.string(128)
            if key in result:
                raise ValueError("finance_import_duplicate_key")
            self.expect(b":")
            if not blobs and key == "blobs":
                result[key] = self.object(blobs=True)
            else:
                if blobs:
                    if key not in self.manifest.blobs:
                        raise ValueError("finance_import_packet_invalid")
                    pin = ImportBlobPin(sha256=key, bytes=self.manifest.blobs[key])
                elif key in {"publication", "projection", "index"}:
                    pin = getattr(self.manifest, key)
                else:
                    raise ValueError("finance_import_packet_invalid")
                self.whitespace()
                if pin is None:
                    if b"".join(self.take() for _ in range(4)) != b"null":
                        raise ValueError("finance_import_packet_invalid")
                    result[key] = None
                else:
                    wire = self.string(4 * ((pin.bytes + 2) // 3))
                    raw = _decode(wire, pin)
                    del wire
                    name = "blob:" + key if blobs else key
                    self.store.put("component", name, raw)
                    del raw
                    result[key] = name
            self.whitespace()
            delimiter = self.take()
            if delimiter == b"}":
                return result
            if delimiter != b",":
                raise ValueError("finance_import_packet_invalid")


def decode_staged_packet(packet, manifest, store):
    if set(packet) != {"publication", "projection", "index", "blobs"} or set(
        packet["blobs"]
    ) != set(manifest.blobs):
        raise PublicationError("finance_import_packet_invalid")
    publication = validate_publication(
        _canonical_json(store.read("component", "publication")[0])
    )
    if namespace_root(publication) != "hbprov1:" + manifest.index.sha256:
        raise PublicationError("finance_import_provenance_root_mismatch")
    if any(publication.get(k) != v for k, v in manifest.binding.model_dump().items()):
        raise PublicationError("finance_import_binding_mismatch")
    index_bytes = store.read("component", "index")[0]
    index = _canonical_json(index_bytes)
    if index.get("source_snapshot_sha256") != manifest.source_snapshot_sha256:
        raise PublicationError("finance_import_source_snapshot_mismatch")
    if {row["sha256"] for row in index["evidence"]} != set(
        manifest.independently_reviewed_evidence
    ):
        raise PublicationError("finance_import_reviewed_evidence_mismatch")
    package = ClosedPackage(
        index_bytes,
        StagedBlobs(store, manifest.blobs),
        frozenset(manifest.independently_reviewed_evidence),
    )
    projection = None
    if manifest.projection is None:
        if packet["projection"] is not None:
            raise PublicationError("finance_import_packet_invalid")
    else:
        projection_raw = store.read("component", "projection")[0]
        projection_value = _canonical_json(projection_raw)
        del projection_raw
        projection = _validate_owned_projection(projection_value)
        if any(
            projection.get(k) != publication[k]
            for k in ("tenant_id", "owner_id", "book_id")
        ):
            raise PublicationError("finance_import_projection_owner_mismatch")
        if (
            projection["accounting_publication_id"] != publication["publication_id"]
            or projection["accounting_content_sha256"] != publication["content_sha256"]
        ):
            raise PublicationError("finance_import_projection_anchor_mismatch")
    return publication, projection, package


def prepare_staged(header, incoming, money, store, guard, diagnostics):
    from adminme.events.schemas.finance_import import FinanceImportManifest
    from adminme.finance.publication import (
        PublicationError,
        canonical_bytes,
        validate_transition,
    )
    from adminme.finance.publication_provenance import (
        namespace_root,
        verify_package,
        parse_blob,
    )
    from adminme.finance.provenance_store import (
        verify_archived,
        ProvenanceBlobArchivedV1,
    )
    from adminme.events.schemas.finance_publications import AcceptedPublicationV1
    from adminme.events.schemas.finance_dated_projection import DatedProjectionV1
    from adminme.finance.planning_store import _accounting_anchor
    import hashlib

    manifest = FinanceImportManifest.model_validate(header["manifest"])
    worker_child._input = incoming
    packet = StagedUpload(manifest, store).packet()
    guard()  # Fresh durable authority after the upload wait.
    publication, projection, package = decode_staged_packet(packet, manifest, store)
    events, metadata = StagedEvents(store), None

    class MoneyConnection:
        def execute(self, sql, parameters):
            response = _sql(
                money,
                {"kind": "sql", "sql": sql, "parameters": list(parameters)},
                read_only=True,
                scope=(
                    header["tenant_id"],
                    header["owner_id"],
                    manifest.binding.book_id,
                ),
            )
            return worker_child.Cursor(response["rows"])

    def send(value):
        nonlocal metadata
        if metadata is not None or value["kind"] != "prepared":
            raise ValueError("synthetic_preparation_protocol")
        metadata = value

    owner, tenant = header["owner_id"], header["tenant_id"]
    if publication["owner_id"] != owner or publication["tenant_id"] != tenant:
        raise ValueError("finance_import_owner_mismatch")
    now = datetime.now(timezone.utc)
    if datetime.fromisoformat(publication["accepted_at"]) > now + timedelta(minutes=5):
        raise ValueError("publication_future_clock")
    conn = MoneyConnection()
    key = (tenant, owner, publication["book_id"])
    old_head = conn.execute(
        "SELECT publication_id FROM finance_publication_heads WHERE tenant_id=? AND owner_id=? AND book_id=?",
        key,
    ).fetchone()
    dated_head = conn.execute(
        "SELECT projection_id FROM finance_dated_projection_heads WHERE tenant_id=? AND owner_id=? AND book_id=?",
        key,
    ).fetchone()
    expected = [
        old_head[0] if old_head else None,
        dated_head[0] if dated_head else None,
    ]
    existing = conn.execute(
        "SELECT content_sha256 FROM finance_publications WHERE tenant_id=? AND owner_id=? AND book_id=? AND publication_id=?",
        (*key, publication["publication_id"]),
    ).fetchone()
    if existing:
        if existing[0] != publication["content_sha256"]:
            raise ValueError("publication_identity_or_prior_conflict")
    elif expected[0] != publication["prior_publication_id"]:
        raise ValueError("publication_prior_conflict")
    previous = conn.execute(
        "SELECT publication_json FROM finance_publications WHERE tenant_id=? AND owner_id=? AND book_id=? AND publication_id=?",
        (*key, publication["prior_publication_id"]),
    ).fetchone()
    prior = json.loads(previous[0]) if previous else None
    validate_transition(prior, publication)
    prior_proof = verify_archived(conn, prior) if prior else None
    proof = verify_package(publication, package, prior_proof)
    if existing:
        verify_archived(conn, publication, prior_proof)
    dated_error = None
    existing_projection = False
    if projection:
        try:
            if datetime.fromisoformat(projection["verified_at"]) > now + timedelta(
                minutes=5
            ):
                raise PublicationError("projection_future_clock")
            _accounting_anchor(projection, publication, proof)
            old = conn.execute(
                "SELECT content_sha256 FROM finance_dated_projections WHERE tenant_id=? AND owner_id=? AND book_id=? AND projection_id=?",
                (*key, projection["projection_id"]),
            ).fetchone()
            existing_projection = bool(old)
            if old and old[0] != projection["content_sha256"]:
                raise ValueError("projection_identity_or_prior_conflict")
            if not old and expected[1] != projection["prior_projection_id"]:
                raise ValueError("projection_prior_conflict")
            if existing and not old and expected[0] != publication["publication_id"]:
                raise ValueError("projection_accounting_anchor_not_current")
        except (PublicationError, ValueError) as exc:
            dated_error = str(exc)
    send(
        {
            "kind": "prepared",
            "expected_heads": expected,
            "book_id": publication["book_id"],
            "publication_id": publication["publication_id"],
            "projection_id": projection["projection_id"] if projection else None,
            "dated_error": dated_error,
            "accounting_existing": bool(existing),
            "dated_existing": existing_projection,
        }
    )
    namespace = namespace_root(publication)
    if not isinstance(namespace, str):
        raise ValueError("finance_worker_provenance_missing")
    root = namespace.removeprefix("hbprov1:")
    chunks = {row["sha256"] for row in parse_blob(package.index, 262144)["chunks"]}

    def event(kind, payload, event_key, model, source="accepted_books"):
        model.model_validate(payload)
        raw = json.dumps(payload, separators=(",", ":")).encode()
        events.append(receipt((tenant, owner, key[2]), kind, source, event_key, raw))
        diagnostics.emitted(len(raw))

    # Exact proof is complete. Read one immutable encrypted blob at a time;
    # emitted bytes remain unpublished in the same encrypted stage.
    blobs, index, approved = (
        package.blobs,
        package.index,
        package.approved_evidence,
    )
    del package
    for digest in [*sorted(blobs), root]:
        is_index = digest == root
        raw = index if is_index else blobs[digest]
        event(
            "finance.provenance_archived",
            {
                "owner_id": owner,
                "book_id": key[2],
                "root_sha256": root,
                "content_sha256": digest,
                "kind": "index"
                if is_index
                else "chunk"
                if digest in chunks
                else "evidence",
                "canonical_json": raw.decode(),
                "approved_evidence": sorted(approved) if is_index else [],
            },
            f"finance-proof:{owner}:{root}:{digest}",
            ProvenanceBlobArchivedV1,
        )
    publication_key = (
        "finance-publication:"
        + hashlib.sha256(
            canonical_bytes(
                [
                    publication[k]
                    for k in (
                        "tenant_id",
                        "owner_id",
                        "book_id",
                        "prior_publication_id",
                    )
                ]
            )
        ).hexdigest()
    )
    event(
        "finance.publication_accepted",
        {"publication": publication},
        publication_key,
        AcceptedPublicationV1,
    )
    if projection and dated_error is None:
        projection_key = (
            "finance-dated-projection:"
            + hashlib.sha256(
                canonical_bytes(
                    [
                        projection[k]
                        for k in (
                            "tenant_id",
                            "owner_id",
                            "book_id",
                            "prior_projection_id",
                        )
                    ]
                )
            ).hexdigest()
        )
        event(
            "finance.projection_verified",
            {"projection": projection},
            projection_key,
            DatedProjectionV1,
            "accepted_books_projection",
        )

    expected = ["finance.provenance_archived"] * (len(manifest.blobs) + 1) + [
        "finance.publication_accepted"
    ]
    if projection and dated_error is None:
        expected.append("finance.projection_verified")
    if metadata is None or [event.type for event in events] != expected:
        raise ValueError("finance_worker_output_invalid")
    guard()  # Validation and all staged emission have completed; recheck expiry/policy.
    names = ["publication", "index", *["blob:" + key for key in manifest.blobs]]
    if manifest.projection is not None:
        names.append("projection")
    store.seal(metadata, names, expected)
    return events, metadata
