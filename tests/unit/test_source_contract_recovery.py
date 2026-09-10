"""Exact provenance and consumed-attempt guards for contract recovery."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.lib.session import build_internal_session
from adminme.lib.skill_runner.wrapper import SkillContext
from adminme.pipelines import load_pipeline_pack
from adminme.source_contract_recovery import (
    ASSESSMENT_CONTRACT,
    CONTRACT_ATTEMPT_TYPE,
    CONTRACT_RESULT_TYPE,
    AmbiguousContractAttempt,
    ContractRecoveryRefused,
    ContractRecoveryTarget,
    SourceAssessmentContractRecoveryBatch,
    build_contract_recovery_request_envelopes,
    contract_recovery_requirement,
    current_contract_result,
    contract_result_key,
    validate_contract_recovery_target,
)
from tests.acceptance.test_bounded_triage_execution import REPO_ROOT, _settings
from tests.fixtures.source_contract_recovery import (
    _envelope,
    seed_lineage,
    target_payload,
)


async def _request(log: EventLog, settings: Any, lineage: dict[str, str]):
    [envelope] = await build_contract_recovery_request_envelopes(
        log,
        SourceAssessmentContractRecoveryBatch(
            request_id="fixture-unit-contract-request",
            required_assessment_contract=ASSESSMENT_CONTRACT,
            targets=[ContractRecoveryTarget(**target_payload(lineage))],
        ),
        tenant_id=settings.instance.tenant_id,
        requested_by_actor=settings.fixture_member_id,
        authority="member_principal",
    )
    return await log.get(await log.append(envelope))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed", ["tenant_id", "owner_scope", "visibility_scope", "sensitivity"]
)
async def test_proven_boundary_does_not_cross_scope(tmp_path, changed):
    settings = _settings(tmp_path)
    lineage = await seed_lineage(settings, legacy_outcome="no_candidate")
    log = EventLog(settings.instance, settings.encryption_key)
    try:
        source = await log.get(lineage["source_event_id"])
        latest = await log.get(lineage["latest_assessment_event_id"])
        base = await log.get(lineage["base_assessment_event_id"])
        assert await contract_recovery_requirement(log, source, base) is None
        assert await contract_recovery_requirement(log, source, latest) is not None
        forged = {**latest, changed: "fixture-different-scope"}
        assert await contract_recovery_requirement(log, source, forged) is None
        with pytest.raises(ContractRecoveryRefused):
            await validate_contract_recovery_target(
                log,
                ContractRecoveryTarget(**target_payload(lineage)),
                required_tenant_id=settings.instance.tenant_id,
                required_owner_scope="private:fixture-other-member",
            )
    finally:
        await log.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("completion", ["language", "failed", "deterministic_terminal"])
async def test_terminal_matrix_requires_matching_assessment_and_clean_current_receipt(
    tmp_path, completion
):
    settings = _settings(tmp_path)
    lineage = await seed_lineage(settings)
    log = EventLog(settings.instance, settings.encryption_key)
    try:
        request = await _request(log, settings, lineage)
        source = await log.get(lineage["source_event_id"])
        prior = await log.get(lineage["latest_assessment_event_id"])
        member = settings.fixture_member_id
        identity = f"message-nature:recovery:{request['event_id']}:{'c' * 64}"
        skill_id = await log.append(
            _envelope(
                settings,
                member,
                type="skill.call.recorded",
                schema_version=2,
                source_adapter="skill_runner",
                source_account_id="system",
                idempotency_key=f"skill:{identity}:skill:classify_message_nature:8.0.1",
                payload={
                    "skill_name": "skill:classify_message_nature",
                    "skill_version": "8.0.1",
                    "inputs": {},
                    "outputs": {"assessment_contract": ASSESSMENT_CONTRACT},
                    "provider": "fixture-provider",
                    "duration_ms": 1,
                },
            ),
            causation_id=source["event_id"],
        )
        assessment_key = f"triage:message-features:recovery:{request['event_id']}:{source['event_id']}"
        assessment_id = await log.append(
            _envelope(
                settings,
                member,
                type="triage.intent.features_extracted",
                schema_version=4,
                source_adapter="deterministic_candidate_eligibility",
                source_account_id="pipeline",
                idempotency_key=assessment_key,
                payload={
                    **prior["payload"],
                    "assessment_outcome": "no_candidate",
                    "extraction_state": "complete",
                    "reason_codes": [
                        "language_assessment_completed",
                        "no_eligible_candidate",
                    ],
                    "supersedes_assessment_event_id": prior["event_id"],
                    "recovery_request_event_id": request["event_id"],
                },
            ),
            causation_id=source["event_id"],
        )
        await log.append(
            _envelope(
                settings,
                member,
                type=CONTRACT_RESULT_TYPE,
                schema_version=1,
                source_adapter="deterministic_candidate_eligibility",
                source_account_id="pipeline",
                idempotency_key=contract_result_key(
                    request["event_id"], source["event_id"]
                ),
                payload={
                    "request_event_id": request["event_id"],
                    "source_event_id": source["event_id"],
                    "assessment_idempotency_key": assessment_key,
                    "required_assessment_contract": ASSESSMENT_CONTRACT,
                    "completed_assessment_contract": None
                    if completion == "failed"
                    else ASSESSMENT_CONTRACT,
                    "completion_kind": completion,
                    "classifier_version": "8.0.1" if completion == "language" else None,
                    "skill_call_event_id": skill_id
                    if completion == "language"
                    else None,
                },
            ),
            causation_id=source["event_id"],
        )
        assessment = await log.get(assessment_id)
        if completion == "language":
            assert await current_contract_result(log, source, assessment) is not None
            # A paired defensive/default failure invalidates the exact purported
            # clean receipt, even though the recorded output carries its marker.
            await log.append(
                _envelope(
                    settings,
                    member,
                    type="skill.call.failed",
                    schema_version=1,
                    source_adapter="skill_runner",
                    source_account_id="system",
                    idempotency_key=f"skill:failed:{identity}:skill:classify_message_nature:8.0.1:output_invalid",
                    payload={
                        "skill_name": "skill:classify_message_nature",
                        "skill_version": "8.0.1",
                        "failure_class": "output_invalid",
                        "error_detail": "Synthetic contradictory output",
                    },
                ),
                causation_id=source["event_id"],
            )
            with pytest.raises(RuntimeError, match="paired failure"):
                await current_contract_result(log, source, assessment)
        else:
            with pytest.raises(RuntimeError):
                await current_contract_result(log, source, assessment)
    finally:
        await log.close()


@pytest.mark.asyncio
async def test_durable_attempt_without_terminal_cannot_spend_transport_after_crash(
    tmp_path,
):
    settings = _settings(tmp_path)
    lineage = await seed_lineage(settings)
    log = EventLog(settings.instance, settings.encryption_key)
    request = await _request(log, settings, lineage)
    source_id = lineage["source_event_id"]
    identity = f"message-nature:recovery:{request['event_id']}:{'d' * 64}"
    calls = 0

    async def interrupted_transport(*_args):
        nonlocal calls
        calls += 1
        raise KeyboardInterrupt("Synthetic process interruption after reservation")

    class Writer:
        async def write(self, envelope: EventEnvelope, **kwargs):
            return await log.append(envelope, **kwargs)

    pack = load_pipeline_pack(REPO_ROOT / "packs/pipelines/noise_filtering")
    skill_context = SkillContext(
        session=build_internal_session(
            "pipeline_runner", "device", settings.instance.tenant_id
        ),
        triggering_event_id=source_id,
        invocation_idempotency_key=identity,
        owner_scope=request["owner_scope"],
        visibility_scope=request["visibility_scope"],
    )
    context = SimpleNamespace(
        event_log=log,
        event_sink=Writer(),
        run_skill_fn=interrupted_transport,
        correlation_id=None,
    )
    try:
        with pytest.raises(KeyboardInterrupt):
            await pack.instance._run_reserved_contract_attempt(
                context, {}, skill_context, request, 0
            )
        assert calls == 1
        assert await log.count_since(None, types=[CONTRACT_ATTEMPT_TYPE]) == 1
    finally:
        await log.close()
    # Open a new SQLCipher connection, preserving the durable reservation.
    log = EventLog(settings.instance, settings.encryption_key)
    context.event_log = log
    try:
        with pytest.raises(AmbiguousContractAttempt, match="no terminal receipt"):
            await pack.instance._run_reserved_contract_attempt(
                context, {}, skill_context, request, 0
            )
        assert calls == 1
        assert await log.count_since(None, types=[CONTRACT_ATTEMPT_TYPE]) == 1
    finally:
        await log.close()
