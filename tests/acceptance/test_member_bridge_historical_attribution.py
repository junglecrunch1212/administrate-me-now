"""Immutable member-bridge ownership survives independently of current grants.

The source chain is produced once through the real runtime and signed enrollment
helper. Each negative case replays an independently changed copy into its own
encrypted log; no enrollment dictionary is accepted as historical authority.
Run behavioral checks only in hosted CI or an authorized disposable lab.
"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import pytest

from adminme.bridge_enrollment import BridgeEnrollmentError, canonical_bytes
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.historical_attribution import audit_historical_attribution
from adminme.provider_bindings import ProviderBinding
from adminme.runtime.application import create_application
from tests.acceptance.test_cos_member_bridge_acquisition import (
    CAPABILITIES,
    MEMBER_A,
    MEMBER_B,
    MEMBER_HEADERS,
    OTHER_HEADERS,
    REQUEST_ID,
    ROOT,
    _assert_history,
    _bridge_headers,
    _configuration_settings,
    _control,
    _enable,
    _history,
    _manual_policies,
    _observation,
    _prepare_helper,
)


async def _all_events(runtime: Any) -> list[dict[str, Any]]:
    return [event async for event in runtime.event_log.read_since()]


def _failed_prior_request(client: TestClient) -> list[dict[str, Any]]:
    """Retain a real terminal wizard failure before any package/index exists."""

    runtime = client.app.state.runtime
    request_id = "history-fixture-prior-release-unavailable"

    def unavailable(**_kwargs: Any) -> None:
        raise BridgeEnrollmentError(
            "release_unavailable", "Fixture release is unavailable"
        )

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(runtime.bridge_enrollment, "prepare", unavailable)
        response = client.post(
            "/api/member/v1/settings/bridges",
            headers=MEMBER_HEADERS,
            json={
                "expected_revision": _control(client)["revision"],
                "request_id": request_id,
                "member_id": MEMBER_A,
                "device_class": "this_mac",
                "capabilities": CAPABILITIES,
            },
        )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["error"] == "release_unavailable"
    assert _control(client)["bridge_wizard"]["current"]["state"] == "failed"
    assert runtime.settings.bridge_enrollments == {}
    assert not runtime.bridge_enrollment.index_path.exists()
    assert not list(runtime.bridge_enrollment.package_dir.glob("*.adminmebridge"))
    retained = [
        event
        for event in client.portal.call(_all_events, runtime)
        if event["payload"].get("request_id") == request_id
    ]
    assert [event["payload"]["state"] for event in retained] == [
        "requested",
        "package_preparing",
        "failed",
    ]
    assert retained[-1]["payload"]["reason"] == "release_unavailable"
    collision = client.post(
        "/api/member/v1/settings/bridges",
        headers=OTHER_HEADERS,
        json={
            "expected_revision": _control(client, OTHER_HEADERS)["revision"],
            "request_id": request_id,
            "member_id": MEMBER_B,
            "device_class": "this_mac",
            "capabilities": CAPABILITIES,
        },
    )
    assert collision.status_code == 409, collision.text
    assert collision.json()["detail"]["error"] == "request_id_unavailable"
    assert _control(client, OTHER_HEADERS)["bridge_wizard"]["current"] is None
    assert runtime.settings.bridge_enrollments == {}
    assert not runtime.bridge_enrollment.index_path.exists()
    assert [
        event
        for event in client.portal.call(_all_events, runtime)
        if event["payload"].get("request_id") == request_id
    ] == retained
    return retained


@pytest.fixture(scope="module")
def bridge_history(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    path = tmp_path_factory.mktemp("immutable-bridge-history")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("ADMINME_TEST_MODE", "1")
        settings = _configuration_settings(path)
        with TestClient(
            create_application(settings), base_url="https://central-fixture.ts.net"
        ) as client:
            runtime = client.app.state.runtime
            _manual_policies(client)
            retained_failure = _failed_prior_request(client)
            helper, transfer = _prepare_helper(client, path, CAPABILITIES)
            for capability in CAPABILITIES:
                _enable(helper, capability)
            helper.finish_permission_phase()
            source_ids = {}
            for capability in CAPABILITIES:
                response = client.post(
                    "/api/bridge/ingest",
                    headers=_bridge_headers(transfer),
                    json=_observation(
                        runtime, capability, f"history-fixture-{capability}"
                    ),
                )
                assert response.status_code == 202, response.text
                source_ids[capability] = response.json()["event_id"]
            client.portal.call(
                runtime.wait_for_subscriber,
                "pipeline:pipeline:member_knowledge_indexing",
                source_ids["apple_notes"],
            )
            client.portal.call(
                runtime.wait_for_subscriber,
                "source-policy-v2:shadow",
                source_ids["apple_notes"],
            )
            diagnostics = client.portal.call(runtime.diagnostics)
            assurance = diagnostics["capabilities"]["policy:source-assurance"][
                "details"
            ]
            assert assurance["state"] == "healthy", assurance
            assert assurance["counts"]["source_facts"] == 3
            assert all(
                assurance["counts"][key] == 0
                for key in ("pending", "missing", "failed")
            )
            for action in ("pause", "resume", "revoke"):
                controlled = client.post(
                    f"/api/member/v1/settings/bridges/{REQUEST_ID}/control",
                    headers=MEMBER_HEADERS,
                    json={
                        "command_id": f"history-fixture-{action}",
                        "action": action,
                    },
                )
                assert controlled.status_code == 200, controlled.text
            report = client.portal.call(_history, runtime.event_log)
            _assert_history(report)
            events = client.portal.call(_all_events, runtime)
            assert [
                event
                for event in events
                if event["payload"].get("request_id")
                == retained_failure[0]["payload"]["request_id"]
            ] == retained_failure
            return {
                "events": events,
                "source_ids": source_ids,
                "enrollment_id": transfer["enrollment_id"],
            }


async def _replay(
    path: Path,
    events: list[dict[str, Any]],
    *,
    watermark: str | None = None,
    provider_bindings: tuple[ProviderBinding, ...] = (),
    forbid_source_content: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    log = EventLog(path, b"h" * 32)
    try:
        for event in events:
            await log.append(
                EventEnvelope(
                    **{
                        key: value
                        for key, value in event.items()
                        if key in EventEnvelope.model_fields
                    }
                )
            )
    finally:
        await log.close()
    # Ownership must still hold in the encrypted artifact after its writer and
    # originating runtime have gone away, as required by the HTR-10 export path.
    reopened = EventLog(path, b"h" * 32, read_only=True)
    original_get = reopened.get

    async def get_without_source_content(event_id: str) -> dict[str, Any] | None:
        assert event_id not in forbid_source_content
        return await original_get(event_id)

    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(reopened, "get", get_without_source_content)
            for source_id in forbid_source_content:
                coordinates = await reopened.get_bridge_attribution_coordinates(
                    source_id
                )
                assert coordinates is not None
                assert not {"body", "title", "summary", "description"} & set(
                    coordinates["payload"]
                )
            return await audit_historical_attribution(
                reopened,
                repository_root=ROOT,
                provider_bindings=provider_bindings,
                hash_key=b"fixture-member-bridge-history",
                through_event_id=watermark,
            )
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_bridge_history_is_self_contained_after_encrypted_reopen(
    tmp_path: Path, bridge_history: dict[str, Any]
) -> None:
    report = await _replay(
        tmp_path / "history.db", copy.deepcopy(bridge_history["events"])
    )
    _assert_history(report)


@pytest.mark.asyncio
async def test_retained_same_request_id_histories_are_isolated_by_member(
    tmp_path: Path, bridge_history: dict[str, Any]
) -> None:
    """A second valid owner's old request cannot replace the first's proofs.

    New production requests now refuse cross-owner ID reuse. Two independently
    generated, signed helper chains model history that could predate that guard.
    Their event links and content-free attestation hashes are rebound only to
    establish one deterministic interleaving in this disposable encrypted log.
    """

    other_root = tmp_path / "other-member-runtime"
    other_root.mkdir()
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("ADMINME_TEST_MODE", "1")
        with TestClient(
            create_application(_configuration_settings(other_root)),
            base_url="https://central-fixture.ts.net",
        ) as client:
            helper, _transfer = _prepare_helper(
                client,
                other_root,
                CAPABILITIES,
                owner_id=MEMBER_B,
                owner_headers=OTHER_HEADERS,
                device_id="history-fixture-other-member-device",
            )
            for capability in CAPABILITIES:
                _enable(helper, capability)
            helper.finish_permission_phase()
            other_events = [
                event
                for event in client.portal.call(_all_events, client.app.state.runtime)
                if event["type"]
                in {
                    "settings.bridge_enrollment_started",
                    "bridge.enrollment_progressed",
                    "bridge.source_authorized",
                    "bridge.enrolled",
                    "adapter.authenticated",
                }
            ]

    original = copy.deepcopy(bridge_history["events"])
    assert {row["tenant_id"] for row in other_events} == {original[0]["tenant_id"]}
    assert (
        next(
            row["payload"]["request_id"]
            for row in other_events
            if row["type"] == "settings.bridge_enrollment_started"
        )
        == REQUEST_ID
    )
    first_observation = next(
        index
        for index, event in enumerate(original)
        if event["event_id"] == min(bridge_history["source_ids"].values())
    )
    ordered = [
        *original[:first_observation],
        *other_events,
        *original[first_observation:],
    ]
    assert len({row["event_id"] for row in ordered}) == len(ordered)
    event_ids = {
        row["event_id"]: f"ev_{index:014d}"
        for index, row in enumerate(ordered, start=1)
    }

    def rebind(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: rebind(item) for key, item in value.items()}
        if isinstance(value, list):
            return [rebind(item) for item in value]
        if isinstance(value, str):
            for old, new in event_ids.items():
                value = value.replace(old, new)
        return value

    events = rebind(ordered)
    by_id = {event["event_id"]: event for event in events}
    receipts = {
        (event["payload"]["member_id"], event["payload"]["capability_id"]): event
        for event in events
        if event["type"] == "bridge.source_authorized"
    }
    assert len(receipts) == 6
    for event in events:
        payload = event["payload"]
        if event["type"] != "bridge.enrollment_progressed":
            continue
        if payload.get("capability_state") == "adapter_authenticated":
            receipt = receipts[(payload["member_id"], payload["capability_id"])]
            authentication_id = receipt["payload"]["authentication_event_id"]
            if authentication_id is not None:
                payload["evidence_sha256"] = hashlib.sha256(
                    canonical_bytes(by_id[authentication_id])
                ).hexdigest()
        elif payload.get("capability_state") in {"settling", "settled"}:
            source_id = event["raw_ref"].removeprefix("event:")
            payload["evidence_sha256"] = hashlib.sha256(
                canonical_bytes(by_id[source_id])
            ).hexdigest()
    _assert_history(await _replay(tmp_path / "two-members.db", events))


@pytest.mark.asyncio
async def test_historical_audit_never_reads_private_bridge_source_content(
    tmp_path: Path, bridge_history: dict[str, Any]
) -> None:
    report = await _replay(
        tmp_path / "history.db",
        copy.deepcopy(bridge_history["events"]),
        forbid_source_content=frozenset(bridge_history["source_ids"].values()),
    )
    _assert_history(report)


@pytest.mark.asyncio
@pytest.mark.parametrize("source_kind", ["apple_notes", "obsidian"])
async def test_notes_history_requires_the_approved_source_kind(
    tmp_path: Path, bridge_history: dict[str, Any], source_kind: str
) -> None:
    events = copy.deepcopy(bridge_history["events"])
    source = next(
        row
        for row in events
        if row["event_id"] == bridge_history["source_ids"]["apple_notes"]
    )
    # The original knowledge schema supports several source kinds. Keep that
    # valid legacy wire shape so the audit must inspect its scalar coordinate;
    # a v2 Apple-only payload already rejects this contradiction on append.
    source["schema_version"] = 1
    source["payload"].pop("body_format")
    source["payload"]["source_kind"] = source_kind
    report = await _replay(tmp_path / "history.db", events)
    if source_kind == "apple_notes":
        _assert_history(report)
    else:
        _assert_one_unattributed(report)


def _assert_one_unattributed(report: dict[str, Any]) -> None:
    assert report["state"] == "degraded", report
    assert report["source_event_count"] == 3
    assert report["attributed_event_count"] == 2
    assert report["unattributed_event_count"] == 1
    assert report["content_exposed"] is False
    assert report["raw_identifiers_exposed"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("capability", CAPABILITIES)
@pytest.mark.parametrize(
    "defect",
    ["member", "account", "device", "visibility", "tenant", "adapter"],
)
async def test_bridge_history_requires_exact_observed_owner_account_and_device(
    tmp_path: Path, bridge_history: dict[str, Any], capability: str, defect: str
) -> None:
    events = copy.deepcopy(bridge_history["events"])
    source = next(
        row
        for row in events
        if row["event_id"] == bridge_history["source_ids"][capability]
    )
    if defect == "member":
        source["owner_scope"] = source["visibility_scope"] = f"private:{MEMBER_B}"
        source["payload"]["member_id"] = MEMBER_B
    elif defect == "account":
        source["source_account_id"] = "provider-account:other-fixture-account"
        if "provider_account_id" in source["payload"]:
            source["payload"]["provider_account_id"] = source["source_account_id"]
    elif defect == "device":
        source["actor_identity"] = "bridge-device:" + "f" * 64
    elif defect == "visibility":
        source["visibility_scope"] = "shared:household"
    elif defect == "tenant":
        source["tenant_id"] = "other-fixture-tenant"
    else:
        source["source_adapter"] += ":unselected-fixture-adapter"
    report = await _replay(tmp_path / "history.db", events)
    _assert_one_unattributed(report)


@pytest.mark.asyncio
@pytest.mark.parametrize("capability", ["apple_calendar", "apple_reminders"])
async def test_bridge_history_requires_the_selected_container(
    tmp_path: Path, bridge_history: dict[str, Any], capability: str
) -> None:
    events = copy.deepcopy(bridge_history["events"])
    source = next(
        row
        for row in events
        if row["event_id"] == bridge_history["source_ids"][capability]
    )
    container = (
        "calendar_external_id" if capability == "apple_calendar" else "list_external_id"
    )
    source["payload"][container] = "unselected-fixture-container"
    _assert_one_unattributed(await _replay(tmp_path / "history.db", events))


@pytest.mark.asyncio
@pytest.mark.parametrize("capability", CAPABILITIES)
@pytest.mark.parametrize("actor", [None, "bridge-device:" + "f" * 64])
async def test_provider_binding_cannot_rescue_a_forged_bridge_observation(
    tmp_path: Path,
    bridge_history: dict[str, Any],
    capability: str,
    actor: str | None,
) -> None:
    events = copy.deepcopy(bridge_history["events"])
    source = next(
        row
        for row in events
        if row["event_id"] == bridge_history["source_ids"][capability]
    )
    source["actor_identity"] = actor
    matching_account = ProviderBinding(
        adapter_id="fixture-central-binding",
        kind=source["source_adapter"],
        provider_family="apple",
        owner_kind="member",
        owner_id=MEMBER_A,
        member_id=MEMBER_A,
        service_principal_id=None,
        provider_account_id=source["source_account_id"],
        workspace="personal",
        permission_scope=("read",),
        binding_provenance="fixture-reviewed-provider-account",
    )
    report = await _replay(
        tmp_path / "history.db", events, provider_bindings=(matching_account,)
    )
    _assert_one_unattributed(report)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["pause", "revoke"])
@pytest.mark.parametrize("capability", CAPABILITIES)
async def test_bridge_history_uses_authority_at_the_time_of_each_source(
    tmp_path: Path, bridge_history: dict[str, Any], action: str, capability: str
) -> None:
    events = copy.deepcopy(bridge_history["events"])
    source = next(
        row
        for row in events
        if row["event_id"] == bridge_history["source_ids"][capability]
    )
    control = next(
        row
        for row in events
        if row["type"] == "settings.bridge_enrollment_controlled"
        and row["payload"]["action"] == action
    )
    # Place the otherwise exact source strictly after this real owner control,
    # and before the following resume (when present). Backdated payload time
    # cannot resurrect an already paused or revoked acquisition authority.
    source["event_id"] = control["event_id"] + "-later-source"
    _assert_one_unattributed(await _replay(tmp_path / "history.db", events))


@pytest.mark.asyncio
@pytest.mark.parametrize("capability", CAPABILITIES)
@pytest.mark.parametrize(
    "defect",
    [
        "missing",
        "future",
        "member",
        "device",
        "login",
        "instance",
        "release",
        "capability_digest",
        "enrollment",
        "progress",
        "configuration",
        "unsigned_progress",
        "missing_permission",
        "missing_attestation",
        "configuration_digest",
    ],
)
async def test_bridge_history_requires_earlier_consistent_immutable_source_authority(
    tmp_path: Path, bridge_history: dict[str, Any], capability: str, defect: str
) -> None:
    events = copy.deepcopy(bridge_history["events"])
    receipt = next(
        row
        for row in events
        if row["type"] == "bridge.source_authorized"
        and row["payload"]["capability_id"] == capability
    )
    payload = receipt["payload"]
    if defect == "missing":
        events.remove(receipt)
    elif defect == "future":
        receipt["event_id"] = bridge_history["source_ids"][capability] + "-future-proof"
    elif defect == "member":
        payload["member_id"] = MEMBER_B
        payload["pack_config"]["member_id"] = MEMBER_B
        receipt["owner_scope"] = receipt["visibility_scope"] = f"private:{MEMBER_B}"
    elif defect in {"device", "login", "capability_digest"}:
        field = {
            "device": "device_identity_sha256",
            "login": "tailscale_identity_sha256",
            "capability_digest": "capability_set_sha256",
        }[defect]
        payload[field] = "f" * 64
    elif defect == "instance":
        payload["instance_id"] = "other-fixture-instance"
    elif defect == "release":
        payload["release_commit"] = "f" * 40
    elif defect == "enrollment":
        payload["enrollment_event_id"] = "missing-fixture-enrollment-event"
    elif defect == "progress":
        payload["progress_idempotency_key"] = "missing-fixture-signed-progress"
    elif defect == "configuration":
        payload["pack_config"]["member_id"] = MEMBER_B
    elif defect == "unsigned_progress":
        progress = next(
            row
            for row in events
            if row.get("idempotency_key") == payload["progress_idempotency_key"]
        )
        assert progress["type"] == "bridge.enrollment_progressed"
        progress["payload"]["signature_verified"] = False
    else:
        state = (
            "adapter_authenticated"
            if defect == "missing_attestation"
            else "permission_verified"
        )
        progress = next(
            row
            for row in events
            if row["type"] == "bridge.enrollment_progressed"
            and row["payload"].get("capability_id") == capability
            and row["payload"].get("capability_state") == state
        )
        if defect == "configuration_digest":
            progress["payload"]["evidence_sha256"] = "b" * 64
        else:
            events.remove(progress)
    report = await _replay(tmp_path / "history.db", events)
    if defect in {"unsigned_progress", "missing_permission", "missing_attestation"}:
        # Every source follows permission setup. An invalid shared revision
        # sequence makes the whole member chain untrustworthy from that point.
        assert report["state"] == "degraded", report
        assert report["source_event_count"] == 3
        assert report["unattributed_event_count"] == 3
    else:
        _assert_one_unattributed(report)


@pytest.mark.asyncio
@pytest.mark.parametrize("capability", ["apple_calendar", "apple_reminders"])
@pytest.mark.parametrize("defect", ["account", "container", "authentication"])
async def test_bridge_history_requires_exact_provider_configuration_and_attestation(
    tmp_path: Path, bridge_history: dict[str, Any], capability: str, defect: str
) -> None:
    events = copy.deepcopy(bridge_history["events"])
    receipt = next(
        row
        for row in events
        if row["type"] == "bridge.source_authorized"
        and row["payload"]["capability_id"] == capability
    )
    payload = receipt["payload"]
    if defect == "account":
        payload["pack_config"]["provider_account_id"] = "provider-account:other-fixture"
    elif defect == "container":
        field = (
            "calendar_identifier"
            if capability == "apple_calendar"
            else "target_list_identifier"
        )
        payload["pack_config"][field] = "unselected-fixture-container"
        payload["pack_config"]["allowed_container_ids"] = [
            "unselected-fixture-container"
        ]
    else:
        assert payload["authentication_event_id"] is not None
        payload["authentication_event_id"] = "missing-fixture-authentication-event"
    _assert_one_unattributed(await _replay(tmp_path / "history.db", events))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect",
    [
        "missing_request",
        "request_owner",
        "missing_package",
        "missing_pairing",
        "future_pairing",
        "paired_device",
    ],
)
async def test_source_receipts_alone_cannot_replace_the_original_owner_enrollment(
    tmp_path: Path, bridge_history: dict[str, Any], defect: str
) -> None:
    events = copy.deepcopy(bridge_history["events"])
    if defect in {"missing_request", "request_owner"}:
        request = next(
            row
            for row in events
            if row["type"] == "settings.bridge_enrollment_started"
            and row["payload"]["request_id"] == REQUEST_ID
        )
        if defect == "missing_request":
            events.remove(request)
        else:
            request["payload"]["requested_by_member_id"] = MEMBER_B
    elif defect == "missing_package":
        events = [
            row
            for row in events
            if not (
                row["type"] == "bridge.enrollment_progressed"
                and row["payload"]["state"] == "package_ready"
            )
        ]
    else:
        paired = next(row for row in events if row["type"] == "bridge.enrolled")
        if defect == "missing_pairing":
            events.remove(paired)
        elif defect == "future_pairing":
            paired["event_id"] = (
                max(bridge_history["source_ids"].values()) + "-future-pairing"
            )
            for receipt in events:
                if receipt["type"] == "bridge.source_authorized":
                    receipt["payload"]["enrollment_event_id"] = paired["event_id"]
                    receipt["raw_ref"] = f"event:{paired['event_id']}"
        else:
            paired["payload"]["bridge_node_id"] = "other-fixture-device"
    report = await _replay(tmp_path / "history.db", events)
    assert report["state"] == "degraded", report
    assert report["source_event_count"] == 3
    assert report["attributed_event_count"] == 0
    assert report["unattributed_event_count"] == 3
