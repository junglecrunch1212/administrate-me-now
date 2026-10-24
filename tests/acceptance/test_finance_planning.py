"""Separate accepted accounting and verified planning through the runtime."""

from copy import deepcopy
from functools import partial
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
                    partial(
                        read,
                        owner,
                        projection_id=value["projection_id"],
                        context_id=context["context_id"],
                        horizon_days=365,
                        report_id="planning_events",
                        offset=offset,
                        limit=100,
                    )
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
        for key, value_ in native["summary"].items():
            if key == "unknowns":
                assert all(
                    member.json()["summary"][key][k] == v for k, v in value_.items()
                )
            else:
                assert member.json()["summary"][key] == value_
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


@pytest.mark.parametrize(
    "fault", ["disabled", "wrong_owner", "wrong_hash", "conflicting_retry"]
)
def test_projection_import_refuses_unauthorized_or_conflicting_boundary(
    tmp_path, fault
):
    from dataclasses import replace

    configured = settings(tmp_path)
    accounting = publication(configured.instance.tenant_id)
    owner = build_internal_session(
        accounting["owner_id"], "principal", accounting["tenant_id"]
    )
    with TestClient(create_application(configured)) as client:
        runtime = client.app.state.runtime
        client.portal.call(import_publication, runtime, owner, accounting)
        value = projection(accounting)
        if fault == "disabled":
            runtime.settings = replace(
                runtime.settings, finance_publications_enabled=False
            )
        elif fault == "wrong_owner":
            value["owner_id"] = "member-other"
        elif fault == "wrong_hash":
            value["accounting_content_sha256"] = "f" * 64
        else:
            client.portal.call(import_projection, runtime, owner, value)
            value["engine_version"] = "conflicting-version"
        seal(value)
        with pytest.raises(PublicationError):
            client.portal.call(import_projection, runtime, owner, value)
        conn = runtime.projections.connection("money")
        head = conn.execute(
            "SELECT projection_id FROM finance_dated_projection_heads"
        ).fetchone()
        assert (head[0] if head else None) == (
            value["projection_id"] if fault == "conflicting_retry" else None
        )
        assert (
            conn.execute(
                "SELECT publication_id FROM finance_publication_heads"
            ).fetchone()[0]
            == accounting["publication_id"]
        )


