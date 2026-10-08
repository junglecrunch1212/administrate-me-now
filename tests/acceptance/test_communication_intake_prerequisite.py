"""The real composition honors immutable prerequisite/cutover release roles.

Each source layout has its own manifest. No fixture setting or environment
switch changes release behavior, and the shared encrypted instance retains the
same source facts across precursor, cutover, and rollback starts.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from adminme.communication_intake_policy import (
    POLICY_EVENT_KEY,
    POLICY_EVENT_TYPE,
    POLICY_ID,
    POLICY_REASON,
    SKIPPED_EVENT_TYPE,
    read_policy_epoch,
)
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.runtime.application import create_application
from tests.acceptance.test_explicit_communication_intake_policy import (
    AT,
    ROOT,
    _events,
    _message,
    _preload,
    _proposal,
    _settings,
    _settle,
    _triage_rows,
)
from tests.fixtures.communication_intake_release import communication_intake_release


def test_checked_in_release_manifest_controls_the_actual_composition(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path / "instance")
    application_manifest = json.loads(
        (ROOT / "bootstrap" / "versions.json").read_text()
    )["administrateme"]
    activates = (
        application_manifest.get("default_communication_intake_policy") == POLICY_ID
    )
    assert settings.communication_intake_release.activates_cutover is activates
    application = create_application(settings)
    with TestClient(application) as client:
        runtime = application.state.runtime
        client.portal.call(_settle, runtime)
        epoch = client.portal.call(
            read_policy_epoch, runtime.event_log, settings.instance.tenant_id
        )
        assert (epoch is not None) is activates
        assert (
            client.portal.call(
                _events, runtime, "skill.call.recorded", "skill.call.failed"
            )
            == []
        )


def test_prerequisite_preserves_historical_triage_then_honors_cutover_on_rollback(
    tmp_path: Path,
) -> None:
    prerequisite = communication_intake_release(
        ROOT, tmp_path / "prerequisite", "rollback-prerequisite"
    )
    cutover = communication_intake_release(ROOT, tmp_path / "cutover", "cutover")
    instance = tmp_path / "instance"
    before = _settings(instance, repository_root=prerequisite)
    seeded = asyncio.run(_preload(before))
    application = create_application(before)
    with TestClient(application) as client:
        runtime = application.state.runtime
        client.portal.call(_settle, runtime)
        assert (
            client.portal.call(
                read_policy_epoch, runtime.event_log, before.instance.tenant_id
            )
            is None
        )
        assert client.portal.call(_events, runtime, POLICY_EVENT_TYPE) == []
        assert client.portal.call(_events, runtime, "triage.item.superseded") == []
        original = client.portal.call(_triage_rows, runtime)
        assert {"untouched", "member-edited", "approved", "manual"} <= set(original)
        assert all(row["reason"] != POLICY_REASON for row in original.values())
        client.portal.call(runtime.events.write, _message(before, "new-before-cutover"))
        client.portal.call(_settle, runtime)
        assert client.portal.call(_triage_rows, runtime) == original
        assert len(client.portal.call(_events, runtime, SKIPPED_EVENT_TYPE)) == 2
        assert (
            client.portal.call(
                _events, runtime, "skill.call.recorded", "skill.call.failed"
            )
            == []
        )

    after = _settings(instance, repository_root=cutover)
    application = create_application(after)
    with TestClient(application) as client:
        runtime = application.state.runtime
        client.portal.call(_settle, runtime)
        epoch = client.portal.call(
            read_policy_epoch, runtime.event_log, after.instance.tenant_id
        )
        assert epoch is not None
        rows = client.portal.call(_triage_rows, runtime)
        assert rows["untouched"]["reason"] == POLICY_REASON
        assert rows["legacy-thank-you"]["reason"] == POLICY_REASON
        assert rows["member-edited"]["title"] == "Member retained fixture work"
        assert rows["approved"]["reason"] != POLICY_REASON
        assert rows["manual"]["reason"] != POLICY_REASON
        assert len(client.portal.call(_events, runtime, "task.created")) == 1
        retired_count = len(
            client.portal.call(_events, runtime, "triage.item.superseded")
        )

    for _restart in range(2):
        application = create_application(before)
        with TestClient(application) as client:
            runtime = application.state.runtime
            client.portal.call(_settle, runtime)
            assert (
                client.portal.call(
                    read_policy_epoch, runtime.event_log, before.instance.tenant_id
                )
                == epoch
            )
            assert client.portal.call(_triage_rows, runtime) == rows
            assert (
                len(client.portal.call(_events, runtime, "triage.item.superseded"))
                == retired_count
            )
            client.portal.call(
                runtime.events.write,
                _proposal(before, seeded["source"], "late-after-rollback"),
            )
            client.portal.call(_settle, runtime)
            assert "late-after-rollback" not in client.portal.call(
                _triage_rows, runtime
            )
            client.portal.call(runtime.projections.rebuild, "triage")
            assert client.portal.call(_triage_rows, runtime) == rows
            assert (
                client.portal.call(
                    _events, runtime, "skill.call.recorded", "skill.call.failed"
                )
                == []
            )


@pytest.mark.parametrize(
    "mutation",
    [
        {"communication_intake_release_role": None},
        {"communication_intake_release_role": "cutover"},
        {"default_communication_intake_policy": POLICY_ID},
        {"supported_communication_intake_policies": []},
        {"supported_communication_intake_policies": [POLICY_ID, POLICY_ID]},
        {"supported_communication_intake_policies": [POLICY_ID, "unknown-policy"]},
        {"default_communication_intake_policy": False},
    ],
)
def test_contradictory_release_manifest_is_refused_before_runtime_start(
    tmp_path: Path, mutation: dict[str, object]
) -> None:
    release = communication_intake_release(
        ROOT, tmp_path / "invalid", "rollback-prerequisite"
    )
    manifest = release / "bootstrap" / "versions.json"
    versions = json.loads(manifest.read_text())
    versions["administrateme"].update(mutation)
    manifest.write_text(json.dumps(versions))
    with pytest.raises(ValueError, match="communication intake release"):
        _settings(tmp_path / "instance", repository_root=release)


@pytest.mark.parametrize(
    "mutation",
    [
        {"source_account_id": "fixture-imposter"},
        {"actor_identity": "fixture-imposter"},
        {"sensitivity": "sensitive"},
        {"idempotency_key": "fixture-wrong-epoch-key"},
        {
            "payload": {
                "policy_id": POLICY_ID,
                "previous_source_watermark": "fixture-missing-watermark",
                "automatic_personal_obligations_enabled": False,
            }
        },
    ],
)
def test_malformed_epoch_refuses_prerequisite_without_appending_or_starting_consumers(
    tmp_path: Path, mutation: dict[str, object]
) -> None:
    release = communication_intake_release(
        ROOT, tmp_path / "prerequisite", "rollback-prerequisite"
    )
    settings = _settings(tmp_path / "instance", repository_root=release)
    value = {
        "event_at_ms": 2_022_408_000_000,
        "tenant_id": settings.instance.tenant_id,
        "type": POLICY_EVENT_TYPE,
        "schema_version": 1,
        "occurred_at": AT,
        "source_adapter": "communication-intake-policy",
        "source_account_id": "system",
        "owner_scope": "shared:household",
        "visibility_scope": "shared:household",
        "sensitivity": "normal",
        "actor_identity": "system:communication-intake-policy",
        "idempotency_key": POLICY_EVENT_KEY,
        "payload": {
            "policy_id": POLICY_ID,
            "previous_source_watermark": None,
            "automatic_personal_obligations_enabled": False,
        },
        **mutation,
    }

    async def seed() -> int:
        log = EventLog(settings.instance, settings.encryption_key)
        try:
            await log.append(EventEnvelope.model_validate(value))
            return await log.count()
        finally:
            await log.close()

    count = asyncio.run(seed())
    application = create_application(settings)
    with pytest.raises(RuntimeError, match="communication intake policy epoch"):
        with TestClient(application):
            pytest.fail("a malformed epoch must prevent startup")
    assert application.state.runtime.started is False
    assert application.state.runtime.pipelines is None

    async def retained_count() -> int:
        log = EventLog(settings.instance, settings.encryption_key, read_only=True)
        try:
            return await log.count()
        finally:
            await log.close()

    assert asyncio.run(retained_count()) == count
