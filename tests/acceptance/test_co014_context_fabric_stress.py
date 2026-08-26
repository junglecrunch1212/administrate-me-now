"""AC-39 deterministic two-year load and production-composition thresholds."""

from pathlib import Path

from fastapi.testclient import TestClient

from adminme.events.envelope import EventEnvelope
from adminme.runtime.application import RuntimeSettings, create_application
from adminme.simulations.context_fabric_stress import run_suite


REPO_ROOT = Path(__file__).resolve().parents[2]
MEMBER_A = "fixture-co014-adult-a"  # fixture:tenant_data:ok
MEMBER_B = "fixture-co014-adult-b"  # fixture:tenant_data:ok
AUTH_A = {"Tailscale-User-Login": "co014-a.ts.net"}  # fixture:tenant_data:ok
AUTH_B = {"Tailscale-User-Login": "co014-b.ts.net"}  # fixture:tenant_data:ok


def test_co014_two_year_low_base_high_profiles_meet_safety_bounds() -> None:
    suite = run_suite(seed=14_365)
    assert [result["profile"]["name"] for result in suite["results"]] == [
        "low",
        "base",
        "high",
    ]
    for result in suite["results"]:
        co_014 = result["co_014"]
        assert co_014["high_priority_recall"] == 1.0
        assert co_014["pagination_missing"] == 0
        assert co_014["cross_scope_links"] == 0
        assert co_014["provider_effects"] == 0
        assert co_014["max_context_sources"] <= 100
        assert co_014["bounded_traversal_nodes"] <= 500
        assert co_014["estimated_context_storage_bytes"] < 1_000_000_000
        assert co_014["estimated_page_query_ms_p95"] < 50
        assert co_014["decision_backlog"] < result["retired_flat_queue"]["backlog"]
    base = suite["results"][1]
    assert base["co_014"]["decision_backlog"] <= 50
    assert base["co_014"]["zero_days"] > 200