@pytest.mark.parametrize("source_case", ["ranges", "grouped_scalars"])
def test_each_opening_selection_is_reachable_with_status_selection_and_summary_budget(
    tmp_path,
    source_case,
):
    from tests.fixtures.finance_planning import with_unavailable_openings

    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        _grant_cos(client, "planning-context-budget")
        accounting, owner = import_fixture(client)
        runtime = client.app.state.runtime
        value = with_unavailable_openings(projection(accounting))
        if source_case == "grouped_scalars":
            from tests.fixtures.finance_planning import (
                with_grouped_issues,
                with_conservative_minima,
            )

            value = with_grouped_issues(with_conservative_minima(value))
        assert validate_projection(value) == value and len(value["contexts"]) == 24
        client.portal.call(import_projection, runtime, owner, value)
        for index, context in enumerate(value["contexts"]):
            turn = f"context-budget-{index}"
            status = _post(client, "status", turn, book_id=value["book_id"])
            assert status.status_code == 200 and status.json().get("planning"), (
                status.text
            )
            selector = {
                key: context[key]
                for key in (
                    "measure",
                    "scenario_id",
                    "income_basis",
                    "opening_basis",
                    "plan_case_id",
                    "currency",
                    "timezone",
                    "as_of_date",
                )
            }
            common = {
                "book_id": value["book_id"],
                "publication_id": accounting["publication_id"],
                "projection_id": value["projection_id"],
                **selector,
            }
            selected = _post(
                client, "query", turn, report_id="planning_contexts", **common
            )
            assert (
                selected.status_code == 200
                and len(selected.json().get("contexts", [])) == 1
            ), selected.text
            assert selected.json()["contexts"][0]["context_id"] == context["context_id"]
            summary = _post(
                client,
                "query",
                turn,
                report_id="planning_projection",
                horizon_days=30,
                **common,
            )
            assert summary.status_code == 200 and "summary" in summary.json(), (
                summary.text
            )
            if context["opening_basis"] == "verified_unrestricted":
                assert summary.json()["summary"]["opening"] == [None, None]
                assert summary.json()["summary"]["safety_state"] == "NOT_ESTABLISHED"
                assert summary.json()["summary"]["estimate_state"] == "UNAVAILABLE"

        async def all_views():
            from adminme.finance.budgets import enforce_context

            for context in value["contexts"]:
                for days in (7, 30, 90, 180, 365):
                    restored_summary = {}
                    restored_unknowns = {}
                    for view in (
                        "balances",
                        "signals",
                        "dates",
                        "provenance",
                        "clocks",
                    ):
                        result = await runtime.projections.read(
                            "money",
                            lambda conn: query_projection(
                                conn,
                                owner,
                                book_id=value["book_id"],
                                projection_id=value["projection_id"],
                                context_id=context["context_id"],
                                report_id="planning_projection",
                                horizon_days=days,
                                view=view,
                            ),
                        )
                        assert result["summary"]["conditions"] == next(
                            h["conditions"]
                            for h in context["horizons"]
                            if h["days"] == days
                        )
                        restored_unknowns.update(result["summary"].get("unknowns", {}))
                        restored_summary.update(
                            {
                                k: v
                                for k, v in result["summary"].items()
                                if k != "unknowns"
                            }
                        )
                        if view == "clocks":
                            assert result["source_clocks"] == value["source_clocks"]
                        if view == "provenance":
                            assert result["engine"][2] == value["input_sha256"]
                            assert result["verification"] == {
                                "id": value["verification_id"],
                                "published_at": value["published_at"],
                                "verified_at": value["verified_at"],
                            }
                        result["provider_effect_count"] = 0
                        result["answer_ref"] = answer_reference(result)
                        try:
                            enforce_context(result)
                        except ValueError as exc:
                            from adminme.finance.budgets import json_bytes

                            raise AssertionError(
                                (
                                    context["context_id"],
                                    days,
                                    view,
                                    len(json_bytes(result)),
                                )
                            ) from exc
                        rendered = render_result(result)
                        assert (
                            "non-additive" in rendered
                            and "unsupported_collections" not in rendered
                        )
                        assert (
                            "daily_balances" in rendered
                        )  # Visible unsupported collection.
                    whole = await runtime.projections.read(
                        "money",
                        lambda conn: query_projection(
                            conn,
                            owner,
                            book_id=value["book_id"],
                            context_id=context["context_id"],
                            report_id="planning_projection",
                            horizon_days=days,
                        ),
                    )
                    restored_summary["unknowns"] = restored_unknowns
                    assert restored_summary == whole["summary"]

        client.portal.call(all_views)


@pytest.mark.parametrize(
    "fault",
    [
        "future_context",
        "future_balance",
        "future_provider_sync",
        "engine_role",
        "gap_audience",
        "gap_scope",
        "gap_link",
        "signal_date",
        "null_date_reason",
    ],
)
def test_projection_provenance_dates_and_private_gap_boundaries(fault):
    from datetime import date, timedelta

    value = projection(publication())
    if fault == "future_context":
        for c in value["contexts"]:
            c["as_of_date"] = "2026-03-08"
            for h in c["horizons"]:
                h["start_inclusive"] = "2026-03-09"
                h["end_exclusive"] = (
                    date(2026, 3, 8) + timedelta(days=h["days"] + 1)
                ).isoformat()
                h["minimum_date"]["date"] = "2026-03-09"
                h["first_reserve_breach_date"]["date"] = "2026-03-09"
    elif fault == "future_balance":
        value["sources"][0]["balance_as_of"] = {
            "date": "2026-03-08",
            "unknown_reason": None,
        }
    elif fault == "future_provider_sync":
        value["sources"][0]["provider_sync_at"] = {
            "value": "2026-03-08T00:00:00Z",
            "unknown_reason": None,
        }
    elif fault == "engine_role":
        value["engines"][2]["role"] = "funding"
    elif fault == "gap_audience":
        value["gaps"][0]["audience_member_ids"] = ["member-other"]
    elif fault == "gap_scope":
        value["gaps"][0]["context_ids"] = ["context-other"]
    elif fault == "gap_link":
        value["gaps"][0]["explanation"] = "See https://example.invalid/private-source"
    elif fault == "signal_date":
        value["contexts"][0]["horizons"][0]["minimum_date"]["date"] = "2026-03-07"
    elif fault == "null_date_reason":
        value["contexts"][0]["horizons"][0]["first_negative_date"]["unknown_reason"] = (
            None
        )
    with pytest.raises(PublicationError):
        validate_projection(seal(value))


