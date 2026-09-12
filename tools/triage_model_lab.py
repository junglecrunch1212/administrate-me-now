"""Capture actual classifier evidence in an explicitly authorized hosted lab.

Run as ``python -m tools.triage_model_lab --output $RUNNER_TEMP/capture.json``.
This is an evidence capture, never a semantic score or an instance repair tool.
Application imports and construction occur only after the hosted-runner guard.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import json
import os
import platform
import secrets
import subprocess
import tempfile
import time
from collections.abc import Mapping
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/triage_model_lab"
FIXTURE_HASHES = {
    "golden-sources-frozen.json": "5126899583303af18a88ed1ba8fbd6106515853e0355d7e6a4067af48b087b40",
    "golden-expectations-frozen.json": "424ace0abeb78ce24e60721f8e6a4240ea593df555419dcf9202dc4580aeb79b",
    "semantic-golden-sources-frozen.json": "a916b5eaa03cc4e13c66c279d414449a71cd9bf62f46ce6113793ddb8b40a118",
    "semantic-golden-expectations-frozen.json": "587a098c00e698eaa534f7d1e8b520b4a753b6689c63a95e94496c4c2019b518",
}
CASE_IDS = tuple([f"G{index:02}" for index in range(1, 12)] + ["G12a", "G12b"])
SEMANTIC_IDS = tuple(f"S{index:02}" for index in range(1, 7))
MEMBER = "fixture-model-lab-member"
ACCOUNT = "fixture-model-lab-account"
HEADERS = {"Tailscale-User-Login": "fixture-model-lab.ts.net"}
CLASSIFIER = "skill:classify_message_nature"
SOURCE_TYPES = ("messaging.received", "messaging.sent", "telephony.sms_received")
SKILL_TYPES = ("skill.call.recorded", "skill.call.failed", "skill.call.suppressed")
TIMEOUT_SECONDS = 420
MAX_SOURCES = 19
MODEL = "openai/gpt-5.6-sol"
MAX_ATTEMPTS_PER_SOURCE = 3


class LabRefused(RuntimeError):
    """The selected execution environment is outside this runner's authority."""


def validate_environment(
    environment: Mapping[str, str], *, system: str, head: str, as_of: date
) -> Path:
    if (
        system != "Linux"
        or environment.get("GITHUB_ACTIONS") != "true"
        or environment.get("RUNNER_ENVIRONMENT") != "github-hosted"
    ):
        raise LabRefused("requires an explicit disposable GitHub-hosted Linux runner")
    approved = environment.get("ADMINME_MODEL_LAB_APPROVED_SHA", "")
    if (
        len(approved) != 40
        or any(character not in "0123456789abcdef" for character in approved)
        or approved != head
    ):
        raise LabRefused("approved lab SHA must exactly match checked-out HEAD")
    if any(
        name.startswith("ADMINME_") and name != "ADMINME_MODEL_LAB_APPROVED_SHA"
        for name in environment
    ):
        raise LabRefused("instance, fixture, and other ADMINME overrides are refused")
    if not environment.get("OPENCLAW_GATEWAY_TOKEN", "").strip():
        raise LabRefused("the hosted lab Gateway token is required")
    for name in ("OPENCLAW_GATEWAY_URL", "OPENCLAW_URL"):
        if environment.get(name) not in {
            None,
            "ws://127.0.0.1:18789",
            "http://127.0.0.1:18789",
            "http://127.0.0.1:18789/tools/invoke",
        }:
            raise LabRefused("only the local pinned Gateway endpoint is allowed")
    if not date(2026, 9, 10) <= as_of < date(2026, 9, 17):
        raise LabRefused("the frozen fixture's future-deadline window has elapsed")
    temporary_root = Path(environment.get("RUNNER_TEMP", ""))
    if not temporary_root.is_absolute() or not temporary_root.is_dir():
        raise LabRefused(
            "RUNNER_TEMP must identify the runner-owned temporary directory"
        )
    return temporary_root.resolve()


