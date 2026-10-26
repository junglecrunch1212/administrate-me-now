"""Owner-filtered metadata via the existing authenticated native composition."""

from dataclasses import replace
import copy

from fastapi.testclient import TestClient
import pytest

from adminme.events.envelope import EventEnvelope
from adminme.finance.importer import import_publication
from adminme.finance.planning_store import import_projection
from adminme.finance.publication import content_hash
from adminme.runtime.application import create_application
from tests.acceptance.test_finance_disclosure import (
    enabled_settings,
    import_fixture,
    revoke,
)
from tests.acceptance.test_finance_native_facade import _post
from tests.acceptance.test_openclaw_native_seam import _grant_cos, PRINCIPAL
from tests.fixtures.finance_planning import projection

# Exercises shipping availability; kill-switch tests explicitly disable it.
pytestmark = pytest.mark.usefixtures("unreleased_finance_advisor")


def read(client, message="bindings", **kwargs):
    return _post(client, "status", message, include_bindings=True, **kwargs)


def test_empty_is_scoped_complete_and_audited(tmp_path):
    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        _grant_cos(client, "metadata-grant")
        response = read(client)
        assert response.status_code == 200, response.text
        value = response.json()
        assert value["state"] == "verified_empty"
        assert value["completeness"]["complete"] is True
        assert value["completeness"]["through_event_id"] is None
        assert value["bindings"] == []

        async def audits():
            return [
                e
                async for e in client.app.state.runtime.event_log.read_since(
                    types=["finance.advisor_accessed"]
                )
            ]

        assert any(
            e["payload"]["operation"] == "finance.bindings"
            for e in client.portal.call(audits)
        )


def test_populated_accounting_and_dated_heads_restart_and_no_financial_values(tmp_path):
    config = enabled_settings(tmp_path)
    with TestClient(create_application(config)) as client:
        _grant_cos(client, "metadata-populated")
        pub, owner = import_fixture(client)
        dated = projection(pub)
        runtime = client.app.state.runtime
        client.portal.call(import_projection, runtime, owner, dated)
        response = read(client)
        assert response.status_code == 200, response.text
        first = response.json()
        assert first["state"] == "populated", first
        binding = first["bindings"][0]
        assert binding["accounting"]["publication_id"] == pub["publication_id"]
        assert binding["dated"]["projection_id"] == dated["projection_id"]
        assert binding["audience_policy_id"] == pub["audience_policy_id"]
        assert first["completeness"]["scanned_events"] == 2
        for forbidden in (
            "value_minor",
            "metrics",
            "source_url",
            "description",
            "accepted_journal_revision_ids",
        ):
            assert forbidden not in response.text
    with TestClient(create_application(config)) as client:
        second = read(client, "restarted").json()
        assert second["bindings"] == first["bindings"]
        assert second["completeness"] == first["completeness"]


@pytest.mark.parametrize("disabled", ["feature", "model"])
def test_disabled_is_access_unavailable_and_never_reads(
    tmp_path, monkeypatch, disabled
):
    config = enabled_settings(tmp_path)
    config = replace(
        config,
        **(
            {"finance_publications_enabled": False}
            if disabled == "feature"
            else {"finance_model_members": frozenset()}
        ),
    )
    with TestClient(create_application(config)) as client:
        _grant_cos(client, "metadata-disabled")

        async def forbidden(*args):
            raise AssertionError("unauthorized history read")

        monkeypatch.setattr(
            client.app.state.runtime.event_log,
            "read_finance_binding_metadata",
            forbidden,
        )
        response = read(client)
        assert response.status_code == 200
        assert response.json()["state"] == "access_unavailable"
        assert response.json()["completeness"]["complete"] is False


def test_missing_auth_or_grant_rejects(tmp_path):
    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        assert client.post(
            "/api/openclaw/v1/tools/finance-status", json={}
        ).status_code in {401, 422}
        response = read(client)
        assert response.status_code == 403
        assert "bindings" not in response.text


def test_foreign_owner_and_tenant_do_not_affect_watermark_or_empty_receipt(tmp_path):
    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        _grant_cos(client, "metadata-isolation")
        before = read(client, "before").json()["completeness"]
        pub, _ = import_fixture(client)
        runtime = client.app.state.runtime
        # Read the storage primitive for a different exact owner and tenant.
        for tenant, owner in (
            (pub["tenant_id"], "member-unrelated"),
            ("tenant-unrelated", PRINCIPAL),
        ):
            result = client.portal.call(
                runtime.event_log.read_finance_binding_metadata, tenant, owner
            )
            assert result == {"state": "complete", "through_event_id": None, "rows": []}
        assert before["scanned_events"] == 0


