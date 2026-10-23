"""Separate accepted accounting and verified planning through the runtime."""

from copy import deepcopy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from adminme.finance.answers import answer_reference, render_result
from adminme.finance.dated_projection import (
    FUNDING,
    validate_projection,
    schema_hash,
)
from adminme.finance.disclosure import check_disclosure, FinanceDisclosureError
from adminme.finance.importer import import_publication
from adminme.finance.planning_store import import_projection, query_projection
from adminme.finance.publication import PublicationError, content_hash
from adminme.lib.session import build_internal_session
from adminme.runtime.application import create_application
from tests.acceptance.test_finance_publications import publication, settings
from tests.acceptance.test_finance_disclosure import (
    enabled_settings,
    import_fixture,
    revoke,
)
from tests.acceptance.test_finance_native_facade import _post
from tests.acceptance.test_openclaw_native_seam import (
    PRINCIPAL,
    DEMO_HEADERS,
    _grant_cos,
)
from tests.fixtures.finance_planning import projection, seal, bounds


def test_contract_and_twelve_declared_contexts_preserve_both_measures_and_horizons():
    value = projection(publication(), 3215)
    assert validate_projection(value) == value
    assert sum(m["row_count"] for m in value["detail_manifest"]) == 3215
    assert len(value["contexts"]) == 12
    assert (
        schema_hash()
        == (
            Path(__file__).resolve().parents[2]
            / "adminme/finance/contracts/household-books.dated-projection.v1.sha256"
        )
        .read_text()
        .strip()
    )
    for c in value["contexts"]:
        assert {h["days"] for h in c["horizons"]} == {7, 30, 90, 180, 365}
        assert c["horizons"][0]["start_inclusive"] == "2026-03-08"
        assert c["horizons"][0]["end_exclusive"] == "2026-03-15"
        expected = (
            800
            if c["measure"] == FUNDING
            and c["income_basis"] == "PLAN_MODELED_NET_FUNDING_NOT_PAYROLL"
            else 500
        )
        assert c["horizons"][1]["closing"] == bounds(expected, expected + 1)
    assert value["source_clocks"]["provider_as_of"]["value"] is None
    assert value["source_clocks"]["retrieved_at"]["value"] is not None
    assert value["refresh_state"] == "OFF"


@pytest.mark.parametrize(
    "fault",
    [
        "range",
        "reason",
        "chosen",
        "bank_income",
        "hypothetical_safety",
        "funding_bank",
        "missing_threshold",
        "start",
        "dst_end",
        "manifest",
        "duplicate_recognition",
        "card_debit",
        "settlement",
        "transfer",
        "missing_context",
    ],
)
def test_semantic_conflations_and_coverage_loss_reject(fault):
    value = projection(publication(), 24)
    c = value["contexts"][0]
    h = c["horizons"][0]
    row = value["detail_events"][0]
    if fault == "range":
        h["closing"] = bounds(200, 100)
    elif fault == "reason":
        c["encumbered"]["lower_unknown_reason"] = None
    elif fault == "chosen":
        value["selected_plan_case_id"] = "other-case"
    elif fault == "bank_income":
        value["contexts"][1]["horizons"][0]["closing"] = bounds(800)
    elif fault == "hypothetical_safety":
        h["safety_state"] = "SOURCE_VERIFIED"
    elif fault == "funding_bank":
        value["contexts"][6]["horizons"][0]["negative_bank_cash"] = {
            "value": False,
            "unknown_reason": None,
        }
    elif fault == "missing_threshold":
        h["checking_shortage"] = {"value": False, "unknown_reason": None}
    elif fault == "start":
        h["start_inclusive"] = "2026-03-07"
    elif fault == "dst_end":
        h["end_exclusive"] = "2026-03-14"
    elif fault == "duplicate_recognition":
        value["detail_events"][1]["recognition_id"] = row["recognition_id"]
    elif fault == "card_debit":
        row["kind"] = "card_purchase"
    elif fault == "settlement":
        row["kind"] = "card_settlement"
        row["direction"] = "outflow"
    elif fault == "transfer":
        row["kind"] = "internal_transfer"
        row["direction"] = "neutral"
    elif fault == "missing_context":
        row["context_id"] = "undeclared"
    seal(value)
    if fault == "manifest":
        value["detail_manifest"][0]["row_count"] -= 1
        value["content_sha256"] = content_hash(value)
    with pytest.raises(PublicationError):
        validate_projection(value)


