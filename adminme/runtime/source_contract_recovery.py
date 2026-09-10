"""One exact recovery command through the production pipeline composition.

This maintenance process has no listener, adapters, projections, workbook daemon,
consumer subscriptions or scheduler. Only the normal encrypted event writer and
reviewed language seam are available to the one built-in recovery handler.
"""

from __future__ import annotations

from functools import partial
import json
import os
from pathlib import Path
from typing import Any

from adminme.events.bus import EventBus
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.events.writer import EventWriter
from adminme.lib.instance_config import load_instance_config
from adminme.lib.observation import ObservationManager
from adminme.lib.skill_runner import build_skill_runtime, run_skill
from adminme.pipelines.pack_loader import load_pipeline_pack
from adminme.pipelines.runner import PipelineRunner
from adminme.settings_control import SettingsControlStore
from adminme.source_contract_recovery import (
    CONTRACT_ATTEMPT_TYPE,
    CONTRACT_REFUSAL_TYPE,
    CONTRACT_REQUEST_TYPE,
    CONTRACT_RESULT_TYPE,
    SourceAssessmentContractRecoveryBatch,
    build_contract_recovery_request_envelopes,
    contract_batch_fingerprint,
    contract_request_key,
    contract_recovery_terminal,
)


_ALLOWED_EVENTS = frozenset(
    {
        CONTRACT_REQUEST_TYPE,
        CONTRACT_ATTEMPT_TYPE,
        CONTRACT_REFUSAL_TYPE,
        CONTRACT_RESULT_TYPE,
        "skill.call.recorded",
        "skill.call.failed",
        "messaging.classified",
        "messaging.semantic_features_extracted",
        "triage.intent.features_extracted",
        "triage.source_publication.planned",
        "triage.source_publication.completed",
        "triage.item.proposed",
        "triage.item.superseded",
    }
)
_EVENT_PRODUCERS = {
    **{
        name: ("deterministic_candidate_eligibility", "pipeline")
        for name in _ALLOWED_EVENTS
    },
    CONTRACT_REQUEST_TYPE: ("source_assessment_contract_recovery", "operator"),
    CONTRACT_REFUSAL_TYPE: ("noise_filtering", "pipeline"),
    "messaging.classified": ("noise_filtering", "pipeline"),
    "messaging.semantic_features_extracted": ("noise_filtering", "pipeline"),
    "skill.call.recorded": ("skill_runner", "system"),
    "skill.call.failed": ("skill_runner", "system"),
}


class MaintenanceRecoveryError(RuntimeError):
    """Content-free refusal of a maintenance execution boundary."""


class _ExistingSchemaEventLog(EventLog):
    def _migrate(self) -> None:
        # EventLog invokes this on its own connection thread. Maintenance never
        # applies even an additive migration to the selected predecessor state.
        applied = {
            row[0] for row in self._conn.execute("SELECT version FROM _schema_version")
        }
        if applied != {version for version, _filename in self.MIGRATIONS}:
            raise MaintenanceRecoveryError("maintenance_requires_existing_schema")


class _RecoveryWriter:
    def __init__(self, writer: EventWriter, source: dict[str, Any]) -> None:
        self.writer = writer
        self.source = source

    @property
    def tenant_id(self) -> str | None:
        return self.writer.tenant_id

    async def write(self, envelope: EventEnvelope, **kwargs: Any) -> str:
        return (await self.write_batch([envelope], **kwargs))[0]

    async def write_batch(
        self, envelopes: list[EventEnvelope], **kwargs: Any
    ) -> list[str]:
        for envelope in envelopes:
            if (
                envelope.type not in _ALLOWED_EVENTS
                or (envelope.source_adapter, envelope.source_account_id)
                != _EVENT_PRODUCERS[envelope.type]
                or any(
                    getattr(envelope, field) != self.source.get(field)
                    for field in (
                        "tenant_id",
                        "owner_scope",
                        "visibility_scope",
                        "sensitivity",
                    )
                )
                or envelope.payload.get("source_event_id", self.source["event_id"])
                != self.source["event_id"]
                or envelope.payload.get(
                    "replacement_source_event_id", self.source["event_id"]
                )
                != self.source["event_id"]
            ):
                raise MaintenanceRecoveryError("maintenance_write_outside_exact_scope")
            if envelope.type == "triage.item.superseded":
                prior = await self.writer._event_log.get(
                    str(kwargs.get("causation_id") or "")
                )
                if (
                    prior is None
                    or prior.get("type") != "triage.item.proposed"
                    or (prior.get("payload") or {}).get("source_event_id")
                    != self.source["event_id"]
                    or (prior.get("payload") or {}).get("proposal_id")
                    != envelope.payload.get("proposal_id")
                    or any(
                        prior.get(field) != self.source.get(field)
                        for field in (
                            "tenant_id",
                            "owner_scope",
                            "visibility_scope",
                            "sensitivity",
                        )
                    )
                ):
                    raise MaintenanceRecoveryError(
                        "maintenance_supersession_outside_exact_source"
                    )
        return await self.writer.write_batch(envelopes, **kwargs)