def test_private_gap_resolution_and_source_hashes_remain_scoped(tmp_path):
    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        _grant_cos(client, "gap-resolution")
        accounting, owner = import_fixture(client)
        runtime = client.app.state.runtime
        value = projection(accounting)
        client.portal.call(import_projection, runtime, owner, value)
        c = value["contexts"][0]
        common = {
            "book_id": value["book_id"],
            "publication_id": accounting["publication_id"],
            "projection_id": value["projection_id"],
            "context_id": c["context_id"],
        }
        gap = _post(
            client,
            "query",
            "gap-detail",
            report_id="planning_gaps",
            reference_id="flow_coverage_incomplete",
            **common,
        )
        assert gap.status_code == 200 and gap.json().get("records"), gap.text
        record = gap.json()["records"][0]
        assert (
            record["required_input"] and record["timing"] == "before_safety_conclusion"
        )
        evidence = _post(
            client,
            "query",
            "evidence-detail",
            report_id="planning_evidence",
            reference_id=record["evidence_ids"][0],
            **common,
        )
        assert evidence.status_code == 200 and evidence.json().get("records"), (
            evidence.text
        )
        source_id = evidence.json()["records"][0]["source_id"]
        source = _post(
            client,
            "query",
            "source-detail",
            report_id="planning_sources",
            reference_id=source_id,
            **common,
        )
        assert source.status_code == 200 and source.json().get("records"), source.text
        assert (
            source.json()["records"][0]["content_sha256"]
            == evidence.json()["records"][0]["content_sha256"]
        )
        assert source.json()["records"][0]["provider_as_of"]["value"] is None
        assert source.json()["records"][0]["provider_sync_at"] == {
            "value": None,
            "unknown_reason": "provider_sync_clock_unknown",
        }
        assert source.json()["records"][0]["balance_as_of"]["date"] is None
        assert "https://" not in gap.text + evidence.text + source.text
        engines = _post(
            client, "query", "engine-detail", report_id="planning_engines", **common
        )
        assert engines.status_code == 200 and {
            e["role"] for e in engines.json().get("engines", [])
        } == {"dated", "funding", "income"}, engines.text
        other = build_internal_session(
            "member-other", "principal", accounting["tenant_id"]
        )

        async def foreign():
            return await runtime.projections.read(
                "money",
                lambda conn: query_projection(
                    conn,
                    other,
                    book_id=value["book_id"],
                    context_id=c["context_id"],
                    report_id="planning_evidence",
                    reference_id="evidence-synthetic",
                ),
            )

        assert client.portal.call(foreign)["state"] == "missing_evidence"


def test_projection_filters_cannot_be_ignored_on_other_reports(tmp_path):
    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        _grant_cos(client, "planning-filter-boundary")
        accounting, owner = import_fixture(client)
        value = projection(accounting)
        client.portal.call(import_projection, client.app.state.runtime, owner, value)
        invalid = [
            {"report_id": "owner_choices", "reference_id": "evidence-synthetic"},
            {"report_id": "owner_choices", "projection_view": "provenance"},
            {"report_id": "planning_contexts", "reference_id": "evidence-synthetic"},
            {
                "report_id": "planning_projection",
                "context_id": value["contexts"][0]["context_id"],
                "measure": "BANK_CASH_SUBSET",
            },
        ]
        for index, selection in enumerate(invalid):
            response = _post(
                client,
                "query",
                f"invalid-filter-{index}",
                book_id=value["book_id"],
                publication_id=accounting["publication_id"],
                **selection,
            )
            assert response.status_code == 200, response.text
            assert response.json()["state"] == "missing_evidence", response.text
            assert (
                not {"contexts", "summary", "records", "choices"}
                & response.json().keys()
            )