def load_frozen_cases(directory: Path = FIXTURES) -> list[dict[str, Any]]:
    documents: dict[str, Any] = {}
    if {path.name for path in directory.iterdir()} != set(FIXTURE_HASHES):
        raise LabRefused("the frozen fixture manifest must contain exactly four files")
    for name, expected in FIXTURE_HASHES.items():
        raw = (directory / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise LabRefused(f"frozen fixture digest mismatch: {name}")
        documents[name] = json.loads(raw)
    original = documents["golden-sources-frozen.json"]
    supplement = documents["semantic-golden-sources-frozen.json"]
    sources = original["sources"]
    semantic = supplement["inputs"]
    if (
        original["unique_source_count"] != 13
        or tuple(item["case_id"] for item in sources) != CASE_IDS
        or tuple(item["case_id"] for item in semantic) != SEMANTIC_IDS
        or tuple(
            item["case_id"]
            for item in documents["golden-expectations-frozen.json"]["cases"]
        )
        != CASE_IDS
    ):
        raise LabRefused("frozen source manifest is incomplete or reordered")
    # Only source facts are returned. Expectations and supplemental setup prose
    # must never enter the model's body, context, subject, or skill arguments.
    result = [dict(item) for item in sources]
    for item in semantic:
        result.append(
            {
                "case_id": item["case_id"],
                "message_id": f"synthetic-message-{item['case_id'].lower()}",
                "thread_id": f"synthetic-thread-{item['case_id'].lower()}",
                "direction": "incoming",
                "source_channel": "gmail",
                "automated": True,
                "occurred_at": supplement["frozen_at"],
                "subject": item["subject"],
                "body_text": item["body_text"],
            }
        )
    if len(result) != MAX_SOURCES or len({item["message_id"] for item in result}) != 19:
        raise LabRefused(
            "exactly nineteen distinct frozen source messages are required"
        )
    return result


def _sha_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def _save_report(path: Path, report: dict[str, Any], token: str) -> None:
    serialized = json.dumps(report, indent=2, ensure_ascii=False, default=str)
    for secret in (token, os.environ.get("OPENAI_API_KEY", "")):
        if secret:
            serialized = serialized.replace(secret, "[REDACTED_LAB_CREDENTIAL]")
    pending = path.with_name(path.name + ".partial")
    with pending.open("w", encoding="utf-8") as stream:
        os.chmod(pending, 0o600)
        stream.write(serialized + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    pending.replace(path)


def _source_envelope(case: Mapping[str, Any], tenant_id: str) -> Any:
    from adminme.events.envelope import EventEnvelope

    outgoing = case["direction"] == "outgoing"
    channel = case["source_channel"]
    provider = {"gmail": "gmail", "imessage": "bluebubbles"}.get(channel, "fixture")
    common = {
        "member_id": MEMBER,
        "provider": provider,
        "provider_account_id": ACCOUNT,
        "workspace": "personal",
        "routing_scope": "member_private",
        "routing_target_id": MEMBER,
        "source_channel": channel,
        "native_message_id": case["message_id"],
        "thread_id": case["thread_id"],
        "subject": case.get("subject"),
        "body_text": case["body_text"],
        "body_html": None,
        "to_identifier": "fixture-peer@example.invalid"
        if outgoing
        else "fixture-model-lab@example.invalid",
    }
    if outgoing:
        payload = {
            **common,
            "sent_at": case["occurred_at"],
            "delivery_status": "sent",
            "send_as": "member",
        }
    else:
        payload = {
            **common,
            "received_at": case["occurred_at"],
            "attachments": [],
            "from_identifier": "no-reply@example.invalid"
            if case["automated"]
            else "fixture-peer@example.invalid",
            "to_identifiers": ["fixture-model-lab@example.invalid"]
            + (
                ["fixture-group-peer@example.invalid"]
                if case["direction"] == "incoming_group"
                else []
            ),
            "provider_labels": ["CATEGORY_UPDATES"] if case["automated"] else ["INBOX"],
        }
    return EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id=tenant_id,
        occurred_at=case["occurred_at"],
        type="messaging.sent" if outgoing else "messaging.received",
        schema_version=5,
        source_adapter="fixture-model-lab-source",
        source_account_id=ACCOUNT,
        owner_scope=f"private:{MEMBER}",
        visibility_scope=f"private:{MEMBER}",
        sensitivity="normal",
        idempotency_key=f"fixture-model-lab-source:{case['case_id']}",
        payload=payload,
    )


async def _events(runtime: Any) -> list[dict[str, Any]]:
    events = [event async for event in runtime.event_log.read_since()]
    for event in events:
        if event["type"] != "skill.call.recorded":
            continue
        inputs = event["payload"].get("inputs")
        captured = {"output_sha256": _sha_json(event["payload"].get("outputs"))}
        if isinstance(inputs, dict) and set(inputs) == {"_spilled_to"}:
            spill = Path(inputs["_spilled_to"]).resolve(strict=True)
            allowed = (
                runtime.settings.instance.raw_events_dir / "skill_large_inputs"
            ).resolve()
            if not spill.is_relative_to(allowed):
                raise LabRefused("receipt input spill is outside the disposable state")
            inputs = json.loads(spill.read_text(encoding="utf-8"))
            captured["expanded_inputs"] = inputs
        captured["input_sha256"] = _sha_json(inputs)
        # Capture metadata is separate from the immutable receipt payload.
        event["lab_capture"] = captured
    return events


async def _guard_inventory(runtime: Any, source_ids: set[str]) -> None:
    if not await runtime.observation.is_active():
        raise LabRefused("observation mode became inactive")
    metadata = [
        event
        async for event in runtime.event_log.read_metadata_since(
            types=[*SOURCE_TYPES, *SKILL_TYPES]
        )
    ]
    observed = {
        event["event_id"] for event in metadata if event["type"] in SOURCE_TYPES
    }
    if observed != source_ids or len(observed) > MAX_SOURCES:
        raise LabRefused("unexpected source fact or original-source count")
    attempts = dict.fromkeys(source_ids, 0)
    successes = dict.fromkeys(source_ids, 0)
    for header in metadata:
        if header["type"] not in SKILL_TYPES:
            continue
        event = await runtime.event_log.get(header["event_id"])
        if (
            event["payload"].get("skill_name") != CLASSIFIER
            or event.get("causation_id") not in source_ids
        ):
            raise LabRefused("unexpected model skill or non-source skill invocation")
        source_id = event["causation_id"]
        attempts[source_id] += 1
        successes[source_id] += int(event["type"] == "skill.call.recorded")
        if attempts[source_id] > MAX_ATTEMPTS_PER_SOURCE or successes[source_id] > 1:
            raise LabRefused("unexpected repeated source classifier receipt")


async def _settle(
    runtime: Any,
    source_ids: set[str],
    source_id: str | None = None,
    deadline: float | None = None,
) -> Any:
    deadline = deadline if deadline is not None else time.monotonic() + TIMEOUT_SECONDS
    if source_id:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("the case's 420-second settlement deadline elapsed")
        await runtime.wait_for_subscriber(
            "pipeline:pipeline:noise_filtering", source_id, timeout_s=remaining
        )
    # Same fixed-point criterion used by the production composition stress
    # harness: every subscriber drained, plus five unchanged event watermarks.
    previous = None
    stable = 0
    while time.monotonic() < deadline:
        await _guard_inventory(runtime, source_ids)
        statuses = {
            name: await runtime.bus.subscriber_status(name)
            for name in sorted(runtime.bus._subscribers)
        }
        if not statuses or any(value["degraded"] for value in statuses.values()):
            raise RuntimeError("production subscriber missing or degraded")
        watermark = await runtime.event_log.latest_event_id()
        if (
            all(value["lag_count"] == 0 for value in statuses.values())
            and watermark == previous
        ):
            stable += 1
            if stable == 5:
                return statuses
        else:
            stable = 0
        previous = watermark
        await asyncio.sleep(0.05)
    raise TimeoutError(
        "source and derived production consumers did not settle within 420 seconds"
    )


def _member_surface(client: Any, surface: str) -> dict[str, Any]:
    suffix = (
        "?triage_lens=all_candidates&triage_limit=100" if surface == "triage" else ""
    )
    response = client.get(f"/api/member/v1/surfaces/{surface}{suffix}", headers=HEADERS)
    if response.status_code != 200:
        raise RuntimeError(f"member {surface} read failed: HTTP {response.status_code}")
    result = response.json()
    if surface == "triage" and result.get("triage", {}).get("has_more"):
        raise RuntimeError(
            "bounded member triage capture unexpectedly requires pagination"
        )
    return result


def _case_evidence(events: list[dict[str, Any]], source_id: str) -> dict[str, Any]:
    related_ids = {source_id}
    selected: list[dict[str, Any]] = []
    # Event append order is causal; include assessments, publication and
    # reconciliation descendants without matching candidate titles.
    for event in events:
        if (
            event["event_id"] == source_id
            or event.get("causation_id") in related_ids
            or event.get("payload", {}).get("source_event_id") == source_id
        ):
            related_ids.add(event["event_id"])
            selected.append(event)
    receipts = [event for event in selected if event["type"] in SKILL_TYPES]
    assessments = [
        event
        for event in selected
        if event["type"] == "triage.intent.features_extracted"
    ]
    failures = []
    if not any(event["type"] == "skill.call.recorded" for event in receipts):
        failures.append("no_source_correlated_actual_classifier_receipt")
    if any(event["type"] != "skill.call.recorded" for event in receipts):
        failures.append("failed_or_suppressed_classifier_attempt")
    for receipt in receipts:
        if receipt["type"] != "skill.call.recorded":
            continue
        payload = receipt["payload"]
        output = payload.get("outputs") or {}
        if (
            payload.get("provider") != MODEL
            or "skill_failure_defensive_default" in output.get("reasons", [])
            or output.get("assessment_contract") != "personal-obligation-assessment-v1"
        ):
            failures.append("incomplete_actual_classifier_receipt")
    if not assessments or any(
        event["payload"].get("extraction_state") != "complete"
        or event["payload"].get("assessment_outcome")
        not in {"eligible_candidates", "no_candidate"}
        for event in assessments
    ):
        failures.append("failed_or_incomplete_source_assessment")
    return {
        "source_event_id": source_id,
        "events": selected,
        "failures": sorted(set(failures)),
    }


def _g12a_items(surface: dict[str, Any], source_id: str) -> tuple[dict, dict]:
    candidates = [
        item
        for item in surface["items"]
        if item.get("source_event_id") == source_id
        and item.get("target_kind") == "task"
    ]
    if len(candidates) != 1:
        raise RuntimeError("G12a requires exactly one actually produced task candidate")
    selected = candidates[0]
    if selected.get("admission_decision") != "duplicate":
        return selected, selected
    primary_id = selected.get("duplicate_of_proposal_id")
    primary = [
        item for item in surface["items"] if item.get("proposal_id") == primary_id
    ]
    if (
        not primary_id
        or len(primary) != 1
        or primary[0] is selected
        or primary[0].get("duplicate_of_proposal_id")
        or primary[0].get("target_kind") != "task"
    ):
        raise RuntimeError(
            "G12a exact duplicate must identify one available primary proposal"
        )
    return selected, primary[0]


def _validate_g12a_link(
    selected: dict[str, Any],
    effective: dict[str, Any],
    evidence: dict[str, dict[str, Any]],
    tenant_id: str,
) -> dict[str, Any]:
    """Accept one exact production duplicate pointer, never a similarity search."""
    from adminme.source_publication import source_assessment_identity_valid

    def scoped(value):
        return (
            value.get("tenant_id") == tenant_id
            and value.get("owner_scope") == f"private:{MEMBER}"
            and value.get("visibility_scope") == f"private:{MEMBER}"
            and value.get("sensitivity") in {"normal", "sensitive"}
        )

    def proof(item):
        source = evidence.get(str(item.get("source_event_id"))) or {}
        proposal = evidence.get(str(item.get("proposal_event_id"))) or {}
        assessment = evidence.get(str(item.get("extraction_event_id"))) or {}
        payload = proposal.get("payload") or {}
        source_payload = source.get("payload") or {}
        interpretation = payload.get("interpretation") or {}
        obligation = interpretation.get("obligation_assessment") or {}
        references = interpretation.get("evidence_refs") or []
        candidate = payload.get("candidate") or {}
        if (
            not all(scoped(value) for value in (item, source, proposal, assessment))
            or item.get("target_kind") != "task"
            or item.get("owner_member_id") != MEMBER
            or item.get("route") != {"member_id": MEMBER}
            or source.get("type") != "messaging.received"
            or source.get("source_adapter") != "fixture-model-lab-source"
            or source.get("source_account_id") != ACCOUNT
            or source_payload.get("member_id") != MEMBER
            or source_payload.get("provider_account_id") != ACCOUNT
            or source_payload.get("provider") != "gmail"
            or source_payload.get("workspace") != "personal"
            or proposal.get("type") != "triage.item.proposed"
            or proposal.get("source_adapter") != "deterministic_candidate_eligibility"
            or proposal.get("source_account_id") != "pipeline"
            or payload.get("proposal_id") != item.get("proposal_id")
            or payload.get("source_event_id") != source.get("event_id")
            or payload.get("target_kind") != "task"
            or payload.get("owner_member_id") != MEMBER
            or payload.get("route") != {"member_id": MEMBER}
            or payload.get("extraction_event_id") != assessment.get("event_id")
            or proposal.get("causation_id") != assessment.get("event_id")
            or item.get("content_revision_event_id") != proposal.get("event_id")
            or item.get("candidate") != candidate
            or item.get("title") != payload.get("title")
            or item.get("interpretation") != interpretation
            or not source_assessment_identity_valid(
                source=source, assessment=assessment
            )
            or (assessment.get("payload") or {}).get("assessment_outcome")
            != "eligible_candidates"
            or (assessment.get("payload") or {}).get("extraction_state") != "complete"
            or obligation.get("evidence_verified") is not True
            or obligation.get("independent_obligation") is not True
            or obligation.get("actionability") != "required"
            or obligation.get("condition_state") not in {"met", "not_applicable"}
            or obligation.get("date_role") != "deadline"
            or "due" not in (obligation.get("verified_date_fields") or [])
            or interpretation.get("action_owner") != "member"
            or interpretation.get("lifecycle_state") != "open"
            or len(references) != 1
        ):
            raise RuntimeError(
                "G12a proposal lacks exact owned source/assessment/grounding proof"
            )
        quote = references[0].get("quote")
        if (
            not isinstance(quote, str)
            or not quote
            or references[0].get("source_event_id") != source["event_id"]
            or quote not in str(source_payload.get("body_text") or "")
            or quote != payload.get("title")
            or not any(
                value.get("role") == "date"
                and value.get("source_event_id") == source["event_id"]
                and value.get("quote")
                and value["quote"] in source_payload["body_text"]
                for value in obligation.get("evidence") or []
            )
        ):
            raise RuntimeError("G12a action or due evidence is not exact source text")
        try:
            due = datetime.fromisoformat(str(candidate["due"]).replace("Z", "+00:00"))
            if due.tzinfo is None:
                raise ValueError("due has no time zone")
        except (KeyError, ValueError) as exc:
            raise RuntimeError(
                "G12a requires an actually produced grounded task due"
            ) from exc
        return quote, due.astimezone(timezone.utc).isoformat()

    selected_identity = proof(selected)
    effective_identity = proof(effective)
    if selected_identity != effective_identity:
        raise RuntimeError("G12a duplicate primary has different action or due")
    if (
        effective.get("admission_decision") != "admit"
        or effective.get("state") != "pending_review"
        or effective.get("duplicate_of_proposal_id")
        or (effective.get("review_state") or {}).get("state") != "ready"
    ):
        raise RuntimeError("G12a effective task proposal is not currently approvable")
    duplicate = selected.get("proposal_id") != effective.get("proposal_id")
    reconciliation_id = None
    if duplicate:
        reconciliation_id = (selected.get("reconciliation") or {}).get("event_id")
        receipt = evidence.get(str(reconciliation_id)) or {}
        payload = receipt.get("payload") or {}
        relationships = payload.get("relationships") or []
        if (
            selected.get("admission_decision") != "duplicate"
            or selected.get("state") != "dismissed"
            or selected.get("resolved_by") != "system:cross-source-reconciliation"
            or selected.get("resolution_reason") != "reconciliation:duplicate"
            or selected.get("last_event_id") != reconciliation_id
            or selected.get("duplicate_of_proposal_id") != effective.get("proposal_id")
            or not scoped(receipt)
            or receipt.get("type") != "triage.reconciliation.decided"
            or receipt.get("source_adapter") != "cross-source-reconciliation"
            or receipt.get("source_account_id") != "internal"
            or payload.get("decision") != "duplicate"
            or payload.get("admission_active") is not True
            or payload.get("proposal_id") != selected.get("proposal_id")
            or payload.get("source_event_id") != selected.get("source_event_id")
            or payload.get("owner_member_id") != MEMBER
            or payload.get("primary_proposal_id") != effective.get("proposal_id")
            or not any(
                value.get("relationship_type") == "exact_duplicate"
                and value.get("automatic_effect") is True
                and value.get("authority") == "deterministic_derived"
                and value.get("related_source_event_id")
                == effective.get("source_event_id")
                for value in relationships
            )
        ):
            raise RuntimeError(
                "G12a duplicate pointer lacks authoritative exact reconciliation"
            )
    return {
        "selected_source_event_id": selected["source_event_id"],
        "selected_proposal_id": selected["proposal_id"],
        "effective_source_event_id": effective["source_event_id"],
        "effective_proposal_id": effective["proposal_id"],
        "effective_assessment_event_id": effective["extraction_event_id"],
        "exact_duplicate_reconciliation_event_id": reconciliation_id,
        "followed_exact_duplicate": duplicate,
        "action_quote": selected_identity[0],
        "due": selected_identity[1],
    }


def _complete_g12a(client: Any, source_id: str) -> dict[str, Any]:
    selected, item = _g12a_items(_member_surface(client, "triage"), source_id)
    runtime = client.app.state.runtime
    evidence_ids = {
        str(value[key])
        for value in (selected, item)
        for key in ("source_event_id", "proposal_event_id", "extraction_event_id")
        if value.get(key)
    }
    if selected.get("reconciliation", {}).get("event_id"):
        evidence_ids.add(selected["reconciliation"]["event_id"])
    evidence = {
        event_id: client.portal.call(runtime.event_log.get, event_id)
        for event_id in sorted(evidence_ids)
    }
    linkage = _validate_g12a_link(
        selected, item, evidence, runtime.settings.instance.tenant_id
    )
    approved = client.post(
        f"/api/member/v1/triage/{item['proposal_id']}/approve",
        headers=HEADERS,
        json={
            "request_id": "fixture-model-lab-g12-approve",
            "expected_last_event_id": item["last_event_id"],
        },
    )
    if approved.status_code != 200:
        raise RuntimeError(f"actual G12a approval failed: HTTP {approved.status_code}")
    approval = approved.json()
    destination = approval.get("destination") or {}
    if (
        destination.get("record_kind") != "task"
        or destination.get("state") != "materialized"
        or destination.get("record_id") != f"triage_task_{item['proposal_id']}"
    ):
        raise RuntimeError("G12a approval did not materialize its actual local task")
    task_id = destination["record_id"]
    reminders = _member_surface(client, "reminders")
    tasks = [
        task for task in reminders["cockpit"]["tasks"] if task["task_id"] == task_id
    ]
    if (
        len(tasks) != 1
        or tasks[0].get("owner_member_id") != MEMBER
        or tasks[0].get("title") != item["title"]
        or tasks[0].get("due_date") != item["candidate"]["due"]
    ):
        raise RuntimeError("actual G12a task is not singular with its approved fields")
    completed = client.post(
        "/api/member/v1/lifecycle",
        headers=HEADERS,
        json={
            "item_kind": "task",
            "item_id": task_id,
            "action": "complete",
            "request_id": "fixture-model-lab-g12-complete",
            "expected_last_event_id": tasks[0]["last_event_id"],
        },
    )
    if (
        completed.status_code != 200
        or completed.json().get("task", {}).get("status") != "done"
    ):
        raise RuntimeError(
            f"actual G12a completion failed: HTTP {completed.status_code}"
        )
    return {
        "source_proposal_link": linkage,
        "selection_evidence": list(evidence.values()),
        "approval": approval,
        "completion": completed.json(),
        "task_id": task_id,
    }


def _immutable_counts(events: list[dict[str, Any]]) -> dict[str, list[str]]:
    types = [
        *SOURCE_TYPES,
        *SKILL_TYPES,
        "triage.item.proposed",
        "task.created",
        "task.completed",
    ]
    return {
        kind: [event["event_id"] for event in events if event["type"] == kind]
        for kind in types
    }


def _task_rows(runtime: Any) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in runtime.projections.connection("tasks").execute(
            "SELECT * FROM tasks ORDER BY task_id"
        )
    ]