async def run_source_contract_recovery(
    *,
    instance_dir: Path,
    repository_root: Path,
    operation_dir: Path,
    encryption_key: bytes,
    gateway_token: str | None,
    batch: SourceAssessmentContractRecoveryBatch,
    requested_by_actor: str,
    run_skill_fn: Any = None,
    publication_only: bool = False,
) -> dict[str, Any]:
    """Execute or resume one canonical authorized request, never a replay scan.

    The bootstrap controller owns OS quiescence, source identity, backup and
    deadlines. This composition independently restricts event scope and writes.
    ``run_skill_fn`` replaces only the external seam in synthetic process tests.
    """
    if repository_root.resolve() != Path(__file__).resolve().parents[2]:
        raise MaintenanceRecoveryError("maintenance_code_repository_mismatch")
    if len(batch.targets) != 1:
        raise MaintenanceRecoveryError("maintenance_requires_one_exact_source")
    config = load_instance_config(instance_dir)
    readonly = EventLog(config, encryption_key, read_only=True)
    try:
        applied = await readonly._run_locked(
            lambda: {
                row[0]
                for row in readonly._conn.execute("SELECT version FROM _schema_version")
            }
        )
        if applied != {version for version, _filename in EventLog.MIGRATIONS}:
            raise MaintenanceRecoveryError("maintenance_requires_existing_schema")
    finally:
        await readonly.close()
    log = _ExistingSchemaEventLog(config, encryption_key)
    bus: EventBus | None = None
    writer: EventWriter | None = None
    try:
        source = await log.get(batch.targets[0].source_event_id)
        if source is None or source.get("tenant_id") != config.tenant_id:
            raise MaintenanceRecoveryError("maintenance_source_tenant_mismatch")
        if (
            not str(source.get("owner_scope") or "").startswith("private:")
            or source.get("visibility_scope") != source.get("owner_scope")
            or source.get("sensitivity") == "privileged"
        ):
            raise MaintenanceRecoveryError("maintenance_source_scope_invalid")
        # This empty bus exists solely for EventWriter's commit/notify contract.
        # It never registers a consumer or opens the active checkpoint database.
        bus = EventBus(log, operation_dir / "notification-checkpoints.db")
        writer = EventWriter(log, bus)
        events = _RecoveryWriter(writer, source)
        observation = ObservationManager(events, config.config_dir / "runtime.yaml")
        if not await observation.is_active():
            raise MaintenanceRecoveryError("maintenance_requires_observation")
        policy = SettingsControlStore(config.config_dir / "settings-control.json")
        policy_snapshot = policy.read()
        replay_policy = SettingsControlStore(operation_dir / "settings-control.json")
        replay_policy.path.write_text(json.dumps(policy_snapshot), encoding="utf-8")
        os.chmod(replay_policy.path, 0o600)
        await replay_policy.replay_source_policy_v2_events(log)
        if replay_policy.read() != policy_snapshot:
            raise MaintenanceRecoveryError("maintenance_policy_snapshot_stale")
        request = await log.get_by_idempotency(
            config.tenant_id, contract_request_key(batch.request_id, source["event_id"])
        )
        if request is None:
            if publication_only:
                raise MaintenanceRecoveryError("maintenance_publication_request_absent")
            envelopes = await build_contract_recovery_request_envelopes(
                log,
                batch,
                tenant_id=config.tenant_id,
                requested_by_actor=requested_by_actor,
                authority="guarded_release_exception",
                required_owner_scope=source["owner_scope"],
            )
            request_id = (
                await events.write_batch(envelopes, correlation_id=batch.request_id)
            )[0]
        else:
            if (
                request.get("type") != CONTRACT_REQUEST_TYPE
                or request.get("source_adapter")
                != "source_assessment_contract_recovery"
                or (request.get("payload") or {}).get("batch_fingerprint")
                != contract_batch_fingerprint(batch)
                or any(
                    request.get(key) != source.get(key)
                    for key in (
                        "tenant_id",
                        "owner_scope",
                        "visibility_scope",
                        "sensitivity",
                    )
                )
            ):
                raise MaintenanceRecoveryError("maintenance_request_identity_conflict")
            request_id = request["event_id"]
        if publication_only:
            terminal = await contract_recovery_terminal(log, request_id)
            if terminal.get("status") != "completed":
                raise MaintenanceRecoveryError(
                    "maintenance_publication_terminal_invalid"
                )
            if terminal.get("publication_complete") is True:
                return terminal

            async def selected_skill(*_args: Any, **_kwargs: Any) -> Any:
                raise MaintenanceRecoveryError(
                    "maintenance_publication_model_forbidden"
                )
        else:
            skill_runtime = build_skill_runtime(
                event_sink=events,
                event_log=log,
                raw_events_dir=config.raw_events_dir,
                gateway_token=gateway_token,
            )
            selected_skill = run_skill_fn or partial(run_skill, _runtime=skill_runtime)
        runner = PipelineRunner(
            bus,
            log,
            events,
            config,
            observation_manager=observation,
            run_skill_fn=selected_skill,
            candidate_policy_fn=partial(
                policy.effective_candidate_policy, state=policy_snapshot
            ),
        )
        pack = load_pipeline_pack(repository_root / "packs/pipelines/noise_filtering")
        if pack.pack_id != "pipeline:noise_filtering":
            raise MaintenanceRecoveryError("maintenance_pipeline_identity_invalid")
        runner.register(pack)
        result = await runner.dispatch_source_contract_recovery(request_id)
        if not await observation.is_active():
            raise MaintenanceRecoveryError("maintenance_observation_changed")
        return result
    finally:
        if writer is not None:
            writer.stop_accepting()
        try:
            if bus is not None:
                await bus.stop()
        finally:
            await log.close()