def test_history_gap_is_not_empty_or_populated(tmp_path):
    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        _grant_cos(client, "metadata-gap")
        pub, owner = import_fixture(client)
        pub = copy.deepcopy(pub)
        pub.update(
            publication_id="publication-gap", prior_publication_id="unknown-predecessor"
        )
        pub["content_sha256"] = content_hash(pub)
        envelope = EventEnvelope(
            event_at_ms=1,
            tenant_id=owner.tenant_id,
            type="finance.publication_accepted",
            schema_version=1,
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter="accepted_books",
            source_account_id=pub["book_id"],
            owner_scope=f"private:{PRINCIPAL}",
            visibility_scope=f"private:{PRINCIPAL}",
            sensitivity="privileged",
            actor_identity=PRINCIPAL,
            payload={"publication": pub},
        )
        client.portal.call(client.app.state.runtime.event_log.append, envelope)
        result = read(client).json()
        assert result["state"] == "history_incomplete"
        assert result["bindings"] == []
        assert result["completeness"]["complete"] is False


def test_schema_and_scan_limit_never_claim_absence(tmp_path, monkeypatch):
    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        _grant_cos(client, "metadata-limits")
        runtime = client.app.state.runtime
        for index, state in enumerate(("schema_unavailable", "history_incomplete")):

            async def bounded(*args):
                return {
                    "state": state,
                    "through_event_id": "ev-bound",
                    "rows": [],
                    "reason": "history_scan_limit",
                }

            monkeypatch.setattr(
                runtime.event_log, "read_finance_binding_metadata", bounded
            )
            value = read(client, f"limit-{index}").json()
            assert value["state"] == state
            assert value["bindings"] == []
            assert value["completeness"]["complete"] is False


def test_revocation_during_audit_prevents_metadata_disclosure(tmp_path, monkeypatch):
    from adminme.products.openclaw import main

    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        grant = _grant_cos(client, "metadata-revoke")
        pub, _ = import_fixture(client)
        original = main._finance_audit

        async def revoke_after_audit(request, session, provenance, **kwargs):
            result = await original(request, session, provenance, **kwargs)
            await revoke(client.app.state.runtime, grant)
            return result

        monkeypatch.setattr(main, "_finance_audit", revoke_after_audit)
        result = read(client)
        assert result.status_code == 403
        assert pub["book_id"] not in result.text
        assert pub["content_sha256"] not in result.text


def test_fixed_watermark_excludes_successor_appended_after_snapshot(
    tmp_path, monkeypatch
):
    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        _grant_cos(client, "metadata-watermark")
        pub, owner = import_fixture(client)
        runtime = client.app.state.runtime
        original = runtime.event_log.read_finance_binding_metadata

        async def append_after_snapshot(*args):
            result = await original(*args)
            successor = copy.deepcopy(pub)
            successor.update(
                publication_id="publication-next",
                prior_publication_id=pub["publication_id"],
            )
            successor["content_sha256"] = content_hash(successor)
            await import_publication(runtime, owner, successor)
            return result

        monkeypatch.setattr(
            runtime.event_log, "read_finance_binding_metadata", append_after_snapshot
        )
        first = read(client).json()
        assert (
            first["bindings"][0]["accounting"]["publication_id"]
            == pub["publication_id"]
        )
        monkeypatch.setattr(
            runtime.event_log, "read_finance_binding_metadata", original
        )
        second = read(client, "next").json()
        assert (
            second["bindings"][0]["accounting"]["publication_id"] == "publication-next"
        )
        assert (
            second["completeness"]["through_event_id"]
            != first["completeness"]["through_event_id"]
        )


def test_real_history_bound_preserves_log_and_refuses_partial_heads(tmp_path):
    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        _grant_cos(client, "metadata-real-limit")
        pub, owner = import_fixture(client)
        runtime = client.app.state.runtime
        rows = [
            EventEnvelope(
                event_at_ms=1,
                tenant_id=owner.tenant_id,
                type="finance.publication_accepted",
                schema_version=1,
                occurred_at=EventEnvelope.now_utc_iso(),
                source_adapter="accepted_books",
                source_account_id=pub["book_id"],
                owner_scope=f"private:{PRINCIPAL}",
                visibility_scope=f"private:{PRINCIPAL}",
                sensitivity="privileged",
                actor_identity=PRINCIPAL,
                payload={"publication": pub},
            )
            for _ in range(256)
        ]
        client.portal.call(runtime.event_log.append_batch, rows)
        result = read(client).json()
        assert result["state"] == "history_incomplete", result
        assert result["reason"] == "history_scan_limit"
        assert result["bindings"] == []
        assert result["completeness"]["complete"] is False
        assert client.portal.call(runtime.event_log.count) >= 257