def test_co014_production_surface_pages_every_candidate_without_scope_leak(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    settings = RuntimeSettings.credential_free(
        instance,
        encryption_key=b"c" * 32,
        repository_root=REPO_ROOT,
        member_identities={
            "co014-a.ts.net": MEMBER_A,
            "co014-b.ts.net": MEMBER_B,
        },
        member_roles={MEMBER_A: "principal", MEMBER_B: "principal"},
        openclaw_enabled=False,
    )
    app = create_application(settings)
    with TestClient(app) as client:
        runtime = app.state.runtime
        envelopes = []
        for index in range(521):
            envelopes.append(
                EventEnvelope(
                    event_at_ms=1_900_000_000_000 + index,
                    tenant_id=runtime.settings.instance.tenant_id,
                    type="triage.item.proposed",
                    schema_version=1,
                    occurred_at=f"2030-01-{1 + index // 24:02d}T{index % 24:02d}:00:00Z",
                    source_adapter="fixture-co014",
                    source_account_id="fixture-account",
                    owner_scope=f"private:{MEMBER_A}",
                    visibility_scope=f"private:{MEMBER_A}",
                    sensitivity="normal",
                    idempotency_key=f"co014-process-{index}",
                    payload={
                        "proposal_id": f"co014-proposal-{index:04d}",
                        "target_kind": "task",
                        "owner_member_id": MEMBER_A,
                        "title": f"Tenant-neutral candidate {index:04d}",
                        "candidate": {},
                        "route": {"member_id": MEMBER_A},
                        "source_event_id": f"co014-source-{index:04d}",
                        "confidence": None,
                        "reasons": ["tenant-neutral fixture"],
                    },
                )
            )
        event_ids = client.portal.call(runtime.events.write_batch, envelopes)
        client.portal.call(runtime.wait_for_projection, "triage", event_ids[-1])
        client.portal.call(runtime.wait_for_projection, "context_fabric", event_ids[-1])
        seen: set[str] = set()
        cursor = None
        first_cursor = None
        first_inventory = None
        while True:
            params = "triage_lens=all_candidates&triage_limit=17"
            if cursor:
                params += f"&triage_cursor={cursor}"
            response = client.get(
                f"/api/member/v1/surfaces/triage?{params}", headers=AUTH_A
            )
            assert response.status_code == 200, response.text
            page = response.json()
            first_inventory = first_inventory or page["triage"]["inventory"]
            first_cursor = first_cursor or page["triage"]["next_cursor"]
            for item in page["items"]:
                assert item["proposal_id"] not in seen
                seen.add(item["proposal_id"])
            cursor = page["triage"]["next_cursor"]
            if cursor is None:
                break
        assert len(seen) == 521
        assert first_inventory["total_candidates"] == 521
        assert first_cursor is not None
        other = client.get(
            "/api/member/v1/surfaces/triage?triage_lens=all_candidates",
            headers=AUTH_B,
        )
        assert other.status_code == 200
        assert other.json()["triage"]["inventory"]["total_candidates"] == 0
        today = client.get("/api/member/v1/surfaces/today", headers=AUTH_A).json()
        exact_question = next(
            item
            for item in today["brief"]["questions"]
            if item["candidate_id"] == "co014-proposal-0508"
        )
        assert exact_question["decision_context"]["contract"] == (
            "member-decision-context-v1"
        )
        assert exact_question["decision_context"]["decision_maker"] == "member"
        assert exact_question["decision_context"]["what_happened"] == (
            "Tenant-neutral candidate 0508"
        )
        assert exact_question["surface_link"]["href"].startswith(
            "/member/triage?item=co014-proposal-0508&matter="
        )
        carousel_questions = [
            item
            for item in today["brief"]["carousel"]["items"]
            if item["kind"] == "clarifying_question"
        ]
        assert carousel_questions
        questions_by_id = {
            item["candidate_id"]: item for item in today["brief"]["questions"]
        }
        for carousel_question in carousel_questions:
            day_brief_question = questions_by_id[carousel_question["candidate_id"]]
            assert (
                carousel_question["decision_context"]
                == day_brief_question["decision_context"]
            )
            assert carousel_question["title"] == day_brief_question["title"]
            assert carousel_question["context"] == day_brief_question["context"]
            assert carousel_question["micro_step"] == day_brief_question["micro_step"]
        focused_pending = client.get(
            "/api/member/v1/surfaces/triage"
            "?triage_lens=all_candidates&triage_limit=10"
            "&triage_item=co014-proposal-0508",
            headers=AUTH_A,
        ).json()
        assert focused_pending["focus"] == {
            "requested_candidate_id": "co014-proposal-0508",
            "state": "pending",
            "candidate_state": "pending_review",
            "matter_id": exact_question["matter_id"],
        }
        assert any(
            item["proposal_id"] == "co014-proposal-0508"
            for matter in focused_pending["triage"]["matters"]
            for item in matter["candidates"]
        )
        cross_scope_focus = client.get(
            "/api/member/v1/surfaces/triage?triage_item=co014-proposal-0508",
            headers=AUTH_B,
        ).json()
        assert cross_scope_focus["focus"]["state"] == "not_found"
        learning_page = client.get(
            "/api/member/v1/surfaces/triage?triage_lens=all_candidates",
            headers=AUTH_A,
        ).json()
        for offset, item in enumerate(learning_page["items"][:3]):
            dismissed = client.post(
                f"/api/member/v1/triage/{item['proposal_id']}/dismiss",
                headers=AUTH_A,
                json={
                    "request_id": f"co014-feedback-{offset:04d}",
                    "expected_last_event_id": item["last_event_id"],
                    "reason": "Tenant-neutral learning fixture",
                    "feedback_reason": "not_actionable",
                },
            )
            assert dismissed.status_code == 200, dismissed.text
            assert dismissed.json()["feedback_event_id"]
        stale_page = client.get(
            "/api/member/v1/surfaces/triage"
            f"?triage_lens=all_candidates&triage_cursor={first_cursor}",
            headers=AUTH_A,
        )
        assert stale_page.status_code == 422
        assert stale_page.json() == {"detail": "stale triage cursor"}
        refreshed_inventory = client.get(
            "/api/member/v1/surfaces/triage?triage_lens=all_candidates",
            headers=AUTH_A,
        ).json()["triage"]["inventory"]
        assert refreshed_inventory["total_candidates"] == 518
        suggestions = client.get(
            "/api/member/v1/surfaces/triage?triage_lens=all_candidates",
            headers=AUTH_A,
        ).json()["triage"]["feedback_suggestions"]
        assert any(
            suggestion["scope_kind"] == "candidate_kind"
            and suggestion["scope_value"] == "task"
            and suggestion["occurrences"] == 3
            for suggestion in suggestions
        )
        preview = client.post(
            "/api/member/v1/triage-policy/preview",
            headers=AUTH_A,
            json={
                "scope_kind": "candidate_kind",
                "scope_value": "task",
                "disposition": "route_to_low_signal",
            },
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["affected_feedback_count"] == 3
        assert preview.json()["provider_effects"] == 0
        approved = client.post(
            "/api/member/v1/triage-policy/approve",
            headers=AUTH_A,
            json={
                "scope_kind": "candidate_kind",
                "scope_value": "task",
                "disposition": "route_to_low_signal",
                "request_id": "co014-rule-approve-0001",
                "expected_affected_count": 3,
            },
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["state"] == "shadow"
        after_approval = client.get(
            "/api/member/v1/surfaces/triage?triage_lens=all_candidates",
            headers=AUTH_A,
        ).json()["triage"]
        assert after_approval["feedback_suggestions"] == []
        assert [
            rule
            for rule in after_approval["policy_rules"]
            if rule["status"] == "active"
        ]
        learned = client.get(
            "/api/member/v1/surfaces/triage?triage_lens=learned_rules",
            headers=AUTH_A,
        ).json()
        assert learned["items"]
        assert all(item["policy_shadow_only"] for item in learned["items"])
        assert all(item["policy_disposition"] != "none" for item in learned["items"])
        revoked = client.post(
            f"/api/member/v1/triage-policy/{approved.json()['rule_id']}/revoke",
            headers=AUTH_A,
            json={
                "request_id": "co014-rule-revoke-0001",
                "reason": "Tenant-neutral reversal proof",
            },
        )
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["state"] == "revoked"
        after_revoke = client.get(
            "/api/member/v1/surfaces/triage?triage_lens=all_candidates",
            headers=AUTH_A,
        ).json()["triage"]
        assert after_revoke["feedback_suggestions"] == []
        after_revoke_matches = client.get(
            "/api/member/v1/surfaces/triage?triage_lens=learned_rules",
            headers=AUTH_A,
        ).json()
        assert after_revoke_matches["items"] == []
        focused_item = next(
            item
            for item in focused_pending["items"]
            if item["proposal_id"] == "co014-proposal-0508"
        )
        resolved = client.post(
            "/api/member/v1/triage/co014-proposal-0508/dismiss",
            headers=AUTH_A,
            json={
                "request_id": "co015-resolved-deep-link",
                "expected_last_event_id": focused_item["last_event_id"],
                "reason": "Tenant-neutral resolved-link proof",
                "feedback_reason": "wrong_context",
            },
        )
        assert resolved.status_code == 200, resolved.text
        focused_resolved = client.get(
            "/api/member/v1/surfaces/triage?triage_item=co014-proposal-0508",
            headers=AUTH_A,
        ).json()
        assert focused_resolved["focus"]["state"] == "resolved"
        assert focused_resolved["focus"]["candidate_state"] == "dismissed"
        assert any(
            item["proposal_id"] == "co014-proposal-0508"
            for item in focused_resolved["recent_items"]
        )
        pending_intent_id = "co014-pending-interpretation"
        pending_event_id = client.portal.call(
            runtime.events.write,
            EventEnvelope(
                event_at_ms=1_900_000_100_000,
                tenant_id=runtime.settings.instance.tenant_id,
                type="triage.intent.submitted",
                schema_version=1,
                occurred_at="2030-02-01T00:00:00Z",
                source_adapter="fixture-co014",
                source_account_id=MEMBER_A,
                owner_scope=f"private:{MEMBER_A}",
                visibility_scope=f"private:{MEMBER_A}",
                sensitivity="normal",
                idempotency_key="co014-pending-interpretation",
                payload={
                    "intent_id": pending_intent_id,
                    "member_id": MEMBER_A,
                    "text": "Unclassified tenant-neutral fixture",
                    "source_surface": "triage",
                    "kind_hint": None,
                    "submitted_at": "2030-02-01T00:00:00Z",
                    "client_request_id": "co014-pending-interpretation",
                },
            ),
        )
        client.portal.call(runtime.wait_for_projection, "triage", pending_event_id)
        pending_page = client.get(
            "/api/member/v1/surfaces/triage?triage_lens=needs_interpretation",
            headers=AUTH_A,
        ).json()
        pending_item = next(
            item
            for item in pending_page["items"]
            if item["proposal_id"] == pending_intent_id
        )
        pending_dismiss = client.post(
            f"/api/member/v1/triage/{pending_intent_id}/dismiss",
            headers=AUTH_A,
            json={
                "request_id": "co014-dismiss-interpretation",
                "expected_last_event_id": pending_item["last_event_id"],
                "reason": "Tenant-neutral holding-state dismissal",
                "feedback_reason": "not_actionable",
            },
        )
        assert pending_dismiss.status_code == 200, pending_dismiss.text
        before = {
            "triage": runtime.projections.connection("triage")
            .execute("SELECT COUNT(*) FROM triage_items")
            .fetchone()[0],
            "contexts": runtime.projections.connection("context_fabric")
            .execute("SELECT COUNT(*) FROM contexts")
            .fetchone()[0],
            "edges": runtime.projections.connection("context_fabric")
            .execute("SELECT COUNT(*) FROM context_edges")
            .fetchone()[0],
        }
        client.portal.call(runtime.projections.rebuild, "triage")
        client.portal.call(runtime.projections.rebuild, "context_fabric")
        after = {
            "triage": runtime.projections.connection("triage")
            .execute("SELECT COUNT(*) FROM triage_items")
            .fetchone()[0],
            "contexts": runtime.projections.connection("context_fabric")
            .execute("SELECT COUNT(*) FROM contexts")
            .fetchone()[0],
            "edges": runtime.projections.connection("context_fabric")
            .execute("SELECT COUNT(*) FROM context_edges")
            .fetchone()[0],
        }
        assert after == before
        assert runtime.fixture_adapter is None