def _retry_policy() -> dict[str, Any]:
    path = ROOT / "packs/pipelines/noise_filtering/handler.py"
    source = path.read_bytes()
    assignments = [
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name)
            and target.id == "_TRANSIENT_RECOVERY_DELAYS_SECONDS"
            for target in node.targets
        )
    ]
    if len(assignments) != 1:
        raise LabRefused("production retry policy constant is unavailable")
    delays = ast.literal_eval(assignments[0].value)
    if delays != (0.5, 2.0) or 1 + len(delays) != MAX_ATTEMPTS_PER_SOURCE:
        raise LabRefused(
            "production retry policy changed; review the lab attempt ceiling"
        )
    return {
        "source_path": str(path.relative_to(ROOT)),
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "constant": "_TRANSIENT_RECOVERY_DELAYS_SECONDS",
        "delay_seconds": list(delays),
        "max_gateway_skill_attempts_per_source": MAX_ATTEMPTS_PER_SOURCE,
        "max_source_gateway_skill_attempts": MAX_SOURCES * MAX_ATTEMPTS_PER_SOURCE,
        "gateway_internal_provider_attempts": "not inferred from terminal app receipts",
    }


def _capture_validation_artifacts(runtime: Any) -> list[dict[str, Any]]:
    directory = runtime.settings.instance.raw_events_dir / "skill_validation_failures"
    if not directory.is_dir():
        return []
    return [
        {
            "name": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "raw_response": json.loads(path.read_text(encoding="utf-8")),
        }
        for path in sorted(directory.glob("*.json"))
    ]