def test_provider_sync_clock_is_preserved_without_relabeling_other_clocks(tmp_path):
    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        _grant_cos(client, "source-sync-clock")
        accounting, owner = import_fixture(client)
        value = projection(accounting)
        sync = {"value": "2026-03-07T10:12:00-05:00", "unknown_reason": None}
        value["sources"][0]["provider_sync_at"] = sync
        value = seal(value)
        client.portal.call(import_projection, client.app.state.runtime, owner, value)
        response = _post(
            client,
            "query",
            "source-sync-clock",
            book_id=value["book_id"],
            publication_id=accounting["publication_id"],
            projection_id=value["projection_id"],
            context_id=value["contexts"][0]["context_id"],
            report_id="planning_sources",
            reference_id="source-synthetic",
        )
        assert response.status_code == 200 and response.json().get("records"), (
            response.text
        )
        source = response.json()["records"][0]
        assert source["provider_sync_at"] == sync
        assert source["provider_as_of"]["value"] is None
        assert source["balance_as_of"]["date"] is None
        assert source["observed_at"]["value"] is None
        assert source["retrieved_at"]["value"] == "2026-03-07T17:00:00Z"


@pytest.mark.parametrize(
    "fault",
    [
        "duplicate_inventory",
        "duplicate_reference",
        "missing_reference",
        "inventory_digest",
        "context_digest",
        "unused_inventory",
        "mixed_layout",
        "bank_alias_changed",
        "undated_reason",
        "partial_date",
    ],
)
def test_shared_inventory_rejects_ambiguous_or_incomplete_context_coverage(fault):
    from tests.fixtures.finance_planning import shared_projection

    value = shared_projection(publication())
    if fault == "duplicate_inventory":
        value["shared_event_inventory"].append(
            deepcopy(value["shared_event_inventory"][0])
        )
    elif fault == "duplicate_reference":
        value["detail_manifest"][0]["record_ids"].append(
            value["detail_manifest"][0]["record_ids"][0]
        )
    elif fault == "missing_reference":
        value["detail_manifest"][0]["record_ids"][0] = "absent-source-event"
    elif fault == "inventory_digest":
        value["shared_inventory_sha256"] = "f" * 64
    elif fault == "context_digest":
        value["detail_manifest"][0]["rows_sha256"] = "f" * 64
    elif fault == "unused_inventory":
        extra = deepcopy(value["shared_event_inventory"][0])
        extra["record_id"] = "unreferenced-record"
        extra["event_id"] = "unreferenced-source-event"
        value["shared_event_inventory"].append(extra)
        seal(value)
    elif fault == "mixed_layout":
        value["detail_events"] = [
            {
                **value["shared_event_inventory"][0],
                "context_id": value["contexts"][0]["context_id"],
            }
        ]
    elif fault == "bank_alias_changed":
        value["detail_manifest"][1]["record_ids"].reverse()
        seal(value)
    elif fault == "undated_reason":
        value["shared_event_inventory"][0]["date_unknown_reason"] = None
        seal(value)
    elif fault == "partial_date":
        value["shared_event_inventory"][0]["latest_date"] = "2026-03-08"
        seal(value)
    value["content_sha256"] = content_hash(value)
    with pytest.raises(PublicationError):
        validate_projection(value)