def test_selected_case_null_endpoint_and_partial_cent_outputs_round_trip_without_math():
    value = projection(publication())
    value["selected_plan_case_id"] = "case-maintain"
    for c in value["contexts"]:
        if c["scenario_id"] == "chosen":
            c["plan_case_id"] = "case-maintain"
        c["horizons"][1]["closing"] = {
            "lower_minor": -1,
            "upper_minor": None,
            "lower_unknown_reason": None,
            "upper_unknown_reason": "partial_month_unknown",
        }
    assert validate_projection(seal(value)) == value
    assert value["contexts"][0]["horizons"][1]["closing"]["lower_minor"] == -1


def test_card_settlement_transfer_and_provision_recognition_are_separate():
    value = projection(publication(), 48)
    for index, row in enumerate(value["detail_events"]):
        kind = ("card_purchase", "card_settlement", "internal_transfer", "provision")[
            index % 4
        ]
        row["kind"] = kind
        row["amount"] = bounds(100)
        row["bank_effect"] = bounds(-100 if kind == "card_settlement" else 0)
        row["debt_effect"] = bounds(
            -100 if kind == "card_settlement" else 100 if kind == "card_purchase" else 0
        )
        row["direction"] = "neutral" if kind == "internal_transfer" else "outflow"
    assert validate_projection(seal(value)) == value
    bill = deepcopy(value["detail_events"][3])
    bill["event_id"] = "event-bill"
    bill["kind"] = "bill"
    value["detail_events"].append(bill)
    with pytest.raises(PublicationError, match="duplicate_recognition"):
        validate_projection(seal(value))


def test_atomic_import_full_pagination_replay_and_accounting_hold_unchanged(tmp_path):
    configured = settings(tmp_path)
    accounting = publication(configured.instance.tenant_id)
    original = deepcopy(accounting)
    owner = build_internal_session(
        accounting["owner_id"], "principal", accounting["tenant_id"]
    )
    other = build_internal_session("member-other", "principal", accounting["tenant_id"])
    value = projection(accounting, 3215)
    app = create_application(configured)
    with TestClient(app) as client:
        runtime = app.state.runtime
        client.portal.call(import_publication, runtime, owner, accounting)
        first = client.portal.call(import_projection, runtime, owner, value)
        assert client.portal.call(import_projection, runtime, owner, value)[
            "idempotent"
        ]

        async def read(session, **kwargs):
            return await runtime.projections.read(
                "money",
                lambda conn: query_projection(
                    conn, session, book_id=value["book_id"], **kwargs
                ),
            )

        assert client.portal.call(read, other)["state"] == "missing_evidence"
        restored = []
        for context in value["contexts"]:
            offset = 0
            while offset is not None:
                result = client.portal.call(
                    read,
                    owner,
                    projection_id=value["projection_id"],
                    context_id=context["context_id"],
                    horizon_days=365,
                    report_id="planning_events",
                    offset=offset,
                    limit=100,
                )
                restored.extend(result["events"])
                offset = result["next_offset"]
        assert restored == value["detail_events"] and len(restored) == 3215
        invalid = deepcopy(value)
        invalid["projection_id"] = "projection-invalid"
        invalid["prior_projection_id"] = value["projection_id"]
        invalid["accounting_revision_ids"] = ["revision-pending"]
        seal(invalid)
        with pytest.raises(PublicationError, match="unaccepted_accounting"):
            client.portal.call(import_projection, runtime, owner, invalid)
        assert accounting == original
        # Rebuild from immutable events; neither forecast import nor replay can
        # clear the separate accounting publication's held revision list.
        client.portal.call(runtime.projections.rebuild, "money")
        assert (
            client.portal.call(read, owner)["projection"]["id"]
            == value["projection_id"]
        )
        assert first["provider_effect_count"] == 0
    with TestClient(create_application(configured)) as client:
        assert client.portal.call(
            import_projection, client.app.state.runtime, owner, value
        )["idempotent"]
        conn = client.app.state.runtime.projections.connection("money")
        raw = json.loads(
            conn.execute(
                "SELECT publication_json FROM finance_publications"
            ).fetchone()[0]
        )
        assert raw == original


