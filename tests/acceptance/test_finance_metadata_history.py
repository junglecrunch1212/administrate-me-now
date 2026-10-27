"""Bounded retained-history reader, on isolated SQLCipher test storage only."""

from copy import deepcopy
import importlib
import json

from fastapi.testclient import TestClient
import pytest

from adminme.events.registry import registry
from adminme.finance.publication import content_hash, validate_publication
from adminme.lib.session import build_internal_session
from adminme.runtime.application import create_application
from tests.acceptance.test_finance_metadata_owner import (
    settings,
    control,
    owner_read,
    seed_retained_publication,
    publication_fixture,
    retained_envelope,
    PRINCIPAL,
    OTHER,
    DEMO_HEADERS,
)


def test_dependency_registry_and_metadata_keeps_books_closed(tmp_path):
    from adminme.finance.publication_provenance import namespace_root

    for name in (
        "publication",
        "cash_publication",
        "publication_provenance",
        "dated_projection",
        "metadata",
        "metadata_control",
    ):
        importlib.import_module("adminme.finance." + name)
    with TestClient(create_application(settings(tmp_path))) as client:
        pub = publication_fixture(client.app.state.runtime.settings.instance.tenant_id)
        assert validate_publication(pub) == pub
        assert namespace_root(pub) is None
        for kind in (
            "finance.publication_accepted",
            "finance.projection_verified",
            "settings.finance_metadata_changed",
            "finance.metadata_inspected",
        ):
            assert registry.get(kind, 1) is not None
        # The combined release exposes Finance routes, but metadata access must
        # not create a Books grant or authorize a projection read.
        finance_before = client.get(
            "/api/member/v1/settings/finance", headers=DEMO_HEADERS
        )
        assert finance_before.status_code == 200, finance_before.text
        policy = finance_before.json()
        assert policy["revision"] == 0 and policy["binding"] is None
        assert policy["books_enabled"] is False
        assert policy["cos_analysis_enabled"] is False
        assert client.app.state.runtime.settings.finance_publications_enabled is False
        assert control(client, owner=True).json()["cos_read_enabled"] is False
        assert owner_read(client).json()["provider_effect_count"] == 0
        finance_after = client.get(
            "/api/member/v1/settings/finance", headers=DEMO_HEADERS
        )
        assert finance_after.status_code == 200, finance_after.text
        assert finance_after.json() == policy
        planning = client.get(
            "/api/member/v1/finance/planning",
            headers=DEMO_HEADERS,
            params={"book_id": pub["book_id"]},
        )
        assert planning.status_code == 403
        assert planning.json()["detail"] == "finance_book_not_authorized"

        async def effects():
            return [
                e
                async for e in client.app.state.runtime.event_log.read_since(
                    types=[
                        "settings.finance_access_changed",
                        "settings.finance_import_approved",
                        "finance.import_result",
                        "service.delegation.requested",
                    ]
                )
            ]

        assert client.portal.call(effects) == []


def test_populated_is_fixed_owner_scoped_and_contains_no_financial_values(tmp_path):
    config = settings(tmp_path)
    with TestClient(create_application(config)) as client:
        runtime = client.app.state.runtime
        pub = seed_retained_publication(client)
        foreign_owner = publication_fixture(config.instance.tenant_id, OTHER)
        foreign_tenant = publication_fixture("tenant-other-synthetic")
        client.portal.call(runtime.event_log.append, retained_envelope(foreign_owner))
        client.portal.call(runtime.event_log.append, retained_envelope(foreign_tenant))
        assert control(client, owner=True).status_code == 200
        response = owner_read(client)
        result = response.json()
        assert result["state"] == "populated", result
        assert result["completeness"]["scanned_events"] == 1
        assert (
            result["bindings"][0]["accounting"]["publication_id"]
            == pub["publication_id"]
        )
        assert result["identity"]["owner_id"] == PRINCIPAL
        for forbidden in (
            "value_minor",
            "metrics",
            "description",
            "source_url",
            "accepted_journal_revision_ids",
            OTHER,
            "tenant-other-synthetic",
        ):
            assert forbidden not in response.text
    with TestClient(create_application(config)) as client:
        repeated = owner_read(client).json()
        assert repeated["completeness"] == result["completeness"]
        assert repeated["bindings"] == result["bindings"]


def test_current_role_tenant_and_scope_required_by_reader(tmp_path):
    from adminme.finance.metadata import binding_metadata

    with TestClient(create_application(settings(tmp_path))) as client:
        runtime = client.app.state.runtime
        seed_retained_publication(client)
        for member, role, tenant in (
            (PRINCIPAL, "child", runtime.settings.instance.tenant_id),
            (PRINCIPAL, "principal", "foreign-tenant"),
            (OTHER, "principal", runtime.settings.instance.tenant_id),
        ):
            session = build_internal_session(member, role, tenant)
            result = client.portal.call(binding_metadata, runtime, session)
            assert result["state"] in {"access_unavailable", "verified_empty"}
            assert result["bindings"] == []