def test_shared_inventory_full_pagination_replay_and_owner_isolation(tmp_path):
    import hashlib
    from adminme.finance.publication import canonical_bytes
    from tests.fixtures.finance_planning import (
        shared_projection,
        with_grouped_issues,
        with_conservative_minima,
    )

    configured = enabled_settings(tmp_path)
    with TestClient(create_application(configured)) as client:
        _grant_cos(client, "shared-inventory")
        accounting, owner = import_fixture(client)
        value = with_grouped_issues(
            with_conservative_minima(shared_projection(accounting))
        )
        assert len(value["shared_event_inventory"]) == 3307
        assert len({r["event_id"] for r in value["shared_event_inventory"]}) == 3215
        assert max(m["row_count"] for m in value["detail_manifest"]) == 2818
        assert sum(m["row_count"] for m in value["detail_manifest"]) == 38382
        source_groups = {
            tuple(manifest["record_ids"]): manifest["row_count"]
            for manifest in value["detail_manifest"]
        }
        assert len(source_groups) == 18 and sum(source_groups.values()) == 35988
        assert len(canonical_bytes(value)) < 8 * 1024 * 1024
        runtime = client.app.state.runtime
        client.portal.call(import_projection, runtime, owner, value)

        async def all_contexts(session):
            total = 0
            for manifest in value["detail_manifest"]:
                offset = 0
                restored = []
                while offset is not None:
                    answer = await runtime.projections.read(
                        "money",
                        lambda conn: query_projection(
                            conn,
                            session,
                            book_id=value["book_id"],
                            projection_id=value["projection_id"],
                            context_id=manifest["context_id"],
                            report_id="planning_events",
                            horizon_days=365,
                            offset=offset,
                            limit=100,
                        ),
                    )
                    assert "record_ids" not in answer["manifest"]
                    restored.extend(answer["events"])
                    offset = answer["next_offset"]
                inventory = {
                    row["record_id"]: row for row in value["shared_event_inventory"]
                }
                assert [row["event_id"] for row in restored] == [
                    inventory[record_id]["event_id"]
                    for record_id in manifest["record_ids"]
                ]
                assert all(
                    row["context_id"] == manifest["context_id"] for row in restored
                )
                assert (
                    hashlib.sha256(canonical_bytes(restored)).hexdigest()
                    == manifest["rows_sha256"]
                )
                total += len(restored)
            return total

        assert client.portal.call(all_contexts, owner) == 38382
        conn = runtime.projections.connection("money")
        assert (
            conn.execute("SELECT COUNT(*) FROM finance_dated_shared_events").fetchone()[
                0
            ]
            == 3307
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM finance_dated_event_bindings"
            ).fetchone()[0]
            == 38382
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM finance_dated_projection_events"
            ).fetchone()[0]
            == 0
        )
        metadata = json.loads(
            conn.execute(
                "SELECT metadata_json FROM finance_dated_projections"
            ).fetchone()[0]
        )
        assert "shared_event_inventory" not in metadata
        assert all("record_ids" not in m for m in metadata["detail_manifest"])
        for index in (0, 6, 12):
            response = _post(
                client,
                "query",
                f"shared-page-{index}",
                book_id=value["book_id"],
                publication_id=accounting["publication_id"],
                projection_id=value["projection_id"],
                context_id=value["contexts"][index]["context_id"],
                report_id="planning_events",
                horizon_days=365,
            )
            assert (
                response.status_code == 200
                and len(response.json().get("events", [])) == 1
            ), response.text
        other = build_internal_session(
            "member-other", "principal", accounting["tenant_id"]
        )
        assert (
            query_projection(
                conn,
                other,
                book_id=value["book_id"],
                context_id=value["contexts"][0]["context_id"],
                report_id="planning_events",
            )["state"]
            == "missing_evidence"
        )
        client.portal.call(runtime.projections.rebuild, "money")
        assert client.portal.call(all_contexts, owner) == 38382
    with TestClient(create_application(configured)) as client:
        assert client.portal.call(
            import_projection, client.app.state.runtime, owner, value
        )["idempotent"]
        conn = client.app.state.runtime.projections.connection("money")
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM finance_dated_event_bindings"
            ).fetchone()[0]
            == 38382
        )


