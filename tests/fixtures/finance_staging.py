"""Explicit hosted-test prototype: encrypted unpublished stage and phase handoff.

No deployment switch, production key authority, provider access, or performance
claim. The original SyntheticStorage remains the default non-staged fixture.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import resource
import secrets
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from adminme.events.envelope import EventEnvelope
from adminme.events.invariants import validate_envelope_invariants
from adminme.finance.import_work import run_cpu
from adminme.finance.publication import PublicationError
from tests.fixtures.finance_storage_isolation import (
    SyntheticStorage,
    artifact_digest,
    executor,
)
from tests.fixtures.finance_staging_store import (
    StageStore,
    StagedEvents,
    cleanup,
    identity,
)


class StagedStorage(SyntheticStorage):
    executor_script = Path(__file__).resolve()

    def __init__(self, root, **kwargs):
        super().__init__(root, **kwargs)
        self.stage_descriptor = None
        self.staging_source = artifact_digest()
        self.stage_lifetimes = []

    def _spawn(self):
        if artifact_digest() != self.staging_source:
            raise PublicationError("synthetic_staging_source_changed")
        prior = self.process
        super()._spawn()
        if prior is None:
            self.boundaries.append(
                {
                    "stage": "executor_spawned",
                    "pid": self.process.pid,
                    "birth": self.birth,
                    "predecessor_reaped": all(x["reaped"] for x in self.lifetimes),
                }
            )

    def _send(self, command):
        if command["op"] == "prepare":
            if self.stage_descriptor is not None:
                raise PublicationError("synthetic_stage_busy")
            self.stage_descriptor = {
                "operation": command["operation"],
                "encryption_key": secrets.token_bytes(32).hex(),
                "mac_key": secrets.token_bytes(32).hex(),
                "artifact": self.staging_source,
            }
            command = {**command, "stage": self.stage_descriptor}
        elif command["op"] == "apply" and self.stage_descriptor is not None:
            raise PublicationError("synthetic_phase_overlap")
        super()._send(command)

    def _reap(self, graceful=True):
        # If wait fails, super quarantines. No cleanup/key release or new owner.
        pid = self.process.pid if self.process is not None else None
        super()._reap(graceful)
        if self.quarantined:
            return
        descriptor = self.stage_descriptor
        if descriptor is not None:
            assert self.process is None
            cleanup(self.root, descriptor["operation"])
            self.stage_lifetimes.append(
                {
                    "operation": descriptor["operation"],
                    "pid": pid,
                    "reaped": True,
                    "stage_removed": True,
                }
            )
            self.stage_descriptor = None

    async def after_append(self, result):
        # Called under the existing parent writer/operation locks, after COMMIT
        # (or exact durable recovery). No stage/process wait occurs in SQLite's
        # write transaction. The next apply can only spawn a fresh owner.
        await run_cpu(self._reap)
        assert self.process is None and self.stage_descriptor is None
        self.boundaries.append(
            {"stage": "preparation_reaped_before_money", "events": len(result)}
        )


def fresh_controller_recovery():
    """No authoritative durable operation anchor exists in this prototype.

    Volatile refs/keys from a lost controller cannot justify exact reconciliation
    or a commit retry. Existing event history may be durable, but this boundary
    deliberately makes no claim identifying a complete lost operation.
    """
    raise PublicationError("synthetic_controller_outcome_unknown")


def new_stage(root, header):
    descriptor = header["stage"]
    if (
        set(descriptor) != {"operation", "encryption_key", "mac_key", "artifact"}
        or descriptor["operation"] != header["operation"]
        or descriptor["artifact"] != artifact_digest()
    ):
        raise ValueError("synthetic_stage_binding")
    return StageStore(
        root,
        identity(header, descriptor["artifact"]),
        bytes.fromhex(descriptor["encryption_key"]),
        bytes.fromhex(descriptor["mac_key"]),
    )


async def append_staged(log, scope, stored, commit_guard, correlation_id):
    """Only the synthetic stage's authenticated receipts; production unchanged."""
    if not isinstance(stored, StagedEvents) or not 1 <= len(stored) <= 131:
        raise ValueError("synthetic_stage_required")
    context = stored.store.context
    if (context["tenant_id"], context["owner_id"], context["book_id"]) != scope:
        raise ValueError("synthetic_stage_scope")
    stored.validate_all()
    stored.store.close_writer()

    def insert():
        reader = stored.store.connect(readonly=True)
        try:
            stored.store.check_seal(reader)

            def rows():
                for ordinal in range(len(stored)):
                    event = stored.event(ordinal, reader)
                    if (event.tenant_id, event.owner_id, event.book_id) != scope:
                        raise ValueError("synthetic_stage_scope")
                    envelope = EventEnvelope(
                        event_at_ms=int(time.time() * 1000),
                        tenant_id=scope[0],
                        type=event.type,
                        schema_version=1,
                        occurred_at=EventEnvelope.now_utc_iso(),
                        source_adapter=event.source_adapter,
                        source_account_id=scope[2],
                        owner_scope=f"private:{scope[1]}",
                        visibility_scope=f"private:{scope[1]}",
                        sensitivity="privileged",
                        actor_identity=scope[1],
                        idempotency_key=event.idempotency_key,
                        payload={},
                    )
                    validate_envelope_invariants(envelope)
                    row, _ = log._row_with_payload(
                        envelope, event.payload_json, correlation_id, None
                    )
                    yield row

            # Same existing transaction/duplicate handling and both guard calls.
            # Local stage reads cannot acquire a competing writer or ask for IPC.
            calls = 0

            def guarded():
                nonlocal calls
                calls += 1
                if calls == 2:
                    # Local immutable-stage integrity work precedes the existing
                    # final confirmation/recheck; that authority guard remains
                    # the last operation immediately before COMMIT.
                    stored.store.check_seal(reader)
                commit_guard()

            return log._insert_rows(rows(), guarded)
        finally:
            reader.close()

    async with log._lock:
        log._ensure_open()
        return await log._run_on_connection_thread(insert)


if __name__ == "__main__":
    if sys.argv[1:] != ["executor"] or os.name != "posix":
        raise SystemExit("synthetic staging executor only")
    resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (194 * 1024 * 1024,) * 2)
    asyncio.run(executor(staged=True))