def test_fixed_watermark_excludes_successor_committed_after_snapshot(
    tmp_path, monkeypatch
):
    with TestClient(create_application(settings(tmp_path))) as client:
        pub = seed_retained_publication(client)
        runtime = client.app.state.runtime
        assert control(client, owner=True).status_code == 200
        original = runtime.event_log.read_finance_binding_metadata

        async def append_after_snapshot(*args):
            result = await original(*args)
            successor = deepcopy(pub)
            successor.update(
                publication_id="publication-next",
                prior_publication_id=pub["publication_id"],
            )
            successor["content_sha256"] = content_hash(successor)
            await runtime.event_log.append(retained_envelope(successor))
            return result

        monkeypatch.setattr(
            runtime.event_log, "read_finance_binding_metadata", append_after_snapshot
        )
        first = owner_read(client).json()
        monkeypatch.setattr(
            runtime.event_log, "read_finance_binding_metadata", original
        )
        second = owner_read(client).json()
        assert (
            first["bindings"][0]["accounting"]["publication_id"]
            == pub["publication_id"]
        )
        assert (
            second["bindings"][0]["accounting"]["publication_id"] == "publication-next"
        )
        assert first["completeness"]["scanned_events"] == 1
        assert second["completeness"]["scanned_events"] == 2
        assert (
            first["completeness"]["through_event_id"]
            != second["completeness"]["through_event_id"]
        )
        assert (
            first["completeness"]["history_sha256"]
            != second["completeness"]["history_sha256"]
        )


def test_history_gap_never_reports_empty_or_partial_head(tmp_path):
    with TestClient(create_application(settings(tmp_path))) as client:
        pub = seed_retained_publication(client)
        pub.update(
            publication_id="publication-gap", prior_publication_id="missing-predecessor"
        )
        pub["content_sha256"] = content_hash(pub)
        client.portal.call(
            client.app.state.runtime.event_log.append, retained_envelope(pub)
        )
        assert control(client, owner=True).status_code == 200
        result = owner_read(client).json()
        assert result["state"] == "history_incomplete"
        assert result["bindings"] == [] and result["completeness"]["complete"] is False


def test_real_row_scan_bound_preserves_log_and_refuses_partial_heads(tmp_path):
    with TestClient(create_application(settings(tmp_path))) as client:
        pub = seed_retained_publication(client)
        runtime = client.app.state.runtime
        client.portal.call(
            runtime.event_log.append_batch, [retained_envelope(pub) for _ in range(256)]
        )
        assert control(client, owner=True).status_code == 200
        result = owner_read(client).json()
        assert result["state"] == "history_incomplete", result
        assert result["reason"] == "history_scan_limit"
        assert result["bindings"] == [] and result["completeness"]["complete"] is False
        assert client.portal.call(runtime.event_log.count) >= 257


@pytest.mark.parametrize(
    "kind", ["version", "scope", "bytes", "invalid_json", "long_scalar"]
)
def test_corrupt_synthetic_history_fails_closed(tmp_path, kind):
    with TestClient(create_application(settings(tmp_path))) as client:
        runtime = client.app.state.runtime
        pub = seed_retained_publication(client)

        # Deliberate corruption only in this test's fresh SQLCipher store.
        def corrupt():
            # Insert a malformed retained row; append-only triggers stay intact.
            row = dict(
                runtime.event_log._conn.execute(
                    "SELECT * FROM events WHERE type='finance.publication_accepted' LIMIT 1"
                ).fetchone()
            )
            row["event_id"] = "7ZZZZZZZZZZZZZZZZZZZZZZZZZ"
            if kind == "version":
                row["schema_version"] = 99
            elif kind == "scope":
                row["visibility_scope"] = "shared:household"
            elif kind == "bytes":
                row["payload"] = '{"padding":"' + "x" * 33_554_432 + '"}'
            elif kind == "invalid_json":
                row["payload"] = "{"
            else:
                value = deepcopy(pub)
                value["book_id"] = "x" * 10000
                row["payload"] = json.dumps({"publication": value})
            runtime.event_log._conn.execute(
                "INSERT INTO events ("
                + ",".join(row)
                + ") VALUES ("
                + ",".join("?" for _ in row)
                + ")",
                tuple(row.values()),
            )

        client.portal.call(runtime.event_log._run_locked, corrupt)
        assert control(client, owner=True).status_code == 200
        result = owner_read(client).json()
        assert result["state"] == (
            "schema_unavailable" if kind == "version" else "history_incomplete"
        ), result
        assert result["bindings"] == [] and result["completeness"]["complete"] is False
        assert len(json.dumps(result)) < 8192


def test_schema_unavailable_and_busy_reader_never_claim_empty(tmp_path, monkeypatch):
    from adminme.finance import metadata

    with TestClient(create_application(settings(tmp_path))) as client:
        assert control(client, owner=True).status_code == 200
        original = registry.get
        with monkeypatch.context() as patch:
            patch.setattr(
                registry,
                "get",
                lambda kind, version: (
                    None
                    if kind == "finance.projection_verified"
                    else original(kind, version)
                ),
            )
            result = owner_read(client).json()
            assert (
                result["state"] == "schema_unavailable"
                and not result["completeness"]["complete"]
            )
        runtime = client.app.state.runtime
        session = build_internal_session(
            PRINCIPAL, "principal", runtime.settings.instance.tenant_id
        )

        async def busy():
            import asyncio

            lock = runtime.settings_command_locks.setdefault(
                "finance-metadata:read", asyncio.Lock()
            )
            async with lock:
                return await metadata.inspect_metadata(runtime, session, None, None)

        result = client.portal.call(busy)
        assert result["state"] == "temporarily_unavailable"
        assert result["bindings"] == [] and not result["completeness"]["complete"]