def test_conservative_minimum_and_absent_zero_crossing_remain_source_typed(tmp_path):
    from tests.fixtures.finance_planning import with_conservative_minima

    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        _grant_cos(client, "scalar-minimum")
        accounting, owner = import_fixture(client)
        value = with_conservative_minima(projection(accounting))
        client.portal.call(import_projection, client.app.state.runtime, owner, value)
        common = {
            "book_id": value["book_id"],
            "publication_id": accounting["publication_id"],
            "projection_id": value["projection_id"],
            "context_id": value["contexts"][0]["context_id"],
            "report_id": "planning_projection",
            "horizon_days": 30,
        }
        answer = _post(client, "query", "scalar-minimum", **common)
        assert answer.status_code == 200 and "summary" in answer.json(), answer.text
        assert answer.json()["summary"]["minimum"] == {
            "kind": "CONSERVATIVE_SOURCE_SCALAR",
            "value_minor": 100,
            "unknown_reason": None,
        }
        assert "source scalar, not a lower/upper range" in render_result(answer.json())
        signals = _post(
            client, "query", "scalar-dates", projection_view="dates", **common
        )
        assert signals.status_code == 200 and "summary" in signals.json(), signals.text
        assert signals.json()["summary"]["first_zero_crossing_date"] == {
            "date": None,
            "unknown_reason": "source_does_not_provide_zero_crossing",
        }


def test_shared_record_keys_preserve_original_event_ids_across_source_variants():
    from tests.fixtures.finance_planning import shared_projection

    value = shared_projection(publication())
    manifest = value["detail_manifest"][
        6
    ]  # A funding view, independent of bank aliases.
    original_id = manifest["record_ids"][100]
    original = next(
        row
        for row in value["shared_event_inventory"]
        if row["record_id"] == original_id
    )
    variant = deepcopy(original)
    variant["record_id"] = "distinct-source-variant"
    variant["amount"] = bounds(2)
    variant["bank_effect"] = bounds(2)
    value["shared_event_inventory"].append(variant)
    manifest["record_ids"][100] = variant["record_id"]
    assert variant["event_id"] == original["event_id"]
    assert validate_projection(seal(value)) == value


@pytest.mark.parametrize(
    "fault",
    [
        "required_group_omitted",
        "required_flag_changed",
        "issue_omitted",
        "issue_identity",
        "issue_audience",
        "issue_membership",
        "group_scope",
    ],
)
def test_original_issue_flags_and_memberships_cannot_disappear_in_groups(fault):
    from tests.fixtures.finance_planning import with_grouped_issues

    value = with_grouped_issues(projection(publication()))
    if fault == "required_group_omitted":
        value["contexts"][0]["horizons"][0]["conditions"].remove("source-issues-0")
    elif fault == "required_flag_changed":
        value["gap_issues"][0]["required"] = False
    elif fault == "issue_omitted":
        value["gap_issues"].pop()
    elif fault == "issue_identity":
        value["gap_issues"][1]["issue_id"] = value["gap_issues"][0]["issue_id"]
    elif fault == "issue_audience":
        value["gap_issues"][0]["audience_member_ids"] = ["member-other"]
    elif fault == "issue_membership":
        value["gap_issues"][0]["memberships"][0]["context_id"] = "context-other"
    elif fault == "group_scope":
        value["gaps"][-1]["horizon_days"] = [30]
    if fault not in {"required_flag_changed", "issue_omitted"}:
        seal(value)
    value["content_sha256"] = content_hash(value)
    with pytest.raises(PublicationError):
        validate_projection(value)


