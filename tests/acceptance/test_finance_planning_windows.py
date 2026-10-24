"""Preserve uncertain source windows; horizon overlap is not occurrence proof."""

from copy import deepcopy
from datetime import date, timedelta
from functools import partial

import pytest
from fastapi.testclient import TestClient

from adminme.finance.answers import render_result
from adminme.finance.dated_projection import validate_projection
from adminme.finance.planning_store import import_projection, query_projection
from adminme.finance.publication import PublicationError
from adminme.lib.session import build_internal_session
from adminme.runtime.application import create_application
from tests.acceptance.test_finance_disclosure import enabled_settings, import_fixture
from tests.acceptance.test_finance_native_facade import _post
from tests.acceptance.test_finance_publications import publication
from tests.acceptance.test_openclaw_native_seam import DEMO_HEADERS, _grant_cos
from tests.fixtures.finance_planning import projection, seal, with_boundary_windows


def shared_layout(value):
    """Small equivalent manifest fixture; original IDs and order are unchanged."""
    value = deepcopy(value)
    value["shared_event_inventory"] = []
    for index, row in enumerate(value["detail_events"]):
        record_id = f"record-{index}"
        value["shared_event_inventory"].append(
            {
                "record_id": record_id,
                **{k: v for k, v in row.items() if k != "context_id"},
            }
        )
        manifest = next(
            m for m in value["detail_manifest"] if m["context_id"] == row["context_id"]
        )
        manifest.setdefault("record_ids", []).append(record_id)
    value["detail_events"] = []
    return seal(value)


@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize(
    "first,last,valid",
    [
        (0, 0, True),  # inclusive first day
        (-2, 0, True),  # touches inclusive start from outside
        (364, 364, True),  # last included day
        (364, 365, True),  # possible occurrence at excluded end is preserved
        (364, 370, True),  # extends beyond the largest horizon
        (-2, 370, True),  # uncertainty spans both boundaries
        (-2, -1, False),  # wholly before
        (365, 365, False),  # excluded end only
        (365, 370, False),  # wholly after
        (2, 1, False),  # reversed source window
    ],
)
def test_original_inclusive_windows_overlap_half_open_context(
    first, last, valid, shared
):
    value = projection(publication(), 24)
    contexts_before = deepcopy(value["contexts"])
    start = date.fromisoformat(value["contexts"][0]["as_of_date"]) + timedelta(days=1)
    for row in value["detail_events"]:
        row["earliest_date"] = (start + timedelta(days=first)).isoformat()
        row["latest_date"] = (start + timedelta(days=last)).isoformat()
    seal(value)
    if shared:
        value = shared_layout(value)
    if valid:
        original = deepcopy(value)
        assert validate_projection(value) == original
        assert value["contexts"] == contexts_before  # no recomputed forecast totals
    else:
        with pytest.raises(PublicationError, match="projection_detail_date_invalid"):
            validate_projection(value)


@pytest.mark.parametrize("shared", [False, True])
def test_overlap_pages_preserve_source_bounds_flags_and_scope_through_replay(
    tmp_path, shared
):
    configured = enabled_settings(tmp_path)
    with TestClient(create_application(configured)) as client:
        _grant_cos(client, "window-overlap")
        accounting, owner = import_fixture(client)
        value = with_boundary_windows(projection(accounting, 24))
        expanded = []
        for context in value["contexts"]:
            rows = [
                r
                for r in value["detail_events"]
                if r["context_id"] == context["context_id"]
            ]
            expanded.extend(rows)
            start = date.fromisoformat(context["as_of_date"]) + timedelta(days=1)
            for label, first, last, included in (
                ("seven-overlap", 6, 7, True),
                ("after-seven", 7, 7, False),
            ):
                row = deepcopy(rows[0])
                row.update(
                    event_id=f"event-{label}",
                    recognition_id=f"recognition-{label}",
                    earliest_date=(start + timedelta(days=first)).isoformat(),
                    latest_date=(start + timedelta(days=last)).isoformat(),
                    included=included,
                )
                expanded.append(row)
        value["detail_events"] = expanded
        seal(value)
        originals = deepcopy(value["detail_events"])
        if shared:
            value = shared_layout(value)
        runtime = client.app.state.runtime
        client.portal.call(import_projection, runtime, owner, value)
        other = build_internal_session("member-other", "principal", owner.tenant_id)

        async def read(session, **kwargs):
            return await runtime.projections.read(
                "money",
                lambda conn: query_projection(
                    conn, session, book_id=value["book_id"], **kwargs
                ),
            )

        assert client.portal.call(read, other)["state"] == "missing_evidence"
        for replay in (False, True):
            if replay:
                client.portal.call(runtime.projections.rebuild, "money")
            for context in value["contexts"]:
                expected = [
                    r for r in originals if r["context_id"] == context["context_id"]
                ]
                for days in (7, 30, 90, 180, 365):
                    rows = []
                    offset = 0
                    while offset is not None:
                        answer = client.portal.call(
                            partial(
                                read,
                                owner,
                                context_id=context["context_id"],
                                report_id="planning_events",
                                horizon_days=days,
                                limit=1,
                                offset=offset,
                            )
                        )
                        assert len(answer["events"]) <= 1
                        rows.extend(answer["events"])
                        offset = answer["next_offset"]
                    selected = expected if days == 365 else [expected[0], expected[2]]
                    if days not in (7, 365):
                        selected.append(expected[3])
                    assert rows == selected
                    assert "do not prove occurrence" in render_result(answer)

        context = value["contexts"][0]
        native = _post(
            client,
            "query",
            "window-native",
            book_id=value["book_id"],
            publication_id=accounting["publication_id"],
            projection_id=value["projection_id"],
            context_id=context["context_id"],
            report_id="planning_events",
            horizon_days=365,
            offset=1,
        )
        assert native.status_code == 200 and native.json().get("events"), native.text
        assert native.json()["events"] == [originals[1]]
        assert "Producer totals are unchanged" in render_result(native.json())
        member = client.get(
            "/api/member/v1/finance/planning",
            headers=DEMO_HEADERS,
            params={
                "book_id": value["book_id"],
                "context_id": context["context_id"],
                "report_id": "planning_events",
                "horizon_days": 365,
            },
        )
        assert member.status_code == 200 and member.json()["events"] == originals[:4]

    with TestClient(create_application(configured)) as restarted:
        restored = restarted.get(
            "/api/member/v1/finance/planning",
            headers=DEMO_HEADERS,
            params={
                "book_id": value["book_id"],
                "context_id": context["context_id"],
                "report_id": "planning_events",
                "horizon_days": 365,
            },
        )
        assert (
            restored.status_code == 200 and restored.json()["events"] == originals[:4]
        )