def test_native_and_member_cards_match_and_new_projection_invalidates_receipt(tmp_path):
    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        _grant_cos(client, "planning-read")
        accounting, owner = import_fixture(client)
        value = projection(accounting)
        runtime = client.app.state.runtime
        client.portal.call(import_projection, runtime, owner, value)
        context = value["contexts"][0]["context_id"]
        response = _post(
            client,
            "query",
            "planning-card",
            book_id=value["book_id"],
            publication_id=accounting["publication_id"],
            report_id="planning_projection",
            projection_id=value["projection_id"],
            context_id=context,
            horizon_days=30,
        )
        assert response.status_code == 200, response.text
        native = response.json()
        assert "summary" in native, native
        member = client.get(
            "/api/member/v1/finance/planning",
            headers=DEMO_HEADERS,
            params={
                "book_id": value["book_id"],
                "projection_id": value["projection_id"],
                "context_id": context,
                "report_id": "planning_projection",
                "horizon_days": 30,
            },
        )
        assert member.status_code == 200, member.text
        assert member.json()["summary"] == native["summary"]
        assert native["context"] == member.json()["context"]
        rendered = render_result(native)
        assert (
            "non-additive" in rendered
            and "INCOMPLETE" in rendered
            and "not available surplus" in rendered
        )
        assert answer_reference(native) == native["answer_ref"]
        successor = deepcopy(value)
        successor["projection_id"] = "projection-synthetic-2"
        successor["prior_projection_id"] = value["projection_id"]
        client.portal.call(import_projection, runtime, owner, seal(successor))
        _, key = runtime.member_chat_binding(PRINCIPAL)

        async def check():
            await check_disclosure(
                runtime, owner, key, run_id="planning-card", require_run=True
            )

        with pytest.raises(FinanceDisclosureError, match="projection_changed"):
            client.portal.call(check)


def test_planning_retrieval_rechecks_revoked_actor_and_human_identity(
    tmp_path, monkeypatch
):
    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        grant = _grant_cos(client, "planning-revocation")
        accounting, owner = import_fixture(client)
        runtime = client.app.state.runtime
        value = projection(accounting)
        client.portal.call(import_projection, runtime, owner, value)
        original = runtime.projections.read
        changed = False

        async def read_then_revoke(name, query):
            nonlocal changed
            answer = await original(name, query)
            if name == "money" and not changed:
                changed = True
                await revoke(runtime, grant)
            return answer

        monkeypatch.setattr(runtime.projections, "read", read_then_revoke)
        response = _post(
            client,
            "query",
            "planning-revoked",
            book_id=value["book_id"],
            publication_id=accounting["publication_id"],
            report_id="planning_contexts",
        )
        assert response.status_code == 403 and "contexts" not in response.text


def test_member_identity_removed_after_planning_read_cannot_receive_card(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        accounting, owner = import_fixture(client)
        runtime = client.app.state.runtime
        value = projection(accounting)
        client.portal.call(import_projection, runtime, owner, value)
        original = runtime.projections.read

        async def read_then_unmap(name, query):
            result = await original(name, query)
            if name == "money":
                runtime.settings = replace(runtime.settings, member_identities={})
            return result

        monkeypatch.setattr(runtime.projections, "read", read_then_unmap)
        response = client.get(
            "/api/member/v1/finance/planning",
            headers=DEMO_HEADERS,
            params={"book_id": value["book_id"]},
        )
        assert response.status_code == 401
        assert "contexts" not in response.text