def capture(
    output: Path, temporary_root: Path, head: str, cases: list[dict[str, Any]]
) -> int:
    from fastapi.testclient import TestClient

    from adminme.lib.instance_config import load_instance_config
    from adminme.lib.skill_runner.pack_loader import load_pack
    from adminme.runtime.application import RuntimeSettings, create_application

    token = os.environ["OPENCLAW_GATEWAY_TOKEN"]
    pack_root = ROOT / "packs/skills/classify_message_nature"
    pack = load_pack(pack_root)
    report: dict[str, Any] = {
        "contract": "triage-actual-model-capture-v1",
        "commit": head,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "golden_semantic_acceptance": "pending_review",
        "capture_status": "incomplete",
        "fixture_sha256": FIXTURE_HASHES,
        "original_source_limit": MAX_SOURCES,
        "production_retry_policy": _retry_policy(),
        "settlement_deadline_seconds_per_case": TIMEOUT_SECONDS,
        "model_substitutions": False,
        "expected_provider_model": MODEL,
        "invocation_identity": "The pinned Gateway omits invocation_id; immutable source-correlated receipt IDs identify app attempts. No Gateway ID is invented.",
        "expected_labels_sent_to_model": False,
        "clock": "real UTC runtime clock; frozen source occurred_at values preserved",
        "gateway": "http://127.0.0.1:18789/tools/invoke",
        "health_probes": "Gateway/job health evidence is separate; only source-correlated skill receipts count as inference",
        "classifier": {
            "skill_version": pack.version,
            "prompt_sha256": hashlib.sha256(pack.prompt_template.encode()).hexdigest(),
            "input_schema_sha256": _sha_json(pack.input_schema),
            "output_schema_sha256": _sha_json(pack.output_schema),
            "manifest_sha256": hashlib.sha256(
                (pack_root / "SKILL.md").read_bytes()
            ).hexdigest(),
        },
        "cases": [],
        "failures": [],
    }
    _save_report(output, report, token)
    try:
        with tempfile.TemporaryDirectory(
            prefix="adminme-actual-model-lab-", dir=temporary_root
        ) as directory:
            instance = Path(directory)
            os.chmod(instance, 0o700)
            settings = RuntimeSettings(
                instance=load_instance_config(instance),
                encryption_key=secrets.token_bytes(32),
                repository_root=ROOT,
                member_identities={HEADERS["Tailscale-User-Login"]: MEMBER},
                member_roles={MEMBER: "principal"},
                household_mapping={
                    "schema_version": 2,
                    "household_label": "Fixture household",
                    "members": [
                        {
                            "member_id": MEMBER,
                            "label": "Fixture adult",
                            "role": "principal",
                        }
                    ],
                    "memberships": [],
                    "relationships": [],
                    "parties": [],
                },
                adapter_config={"adapter_supervisor_port": 0, "adapters": []},
                deterministic_openclaw_fixture=False,
                openclaw_gateway_token=token,
                openclaw_model_configured=True,
                reconciliation_admission_enabled=True,
                human_triage_management_enabled=True,
            )
            report["tenant_id"] = settings.instance.tenant_id
            with TestClient(create_application(settings)) as client:
                runtime = client.app.state.runtime
                assert client.portal is not None
                source_ids: set[str] = set()
                g12_envelope = None
                completed_task: dict[str, Any] | None = None
                completed_rows: list[dict[str, Any]] | None = None
                try:
                    client.portal.call(_settle, runtime, source_ids)
                    for case in cases:
                        case_report: dict[str, Any] = {
                            "case_id": case["case_id"],
                            "failures": [],
                        }
                        report["cases"].append(case_report)
                        started = time.monotonic()
                        try:
                            if case["case_id"] == "G12b" and completed_task is None:
                                case_report["failures"] = [
                                    "G12a_actual_completed_task_precondition_missing"
                                ]
                                case_report["capture_status"] = "blocked"
                                continue
                            envelope = _source_envelope(
                                case, settings.instance.tenant_id
                            )
                            source_id = client.portal.call(
                                runtime.events.write, envelope
                            )
                            source_ids.add(source_id)
                            case_report["source_event_id"] = source_id
                            case_report["settled_subscribers"] = client.portal.call(
                                _settle,
                                runtime,
                                source_ids,
                                source_id,
                                started + TIMEOUT_SECONDS,
                            )
                            case_report.update(
                                _case_evidence(
                                    client.portal.call(_events, runtime), source_id
                                )
                            )
                            case_report["member_triage"] = _member_surface(
                                client, "triage"
                            )
                            if (
                                case["case_id"] == "G12a"
                                and not case_report["failures"]
                            ):
                                g12_envelope = envelope
                                try:
                                    completed_task = _complete_g12a(client, source_id)
                                    case_report["member_actions"] = completed_task
                                    client.portal.call(
                                        _settle,
                                        runtime,
                                        source_ids,
                                        None,
                                        started + TIMEOUT_SECONDS,
                                    )
                                    completed_rows = _task_rows(runtime)
                                    case_report["completed_canonical_tasks"] = (
                                        completed_rows
                                    )
                                    if (
                                        len(completed_rows) != 1
                                        or completed_rows[0]["task_id"]
                                        != completed_task["task_id"]
                                        or completed_rows[0]["status"] != "done"
                                    ):
                                        raise RuntimeError(
                                            "G12a canonical completed task is not singular"
                                        )
                                except LabRefused:
                                    raise
                                except RuntimeError as exc:
                                    completed_task = None
                                    case_report["failures"].append(str(exc))
                            if case["case_id"] == "G12b":
                                assert completed_task is not None
                                assert g12_envelope is not None
                                before = client.portal.call(_events, runtime)
                                before_tasks = _task_rows(runtime)
                                replay_id = client.portal.call(
                                    runtime.events.write, g12_envelope
                                )
                                client.portal.call(
                                    _settle,
                                    runtime,
                                    source_ids,
                                    None,
                                    started + TIMEOUT_SECONDS,
                                )
                                after = client.portal.call(_events, runtime)
                                unchanged = _immutable_counts(
                                    before
                                ) == _immutable_counts(after)
                                task_rows = _task_rows(runtime)
                                singular = (
                                    len(task_rows) == 1
                                    and task_rows[0]["status"] == "done"
                                    and task_rows[0]["task_id"]
                                    == completed_task["task_id"]
                                )
                                task_unchanged = (
                                    completed_rows == before_tasks == task_rows
                                )
                                case_report["redelivery"] = {
                                    "source_event_id": replay_id,
                                    "before": _immutable_counts(before),
                                    "after": _immutable_counts(after),
                                    "unchanged": unchanged,
                                    "completed_task_unchanged": task_unchanged,
                                }
                                case_report["canonical_tasks"] = task_rows
                                if not unchanged or not singular or not task_unchanged:
                                    case_report["failures"].append(
                                        "G12_redelivery_or_completed_task_continuity_failed"
                                    )
                            case_report["capture_status"] = (
                                "complete" if not case_report["failures"] else "failed"
                            )
                        except Exception as exc:
                            case_report["capture_status"] = "incomplete"
                            case_report["failures"].append(
                                {"type": type(exc).__name__, "detail": str(exc)}
                            )
                            # Stop adding sources on a failed settlement or
                            # invariant. The final capture retains partial events.
                            raise
                        finally:
                            case_report["duration_seconds"] = round(
                                time.monotonic() - started, 3
                            )
                            _save_report(output, report, token)
                            print(
                                json.dumps(
                                    {
                                        "case_id": case_report["case_id"],
                                        "capture_status": case_report.get(
                                            "capture_status", "incomplete"
                                        ),
                                        "duration_seconds": case_report[
                                            "duration_seconds"
                                        ],
                                    }
                                ),
                                flush=True,
                            )
                finally:
                    report["events"] = client.portal.call(_events, runtime)
                    report["original_source_count"] = len(source_ids)
                    report["skill_terminal_counts"] = {
                        kind: sum(event["type"] == kind for event in report["events"])
                        for kind in SKILL_TYPES
                    }
                    report["raw_validation_failures"] = _capture_validation_artifacts(
                        runtime
                    )
                    for case_report in report["cases"]:
                        if case_report.get("source_event_id"):
                            evidence = _case_evidence(
                                report["events"], case_report["source_event_id"]
                            )
                            case_report["final_source_evidence"] = evidence
                            case_report["skill_terminal_counts"] = {
                                kind: sum(
                                    event["type"] == kind
                                    for event in evidence["events"]
                                )
                                for kind in SKILL_TYPES
                            }
                            if (
                                evidence["failures"]
                                and case_report.get("capture_status") == "complete"
                            ):
                                case_report["failures"].extend(evidence["failures"])
                                case_report["capture_status"] = "failed"
                    report["provider_effect_events"] = [
                        event
                        for event in report["events"]
                        if event["type"].startswith("external_sync.write_")
                        or event["type"] == "external.sent"
                        or event.get("payload", {}).get("provider_called") is True
                    ]
                    if report["provider_effect_events"]:
                        report["failures"].append(
                            "unexpected_provider_effect_or_request"
                        )
                    for surface in ("triage", "reminders"):
                        try:
                            report[f"member_{surface}_final"] = _member_surface(
                                client, surface
                            )
                        except Exception as exc:
                            report["failures"].append(
                                {
                                    "phase": f"final_{surface}",
                                    "type": type(exc).__name__,
                                    "detail": str(exc),
                                }
                            )
    except Exception as exc:
        report["failures"].append(
            {"phase": "capture", "type": type(exc).__name__, "detail": str(exc)}
        )
    attempted_cases = {case["case_id"] for case in report["cases"]}
    for case in cases:
        if case["case_id"] not in attempted_cases:
            report["cases"].append(
                {
                    "case_id": case["case_id"],
                    "capture_status": "not_attempted",
                    "duration_seconds": 0,
                    "failures": ["capture_halted_before_source_append"],
                }
            )
    complete = (
        len(report["cases"]) == MAX_SOURCES
        and report.get("original_source_count") == MAX_SOURCES
        and not report["failures"]
        and all(case.get("capture_status") == "complete" for case in report["cases"])
    )
    report["capture_status"] = "complete" if complete else "incomplete"
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    _save_report(output, report, token)
    return 0 if complete else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    head = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
    ).strip()
    temporary_root = validate_environment(
        os.environ,
        system=platform.system(),
        head=head,
        as_of=datetime.now(timezone.utc).date(),
    )
    if subprocess.check_output(
        ["git", "-C", str(ROOT), "status", "--porcelain"], text=True
    ).strip():
        raise LabRefused("the approved checkout must be clean")
    output = arguments.output.resolve()
    if (
        not output.is_relative_to(temporary_root)
        or not output.parent.is_dir()
        or output.exists()
    ):
        raise LabRefused(
            "report must be a new file under RUNNER_TEMP outside the fresh instance"
        )
    cases = load_frozen_cases()
    return capture(output, temporary_root, head, cases)


if __name__ == "__main__":
    raise SystemExit(main())
