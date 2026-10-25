"""Isolated-process entry point. Invoked only with the pinned interpreter -I -B.

This is a calculation process, not an OS sandbox. Its environment is empty of
credentials, its pipes expose only bounded Finance input and Money SQL, and it
never opens a database, provider, network connection or plaintext staging file.
Keys, connection ownership, authorization and durable effects stay in parent.
"""

from __future__ import annotations

# Direct script invocation works in the installed release and source-based lab.
# No ambient working directory or PYTHONPATH is admitted by the isolated Python.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import re
import json
import resource
import threading
from datetime import datetime, timezone, timedelta
from itertools import islice

from adminme.finance.worker_protocol import (
    CONTROL_LIMIT,
    FRAME_LIMIT,
    UPLOAD_CHUNK,
    read_frame,
    read_json,
    write_frame,
    write_json,
)

_output_lock = threading.Lock()
_input = sys.stdin.buffer
_output = sys.stdout.buffer


def send(value):
    with _output_lock:
        write_json(_output, value)


def usage():
    value = resource.getrusage(resource.RUSAGE_SELF)
    peak = value.ru_maxrss * (1 if sys.platform == "darwin" else 1024)
    current = peak
    if sys.platform == "linux":
        import os

        with open("/proc/self/statm") as stream:
            current = int(stream.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    return {
        "rss_bytes": current,
        "peak_rss_bytes": peak,
        "cpu_seconds": value.ru_utime + value.ru_stime,
    }


class Cursor:
    def __init__(self, rows):
        self.rows = iter(rows)

    def fetchone(self):
        return next(self.rows, None)

    def fetchall(self):
        return list(self.rows)

    def __iter__(self):
        return self.rows


class MoneyConnection:
    """A request-local proxy; the parent supplies its existing owned handle."""

    def execute(self, sql, parameters=()):
        send({"kind": "sql", "sql": sql, "parameters": list(parameters)})
        response = read_json(_input, FRAME_LIMIT)
        if response.get("kind") != "rows":
            raise ValueError("finance_worker_query_failed")
        return Cursor(response["rows"])

    def executemany(self, sql, parameters):
        iterator = iter(parameters)
        while batch := list(islice(iterator, 128)):
            send({"kind": "sql_many", "sql": sql, "parameters": batch})
            response = read_json(_input, CONTROL_LIMIT)
            if response.get("kind") != "rows":
                raise ValueError("finance_worker_query_failed")
        return Cursor([])


class Upload:
    """Restricted closed-packet JSON reader with bounded wire buffering.

    Only object keys, base64 string values and the optional null projection are
    legal. JSON string escapes and arbitrary member order remain supported.
    Each component is decoded and released before the next wire value arrives.
    """

    def __init__(self, manifest):
        self.manifest = manifest
        self.buffer = b""
        self.offset = 0
        self.total = 0
        self.ended = False

    def peek(self):
        if self.offset == len(self.buffer) and not self.ended:
            self.buffer = read_frame(_input, UPLOAD_CHUNK)
            self.offset = 0
            self.total += len(self.buffer)
            if self.total > self.manifest.encoded_body_limit():
                raise ValueError("finance_import_body_limit")
            self.ended = not self.buffer
        return self.buffer[self.offset : self.offset + 1]

    def take(self):
        value = self.peek()
        self.offset += bool(value)
        return value

    def whitespace(self):
        while self.peek() in (b" ", b"\t", b"\r", b"\n"):
            self.take()

    def expect(self, value):
        self.whitespace()
        if self.take() != value:
            raise ValueError("finance_import_packet_invalid")

    def string(self, maximum):
        self.expect(b'"')
        wire = bytearray(b'"')
        special = re.compile(rb'["\\]')
        while True:
            if not self.peek() or len(wire) > maximum + 4096:
                raise ValueError("finance_import_component_size")
            match = special.search(self.buffer, self.offset)
            if match is None:
                wire.extend(self.buffer[self.offset :])
                self.offset = len(self.buffer)
                continue
            wire.extend(self.buffer[self.offset : match.end()])
            self.offset = match.end()
            if match[0] == b'"':
                break
            escaped = self.take()
            if not escaped:
                raise ValueError("finance_import_packet_invalid")
            wire.extend(escaped)
        decoded = json.loads(wire)
        if not isinstance(decoded, str) or len(decoded) > maximum:
            raise ValueError("finance_import_component_size")
        return decoded

    def object(self, blobs=False):
        from adminme.finance.closed_import import _RawComponent, _decode
        from adminme.events.schemas.finance_import import ImportBlobPin

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
                value = self.object(blobs=True)
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
                    value = None
                else:
                    wire = self.string(4 * ((pin.bytes + 2) // 3))
                    value = _RawComponent(_decode(wire, pin))
            result[key] = value
            self.whitespace()
            delimiter = self.take()
            if delimiter == b"}":
                return result
            if delimiter != b",":
                raise ValueError("finance_import_packet_invalid")

    def packet(self):
        packet = self.object()
        self.whitespace()
        if self.peek():
            raise ValueError("finance_import_packet_invalid")
        return packet


def prepare(header):
    from adminme.events.schemas.finance_import import FinanceImportManifest
    from adminme.finance.closed_import import _decode_packet
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
    publication, projection, package = _decode_packet(
        Upload(manifest).packet(), manifest
    )
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
    dated_error = None
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
            if old and old[0] != projection["content_sha256"]:
                raise ValueError("projection_identity_or_prior_conflict")
            if not old and expected[1] != projection["prior_projection_id"]:
                raise ValueError("projection_prior_conflict")
            if existing and expected[0] != publication["publication_id"]:
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
        }
    )
    root = namespace_root(publication).removeprefix("hbprov1:")
    chunks = {row["sha256"] for row in parse_blob(package.index, 262144)["chunks"]}

    def event(kind, payload, event_key, model, source="accepted_books"):
        model.model_validate(payload)
        raw = json.dumps(payload, separators=(",", ":")).encode()
        info = {
            "kind": "event",
            "type": kind,
            "source_adapter": source,
            "idempotency_key": event_key,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        with _output_lock:
            write_json(_output, info)
            write_frame(_output, raw)

    for digest in [*sorted(package.blobs), root]:
        is_index = digest == root
        raw = package.index if is_index else package.blobs[digest]
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
                "approved_evidence": sorted(package.approved_evidence)
                if is_index
                else [],
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


def apply(header):
    from adminme.finance.provenance_store import apply_provenance
    from adminme.finance.projection import apply_publication
    from adminme.finance.planning_store import apply_projection

    raw = read_frame(_input)
    envelope = header["envelope"]
    envelope["payload"] = json.loads(raw)
    handlers = {
        "finance.provenance_archived": apply_provenance,
        "finance.publication_accepted": apply_publication,
        "finance.projection_verified": apply_projection,
    }
    handlers[envelope["type"]](envelope, MoneyConnection())


def main():
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    stop = threading.Event()

    def measure():
        while not stop.wait(0.05):
            send({"kind": "usage", **usage()})

    observer = threading.Thread(target=measure, name="finance-child-usage", daemon=True)
    observer.start()
    try:
        header = read_json(_input)
        if header.get("operation") == "prepare":
            prepare(header)
        elif header.get("operation") == "apply":
            apply(header)
        else:
            raise ValueError("finance_worker_operation_invalid")
        send({"kind": "done", **usage()})
    except BaseException as exc:
        # No exception messages from validators/SQL/private data reach logs.
        from adminme.finance.publication import PublicationError

        reason = (
            str(exc) if isinstance(exc, PublicationError) else "finance_worker_failed"
        )
        if isinstance(exc, ValueError) and str(exc).startswith(
            ("finance_", "publication_", "projection_")
        ):
            reason = str(exc)
        send({"kind": "error", "reason": reason[:120], **usage()})
        raise SystemExit(1) from None
    finally:
        stop.set()
        observer.join(timeout=1)


if __name__ == "__main__":
    main()
