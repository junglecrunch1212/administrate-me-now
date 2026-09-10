"""HTML normalization through the production source-to-Triage composition.

The existing deterministic language transport is fixed fixture behavior. These
tests prove its inputs, exact grounding and displayed evidence, not actual-model
classification accuracy. No provider or private mailbox is involved.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adminme.events.envelope import EventEnvelope
from adminme.message_context import MAX_MESSAGE_CHARS, authored_message_text
from adminme.runtime.application import RuntimeSettings, create_application


ROOT = Path(__file__).resolve().parents[2]
MEMBER = "fixture-html-adult"  # fixture:tenant_data:ok
ACCOUNT = "fixture-html-account"
HEADERS = {"Tailscale-User-Login": "html-fixture.ts.net"}
AT = "2030-10-01T12:00:00Z"
PAYMENT = "Please pay the fixture invoice of $40 & keep the receipt."
PREPARATION = "Please complete the required preparation form before arrival."
VISIBLE = PAYMENT + "\n" + PREPARATION


def _settings(instance: Path) -> RuntimeSettings:
    return RuntimeSettings.credential_free(
        instance,
        encryption_key=b"h" * 32,
        repository_root=ROOT,
        member_identities={"html-fixture.ts.net": MEMBER},
        member_roles={MEMBER: "principal"},
        fixture_member_id=MEMBER,
        adapter_config={"adapter_supervisor_port": 0, "adapters": []},
        deterministic_openclaw_fixture=True,
        reconciliation_admission_enabled=True,
        human_triage_management_enabled=True,
    )


def _large_head() -> str:
    return (
        "<!doctype html><html><head><title>Provider template metadata</title><style>"
        + (
            ".fixture { color: red; content: 'Please pay the hidden CSS charge.'; }"
            * 500
        )
        + "</style><script>const hidden = 'Please pay the hidden script charge.';</script>"
        "</head><body><!-- Please pay the hidden comment charge. -->"
    )


def _source(runtime: Any, name: str, field: str, body: str) -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=1_917_086_400_000,
        tenant_id=runtime.settings.instance.tenant_id,
        type="messaging.received",
        schema_version=4,
        occurred_at=AT,
        source_adapter="fixture-html-source",
        source_account_id=ACCOUNT,
        owner_scope=f"private:{MEMBER}",
        visibility_scope=f"private:{MEMBER}",
        idempotency_key=f"fixture-html-source:{name}",
        payload={
            "source_channel": "gmail",
            "from_identifier": "sender@example.invalid",
            "to_identifier": "member@example.invalid",
            "thread_id": f"fixture-html-thread:{name}",
            "subject": None,
            field: body,
            "received_at": AT,
            "attachments": [],
            "native_message_id": f"fixture-html-message:{name}",
            "member_id": MEMBER,
            "provider": "gmail",
            "provider_account_id": ACCOUNT,
            "workspace": "personal",
            "to_identifiers": ["member@example.invalid"],
            "provider_labels": ["INBOX"],
            "unread": True,
            "archived": False,
        },
    )


async def _events(runtime: Any, event_type: str) -> list[dict[str, Any]]:
    return [event async for event in runtime.event_log.read_since(types=[event_type])]


@pytest.mark.parametrize("field", ["body_html", "body_text"])
def test_large_html_template_preserves_actions_exact_quotes_and_source_facts(
    tmp_path: Path,
    field: str,
) -> None:
    app = create_application(_settings(tmp_path))
    with TestClient(app) as client:
        assert client.portal is not None
        runtime = app.state.runtime
        calls: list[dict[str, Any]] = []
        fixed_fixture = runtime.run_skill_fn

        async def record_fixed_fixture(
            skill_id: str, inputs: dict[str, Any], context: Any
        ) -> Any:
            if skill_id == "skill:classify_message_nature":
                calls.append(
                    {
                        "source_event_id": context.triggering_event_id,
                        "inputs": copy.deepcopy(inputs),
                    }
                )
            return await fixed_fixture(skill_id, inputs, context)

        runtime.run_skill_fn = record_fixed_fixture
        runtime.pipelines._run_skill_fn = record_fixed_fixture
        body = _large_head() + (
            "<p>Please pay the fixture invoice of <b>$40</b> &amp; keep the receipt.</p>"
            "<noscript><p>Please complete the required preparation form before arrival.</p></noscript></body></html>"
        )
        assert body.index("<p>Please pay") > MAX_MESSAGE_CHARS
        source = _source(runtime, "actions", field, body)
        original_payload = copy.deepcopy(source.payload)
        source_id = client.portal.call(runtime.events.write, source)
        client.portal.call(
            runtime.wait_for_subscriber, "pipeline:pipeline:noise_filtering", source_id
        )
        [call] = [call for call in calls if call["source_event_id"] == source_id]
        assert call["inputs"]["body_text"] == VISIBLE
        pack = call["inputs"]["context_pack"]
        [current] = [
            message
            for message in pack["messages"]
            if message["source_id"] == pack["current_source_id"]
        ]
        assert current["body_text"] == VISIBLE
        assert current["text_truncated"] is False
        assert current["occurred_at"] == AT
        assert "hidden" not in repr(call["inputs"])
        stored = client.portal.call(runtime.event_log.get, source_id)
        assert stored["payload"] == original_payload
        assert source.payload == original_payload
        normalized = authored_message_text(stored["payload"])
        assert normalized.canonical_source_text == VISIBLE
        assert normalized.authored_span == (0, len(VISIBLE))
        proposals = [
            event
            for event in client.portal.call(_events, runtime, "triage.item.proposed")
            if event["payload"]["source_event_id"] == source_id
        ]
        assert len(proposals) == 2, proposals
        assert {event["payload"]["target_kind"] for event in proposals} == {"task"}
        assert len({event["payload"]["candidate_key"] for event in proposals}) == 2
        quotes = set()
        for proposal in proposals:
            interpretation = proposal["payload"]["interpretation"]
            assert interpretation["obligation_assessment"]["evidence_verified"] is True
            for reference in interpretation["evidence_refs"]:
                assert reference["source_event_id"] == source_id
                quote = reference["quote"]
                start = normalized.text.index(quote)
                assert (
                    normalized.canonical_source_text[start : start + len(quote)]
                    == quote
                )
                quotes.add(quote)
            client.portal.call(
                runtime.wait_for_subscriber,
                "triage-reconciliation:admission",
                proposal["event_id"],
            )
            decisions = [
                event
                for event in client.portal.call(
                    runtime.event_log.get_by_causation, proposal["event_id"]
                )
                if event["type"] == "triage.reconciliation.decided"
            ]
            assert decisions and decisions[-1]["payload"]["provider_effects"] is False
            client.portal.call(
                runtime.wait_for_projection, "triage", decisions[-1]["event_id"]
            )
        assert quotes == {PAYMENT.rstrip("."), PREPARATION.rstrip(".")}
        response = client.get(
            "/api/member/v1/surfaces/triage?triage_lens=all_candidates&triage_limit=100",
            headers=HEADERS,
        )
        assert response.status_code == 200
        items = {item["proposal_id"]: item for item in response.json()["items"]}
        for proposal in proposals:
            item = items[proposal["payload"]["proposal_id"]]
            assert item["source_context"]["evidence"]["exactly_grounded"] is True

        # An HTML quote boundary at offset zero remains terminally empty,
        # including text after the container closes. The language fixture is
        # never asked to reinterpret that forwarded material as a new action.
        for name, container in [
            ("forwarded", '<div class="gmail_quote">'),
            ("uncertain", '<div class="vendor-reply">'),
        ]:
            quoted = _source(
                runtime,
                name,
                field,
                _large_head()
                + container
                + VISIBLE
                + "</div><p>Please pay another old charge.</p></body></html>",
            )
            quoted_id = client.portal.call(runtime.events.write, quoted)
            client.portal.call(
                runtime.wait_for_subscriber,
                "pipeline:pipeline:noise_filtering",
                quoted_id,
            )
            assert not any(call["source_event_id"] == quoted_id for call in calls)
            [assessment] = [
                event
                for event in client.portal.call(
                    _events, runtime, "triage.intent.features_extracted"
                )
                if event["payload"]["source_event_id"] == quoted_id
            ]
            assert assessment["payload"]["assessment_outcome"] == "no_candidate"
            assert assessment["payload"]["candidates"] == []

        literal = "Please compare the amount < 500 and > 100 with <reference>."
        literal_id = client.portal.call(
            runtime.events.write, _source(runtime, "literal", "body_text", literal)
        )
        client.portal.call(
            runtime.wait_for_subscriber, "pipeline:pipeline:noise_filtering", literal_id
        )
        [literal_call] = [
            call for call in calls if call["source_event_id"] == literal_id
        ]
        assert literal_call["inputs"]["body_text"] == literal
        for event_type in (
            "external_sync.write_requested",
            "external_sync.write_completed",
            "external_sync.write_failed",
        ):
            assert client.portal.call(_events, runtime, event_type) == []
