"""Private source rules through the production app, durable log, and replay.

The source-neutral fixtures deliberately include a Messages conversation and a
future adapter. No live instance, provider, household data, or model is used.
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from adminme.events.envelope import EventEnvelope
from adminme.runtime.application import RuntimeSettings, create_application
from adminme.source_policy import (
    SourceCoordinate,
    SourcePolicyV2,
    source_coordinate_for_event,
)


ROOT = Path(__file__).resolve().parents[2]
MEMBER = "fixture-attention-adult-a"  # fixture:tenant_data:ok
OTHER = "fixture-attention-adult-b"  # fixture:tenant_data:ok
HEADERS = {"Tailscale-User-Login": "attention-a.ts.net"}
OTHER_HEADERS = {"Tailscale-User-Login": "attention-b.ts.net"}
API = "/api/member/v1/triage-learning/v2"


def _settings(path: Path, *, active: bool = True) -> RuntimeSettings:
    return RuntimeSettings.credential_free(
        path,
        encryption_key=b"a" * 32,
        repository_root=ROOT,
        member_identities={"attention-a.ts.net": MEMBER, "attention-b.ts.net": OTHER},
        member_roles={MEMBER: "principal", OTHER: "principal"},
        household_mapping={
            "schema_version": 2,
            "household_label": "Fixture household",
            "members": [
                {"member_id": MEMBER, "label": "Fixture adult A", "role": "principal"},
                {"member_id": OTHER, "label": "Fixture adult B", "role": "principal"},
            ],
            "memberships": [],
            "relationships": [],
            "parties": [],
        },
        adapter_config={"adapter_supervisor_port": 0, "adapters": []},
        fixture_member_id=MEMBER,
        reconciliation_admission_enabled=active,
        human_triage_management_enabled=active,
    )


def _coordinate(
    tenant: str, *, account: str = "fixture-messages-account"
) -> SourceCoordinate:
    return SourceCoordinate(
        tenant_id=tenant,
        owner_principal_id=MEMBER,
        owner_kind="member",
        context_member_id=MEMBER,
        adapter_category="communication",
        adapter_kind="fixture-future-messages",
        adapter_instance_id="fixture-messages-adapter",
        provider="fixture",
        provider_account_id=account,
        workspace="personal",
        source_kind="messaging.received",
        source_class="passive_communication",
        trusted_route=f"member_private:{MEMBER}",
        native_container_kind="conversation",
        native_container_id="fixture-group-conversation",
    )


def _emit(
    client: TestClient,
    index: int,
    *,
    purpose: str = "survey",
    independent: bool = False,
    basis: list[str] | None = None,
    account: str = "fixture-messages-account",
    source_id: str | None = None,
    source_coordinate: SourceCoordinate | None = None,
    decision: str = "admit",
    actionability: str | None = None,
    action_owner: str = "member",
) -> str:
    runtime = client.app.state.runtime
    tenant = runtime.settings.instance.tenant_id
    coordinate = source_coordinate or _coordinate(tenant, account=account)
    proposal_id = f"attention-proposal-{index}"
    source_id = source_id or f"attention-source-{index}"
    now = datetime.now(timezone.utc).isoformat()
    digest = hashlib.sha256(proposal_id.encode()).hexdigest()
    common = {
        "event_at_ms": int(time.time() * 1000),
        "tenant_id": tenant,
        "occurred_at": now,
        "source_account_id": account,
        "owner_scope": f"private:{MEMBER}",
        "visibility_scope": f"private:{MEMBER}",
        "sensitivity": "normal",
    }
    candidate = EventEnvelope(
        **common,
        type="triage.item.proposed",
        schema_version=5,
        source_adapter="deterministic_candidate_eligibility",
        idempotency_key=f"attention-proposal:{index}",
        payload={
            "proposal_id": proposal_id,
            "target_kind": "task",
            "owner_member_id": MEMBER,
            "title": f"Fixture {purpose} {index}",
            "candidate": {"sender_digest": "a" * 64},
            "source_event_id": source_id,
            "route": {"member_id": MEMBER},
            "eligibility_rule_ids": ["fixture-attention"],
            "confidence": 0.01,
            "interpretation": {
                "contract": "triage-context-interpretation-v3",
                "context_fingerprint": digest,
                "thread_key": digest,
                "anchor_event_id": source_id,
                "compact_summary": f"Fixture {purpose}",
                "thread_state": "new",
                "action_owner": action_owner,
                "lifecycle_state": "open",
                "evidence_refs": [
                    {"source_event_id": source_id, "quote": "Fixture source clause"}
                ],
                "context_source_event_ids": [source_id],
                "context_sources": [
                    {
                        "source_event_id": source_id,
                        "relation": "thread",
                        "reason_codes": ["provider_thread_id"],
                        "relationship_source_event_ids": [],
                    }
                ],
                "assessment_state": "verified",
                "obligation_assessment": {
                    "purpose": purpose,
                    "actionability": actionability
                    or ("required" if independent else "informational"),
                    "condition_state": "not_applicable",
                    "independent_obligation": independent,
                    "evidence": [
                        {
                            "source_event_id": source_id,
                            "quote": "Fixture source clause",
                            "role": "action",
                        }
                    ],
                },
            },
        },
    )
    receipt = EventEnvelope(
        **common,
        type="triage.reconciliation.decided",
        schema_version=1,
        source_adapter="triage-reconciliation-v1",
        idempotency_key=f"attention-receipt:{index}",
        payload={
            "reconciliation_id": f"recon_{digest[:32]}",
            "proposal_id": proposal_id,
            "primary_proposal_id": None,
            "matter_id": f"ctx_reconciled_{digest[:32]}",
            "owner_member_id": MEMBER,
            "source_event_id": source_id,
            "evidence_source_event_ids": [source_id],
            "member_authored_text_digest": None,
            "relationships": [],
            "decision": decision,
            "decision_revision": 1,
            "supersedes_reconciliation_event_id": None,
            "policy": {
                "policy_key": coordinate.key,
                "policy_revision": 0,
                "policy_origin": "recommended_default",
                "triage_route": "review",
                "alert_route": "none",
                "context_route": "metadata_only",
                "reason_codes": ["fixture_review_policy"],
            },
            "source_coordinate": coordinate.model_dump(mode="json"),
            "freshness": "current",
            "priority_rank": 60,
            "priority_basis": basis or ["standard_actionable_candidate"],
            "reason_codes": ["fixture_admission"],
            "decided_at": now,
            "admission_active": True,
            "raw_cross_member_join": False,
            "weak_similarity_changed_identity": False,
            "weak_similarity_transferred_ownership": False,
            "weak_similarity_authorized_action": False,
            "provider_effects": False,
            "llm_authority": False,
        },
    )
    event_ids = client.portal.call(runtime.events.write_batch, [candidate, receipt])
    client.portal.call(runtime.wait_for_projection, "triage", event_ids[-1])
    return proposal_id


def _rule(**changes: Any) -> dict[str, Any]:
    return {
        "conditions": {
            "provider_account_id": "fixture-messages-account",
            "adapter_instance_id": "fixture-messages-adapter",
            "message_purpose": "survey",
        },
        "exceptions": [],
        "action": "reference",
        "origin": "explicit",
        **changes,
    }


def _preview(client: TestClient, rule: dict[str, Any]) -> dict[str, Any]:
    response = client.post(f"{API}/preview", headers=HEADERS, json=rule)
    assert response.status_code == 200, response.text
    return response.json()


def _save(
    client: TestClient, rule: dict[str, Any], request_id: str, **options: Any
) -> dict[str, Any]:
    preview = _preview(client, rule)
    response = client.post(
        f"{API}/rules",
        headers=HEADERS,
        json={
            **rule,
            "request_id": request_id,
            "preview_digest": preview["preview_digest"],
            "mode": "active",
            **options,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _row(client: TestClient, proposal_id: str) -> dict[str, Any]:
    conn = client.app.state.runtime.projections.connection("triage")
    return dict(
        conn.execute(
            "SELECT * FROM triage_items WHERE proposal_id=?", (proposal_id,)
        ).fetchone()
    )


def _rules(client: TestClient) -> list[dict[str, Any]]:
    response = client.get(f"{API}/rules", headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()["rules"]


def test_exact_rule_preview_exceptions_conflicts_undo_isolation_and_replay(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "attention"
    instance.mkdir()
    settings = _settings(instance)
    with TestClient(create_application(settings)) as client:
        survey = _emit(client, 1)
        _emit(client, 2, purpose="payment", independent=True)
        _emit(client, 3, account="fixture-other-account")
        protected = _emit(client, 4, basis=["explicit_member_instruction"])
        promise = _emit(client, 5, basis=["exact_member_commitment"])
        rule = _rule()
        preview = _preview(client, rule)
        assert preview["recent_impact"] == {
            "total": 5,
            "matched": 1,
            "exceptions": 0,
            "nonmatches": 2,
            "protected": 2,
        }
        assert preview["mutation_performed"] is False
        # A conversation rule can explicitly exempt a purpose; required payment
        # is independently protected even if the member omits that exception.
        broad = _rule(
            conditions={
                **rule["conditions"],
                "message_purpose": None,
                "native_container_id": "fixture-group-conversation",
            },
            exceptions=[{"message_purpose": "survey"}],
        )
        assert _preview(client, broad)["recent_impact"]["exceptions"] == 3
        saved = _save(client, rule, "attention-save-surveys")
        assert _row(client, survey)["learned_policy_state"] == "context_only"
        assert _row(client, protected)["learned_policy_state"] == "none"
        assert _row(client, promise)["learned_policy_state"] == "none"
        assert client.get(f"{API}/rules", headers=OTHER_HEADERS).json()["rules"] == []
        assert (
            client.post(
                f"{API}/rules/{saved['rule_id']}/review",
                headers=OTHER_HEADERS,
                json={
                    "request_id": "attention-cross-adult",
                    "expected_last_event_id": saved["event_id"],
                    "decision": "revoke",
                    "reason": "Fixture attempted cross-member edit",
                },
            ).status_code
            == 404
        )
        # A more specific explicit review rule wins without reviving inadmissible
        # data. Equal specificity with different actions yields clarification.
        narrow = _rule(
            conditions={**rule["conditions"], "sender_digest": "a" * 64},
            action="review",
        )
        narrowed_preview = _preview(client, narrow)
        assert (
            next(
                value
                for value in narrowed_preview["examples"]
                if value["proposal_id"] == survey
            )["after"]
            == "ordinary_queue"
        )
        _save(client, narrow, "attention-save-narrow")
        assert _row(client, survey)["learned_policy_state"] == "none"
        conflict = _save(
            client, {**narrow, "action": "reference"}, "attention-save-conflict"
        )
        assert _row(client, survey)["learned_policy_state"] == "clarification_required"
        response = client.post(
            f"{API}/rules/{conflict['rule_id']}/review",
            headers=HEADERS,
            json={
                "request_id": "attention-undo-conflict",
                "expected_last_event_id": conflict["event_id"],
                "decision": "suspend",
                "negative_outcome": "false_suppression",
                "reason": "Keep this example reviewable",
            },
        )
        assert response.status_code == 200, response.text
        assert _row(client, survey)["learned_policy_state"] == "none"
        before = [
            tuple(row)
            for row in client.app.state.runtime.projections.connection(
                "triage"
            ).execute(
                "SELECT proposal_id,learned_policy_state,attention_rule_ids_json FROM triage_items ORDER BY proposal_id"
            )
        ]
        client.portal.call(client.app.state.runtime.projections.rebuild, "triage")
        after = [
            tuple(row)
            for row in client.app.state.runtime.projections.connection(
                "triage"
            ).execute(
                "SELECT proposal_id,learned_policy_state,attention_rule_ids_json FROM triage_items ORDER BY proposal_id"
            )
        ]
        assert after == before


def test_trial_counts_future_independent_sources_and_restores_on_limit(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "trial"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        old = _emit(client, 10)
        saved = _save(
            client, _rule(), "attention-trial-save", mode="trial", trial_message_limit=2
        )
        assert _row(client, old)["learned_policy_state"] == "none"
        first = _emit(client, 11, source_id="attention-independent-a")
        assert _row(client, first)["learned_policy_state"] == "context_only"
        _emit(client, 12, source_id="attention-independent-a")
        assert _rules(client)[0]["observed_count"] == 1
        _emit(client, 13, source_id="attention-independent-b")
        rule = next(
            value for value in _rules(client) if value["rule_id"] == saved["rule_id"]
        )
        assert rule["status"] == "expired"
        assert rule["observed_count"] == 2
        assert _row(client, first)["learned_policy_state"] == "none"
        assert rule["history"][-1]["reason"] == "trial_observation_limit"


def test_completion_does_not_teach_future_suppression_and_trials_expire_at_read(
    tmp_path: Path, monkeypatch: Any
) -> None:
    instance = tmp_path / "feedback"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        proposal = _emit(client, 20)
        item = _row(client, proposal)
        response = client.post(
            f"{API}/feedback",
            headers=HEADERS,
            json={
                "request_id": "attention-feedback-completed",
                "proposal_id": proposal,
                "expected_last_event_id": item["last_event_id"],
                "kind": "completion",
            },
        )
        assert response.status_code == 200, response.text
        preview = client.post(
            f"{API}/preview",
            headers=HEADERS,
            json={
                **_rule(),
                "origin": "suggested",
                "feedback_event_ids": [response.json()["event_id"]],
            },
        )
        assert preview.status_code == 422
        assert client.get(f"{API}/rules", headers=HEADERS).json()["suggestions"] == []
        _save(client, _rule(), "attention-expiring-trial", mode="trial")
        future = _emit(client, 21)
        assert _row(client, future)["learned_policy_state"] == "context_only"
        # Read-time clock advance is presentation-only: expiry restores the
        # pre-trial disposition without an event, write, or scheduler callback.
        from adminme.projections.triage import queries

        real_datetime = datetime

        class Later(real_datetime):
            @classmethod
            def now(cls, tz: Any = None) -> datetime:
                return real_datetime.now(tz) + timedelta(days=8)

        monkeypatch.setattr(queries, "datetime", Later)
        public = queries._public(_row(client, future))
        assert public["learned_policy"]["state"] == "none"
        assert public["disposition"]["ordinary_queue"] is True
        assert public["learned_policy"]["reasons"] == ["trial_expired"]


def test_expired_specific_trial_restores_remaining_permanent_rule(
    tmp_path: Path, monkeypatch: Any
) -> None:
    instance = tmp_path / "overlap"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        _emit(client, 30)
        _save(client, _rule(), "attention-permanent-surveys")
        _save(
            client,
            _rule(
                conditions={**_rule()["conditions"], "sender_digest": "a" * 64},
                action="review",
            ),
            "attention-specific-review-trial",
            mode="trial",
        )
        future = _emit(client, 31)
        assert _row(client, future)["learned_policy_state"] == "none"
        from adminme.projections.triage import queries

        real_datetime = datetime

        class Later(real_datetime):
            @classmethod
            def now(cls, tz: Any = None) -> datetime:
                return real_datetime.now(tz) + timedelta(days=8)

        monkeypatch.setattr(queries, "datetime", Later)
        public = queries._public(_row(client, future))
        assert public["learned_policy"]["state"] == "context_only"
        assert public["disposition"]["ordinary_queue"] is False


def test_needs_you_includes_undated_obligations_and_clarifications_with_matching_counts(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "needs-you"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        required = _emit(client, 50, purpose="personal_request", independent=True)
        clarify = _emit(
            client,
            51,
            purpose="personal_request",
            decision="unresolved",
            actionability="unknown",
            action_owner="unknown",
        )
        optional = _emit(
            client,
            52,
            purpose="optional_opportunity",
            decision="unresolved",
            actionability="conditional",
        )
        explicit_optional = _emit(
            client,
            53,
            purpose="optional_opportunity",
            basis=["explicit_member_instruction"],
            actionability="conditional",
        )
        assert _row(client, required)["queue_lane"] == "standard"
        assert _row(client, required)["human_lane"] == "ordinary_obligation"
        assert _row(client, optional)["human_lane"] == "possible_opportunity"
        assert _row(client, required)["confidence"] == 0.01
        for lens, expected in (
            ("ready", {required, clarify, explicit_optional}),
            ("low_signal", {optional}),
        ):
            response = client.get(
                f"/api/member/v1/surfaces/triage?triage_lens={lens}&triage_limit=100",
                headers=HEADERS,
            )
            assert response.status_code == 200, response.text
            page = response.json()["triage"]
            items = [
                candidate
                for matter in page["matters"]
                for candidate in matter["candidates"]
            ]
            assert {item["proposal_id"] for item in items} == expected
            assert page["inventory"]["views"][lens]["candidates"] == len(expected)
            assert {item["attention_view"] for item in items} == {
                "needs_you" if lens == "ready" else "possibilities"
            }


def test_flag_off_keeps_legacy_ready_counts_for_retained_qualified_obligation(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "legacy-ready"
    instance.mkdir()
    with TestClient(create_application(_settings(instance, active=False))) as client:
        required = _emit(client, 54, purpose="personal_request", independent=True)
        response = client.get(
            "/api/member/v1/surfaces/triage?triage_lens=ready&triage_limit=100",
            headers=HEADERS,
        )
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["human_triage_management"]["active"] is False
        assert page["triage"]["inventory"]["views"]["ready"]["candidates"] == 0
        assert page["triage"]["inventory"]["views"]["low_signal"]["candidates"] == 1
        assert page["triage"]["matters"] == []
        assert _row(client, required)["queue_lane"] == "standard"


def _settle_reconciliation(client: TestClient, trigger_event_id: str) -> None:
    runtime = client.app.state.runtime
    client.portal.call(
        runtime.wait_for_subscriber, "triage-reconciliation:admission", trigger_event_id
    )
    children = client.portal.call(runtime.event_log.get_by_causation, trigger_event_id)
    decisions = [
        value for value in children if value["type"] == "triage.reconciliation.decided"
    ]
    if decisions:
        latest = max(str(value["event_id"]) for value in decisions)
        client.portal.call(runtime.wait_for_projection, "triage", latest)
        client.portal.call(runtime.wait_for_projection, "context_fabric", latest)


def test_source_policy_quiet_then_review_restores_only_engine_hidden_work(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "source-policy-restoration"
    instance.mkdir()
    with TestClient(create_application(_settings(instance))) as client:
        runtime = client.app.state.runtime
        tenant = runtime.settings.instance.tenant_id
        proposals = []
        coordinate = None
        for index in range(40, 44):
            now = datetime.now(timezone.utc).isoformat()
            source_id = client.portal.call(
                runtime.events.write,
                EventEnvelope(
                    event_at_ms=int(time.time() * 1000),
                    tenant_id=tenant,
                    type="messaging.received",
                    schema_version=5,
                    occurred_at=now,
                    source_adapter="fixture-messages-adapter",
                    source_account_id="fixture-messages-account",
                    owner_scope=f"private:{MEMBER}",
                    visibility_scope=f"private:{MEMBER}",
                    sensitivity="normal",
                    idempotency_key=f"attention-source-policy:{index}",
                    payload={
                        "source_channel": "imessage",
                        "from_identifier": "fixture-requester",
                        "to_identifier": "fixture-recipient",
                        "thread_id": "fixture-policy-conversation",
                        "body_text": f"Fixture source clause {index}",
                        "received_at": now,
                        "native_message_id": f"fixture-policy-message-{index}",
                        "member_id": MEMBER,
                        "provider": "fixture",
                        "provider_account_id": "fixture-messages-account",
                        "workspace": "personal",
                        "routing_scope": "member_private",
                        "routing_target_id": MEMBER,
                    },
                ),
            )
            source = client.portal.call(runtime.event_log.get, source_id)
            coordinate = source_coordinate_for_event(source)
            proposal = _emit(
                client,
                index,
                purpose="personal_request",
                independent=True,
                source_id=source_id,
                source_coordinate=coordinate,
            )
            _settle_reconciliation(client, _row(client, proposal)["proposal_event_id"])
            proposals.append(proposal)
        assert coordinate is not None
        engine_only, member_dismissed, member_handled, confirmed = proposals
        response = client.post(
            f"/api/member/v1/triage/{member_dismissed}/dismiss",
            headers=HEADERS,
            json={
                "request_id": "attention-human-dismiss",
                "expected_last_event_id": _row(client, member_dismissed)[
                    "last_event_id"
                ],
                "reason": "Member declines this particular item",
            },
        )
        assert response.status_code == 200, response.text
        response = client.post(
            f"/api/member/v1/triage/{member_handled}/manage",
            headers=HEADERS,
            json={
                "request_id": "attention-human-handled",
                "expected_last_event_id": _row(client, member_handled)["last_event_id"],
                "action": "mark_handled",
                "reason_codes": ["already_handled"],
            },
        )
        assert response.status_code == 200, response.text
        # An existing confirmation is immutable domain history; preferences
        # cannot reopen it or issue another provider operation.
        event_id = client.portal.call(
            runtime.events.write,
            EventEnvelope(
                event_at_ms=int(time.time() * 1000),
                tenant_id=tenant,
                type="triage.item.confirmed",
                schema_version=1,
                occurred_at=EventEnvelope.now_utc_iso(),
                source_adapter="fixture-confirmation",
                source_account_id=MEMBER,
                owner_scope=f"private:{MEMBER}",
                visibility_scope=f"private:{MEMBER}",
                sensitivity="normal",
                idempotency_key="attention-existing-confirmation",
                payload={
                    "proposal_id": confirmed,
                    "confirmed_at": EventEnvelope.now_utc_iso(),
                    "confirmed_by_member_id": MEMBER,
                    "materialized_event_ids": ["fixture-existing-local-task"],
                },
            ),
        )
        client.portal.call(runtime.wait_for_projection, "triage", event_id)
        confirmed_before = _row(client, confirmed)["state"]
        assert confirmed_before == "confirmed"
        for revision, mode in enumerate(("context_only", "review")):
            policy = SourcePolicyV2(
                preset="custom",
                triage_mode=mode,
                attention_mode="possibilities",
                context_mode="typed_facts",
            )
            response = client.post(
                f"/api/member/v1/settings/source-policies-v2/{coordinate.key}",
                headers=HEADERS,
                json={
                    "request_id": f"attention-source-policy-{revision}",
                    "expected_revision": revision,
                    "coordinate": coordinate.model_dump(mode="json"),
                    "preset": "custom",
                    "policy": policy.model_dump(mode="json"),
                },
            )
            assert response.status_code == 202, response.text
            assert response.json()["live_admission_changed"] is True
            _settle_reconciliation(client, response.json()["event_id"])
            if mode == "context_only":
                assert _row(client, engine_only)["state"] == "dismissed"
                assert (
                    _row(client, engine_only)["resolved_by"]
                    == "system:cross-source-reconciliation"
                )
            else:
                assert _row(client, engine_only)["state"] == "pending_review"
                assert _row(client, engine_only)["resolved_by"] is None
                assert _row(client, engine_only)["resolution_reason"] is None
            assert _row(client, member_dismissed)["state"] == "dismissed"
            assert _row(client, member_dismissed)["resolved_by"] == MEMBER
            assert _row(client, member_handled)["management_state"] == "handled"
            assert _row(client, confirmed)["state"] == confirmed_before
        page = client.get(
            "/api/member/v1/surfaces/triage?triage_lens=all_candidates&triage_limit=100",
            headers=HEADERS,
        )
        assert page.status_code == 200, page.text
        visible = {
            candidate["proposal_id"]
            for matter in page.json()["triage"]["matters"]
            for candidate in matter["candidates"]
        }
        assert engine_only in visible
        assert not {member_dismissed, member_handled, confirmed} & visible
        before = [
            (
                _row(client, proposal)["state"],
                _row(client, proposal)["management_state"],
                _row(client, proposal)["resolved_by"],
            )
            for proposal in proposals
        ]
        client.portal.call(runtime.projections.rebuild, "triage")
        after = [
            (
                _row(client, proposal)["state"],
                _row(client, proposal)["management_state"],
                _row(client, proposal)["resolved_by"],
            )
            for proposal in proposals
        ]
        assert after == before
