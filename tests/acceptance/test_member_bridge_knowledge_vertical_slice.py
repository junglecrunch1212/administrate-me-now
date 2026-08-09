"""Gate 6: enrolled member bridge to private searchable domain knowledge."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adminme.adapters.cursor_store import AdapterCursorStore
from adminme.events.envelope import EventEnvelope
from adminme.runtime.application import RuntimeSettings, create_application
from adminme_bridge.adapters.base import BridgeContext
from adminme_bridge.connector_packs.apple_notes import AppleNotesAdapter
from adminme_bridge.connector_packs.obsidian import ObsidianAdapter
from adminme_bridge.connector_packs.voice_memos import VoiceMemosAdapter
from adminme_bridge.ingest_client import IngestClient
from adminme_bridge.knowledge_sources import KnowledgeDocument


REPO_ROOT = Path(__file__).resolve().parents[2]
KEY_A = b"k" * 32
KEY_B = b"z" * 32
MEMBER_A = "member-knowledge-a"
MEMBER_B = "member-knowledge-b"
CHILD = "member-knowledge-child"
AMBIENT = "member-knowledge-ambient"
DEVICE = "member-knowledge-device"
LOGIN_A = "member-a.fixture.ts.net"
AUTH_A = {"Tailscale-User-Login": LOGIN_A}


class _TestClientAsyncHttp:
    """Deterministic network boundary that still executes ``IngestClient``."""

    def __init__(self, client: TestClient) -> None:
        self.client = client

    async def post(self, url: str, **kwargs: Any) -> Any:
        path = "/" + url.split("/", 3)[-1]
        return await asyncio.to_thread(self.client.post, path, **kwargs)

    async def aclose(self) -> None:
        return None


class _MutableProvider:
    def __init__(self, documents: list[KnowledgeDocument]) -> None:
        self.items = documents

    async def documents(self) -> list[KnowledgeDocument]:
        return list(self.items)


def _settings(instance_dir: Path, key: bytes) -> RuntimeSettings:
    instance_dir.mkdir()
    return RuntimeSettings.credential_free(
        instance_dir,
        encryption_key=key,
        repository_root=REPO_ROOT,
        member_identities={
            LOGIN_A: MEMBER_A,
            "member-b.fixture.ts.net": MEMBER_B,
            "child.fixture.ts.net": CHILD,
            "ambient.fixture.ts.net": AMBIENT,
            "device.fixture.ts.net": DEVICE,
        },
        member_roles={
            MEMBER_A: "principal",
            MEMBER_B: "principal",
            CHILD: "child",
            AMBIENT: "ambient",
            DEVICE: "device",
        },
    )


def _wait_for(
    client: TestClient,
    path: str,
    headers: dict[str, str],
    predicate: Any,
) -> dict[str, Any]:
    deadline = time.monotonic() + 8
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(path, headers=headers)
        if response.status_code == 200:
            latest = response.json()
            if predicate(latest):
                return latest
        time.sleep(0.03)
    raise AssertionError(f"timed out waiting for {path}: {latest}")


def _projection_rows(runtime: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    knowledge = [
        dict(row)
        for row in runtime.projections.connection("member_knowledge").execute(
            "SELECT * FROM member_knowledge ORDER BY knowledge_id"
        )
    ]
    vectors = [
        dict(row)
        for row in runtime.projections.connection("vector_search").execute(
            "SELECT * FROM embeddings_meta ORDER BY embedding_id"
        )
    ]
    return knowledge, vectors


def _wait_for_causation(
    client: TestClient,
    log: Any,
    causation_id: str,
    expected_types: tuple[str, ...],
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + 8
    derived: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        derived = client.portal.call(log.get_by_causation, causation_id)
        if [event["type"] for event in derived] == list(expected_types):
            return derived
        time.sleep(0.03)
    raise AssertionError(f"timed out waiting for causation {causation_id}")


def _wait_for_vector_source(runtime: Any, source_event_id: str) -> None:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        row = (
            runtime.projections.connection("vector_search")
            .execute(
                "SELECT source_event_id FROM embeddings_meta WHERE source_event_id = ?",
                (source_event_id,),
            )
            .fetchone()
        )
        if row is not None:
            return
        time.sleep(0.03)
    raise AssertionError(f"timed out waiting for vector source {source_event_id}")


async def _knowledge_history(log: Any, knowledge_id: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    async for event in log.read_since(
        types=[
            "member_knowledge.note_added",
            "member_knowledge.note_updated",
            "member_knowledge.note_corrected",
        ]
    ):
        if event["payload"].get("knowledge_id") == knowledge_id:
            events.append(event)
    return events


def test_member_bridge_knowledge_is_private_searchable_and_replayable(
    tmp_path: Path,
) -> None:
    settings_a = _settings(tmp_path / "household-a", KEY_A)
    settings_b = _settings(tmp_path / "household-b", KEY_B)
    app_a = create_application(settings_a)
    app_b = create_application(settings_b)

    normal_body = "The cobalt folder contains the synthetic garden plan."
    privileged_body = "Privileged synthetic health note that must never embed."
    provider = _MutableProvider(
        [
            KnowledgeDocument(
                external_id="Garden Plan.md",
                title="Garden Plan",
                body=normal_body,
                captured_at=datetime(2036, 2, 1, 12, tzinfo=timezone.utc),
            ),
            KnowledgeDocument(
                external_id="Private.txt",
                title="Private",
                body=privileged_body,
                captured_at=datetime(2036, 2, 1, 13, tzinfo=timezone.utc),
                sensitivity="privileged",
            ),
        ]
    )
    cursor_store = AdapterCursorStore(tmp_path / "bridge-a-cursors.db")
    adapter = AppleNotesAdapter(provider=provider, cursor_store=cursor_store)

    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        ingest_client = IngestClient(
            central_endpoint_url="http://administrateme.fixture",
            tailscale_identity_header=LOGIN_A,
            client=_TestClientAsyncHttp(client_a),  # type: ignore[arg-type]
            sleep=lambda _seconds: asyncio.sleep(0),
        )
        context = BridgeContext(
            member_id=MEMBER_A,
            ingest_client=ingest_client,
            correlation_id_factory=lambda: "corr-member-knowledge",
            now_factory=lambda: datetime(2036, 2, 1, 14, tzinfo=timezone.utc),
            tenant_id=settings_a.instance.tenant_id,
        )

        event_ids = client_a.portal.call(adapter.scan_once, context, provider)
        assert len(event_ids) == 2
        assert client_a.portal.call(adapter.scan_once, context, provider) == []

        listed = _wait_for(
            client_a,
            "/api/capture/v1/knowledge",
            AUTH_A,
            lambda payload: len(payload.get("items", [])) == 2,
        )
        by_title = {item["title"]: item for item in listed["items"]}
        normal = by_title["Garden Plan"]
        privileged = by_title["Private"]
        assert normal["owner_scope"] == f"private:{MEMBER_A}"
        assert normal["visibility_scope"] == f"private:{MEMBER_A}"
        assert privileged["sensitivity"] == "privileged"
        assert normal["source_adapter"] == "knowledge-apple-notes"
        assert normal["source_account_id"] == "apple_notes"

        searched = _wait_for(
            client_a,
            "/api/capture/v1/search?q=cobalt%20garden",
            AUTH_A,
            lambda payload: len(payload.get("items", [])) == 1,
        )
        assert (
            searched["items"][0]["knowledge"]["knowledge_id"] == normal["knowledge_id"]
        )
        assert searched["items"][0]["provenance"]["source_adapter"] == (
            "pipeline:member_knowledge_indexing"
        )

        runtime_a = app_a.state.runtime
        vector_rows = [
            dict(row)
            for row in runtime_a.projections.connection("vector_search").execute(
                "SELECT * FROM embeddings_meta"
            )
        ]
        assert len(vector_rows) == 1
        assert vector_rows[0]["linked_id"] == normal["knowledge_id"]
        derived = _wait_for_causation(
            client_a,
            runtime_a.event_log,
            event_ids[1],
            (
                "embedding.revoked",
                "member_knowledge.index_skipped",
                "triage.intent.features_extracted",
            ),
        )
        assert [event["type"] for event in derived] == [
            "embedding.revoked",
            "member_knowledge.index_skipped",
            "triage.intent.features_extracted",
        ]
        assert derived[-1]["payload"]["assessment_outcome"] == "quarantined"
        assert derived[-1]["payload"]["candidates"] == []

        # Cross-owner, child, ambient/device, view-as, and generic-memory paths
        # cannot reveal A's private material.
        for login in (
            "member-b.fixture.ts.net",
            "child.fixture.ts.net",
        ):
            response = client_a.get(
                f"/api/capture/v1/knowledge/{normal['knowledge_id']}",
                headers={"Tailscale-User-Login": login},
            )
            assert response.status_code == 404
        for login in ("ambient.fixture.ts.net", "device.fixture.ts.net"):
            response = client_a.get(
                "/api/capture/v1/knowledge",
                headers={"Tailscale-User-Login": login},
            )
            assert response.status_code == 403
        assert (
            client_a.get(
                "/api/capture/v1/knowledge",
                headers={
                    **AUTH_A,
                    "X-AdminMe-View-Member": MEMBER_B,
                },
            ).status_code
            == 403
        )
        assert not any(
            "memory" in str(getattr(route, "path", "")).casefold()
            for route in app_a.routes
        )
        assert not any(
            str(getattr(route, "path", "")) == "/api/member/v1/external-sync/commands"
            for route in app_a.routes
        )

        # A bridge cannot claim another owner or another running tenant.
        mismatch = EventEnvelope(
            event_at_ms=1_800_000_000_000,
            tenant_id=settings_a.instance.tenant_id,
            type="member_knowledge.note_added",
            schema_version=1,
            occurred_at="2027-01-15T08:00:00Z",
            source_adapter="fixture-claim",
            source_account_id="fixture",
            owner_scope=f"private:{MEMBER_B}",
            visibility_scope=f"private:{MEMBER_B}",
            sensitivity="normal",
            payload={
                "knowledge_id": "claim-mismatch",
                "member_id": MEMBER_B,
                "title": "Denied",
                "body": "denied",
                "source_kind": "fixture",
                "source_external_id": "denied",
                "content_sha256": "0" * 64,
                "captured_at": "2027-01-15T08:00:00Z",
            },
        )
        assert (
            client_a.post(
                "/api/bridge/ingest",
                json=mismatch.model_dump(mode="json"),
                headers=AUTH_A,
            ).status_code
            == 403
        )
        tenant_claim = mismatch.model_copy(
            update={
                "owner_scope": f"private:{MEMBER_A}",
                "visibility_scope": f"private:{MEMBER_A}",
            }
        )
        assert (
            client_b.post(
                "/api/bridge/ingest",
                json=tenant_claim.model_dump(mode="json"),
                headers=AUTH_A,
            ).status_code
            == 403
        )
        assert (
            client_b.get("/api/capture/v1/knowledge", headers=AUTH_A).json()["items"]
            == []
        )

        # A changed source record becomes a correction; history remains in the
        # encrypted log and the stable vector row is deterministically replaced.
        corrected_body = "The cobalt folder now contains the revised orchard plan."
        provider.items[0] = KnowledgeDocument(
            external_id="Garden Plan.md",
            title="Garden Plan revised",
            body=corrected_body,
            captured_at=datetime(2036, 2, 2, 12, tzinfo=timezone.utc),
        )
        correction_ids = client_a.portal.call(adapter.scan_once, context, provider)
        assert len(correction_ids) == 1
        corrected = _wait_for(
            client_a,
            f"/api/capture/v1/knowledge/{normal['knowledge_id']}",
            AUTH_A,
            lambda payload: payload.get("body") == corrected_body,
        )
        assert corrected["supersedes_event_id"] == event_ids[0]
        history = client_a.portal.call(
            _knowledge_history, runtime_a.event_log, normal["knowledge_id"]
        )
        assert [event["type"] for event in history] == [
            "member_knowledge.note_added",
            "member_knowledge.note_corrected",
        ]

        corrected_derivatives = _wait_for_causation(
            client_a,
            runtime_a.event_log,
            correction_ids[0],
            (
                "embedding.generated",
                "skill.call.failed",
                "triage.intent.features_extracted",
            ),
        )
        assert [event["type"] for event in corrected_derivatives] == [
            "embedding.generated",
            "skill.call.failed",
            "triage.intent.features_extracted",
        ]
        assert corrected_derivatives[-2]["payload"]["skill_name"] == (
            "skill:extract_note_semantics"
        )
        assert corrected_derivatives[-1]["payload"]["assessment_outcome"] == "failed"
        _wait_for_vector_source(runtime_a, correction_ids[0])

        before_rebuild = _projection_rows(runtime_a)
        client_a.portal.call(runtime_a.projections.rebuild, "member_knowledge")
        client_a.portal.call(runtime_a.projections.rebuild, "vector_search")
        after_rebuild = _projection_rows(runtime_a)
        assert after_rebuild == before_rebuild

        # The encrypted event database does not contain either plaintext body.
        raw_db = settings_a.instance.event_log_path.read_bytes()
        assert normal_body.encode() not in raw_db
        assert privileged_body.encode() not in raw_db
        assert corrected_body.encode() not in raw_db

        client_a.portal.call(ingest_client.aclose)

    cursor_store.close()

    # Restart opens the same encrypted history and preserves private/search state.
    restarted = create_application(settings_a)
    with TestClient(restarted) as client:
        listed = _wait_for(
            client,
            "/api/capture/v1/knowledge",
            AUTH_A,
            lambda payload: len(payload.get("items", [])) == 2,
        )
        assert {item["title"] for item in listed["items"]} == {
            "Garden Plan revised",
            "Private",
        }
        result = client.get(
            "/api/capture/v1/search?q=revised%20orchard", headers=AUTH_A
        )
        assert result.status_code == 200
        assert len(result.json()["items"]) == 1


@pytest.mark.parametrize(
    ("adapter_class", "expected_source", "expected_sensitivity"),
    [
        (AppleNotesAdapter, "apple_notes", "normal"),
        (VoiceMemosAdapter, "apple_voice_memos", "sensitive"),
        (ObsidianAdapter, "obsidian", "sensitive"),
    ],
)
def test_every_retained_member_source_reaches_the_real_sink(
    tmp_path: Path,
    adapter_class: type[AppleNotesAdapter],
    expected_source: str,
    expected_sensitivity: str,
) -> None:
    settings = _settings(tmp_path / f"source-{expected_source}", KEY_A)
    app = create_application(settings)
    provider = _MutableProvider(
        [
            KnowledgeDocument(
                external_id=f"{expected_source}.md",
                title=f"Fixture {expected_source}",
                body=f"Deterministic provider boundary for {expected_source}.",
                captured_at=datetime(2036, 3, 1, 12, tzinfo=timezone.utc),
            )
        ]
    )
    cursors = AdapterCursorStore(tmp_path / f"{expected_source}-cursor.db")
    adapter = adapter_class(provider=provider, cursor_store=cursors)
    with TestClient(app) as client:
        ingest_client = IngestClient(
            central_endpoint_url="http://administrateme.fixture",
            tailscale_identity_header=LOGIN_A,
            client=_TestClientAsyncHttp(client),  # type: ignore[arg-type]
            sleep=lambda _seconds: asyncio.sleep(0),
        )
        context = BridgeContext(
            member_id=MEMBER_A,
            ingest_client=ingest_client,
            correlation_id_factory=lambda: f"corr-{expected_source}",
            now_factory=lambda: datetime(2036, 3, 1, 12, tzinfo=timezone.utc),
            tenant_id=settings.instance.tenant_id,
        )
        ids = client.portal.call(adapter.scan_once, context, provider)
        assert len(ids) == 1
        event = client.portal.call(app.state.runtime.event_log.get, ids[0])
        assert event["source_account_id"] == expected_source
        assert event["sensitivity"] == expected_sensitivity
        assert event["owner_scope"] == f"private:{MEMBER_A}"
        assert event["visibility_scope"] == f"private:{MEMBER_A}"
        assert client.portal.call(adapter.scan_once, context, provider) == []
        client.portal.call(ingest_client.aclose)
    cursors.close()


def test_bridge_rejects_child_obsidian_and_source_spoofing(tmp_path: Path) -> None:
    settings = _settings(tmp_path / "bridge-denials", KEY_A)
    app = create_application(settings)

    async def count_knowledge_events(log: Any) -> int:
        return await log.count_since(None, types=["member_knowledge.note_added"])

    def envelope(
        *, member_id: str, source_kind: str, source_adapter: str
    ) -> EventEnvelope:
        return EventEnvelope(
            event_at_ms=2_088_288_000_000,
            tenant_id=settings.instance.tenant_id,
            type="member_knowledge.note_added",
            schema_version=1,
            occurred_at="2036-03-05T12:00:00Z",
            source_adapter=source_adapter,
            source_account_id=source_kind,
            owner_scope=f"private:{member_id}",
            visibility_scope=f"private:{member_id}",
            sensitivity="sensitive",
            payload={
                "knowledge_id": f"fixture-{member_id}-{source_kind}",
                "member_id": member_id,
                "title": "Denied fixture",
                "body": "This payload must not reach the event log.",
                "source_kind": source_kind,
                "source_external_id": "denied.md",
                "content_sha256": "1" * 64,
                "captured_at": "2036-03-05T12:00:00Z",
            },
        )

    with TestClient(app) as client:
        child = client.post(
            "/api/bridge/ingest",
            headers={"Tailscale-User-Login": "child.fixture.ts.net"},
            json=envelope(
                member_id=CHILD,
                source_kind="obsidian",
                source_adapter="knowledge-obsidian",
            ).model_dump(mode="json"),
        )
        assert child.status_code == 403

        spoofed = client.post(
            "/api/bridge/ingest",
            headers=AUTH_A,
            json=envelope(
                member_id=MEMBER_A,
                source_kind="apple_notes",
                source_adapter="knowledge-obsidian",
            ).model_dump(mode="json"),
        )
        assert spoofed.status_code == 403
        stored = client.portal.call(count_knowledge_events, app.state.runtime.event_log)
        assert stored == 0