def test_original_issues_are_fully_recoverable_with_required_flags_and_exact_horizons(
    tmp_path,
):
    from tests.fixtures.finance_planning import with_grouped_issues

    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        _grant_cos(client, "original-issue-groups")
        accounting, owner = import_fixture(client)
        value = with_grouped_issues(projection(accounting))
        assert value["issue_manifest"]["row_count"] == 141
        assert value["issue_manifest"]["required_count"] == 49
        assert len(value["gaps"]) <= 128
        assert all(
            len(h["conditions"]) <= 16 for c in value["contexts"] for h in c["horizons"]
        )
        runtime = client.app.state.runtime
        client.portal.call(import_projection, runtime, owner, value)
        context_id = value["contexts"][0]["context_id"]

        async def recover():
            recovered = {}
            for days in (7, 90):
                selected = []
                offset = 0
                while offset is not None:
                    response = await runtime.projections.read(
                        "money",
                        lambda conn: query_projection(
                            conn,
                            owner,
                            book_id=value["book_id"],
                            context_id=context_id,
                            report_id="planning_issues",
                            horizon_days=days,
                            limit=100,
                            offset=offset,
                        ),
                    )
                    selected.extend(response["records"])
                    offset = response["next_offset"]
                assert sum(issue["required"] for issue in selected) == 46
                assert len(selected) == 138
                assert all(days in issue["horizon_days"] for issue in selected)
                recovered.update(
                    {issue["issue_id"]: issue["required"] for issue in selected}
                )
                groups = await runtime.projections.read(
                    "money",
                    lambda conn: query_projection(
                        conn,
                        owner,
                        book_id=value["book_id"],
                        context_id=context_id,
                        report_id="planning_gaps",
                        horizon_days=days,
                        limit=100,
                    ),
                )
                assert (
                    sum(
                        g.get("issue_counts", {}).get("required", 0)
                        for g in groups["records"]
                    )
                    == 46
                )
            return recovered

        recovered = client.portal.call(recover)
        assert recovered == {
            issue["issue_id"]: issue["required"] for issue in value["gap_issues"]
        }
        common = {
            "book_id": value["book_id"],
            "publication_id": accounting["publication_id"],
            "projection_id": value["projection_id"],
            "context_id": context_id,
        }
        group = _post(
            client,
            "query",
            "group-native",
            report_id="planning_gaps",
            reference_id="source-issues-0",
            **common,
        )
        assert group.status_code == 200 and group.json().get("records"), group.text
        assert group.json()["records"][0]["issue_counts"] == {
            "total": 16,
            "required": 16,
        }
        issue = _post(
            client,
            "query",
            "group-native",
            report_id="planning_issues",
            gap_group_id="source-issues-0",
            reference_id="source-issue-000",
            **common,
        )
        assert issue.status_code == 200 and issue.json().get("records"), issue.text
        assert issue.json()["records"][0]["required"] is True
        assert issue.json()["records"][0]["issue_id"] == "source-issue-000"


def test_issue_groups_do_not_invent_context_horizon_cross_products(tmp_path):
    from tests.fixtures.finance_planning import with_grouped_issues

    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        accounting, owner = import_fixture(client)
        value = with_grouped_issues(projection(accounting))
        left, right = [c["context_id"] for c in value["contexts"][6:8]]
        group = next(g for g in value["gaps"] if g["gap_id"] == "source-issues-8")
        group["context_ids"] = [left, right]
        group["horizon_days"] = [7, 30]
        children = [i for i in value["gap_issues"] if i["gap_id"] == group["gap_id"]][
            :2
        ]
        children[0]["memberships"] = [{"context_id": left, "horizon_days": [7]}]
        children[1]["memberships"] = [{"context_id": right, "horizon_days": [30]}]
        value["gap_issues"] = [
            i for i in value["gap_issues"] if i["gap_id"] != group["gap_id"]
        ] + children
        seal(value)
        runtime = client.app.state.runtime
        client.portal.call(import_projection, runtime, owner, value)

        async def check_memberships():
            for context_id, days, expected in (
                (left, 7, [children[0]["issue_id"]]),
                (left, 30, []),
                (right, 7, []),
                (right, 30, [children[1]["issue_id"]]),
            ):
                issues = await runtime.projections.read(
                    "money",
                    lambda conn: query_projection(
                        conn,
                        owner,
                        book_id=value["book_id"],
                        context_id=context_id,
                        report_id="planning_issues",
                        gap_group_id=group["gap_id"],
                        horizon_days=days,
                        limit=100,
                    ),
                )
                assert [row["issue_id"] for row in issues["records"]] == expected
                groups = await runtime.projections.read(
                    "money",
                    lambda conn: query_projection(
                        conn,
                        owner,
                        book_id=value["book_id"],
                        context_id=context_id,
                        report_id="planning_gaps",
                        reference_id=group["gap_id"],
                        horizon_days=days,
                    ),
                )
                assert bool(groups["records"]) == bool(expected)
                if expected:
                    assert groups["records"][0]["issue_counts"] == {
                        "total": 1,
                        "required": 0,
                    }

        client.portal.call(check_memberships)
