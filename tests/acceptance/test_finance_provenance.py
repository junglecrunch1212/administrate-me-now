"""Closed proof import/replay through synthetic production composition only."""

from functools import partial

from fastapi.testclient import TestClient
import pytest

from adminme.finance.importer import import_publication
from adminme.finance.planning_store import import_projection
from adminme.finance.provenance_store import verify_archived
from adminme.finance.publication import PublicationError, content_hash
from adminme.lib.session import build_internal_session
from adminme.runtime.application import create_application
from tests.acceptance.test_finance_publications import settings
from tests.fixtures.finance_provenance import package_fixture
from tests.fixtures.finance_planning import projection


def test_closed_package_required_then_archived_restart_and_dated_binding(tmp_path):
    config = settings(tmp_path)
    p, package = package_fixture(config.instance.tenant_id)
    owner = build_internal_session(p["owner_id"], "principal", p["tenant_id"])
    with TestClient(create_application(config)) as client:
        runtime = client.app.state.runtime
        with pytest.raises(PublicationError, match="closed_package_required"):
            client.portal.call(import_publication, runtime, owner, p)
        accepted = client.portal.call(
            partial(import_publication, runtime, owner, p, provenance_package=package)
        )
        repeat = client.portal.call(import_publication, runtime, owner, p)
        assert repeat["idempotent"] and repeat["event_id"] == accepted["event_id"]

        async def archive():
            return [
                e
                async for e in runtime.event_log.read_since(
                    types=["finance.provenance_archived"]
                )
            ]

        events = client.portal.call(archive)
        assert len(events) == len(package.blobs) + 1
        assert all(
            e["owner_scope"] == f"private:{p['owner_id']}"
            and e["sensitivity"] == "privileged"
            for e in events
        )
        dated = projection(p)
        with pytest.raises(PublicationError, match="source_snapshot_unverified"):
            client.portal.call(import_projection, runtime, owner, dated)
        witness = dict(dated["evidence"][0])
        witness.update(
            evidence_id="hbsource1:" + "a" * 64, artifact_ref="hbsource1:" + "a" * 64
        )
        dated["evidence"].append(witness)
        dated["content_sha256"] = content_hash(dated)
        assert (
            client.portal.call(import_projection, runtime, owner, dated)["state"]
            == "verified"
        )
    with TestClient(create_application(config)) as client:
        runtime = client.app.state.runtime

        async def inspect():
            return await runtime.projections.read(
                "money", lambda conn: verify_archived(conn, p)
            )

        assert client.portal.call(inspect).unit_ids == frozenset(
            p["accepted_journal_revision_ids"]
        )
        assert client.portal.call(import_publication, runtime, owner, p)["idempotent"]


def test_proof_archive_is_rebuildable_and_missing_proof_is_hold(tmp_path):
    import sqlite3
    from pathlib import Path
    from adminme.finance.projection import apply_publication
    from adminme.finance.provenance_store import apply_provenance

    config = settings(tmp_path)
    p, package = package_fixture(config.instance.tenant_id)
    owner = build_internal_session(p["owner_id"], "principal", p["tenant_id"])
    with TestClient(create_application(config)) as client:
        runtime = client.app.state.runtime
        accepted = client.portal.call(
            partial(import_publication, runtime, owner, p, provenance_package=package)
        )

        async def history():
            return [
                e
                async for e in runtime.event_log.read_since(
                    types=[
                        "finance.provenance_archived",
                        "finance.publication_accepted",
                    ]
                )
            ]

        events = client.portal.call(history)
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        (Path(__file__).parents[2] / "adminme/projections/money/schema.sql").read_text()
    )
    with pytest.raises(PublicationError, match="recovery_hold"):
        apply_publication(events[-1], conn)
    for event in events:
        (
            apply_provenance
            if event["type"] == "finance.provenance_archived"
            else apply_publication
        )(event, conn)
    assert verify_archived(conn, p).unit_ids == frozenset(
        p["accepted_journal_revision_ids"]
    )
    assert events[-1]["event_id"] == accepted["event_id"]
    conn.close()


def test_unverified_predecessor_and_revoked_owner_append_no_publication(
    tmp_path, monkeypatch
):
    from dataclasses import replace
    from tests.fixtures.finance_provenance import seal_publication

    config = settings(tmp_path)
    p, package = package_fixture(config.instance.tenant_id)
    owner = build_internal_session(p["owner_id"], "principal", p["tenant_id"])
    with TestClient(create_application(config)) as client:
        runtime = client.app.state.runtime
        p["prior_publication_id"] = "unknown-prior"
        seal_publication(p)
        with pytest.raises(PublicationError, match="prior_conflict"):
            client.portal.call(
                partial(
                    import_publication, runtime, owner, p, provenance_package=package
                )
            )
        p["prior_publication_id"] = None
        seal_publication(p)
        original = runtime.events.write

        async def revoke_after_first_blob(envelope, **kwargs):
            result = await original(envelope, **kwargs)
            if envelope.type == "finance.provenance_archived":
                runtime.settings = replace(runtime.settings, member_roles={})
            return result

        monkeypatch.setattr(runtime.events, "write", revoke_after_first_blob)
        with pytest.raises(PublicationError, match="owner_authority_required"):
            client.portal.call(
                partial(
                    import_publication, runtime, owner, p, provenance_package=package
                )
            )

        async def publications():
            return [
                e
                async for e in runtime.event_log.read_since(
                    types=["finance.publication_accepted"]
                )
            ]

        assert client.portal.call(publications) == []
