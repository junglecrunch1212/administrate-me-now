"""Two authenticated readers, one canonical Book, no general delegation grant.

Synthetic hosted acceptance only. The shipping release ceiling is used without override; live data and activation
remain outside this suite.
"""

from dataclasses import replace
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from adminme.finance.disclosure import check_disclosure, FinanceDisclosureError
from adminme.lib.session import build_internal_session
from adminme.member_chat import MemberChatChunk
from adminme.openclaw_topology import member_agent_id
from adminme.runtime.application import create_application
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
from tests.acceptance.test_finance_activation import (
    CONTROL,
    SURFACE,
    OTHER_HEADERS,
    configured,
    fixture,
    packet_for,
    approve,
    upload,
    events,
)
from tests.acceptance.test_finance_native_facade import _finance_provenance
from tests.acceptance.test_openclaw_native_seam import (
    PRINCIPAL,
    OTHER,
    DEMO_HEADERS,
    OPENCLAW_HEADERS,
)

pytestmark = pytest.mark.usefixtures("unreleased_finance_advisor")


def settings(root):
    return replace(
        configured(root),
        member_chat_enabled_members=frozenset({PRINCIPAL, OTHER}),
        deterministic_member_chat_fixture=True,
        openclaw_model_configured=True,
    )


def access(client, binding, *, readers=(PRINCIPAL, OTHER), analysis=True, books=True):
    current = client.get(CONTROL, headers=DEMO_HEADERS).json()
    response = client.put(
        CONTROL,
        headers=DEMO_HEADERS,
        json={
            "request_id": str(uuid4()),
            "expected_revision": current["revision"],
            "binding": binding,
            "reader_member_ids": list(readers),
            "books_enabled": books,
            "cos_analysis_enabled": analysis,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def seed(client):
    runtime = client.app.state.runtime
    publication, planned, package = fixture(runtime.settings.instance.tenant_id)
    manifest, packet = packet_for(publication, planned, package)
    access(client, manifest["binding"])
    approval = approve(client, manifest)
    assert upload(client, approval, packet).status_code == 200
    return publication, planned, manifest, packet, approval


def tool(client, member, route, run, **fields):
    provenance = _finance_provenance(client, run)
    actor, key = client.app.state.runtime.member_chat_binding(member)
    provenance.update(actor_id=actor, session_key=key, agent_id=member_agent_id(member))
    return client.post(
        "/api/openclaw/v1/tools/finance-" + route,
        headers=OPENCLAW_HEADERS,
        json={"provenance": provenance, **fields},
    )


def query(client, member, publication, run):
    return tool(
        client,
        member,
        "query",
        run,
        book_id=publication["book_id"],
        publication_id=publication["publication_id"],
        report_id="scenarios",
        limit=1,
    )


def test_shared_dashboard_tools_and_replay_preserve_actual_actor_and_owner(tmp_path):
    config = settings(tmp_path)
    with TestClient(create_application(config)) as client:
        p, d, manifest, packet, approval = seed(client)
        runtime = client.app.state.runtime
        assert (
            client.get("/api/member/v1/cos/delegations", headers=OTHER_HEADERS).json()[
                "items"
            ]
            == []
        )
        for member, headers in ((PRINCIPAL, DEMO_HEADERS), (OTHER, OTHER_HEADERS)):
            surface = client.get(SURFACE, headers=headers)
            assert surface.status_code == 200, surface.text
            assert (
                surface.json()["cockpit"]["accepted_books"]["publication_id"]
                == p["publication_id"]
            )
            planning = client.get(
                "/api/member/v1/finance/planning",
                headers=headers,
                params={"book_id": p["book_id"]},
            )
            assert planning.status_code == 200, planning.text
            assert planning.json()["projection"]["id"] == d["projection_id"]
            response = query(client, member, p, "shared-" + member)
            assert response.status_code == 200, response.text
            assert response.json()["metrics"][0]["value_minor"] == 12500
        metadata = tool(
            client, OTHER, "status", "shared-bindings", include_bindings=True
        )
        assert metadata.status_code == 200, metadata.text
        assert metadata.json()["state"] == "access_unavailable"
        assert metadata.json()["reason"] == "canonical_owner_metadata_required"
        assert metadata.json()["completeness"]["complete"] is False
        assert metadata.json()["bindings"] == []
        # Reading another owner's book grants neither import nor private choices.
        assert upload(client, approval, packet, headers=OTHER_HEADERS).status_code in {
            403,
            409,
        }
        assert (
            client.get(
                "/api/member/v1/finance/choices", headers=OTHER_HEADERS
            ).status_code
            == 403
        )
        audit = client.portal.call(events, runtime, ["finance.advisor_accessed"])
        assert any(
            row["actor_identity"] == COS_SERVICE_PRINCIPAL_ID
            and row["payload"]["member_id"] == OTHER
            and row["payload"]["data_owner_id"] == PRINCIPAL
            for row in audit
        )
        controls = client.portal.call(
            events, runtime, ["settings.finance_access_changed"]
        )
        assert controls[-1]["schema_version"] == 2
        assert controls[-1]["payload"]["reader_member_ids"] == [PRINCIPAL, OTHER]
        client.portal.call(runtime.projections.rebuild, "money")
        assert (
            query(client, OTHER, p, "after-replay").json()["metrics"][0]["value_minor"]
            == 12500
        )
    with TestClient(create_application(config)) as reopened:
        assert (
            query(reopened, OTHER, p, "after-restart").json()["metrics"][0][
                "value_minor"
            ]
            == 12500
        )
        assert reopened.get(CONTROL, headers=DEMO_HEADERS).json()[
            "reader_member_ids"
        ] == [PRINCIPAL, OTHER]


def test_retained_result_reuse_and_ordinary_reply_need_no_new_finance_call(tmp_path):
    with TestClient(create_application(settings(tmp_path))) as client:
        p, _, manifest, _, _ = seed(client)
        result = query(client, OTHER, p, "original-run")
        assert result.status_code == 200, result.text
        ref = result.json()["answer_ref"]
        runtime = client.app.state.runtime
        actor = build_internal_session(
            OTHER, "principal", runtime.settings.instance.tenant_id
        )
        _, key = runtime.member_chat_binding(OTHER)
        answer = json.dumps(
            {
                "finance_answer": {
                    "result_refs": [ref],
                    "interpretation": [
                        {
                            "text": "The source estimate is conditional; confirm the current inputs before deciding.",
                            "result_refs": [ref],
                        }
                    ],
                }
            }
        )

        async def reuse():
            return await check_disclosure(
                runtime,
                actor,
                key,
                run_id="a-later-ordinary-run",
                require_run=True,
                answer_text=answer,
            )

        rendered = client.portal.call(reuse)
        assert "12500" in rendered and "CoS interpretation" in rendered
        count = len(client.portal.call(events, runtime, ["finance.advisor_accessed"]))

        async def stream(**_kwargs):
            yield MemberChatChunk(
                kind="delta", text="I can help with that.", run_id="ordinary-run"
            )
            yield MemberChatChunk(kind="final", run_id="ordinary-run")

        async def history(**_kwargs):
            return [{"role": "assistant", "text": "I can help with that."}]

        async def abort(**_kwargs):
            raise AssertionError("Ordinary conversation was blocked")

        runtime.member_chat = SimpleNamespace(
            stream=stream, history=history, abort=abort
        )
        for analysis in (True, False):
            if not analysis:
                access(client, manifest["binding"], analysis=False)
            response = client.post(
                "/api/member/v1/chat/stream",
                headers=OTHER_HEADERS,
                json={
                    "request_id": str(uuid4()),
                    "message": "Can we discuss tomorrow?",
                },
            )
            assert (
                response.status_code == 200 and "I can help with that." in response.text
            )
            assert (
                client.get(
                    "/api/member/v1/chat/history", headers=OTHER_HEADERS
                ).status_code
                == 200
            )
        assert (
            len(client.portal.call(events, runtime, ["finance.advisor_accessed"]))
            == count
        )
        with pytest.raises(FinanceDisclosureError, match="authority_changed"):
            client.portal.call(reuse)
        denied = query(client, OTHER, p, "after-analysis-off")
        assert "12500" not in denied.text and "metrics" not in denied.json()
        # Analysis off does not revoke the member's independent dashboard read.
        assert (
            client.get(SURFACE, headers=OTHER_HEADERS).json()["cockpit"][
                "accepted_books"
            ]["publication_id"]
            == p["publication_id"]
        )
        access(client, manifest["binding"], analysis=True)
        with pytest.raises(FinanceDisclosureError, match="retained_result_unavailable"):
            client.portal.call(reuse)
        access(client, manifest["binding"], readers=(PRINCIPAL,), analysis=False)
        assert (
            client.get(
                "/api/member/v1/finance/planning",
                headers=OTHER_HEADERS,
                params={"book_id": p["book_id"]},
            ).status_code
            == 403
        )
        # Schema-aware feature recovery preserves the imported event history.
        access(
            client,
            manifest["binding"],
            readers=(PRINCIPAL,),
            analysis=False,
            books=False,
        )
        disabled = client.get(SURFACE, headers=DEMO_HEADERS).json()["cockpit"][
            "accepted_books"
        ]
        assert disabled == {
            "state": "disabled", "reports": [], "provider_effect_count": 0
        }
        assert (
            len(client.portal.call(events, runtime, ["finance.publication_accepted"]))
            == 1
        )
        assert (
            len(client.portal.call(events, runtime, ["finance.projection_verified"]))
            == 1
        )


@pytest.mark.parametrize(
    "fault", ["wrong_book", "view_as", "role_changed", "removed_reader"]
)
def test_shared_book_never_substitutes_for_current_reader_authority(tmp_path, fault):
    with TestClient(create_application(settings(tmp_path))) as client:
        p, _, manifest, _, _ = seed(client)
        runtime = client.app.state.runtime
        headers = OTHER_HEADERS
        book = p["book_id"]
        if fault == "wrong_book":
            book = "different-book"
        elif fault == "view_as":
            headers = {**OTHER_HEADERS, "X-AdminMe-View-Member": PRINCIPAL}
        elif fault == "role_changed":
            runtime.settings = replace(
                runtime.settings,
                member_roles={**runtime.settings.member_roles, OTHER: "child"},
            )
        elif fault == "removed_reader":
            access(client, manifest["binding"], readers=(PRINCIPAL,))
        response = client.get(
            "/api/member/v1/finance/planning", headers=headers, params={"book_id": book}
        )
        assert response.status_code in {403, 404}, response.text
        assert "12500" not in response.text and "projection" not in response.json()


@pytest.mark.parametrize(
    "change", ["remove_reader", "disable_analysis", "principal_role"]
)
def test_inflight_shared_result_cannot_cross_a_changed_current_authority(
    tmp_path, monkeypatch, change
):
    from adminme.products.openclaw import main
    from adminme.products.member.main import _write_settings_receipt
    from adminme.finance.access_control import refresh_control

    with TestClient(create_application(settings(tmp_path))) as client:
        p, _, manifest, _, _ = seed(client)
        runtime = client.app.state.runtime
        original = main._finance_audit
        owner = build_internal_session(
            PRINCIPAL, "principal", runtime.settings.instance.tenant_id
        )
        changed = []

        async def revoke_after_audit(request, session, provenance, **kwargs):
            await original(request, session, provenance, **kwargs)
            if kwargs["operation"] != "finance.query":
                return
            changed.append(True)
            if change == "principal_role":
                runtime.settings = replace(
                    runtime.settings,
                    member_roles={**runtime.settings.member_roles, OTHER: "child"},
                )
                return
            current = runtime.finance_access.snapshot(owner.tenant_id, PRINCIPAL)
            runtime.finance_access.begin_change(owner.tenant_id, PRINCIPAL)
            await _write_settings_receipt(
                runtime,
                owner,
                event_type="settings.finance_access_changed",
                schema_version=2,
                payload={
                    "owner_id": PRINCIPAL,
                    "changed_by_member_id": PRINCIPAL,
                    "revision": current.revision + 1,
                    "prior_event_id": current.event_id,
                    "binding": manifest["binding"],
                    "books_enabled": True,
                    "cos_analysis_enabled": change != "disable_analysis",
                    "provider_effects": False,
                    "reader_member_ids": [PRINCIPAL]
                    if change == "remove_reader"
                    else [PRINCIPAL, OTHER],
                },
                request_id=str(uuid4()),
                request_fingerprint="synthetic-revocation",
                owner_member_id=PRINCIPAL,
                sensitivity="privileged",
            )
            await refresh_control(runtime, PRINCIPAL, settle_change=True)

        monkeypatch.setattr(main, "_finance_audit", revoke_after_audit)
        response = query(client, OTHER, p, "inflight-change")
        assert changed and response.status_code == 403, response.text
        assert "12500" not in response.text and "metrics" not in response.json()
