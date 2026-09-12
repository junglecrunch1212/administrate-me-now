"""Synthetic predecessor worker, executed only by hosted acceptance tests.

Only the external llm-task transport is substituted. The caller selects a
reviewed repository archive; no instance, credential or provider is discovered.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

import adminme.lib.skill_runner.wrapper as wrapper
import adminme.runtime.application as application
from adminme.events.envelope import EventEnvelope


MEMBER = "fixture-revision-member"  # fixture:tenant_data:ok
OTHER = "fixture-revision-other"  # fixture:tenant_data:ok
ACCOUNT = "revision-fixture@example.invalid"
ADAPTER = "fixture-revision-gmail"
ADAPTER_KIND = "messaging-gmail-api"
KEY = b"v" * 32
AUTH = {"Tailscale-User-Login": "revision-member.ts.net"}
OTHER_AUTH = {"Tailscale-User-Login": "revision-other.ts.net"}
SOURCE_TYPES = ("messaging.received", "messaging.sent")
MODEL_TYPES = ("skill.call.recorded", "skill.call.failed", "skill.call.suppressed")


def settings(instance: Path, root: Path, *, selected=None, dataset="a" * 32):
    config_dir = instance / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "adapter_supervisor_port": 0,
        "disposable_test_acquisition": {
            "contract": "disposable-test-acquisition-v1",
            "adapter_id": ADAPTER,
            "member_id": MEMBER,
            "provider_account_id": ACCOUNT,
            "message_ids": selected
            if selected is not None
            else [f"message-{i}" for i in range(32)],
        },
        "adapters": [
            {
                "id": ADAPTER,
                "kind": ADAPTER_KIND,
                "enabled": False,
                "config": {
                    "member_id": MEMBER,
                    "provider_account_id": ACCOUNT,
                    "user_email": ACCOUNT,
                    "workspace": "personal",
                    "service_account_key_ref": "op://fixture/gmail/credentials",
                    "permission_scope": [
                        "https://www.googleapis.com/auth/gmail.readonly"
                    ],
                    "binding_provenance": "service-account-delegation",
                    "ownership_attested": True,
                },
            }
        ],
    }
    (config_dir / "adapters.json").write_text(json.dumps(config))
    (config_dir / "disposable-test-dataset.json").write_text(
        json.dumps(
            {
                "contract": "disposable-test-dataset-v1",
                "dataset_id": dataset,
                "reset_manifest_sha256": "b" * 64,
                "acquisition_contract": "disposable-test-acquisition-v1",
            }
        )
    )
    return application.RuntimeSettings.credential_free(
        instance,
        encryption_key=KEY,
        repository_root=root,
        member_identities={
            AUTH["Tailscale-User-Login"]: MEMBER,
            OTHER_AUTH["Tailscale-User-Login"]: OTHER,
        },
        member_roles={MEMBER: "principal", OTHER: "principal"},
        adapter_config=config,
        deterministic_openclaw_fixture=True,
        fixture_member_id=MEMBER,
        reconciliation_admission_enabled=True,
        human_triage_management_enabled=True,
    )


def source(
    config,
    index: int,
    *,
    thread=None,
    owner=MEMBER,
    account=ACCOUNT,
    adapter=ADAPTER_KIND,
):
    occurred = f"2034-02-01T10:{index // 60:02}:{index % 60:02}Z"
    return EventEnvelope(
        event_at_ms=time.time_ns() // 1_000_000,
        tenant_id=config.instance.tenant_id,
        occurred_at=occurred,
        type="messaging.received",
        schema_version=5,
        source_adapter=adapter,
        source_account_id=account,
        owner_scope=f"private:{owner}",
        visibility_scope=f"private:{owner}",
        idempotency_key=f"fixture-revision-source:{index}:{owner}:{account}",
        payload={
            "member_id": owner,
            "provider": "gmail",
            "provider_account_id": account,
            "workspace": "personal",
            "routing_scope": "member_private",
            "routing_target_id": owner,
            "source_channel": "gmail",
            "native_message_id": f"message-{index}",
            "thread_id": thread or f"fixture-revision-thread-{index}",
            "subject": f"Fixture revision source {index}",
            "body_text": f"Please complete the fixture preparation form for activity {index}.",
            "body_html": None,
            "received_at": occurred,
            "attachments": [],
            "from_identifier": "no-reply@example.invalid",
            "to_identifier": ACCOUNT,
            "to_identifiers": [ACCOUNT],
            "provider_labels": ["CATEGORY_UPDATES"],
        },
    )


class Model:
    def __init__(self):
        self.mode = "initial"
        self.calls: list[tuple[str, int]] = []

    async def respond(self, request, args):
        inputs = args["input"]
        index = int(inputs["subject"].rsplit(" ", 1)[-1])
        self.calls.append((self.mode, index))
        if self.mode == "forbid":
            raise AssertionError("unchanged restart or predecessor invoked a model")
        if self.mode == "fail":
            raise httpx.ReadTimeout(
                "synthetic source-revision timeout", request=request
            )
        alias = inputs["context_pack"]["current_source_id"]
        current = next(
            value
            for value in inputs["context_pack"]["messages"]
            if value["source_id"] == alias
        )
        quote = current["body_text"]
        candidate = {
            "target_kind": "task",
            "title": quote,
            "evidence_source_id": alias,
            "evidence_quote": quote,
            "candidate": {},
            "confidence": 0.9,
            "reasons": ["fixture_language_result"],
            "action_owner": "member",
            "lifecycle_state": "open",
            "compact_summary": quote,
            "case_summary": None,
            "why_member": None,
            "next_step": None,
            "thread_state": "waiting_on_member",
            "unresolved_questions": [],
            "related_source_ids": [],
            "obligation_assessment": {
                "purpose": "preparation",
                "actionability": "required",
                "condition": None,
                "condition_state": "not_applicable",
                "beneficiary": None,
                "date_role": "none",
                "qualifying_evidence": [],
            },
        }
        output = {
            "assessment_contract": "personal-obligation-assessment-v1",
            "classification": "transactional",
            "confidence": 0.9,
            "reasons": ["fixture_language_result"],
            "reply_signal": "not_required",
            "automated": True,
            "transactional_due": False,
            "security_code": False,
            "person_to_person": False,
            "deadline": None,
            "candidates": [candidate] if self.mode == "revision" or index == 1 else [],
        }
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "details": {
                        "json": output,
                        "provider": "fixture",
                        "model": "revision-model",
                    },
                    "provider": "fixture",
                    "model": "revision-model",
                },
            },
            request=request,
        )


def runtime_factory(model: Model):
    def build(*, event_sink, event_log, raw_events_dir):
        class Client:
            async def post(self, url, *, json, headers):
                assert json["tool"] == "llm-task"
                assert json["args"]["schema"]["title"].startswith(
                    "classify_message_nature"
                )
                return await model.respond(
                    httpx.Request("POST", url, json=json), json["args"]
                )

        return wrapper._Runtime(
            event_sink=event_sink,
            event_log=event_log,
            raw_events_dir=raw_events_dir,
            httpx_client_factory=Client,
            gateway_url="http://fixture.invalid/tools/invoke",
            gateway_token="fixture-token-never-used",
        )

    return build


async def events(runtime):
    return [event async for event in runtime.event_log.read_since()]


async def settle(runtime):
    deadline = asyncio.get_running_loop().time() + 60
    previous = None
    stable = 0
    while asyncio.get_running_loop().time() < deadline:
        statuses = [
            await runtime.bus.subscriber_status(name)
            for name in runtime.bus._subscribers
        ]
        assert statuses and not any(item["degraded"] for item in statuses), statuses
        latest = await runtime.event_log.latest_event_id()
        # Workbook regeneration is scheduled after its subscriber checkpoint
        # advances. Wait for that real work before freezing all-event history.
        workbooks_idle = runtime.xlsx_projection is not None and all(
            task.done() for task in runtime.xlsx_projection._pending_tasks.values()
        )
        stable = (
            stable + 1
            if latest == previous
            and all(item["lag_count"] == 0 for item in statuses)
            and workbooks_idle
            else 0
        )
        if stable >= 5:
            return
        previous = latest
        await asyncio.sleep(0.02)
    raise AssertionError(f"synthetic revision consumers did not settle: {statuses}")


def dismiss(client, source_id):
    response = client.get(
        "/api/member/v1/surfaces/triage?triage_lens=all_candidates&triage_limit=100",
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    [item] = [
        value
        for value in response.json()["items"]
        if value["source_event_id"] == source_id
    ]
    response = client.post(
        f"/api/member/v1/triage/{item['proposal_id']}/dismiss",
        headers=AUTH,
        json={
            "request_id": "fixture-revision-member-dismissal",
            "expected_last_event_id": item["last_event_id"],
            "reason": "Already handled",
        },
    )
    assert response.status_code == 200, response.text
    client.portal.call(settle, client.app.state.runtime)
    return item["proposal_id"], response.json()["event_id"]


def snapshot(client):
    runtime = client.app.state.runtime
    all_events = client.portal.call(events, runtime)
    assert not [
        event
        for event in all_events
        if event["type"].startswith("external_sync.write_")
    ]
    assert not runtime.adapters.inventory()
    response = client.get(
        "/api/member/v1/surfaces/triage?triage_lens=all_candidates&triage_limit=100",
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    return {
        "events": all_events,
        "member_surface": response.json(),
        "consumers": {
            name: client.portal.call(runtime.bus.subscriber_status, name)
            for name in runtime.bus._subscribers
        },
    }


def main():
    root, instance, result, mode = (
        Path(sys.argv[1]),
        Path(sys.argv[2]),
        Path(sys.argv[3]),
        sys.argv[4],
    )
    assert Path(application.__file__).resolve().is_relative_to(root.resolve())
    assert Path(wrapper.__file__).resolve().is_relative_to(root.resolve())
    config = settings(instance, root)
    model = Model()
    if mode != "prepare":
        model.mode = "forbid"
    application.build_fixture_runtime = runtime_factory(model)
    with TestClient(application.create_application(config)) as client:
        runtime = client.app.state.runtime
        client.portal.call(settle, runtime)
        if mode == "prepare":
            ids = []
            for index in range(2):
                ids.append(
                    client.portal.call(runtime.events.write, source(config, index))
                )
                client.portal.call(settle, runtime)
            dismiss(client, ids[1])
        captured = snapshot(client)
        captured["model_calls"] = model.calls
        result.write_text(json.dumps(captured))


if __name__ == "__main__":
    main()
