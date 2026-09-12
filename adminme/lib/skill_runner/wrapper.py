"""Skill runner wrapper — thin client for OpenClaw's skill substrate.

Every AdministrateMe LLM call flows through this wrapper. AdministrateMe
NEVER imports anthropic / openai / any provider SDK [§8] / [D6]; the only
seam to OpenClaw is `httpx.AsyncClient` against the gateway HTTP API
`POST http://127.0.0.1:18789/tools/invoke` with `tool: "llm-task"` per
[ADR-0002].

Public surface:
- `run_skill(skill_id, inputs, ctx, *, input_sensitivities=None)` — the
  9-step flow per [BUILD.md §L4-continued].
- `SkillContext` — frozen dataclass carrying Session + correlation +
  observation/dry-run flags.
- `SkillResult` — output + provider + token/cost provenance + duration.
- `set_default_event_sink(events)` — module-level injection used by service
  start (08b's outbound() uses the same pattern).
- Four exceptions: `SkillInputInvalid`, `SkillSensitivityRefused`,
  `SkillScopeInsufficient`, `OpenClawResponseMalformed`.

The wrapper is the SINGLE EMIT SEAM for `skill.call.recorded`,
`skill.call.failed`, `skill.call.suppressed`. `verify_invariants.sh`
allowlists these emits to this file only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

from adminme.events.envelope import EventEnvelope
from adminme.events.log import IdempotencyConflict
from adminme.lib.skill_runner.pack_loader import LoadedPack, load_pack

if TYPE_CHECKING:
    from adminme.events.writer import EventSink
    from adminme.lib.session import Session

_log = logging.getLogger(__name__)

OPENCLAW_GATEWAY_URL = "http://127.0.0.1:18789/tools/invoke"
OPENCLAW_AGENT_ID = "main"
OPENCLAW_GATEWAY_TOKEN_ENV = "OPENCLAW_GATEWAY_TOKEN"
INPUT_SPILL_THRESHOLD_BYTES = 50 * 1024


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SkillInputInvalid(Exception):
    """`inputs` failed validation against the pack's input.schema.json."""


class SkillSensitivityRefused(Exception):
    """Caller passed privileged inputs to a non-privileged-declaring skill
    [§13]."""


class SkillScopeInsufficient(Exception):
    """`context_scopes_required` is not a subset of `Session.allowed_scopes`."""


class OpenClawResponseMalformed(Exception):
    """200 response from OpenClaw with an envelope shape that does not
    conform to `{ok: true, result}` per [ADR-0002] / d02."""


class OpenClawUnreachable(Exception):
    """OpenClaw could not complete its single routed invocation."""


class OpenClawTimeout(Exception):
    """OpenClaw's single routed invocation timed out."""


class SkillHandlerError(Exception):
    """Pack `handler.py` `post_process` raised."""


class SkillOutputInvalid(Exception):
    """OpenClaw response (post-handler) failed output.schema.json."""


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SkillContext:
    """Per-call context. Carries Session, optional correlation override,
    and the two short-circuit flags."""

    session: "Session"
    correlation_id: str | None = None
    observation_mode_active: bool = False
    dry_run: bool = False
    triggering_event_id: str | None = None
    invocation_idempotency_key: str | None = None
    retry_prior_failure: bool = False
    owner_scope: str = "shared:household"
    visibility_scope: str = "shared:household"
    sensitivity: str = "normal"
    compatible_failure_recovery_authorized: bool = False
    require_clean_result: bool = False
    compatible_success_identities: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillResult:
    output: dict
    openclaw_invocation_id: str | None
    provider: str
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    duration_ms: int
    skill_version: str | None = None
    recorded_event_id: str | None = None


def _recorded_skill_result(event: dict[str, Any]) -> SkillResult:
    """Return the canonical result stored for one reviewed invocation."""

    payload = event["payload"]
    return SkillResult(
        output=dict(payload["outputs"]),
        openclaw_invocation_id=payload.get("openclaw_invocation_id"),
        provider=str(payload["provider"]),
        input_tokens=payload.get("input_tokens"),
        output_tokens=payload.get("output_tokens"),
        cost_usd=payload.get("cost_usd"),
        duration_ms=int(payload["duration_ms"]),
        skill_version=str(payload["skill_version"]),
        recorded_event_id=str(event["event_id"]),
    )


def _same_recorded_scope(event: dict[str, Any], ctx: SkillContext) -> bool:
    return all(
        event.get(key) == value
        for key, value in {
            "tenant_id": ctx.session.tenant_id,
            "owner_scope": ctx.owner_scope,
            "visibility_scope": ctx.visibility_scope,
            "sensitivity": ctx.sensitivity,
        }.items()
    )


_RECORDED_FAILURE_TYPES: dict[str, type[Exception]] = {
    "input_invalid": SkillInputInvalid,
    "sensitivity_refused": SkillSensitivityRefused,
    "scope_insufficient": SkillScopeInsufficient,
    "openclaw_malformed_response": OpenClawResponseMalformed,
    "openclaw_timeout": OpenClawTimeout,
    "openclaw_unreachable": OpenClawUnreachable,
    "handler_raised": SkillHandlerError,
    "output_invalid": SkillOutputInvalid,
}


def _raise_recorded_failure(
    event: dict[str, Any],
    *,
    compatible_receipt: dict[str, Any] | None = None,
    compatible_terminal: bool = False,
) -> None:
    payload = event["payload"]
    failure_class = str(payload.get("failure_class") or "")
    error_type = _RECORDED_FAILURE_TYPES.get(failure_class, OpenClawUnreachable)
    error = error_type(str(payload.get("error_detail") or failure_class))
    error.skill_version = payload.get("skill_version")  # type: ignore[attr-defined]
    if compatible_receipt is not None:
        error.compatible_receipt_event_id = compatible_receipt["event_id"]  # type: ignore[attr-defined]
        error.compatible_receipt_key = compatible_receipt["idempotency_key"]  # type: ignore[attr-defined]
    if compatible_terminal:
        error.compatible_failure_event_id = event["event_id"]  # type: ignore[attr-defined]
        error.compatible_failure_key = event["idempotency_key"]  # type: ignore[attr-defined]
        error.compatible_failure_class = failure_class  # type: ignore[attr-defined]
    raise error


async def _compatible_recorded_result(
    runtime: _Runtime,
    pack: LoadedPack,
    ctx: SkillContext,
    inputs: dict,
    event: dict[str, Any],
    version: str,
    identity: str,
    receipts: list[dict[str, Any]],
) -> SkillResult:
    """Reuse a declared patch-compatible success without inventing a receipt."""
    payload = event.get("payload") or {}
    expected_key = f"skill:{identity}:{pack.pack_id}:{version}"
    if (
        event.get("type") != "skill.call.recorded"
        or event.get("schema_version") not in {1, 2}
        or event.get("idempotency_key") != expected_key
        or not _same_recorded_scope(event, ctx)
        or payload.get("skill_name") != pack.pack_id
        or payload.get("skill_version") != version
        or payload.get("provider") in {None, "", "suppressed"}
    ):
        raise SkillOutputInvalid("compatible receipt identity or scope is invalid")

    recorded_inputs = payload.get("inputs")
    if isinstance(recorded_inputs, dict) and set(recorded_inputs) == {"_spilled_to"}:
        try:
            spill = Path(recorded_inputs["_spilled_to"]).resolve(strict=True)
            allowed = (runtime.raw_events_dir / "skill_large_inputs").resolve()
            if not spill.is_relative_to(allowed):
                raise ValueError("input spill is outside its owned directory")
            recorded_inputs = json.loads(spill.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError) as exc:
            raise SkillOutputInvalid(
                "compatible receipt inputs are unavailable"
            ) from exc
    if recorded_inputs != inputs:
        raise SkillInputInvalid("compatible receipt inputs do not match invocation")
    try:
        Draft202012Validator(pack.output_schema).validate(payload.get("outputs"))
    except JsonSchemaValidationError as exc:
        raise SkillOutputInvalid("compatible receipt output is invalid") from exc

    # A defensive default can have both failed and recorded receipts. Only a
    # clean success is compatible; a patch never converts a failure to success.
    failure_prefix = f"skill:failed:{identity}:{pack.pack_id}:{version}:"
    for prior in receipts:
        if (
            prior.get("type") == "skill.call.failed"
            and _same_recorded_scope(prior, ctx)
            and str(prior.get("idempotency_key") or "").startswith(failure_prefix)
        ):
            _raise_recorded_failure(prior, compatible_receipt=event)
    return _recorded_skill_result(event)


# ---------------------------------------------------------------------------
# EventLog DI (matches 08b's ObservationManager pattern)
# ---------------------------------------------------------------------------


_DEFAULT_EVENT_SINK: "EventSink | None" = None


def set_default_event_sink(events: "EventSink | None") -> None:
    """Wire the module-level event sink used by `run_skill()` when no
    per-call override is supplied. Bootstrap §7 calls this at service
    start; tests inject a per-test log via `_runtime`."""
    global _DEFAULT_EVENT_SINK
    _DEFAULT_EVENT_SINK = events


@dataclass(frozen=True)
class _Runtime:
    """Test-injectable bundle. Production callers do not construct this
    directly; production wiring lives in bootstrap."""

    event_sink: "EventSink"
    raw_events_dir: Path
    httpx_client_factory: Any = None  # callable() -> httpx.AsyncClient | None
    gateway_url: str = OPENCLAW_GATEWAY_URL
    gateway_token: str | None = field(default=None, repr=False)
    event_log: Any = None
    invocation_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = field(
        default_factory=weakref.WeakValueDictionary,
        compare=False,
        repr=False,
    )


_RUNTIME_OVERRIDE: _Runtime | None = None


def build_skill_runtime(
    *,
    event_sink: "EventSink",
    event_log: Any,
    raw_events_dir: Path,
    gateway_token: str | None,
) -> _Runtime:
    """Build the explicit production dependency bundle for ``run_skill``.

    The central runtime must not rely on process-global environment lookup for
    durable event replay or spill paths.  Keeping the event log in this bundle
    also lets retried pipeline deliveries reuse the already-recorded model
    result instead of making a second, potentially different invocation.
    """

    return _Runtime(
        event_sink=event_sink,
        event_log=event_log,
        raw_events_dir=Path(raw_events_dir),
        gateway_token=gateway_token,
    )


def _set_runtime_for_tests(runtime: _Runtime | None) -> None:
    global _RUNTIME_OVERRIDE
    _RUNTIME_OVERRIDE = runtime


def _resolve_runtime() -> _Runtime:
    if _RUNTIME_OVERRIDE is not None:
        return _RUNTIME_OVERRIDE
    if _DEFAULT_EVENT_SINK is None:
        raise OpenClawUnreachable(
            "skill_runner: no default EventSink configured; "
            "call set_default_event_sink(...) at service start"
        )
    # Production resolves raw_events_dir via InstanceConfig at bootstrap.
    # If we land here without an override AND no instance dir, surface
    # the gap loudly rather than guess.
    instance_dir = os.environ.get("ADMINME_INSTANCE_DIR")
    if not instance_dir:
        raise OpenClawUnreachable(
            "skill_runner: ADMINME_INSTANCE_DIR unset; cannot resolve "
            "raw_events_dir per [§15]"
        )
    return _Runtime(
        event_sink=_DEFAULT_EVENT_SINK,
        raw_events_dir=Path(instance_dir) / "data" / "raw_events",
        gateway_token=os.environ.get(OPENCLAW_GATEWAY_TOKEN_ENV),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_gateway_token(runtime: _Runtime) -> str | None:
    """Return the token injected by the production composition root.

    The legacy fallback runtime may populate this field from its environment,
    but an explicitly constructed runtime must never depend on ambient process
    state for Gateway authentication.
    """

    return runtime.gateway_token


def _build_envelope(
    *,
    tenant_id: str,
    event_type: str,
    schema_version: int,
    payload: dict,
    actor_identity: str,
    owner_scope: str = "shared:household",
    visibility_scope: str = "shared:household",
    sensitivity: str = "normal",
) -> EventEnvelope:
    now_iso = EventEnvelope.now_utc_iso()
    return EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id=tenant_id,
        type=event_type,
        schema_version=schema_version,
        occurred_at=now_iso,
        recorded_at=now_iso,
        source_adapter="skill_runner",
        source_account_id="system",
        owner_scope=owner_scope,
        visibility_scope=visibility_scope,
        sensitivity=sensitivity,  # type: ignore[arg-type]
        actor_identity=actor_identity,
        payload=payload,
    )


async def _emit_failed(
    runtime: _Runtime,
    ctx: SkillContext,
    *,
    pack_id_str: str,
    pack_version: str,
    failure_class: str,
    error_detail: str,
    provider_attempted: str | None,
    duration_ms: int | None,
) -> None:
    correlation = ctx.correlation_id or ctx.session.correlation_id
    envelope = _build_envelope(
        tenant_id=ctx.session.tenant_id,
        event_type="skill.call.failed",
        schema_version=1,
        payload={
            "skill_name": pack_id_str,
            "skill_version": pack_version,
            "failure_class": failure_class,
            "error_detail": error_detail,
            "correlation_id": correlation,
            "provider_attempted": provider_attempted,
            "duration_ms_until_failure": duration_ms,
        },
        actor_identity=ctx.session.auth_member_id,
        owner_scope=ctx.owner_scope,
        visibility_scope=ctx.visibility_scope,
        sensitivity=ctx.sensitivity,
    )
    invocation_identity = ctx.invocation_idempotency_key or ctx.triggering_event_id
    if invocation_identity:
        envelope.idempotency_key = (
            f"skill:failed:{invocation_identity}:{pack_id_str}:"
            f"{pack_version}:{failure_class}"
        )
    await runtime.event_sink.write(
        envelope,
        correlation_id=correlation,
        causation_id=ctx.triggering_event_id,
    )


async def _emit_suppressed(
    runtime: _Runtime,
    ctx: SkillContext,
    *,
    pack_id_str: str,
    pack_version: str,
    reason: str,
    would_have_sent: dict,
) -> None:
    correlation = ctx.correlation_id or ctx.session.correlation_id
    envelope = _build_envelope(
        tenant_id=ctx.session.tenant_id,
        event_type="skill.call.suppressed",
        schema_version=1,
        payload={
            "skill_name": pack_id_str,
            "skill_version": pack_version,
            "reason": reason,
            "would_have_sent": would_have_sent,
            "correlation_id": correlation,
        },
        actor_identity=ctx.session.auth_member_id,
        owner_scope=ctx.owner_scope,
        visibility_scope=ctx.visibility_scope,
        sensitivity=ctx.sensitivity,
    )
    if ctx.triggering_event_id:
        envelope.idempotency_key = (
            f"skill:suppressed:{ctx.triggering_event_id}:{pack_id_str}:"
            f"{pack_version}:{reason}"
        )
    await runtime.event_sink.write(
        envelope,
        correlation_id=correlation,
        causation_id=ctx.triggering_event_id,
    )


async def _emit_recorded(
    runtime: _Runtime,
    ctx: SkillContext,
    *,
    pack_id_str: str,
    pack_version: str,
    invocation_id: str | None,
    inputs_payload: dict,
    outputs: dict,
    provider: str,
    input_tokens: int | None,
    output_tokens: int | None,
    cost_usd: float | None,
    duration_ms: int,
) -> str:
    correlation = ctx.correlation_id or ctx.session.correlation_id
    envelope = _build_envelope(
        tenant_id=ctx.session.tenant_id,
        event_type="skill.call.recorded",
        schema_version=2,
        payload={
            "skill_name": pack_id_str,
            "skill_version": pack_version,
            "openclaw_invocation_id": invocation_id,
            "inputs": inputs_payload,
            "outputs": outputs,
            "provider": provider,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd,
            "duration_ms": duration_ms,
        },
        actor_identity=ctx.session.auth_member_id,
        owner_scope=ctx.owner_scope,
        visibility_scope=ctx.visibility_scope,
        sensitivity=ctx.sensitivity,
    )
    invocation_identity = ctx.invocation_idempotency_key or ctx.triggering_event_id
    envelope.idempotency_key = (
        f"skill:{invocation_identity}:{pack_id_str}:{pack_version}"
        if invocation_identity
        else None
    )
    return await runtime.event_sink.write(
        envelope,
        correlation_id=correlation,
        causation_id=ctx.triggering_event_id,
    )


def _spill_inputs_if_large(
    runtime: _Runtime, inputs: dict, *, subdir: str = "skill_large_inputs"
) -> dict:
    """If `inputs` (JSON-serialized) exceeds the spill threshold, write
    it to `<raw_events>/<subdir>/<event_id>.json` and return a sentinel
    `{"_spilled_to": "<abs path>"}` payload. Otherwise return inputs
    unchanged."""
    serialized = json.dumps(inputs, separators=(",", ":"))
    if len(serialized.encode("utf-8")) <= INPUT_SPILL_THRESHOLD_BYTES:
        return inputs
    spill_dir = runtime.raw_events_dir / subdir
    spill_dir.mkdir(parents=True, exist_ok=True)
    spill_id = uuid.uuid4().hex
    spill_path = spill_dir / f"{spill_id}.json"
    spill_path.write_text(serialized, encoding="utf-8")
    return {"_spilled_to": str(spill_path)}


def _save_validation_failure(runtime: _Runtime, raw_response: Any) -> Path:
    """Persist a malformed/post-process-failing response to
    `<raw_events>/skill_validation_failures/` for replay debugging."""
    target_dir = runtime.raw_events_dir / "skill_validation_failures"
    target_dir.mkdir(parents=True, exist_ok=True)
    spill_id = uuid.uuid4().hex
    target = target_dir / f"{spill_id}.json"
    try:
        body = json.dumps(raw_response, default=str)
    except Exception:  # noqa: BLE001
        body = json.dumps({"_unserializable": str(raw_response)})
    target.write_text(body, encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# OpenClaw HTTP call
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _OpenClawCallOutcome:
    """Internal result of one /tools/invoke attempt against one provider."""

    kind: str  # "ok" | "transient" | "timeout" | "malformed" | "deterministic_4xx"
    raw_result: Any = None
    invocation_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    error_detail: str = ""
    provider_model: str | None = None


def _build_request_body(
    *,
    pack: LoadedPack,
    inputs: dict,
    session_key: str,
) -> dict:
    """Translate pack manifest → llm-task tool args per [ADR-0002]."""
    fm = pack.skill_frontmatter
    timeout_seconds = int(fm.get("timeout_seconds", 15))
    args: dict[str, Any] = {
        "prompt": pack.prompt_template,
        "input": inputs,
        "schema": pack.output_schema,
        "maxTokens": int(fm.get("max_tokens", 800)),
        "timeoutMs": timeout_seconds * 1000,
        "temperature": float(fm.get("temperature", 0.0)),
    }
    if "thinking" in fm:
        args["thinking"] = fm["thinking"]
    return {
        "tool": "llm-task",
        "action": "json",
        "args": args,
        "agentId": OPENCLAW_AGENT_ID,
        "sessionKey": session_key,
        "dryRun": False,
    }


def _parse_openclaw_response(payload: Any) -> _OpenClawCallOutcome:
    """Parse a 200 response body. Per [ADR-0002] the envelope is
    `{ok: true, result: ...}` on success; deviations are malformed."""
    if not isinstance(payload, dict):
        return _OpenClawCallOutcome(
            kind="malformed", error_detail="response body is not a JSON object"
        )
    if "ok" not in payload:
        return _OpenClawCallOutcome(
            kind="malformed", error_detail="response missing 'ok' field"
        )
    if payload["ok"] is not True:
        return _OpenClawCallOutcome(
            kind="malformed",
            error_detail=f"response ok={payload['ok']!r} (expected True on 200)",
        )
    result = payload.get("result")
    if not isinstance(result, dict):
        return _OpenClawCallOutcome(
            kind="malformed", error_detail="response 'result' is not an object"
        )
    # llm-task returns details.json containing the parsed JSON per
    # docs/reference/openclaw/tools/llm-task.md.
    details = result.get("details")
    parsed = details.get("json") if isinstance(details, dict) else None
    # Allow callers / mocks to put the parsed object at result.json directly
    # for simplicity (the reference llm-task docs show details.json but the
    # Lobster example collapses it). Try both.
    if parsed is None:
        parsed = result.get("json")
    if parsed is None:
        # Some response shapes inline the parsed JSON as the `result` itself.
        parsed = result.get("output")
    if not isinstance(parsed, dict):
        return _OpenClawCallOutcome(
            kind="malformed",
            error_detail="response 'result' did not include parsed json output",
        )
    invocation_id = result.get("invocation_id") or result.get("invocationId")
    provider_model = None
    # The pinned llm-task reports the actual routed pair beside details.json.
    # Retain older top-level pairs, but never combine fields from two levels.
    for provenance in (details, result):
        if not isinstance(provenance, dict):
            continue
        provider = provenance.get("provider")
        model = provenance.get("model")
        if (
            isinstance(provider, str)
            and provider.strip()
            and isinstance(model, str)
            and model.strip()
        ):
            provider_model = f"{provider}/{model}"
            break
    return _OpenClawCallOutcome(
        kind="ok",
        raw_result=parsed,
        invocation_id=invocation_id if isinstance(invocation_id, str) else None,
        input_tokens=result.get("tokens_in")
        if isinstance(result.get("tokens_in"), int)
        else None,
        output_tokens=result.get("tokens_out")
        if isinstance(result.get("tokens_out"), int)
        else None,
        cost_usd=result.get("cost_usd")
        if isinstance(result.get("cost_usd"), (int, float))
        else None,
        provider_model=provider_model,
    )


async def _post_one_provider(
    *,
    runtime: _Runtime,
    body: dict,
    token: str | None,
    timeout_seconds: int,
) -> _OpenClawCallOutcome:
    """One POST to `/tools/invoke` for a single provider."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    # HTTPX limits individual network operations. The surrounding asyncio
    # deadline also bounds a streaming response and injected clients in total.
    httpx_timeout = httpx.Timeout(timeout_seconds + 2.0)

    if runtime.httpx_client_factory is not None:
        client = runtime.httpx_client_factory()
        owns_client = False
    else:
        client = httpx.AsyncClient(timeout=httpx_timeout)
        owns_client = True

    try:
        try:
            async with asyncio.timeout(timeout_seconds + 2.0):
                response = await client.post(
                    runtime.gateway_url, json=body, headers=headers
                )
        except TimeoutError:
            return _OpenClawCallOutcome(
                kind="timeout", error_detail="openclaw total invocation timeout"
            )
        except httpx.TimeoutException as exc:
            return _OpenClawCallOutcome(
                kind="timeout",
                error_detail=f"openclaw timeout: {exc}",
            )
        except httpx.HTTPError as exc:
            return _OpenClawCallOutcome(
                kind="transient",
                error_detail=f"openclaw network error: {exc}",
            )
    finally:
        if owns_client:
            await client.aclose()

    status = response.status_code
    if status == 200:
        try:
            payload = response.json()
        except json.JSONDecodeError:
            return _OpenClawCallOutcome(
                kind="malformed", error_detail="200 response was not JSON"
            )
        return _parse_openclaw_response(payload)
    if 500 <= status <= 599:
        return _OpenClawCallOutcome(
            kind="transient",
            error_detail=f"openclaw {status}: {response.text[:200]}",
        )
    return _OpenClawCallOutcome(
        kind="deterministic_4xx",
        error_detail=f"openclaw {status}: {response.text[:200]}",
    )


# ---------------------------------------------------------------------------
# run_skill — the 9-step flow per [BUILD.md L4-continued] + [ADR-0002]
# ---------------------------------------------------------------------------


async def run_skill(
    skill_id: str,
    inputs: dict,
    ctx: SkillContext,
    *,
    input_sensitivities: dict[str, str] | None = None,
    _runtime: _Runtime | None = None,
) -> SkillResult:
    """Run one durable skill identity at a time inside the shared runtime."""

    runtime = _runtime or _resolve_runtime()
    invocation_identity = ctx.invocation_idempotency_key or ctx.triggering_event_id
    if not invocation_identity:
        return await _run_skill_unlocked(
            skill_id,
            inputs,
            ctx,
            input_sensitivities=input_sensitivities,
            _runtime=runtime,
        )
    lock_key = ":".join((ctx.session.tenant_id, skill_id, invocation_identity))
    lock = runtime.invocation_locks.get(lock_key)
    if lock is None:
        lock = asyncio.Lock()
        runtime.invocation_locks[lock_key] = lock
    async with lock:
        return await _run_skill_unlocked(
            skill_id,
            inputs,
            ctx,
            input_sensitivities=input_sensitivities,
            _runtime=runtime,
        )


async def _run_skill_unlocked(
    skill_id: str,
    inputs: dict,
    ctx: SkillContext,
    *,
    input_sensitivities: dict[str, str] | None = None,
    _runtime: _Runtime | None = None,
) -> SkillResult:
    """Validate and invoke after any exact-identity single-flight lock."""
    runtime = _runtime or _resolve_runtime()

    # Step 1 — load pack.
    pack = load_pack(
        Path(skill_id) if Path(skill_id).is_absolute() else _resolve_pack_root(skill_id)
    )
    pack_id_str = pack.pack_id
    pack_version = pack.version
    fm = pack.skill_frontmatter
    compatible_versions = tuple(fm.get("compatible_success_versions") or ())
    compatible_prior: tuple[dict[str, Any], str, str] | None = None
    compatible_receipts: list[dict[str, Any]] = []

    invocation_identity = ctx.invocation_idempotency_key or ctx.triggering_event_id
    idempotency_key = (
        f"skill:{invocation_identity}:{pack_id_str}:{pack_version}"
        if invocation_identity
        else None
    )
    if idempotency_key and runtime.event_log is not None:
        assert invocation_identity is not None
        prior = await runtime.event_log.get_by_idempotency(
            ctx.session.tenant_id, idempotency_key
        )
        if prior is not None:
            if ctx.require_clean_result:
                failures = await runtime.event_log.get_by_idempotency_keys(
                    ctx.session.tenant_id,
                    [
                        f"skill:failed:{invocation_identity}:{pack_id_str}:{pack_version}:{failure}"
                        for failure in _RECORDED_FAILURE_TYPES
                    ],
                )
                try:
                    Draft202012Validator(pack.input_schema).validate(inputs)
                except JsonSchemaValidationError as exc:
                    raise SkillInputInvalid(
                        "recorded receipt inputs are invalid"
                    ) from exc
                return await _compatible_recorded_result(
                    runtime=runtime,
                    pack=pack,
                    ctx=ctx,
                    inputs=inputs,
                    event=prior,
                    version=pack_version,
                    identity=invocation_identity,
                    receipts=failures,
                )
            return _recorded_skill_result(prior)
        compatible_identities = tuple(
            dict.fromkeys((invocation_identity, *ctx.compatible_success_identities))
        )
        if compatible_versions and len(compatible_identities) > 3:
            raise SkillInputInvalid(
                "compatible success lookup permits at most three identities"
            )
        for compatible_version in compatible_versions:
            keys = [
                key
                for identity in compatible_identities
                for key in (
                    f"skill:{identity}:{pack_id_str}:{compatible_version}",
                    *(
                        f"skill:failed:{identity}:{pack_id_str}:{compatible_version}:{failure}"
                        for failure in _RECORDED_FAILURE_TYPES
                    ),
                )
            ]
            receipts = await runtime.event_log.get_by_idempotency_keys(
                ctx.session.tenant_id, keys
            )
            compatible_receipts.extend(receipts)
            by_key = {receipt.get("idempotency_key"): receipt for receipt in receipts}
            for compatible_identity in compatible_identities:
                prior = by_key.get(
                    f"skill:{compatible_identity}:{pack_id_str}:{compatible_version}"
                )
                if prior is not None:
                    compatible_prior = (prior, compatible_version, compatible_identity)
                    break
            if compatible_prior is not None:
                break
        related = await runtime.event_log.get_by_causation(ctx.triggering_event_id)
        if compatible_prior is None and not ctx.compatible_failure_recovery_authorized:
            for terminal in reversed([*compatible_receipts, *related]):
                if (
                    terminal["type"] == "skill.call.failed"
                    and _same_recorded_scope(terminal, ctx)
                    and terminal["payload"].get("skill_name") == pack_id_str
                    and terminal["payload"].get("skill_version") in compatible_versions
                ):
                    # A patch is not permission to repeat a spent source call.
                    # The pipeline's validated recovery-request path creates
                    # a separate authorized identity when a retry is required.
                    _raise_recorded_failure(terminal, compatible_terminal=True)
        related_terminals = [
            event
            for event in related
            if event["type"] in {"skill.call.failed", "skill.call.suppressed"}
            and event["payload"].get("skill_name") == pack_id_str
            and event["payload"].get("skill_version") == pack_version
        ]
        prior_terminal = related_terminals[-1] if related_terminals else None
        if prior_terminal is not None:
            if prior_terminal["type"] == "skill.call.suppressed":
                defensive_default = fm.get("on_failure") or {}
                return SkillResult(
                    output=(
                        defensive_default if isinstance(defensive_default, dict) else {}
                    ),
                    openclaw_invocation_id=None,
                    provider="suppressed",
                    input_tokens=None,
                    output_tokens=None,
                    cost_usd=None,
                    duration_ms=0,
                )
            exact_failure_prefix = (
                f"skill:failed:{invocation_identity}:{pack_id_str}:{pack_version}:"
            )
            exact_invocation_failure = next(
                (
                    event
                    for event in reversed(related_terminals)
                    if event["type"] == "skill.call.failed"
                    and str(event.get("idempotency_key") or "").startswith(
                        exact_failure_prefix
                    )
                ),
                None,
            )
            if exact_invocation_failure is not None:
                # A later sibling attempt can be the newest causal terminal.
                # The current identity is nevertheless already spent and must
                # remain terminal across a crash/restart.
                prior_terminal = exact_invocation_failure
            elif ctx.retry_prior_failure:
                # No result exists for this exact reviewed identity. It may
                # bypass one older causal failure, then its own failure key
                # will make every replay terminal.
                prior_terminal = None
        if prior_terminal is not None:
            _raise_recorded_failure(prior_terminal)

    # Step 2 — input validation.
    try:
        Draft202012Validator(pack.input_schema).validate(inputs)
    except JsonSchemaValidationError as exc:
        await _emit_failed(
            runtime,
            ctx,
            pack_id_str=pack_id_str,
            pack_version=pack_version,
            failure_class="input_invalid",
            error_detail=f"input schema rejected payload: {exc.message}",
            provider_attempted=None,
            duration_ms=0,
        )
        raise SkillInputInvalid(str(exc)) from exc

    # Step 3 — sensitivity check.
    declared_sensitivity = str(fm.get("sensitivity_required", "normal"))
    if input_sensitivities:
        max_sens = _max_sensitivity(input_sensitivities.values())
        if max_sens == "privileged" and declared_sensitivity != "privileged":
            await _emit_failed(
                runtime,
                ctx,
                pack_id_str=pack_id_str,
                pack_version=pack_version,
                failure_class="sensitivity_refused",
                error_detail=(
                    f"caller passed privileged input but skill declares "
                    f"sensitivity_required={declared_sensitivity!r}"
                ),
                provider_attempted=None,
                duration_ms=0,
            )
            raise SkillSensitivityRefused("skill refuses privileged inputs")

    # Step 4 — scope check.
    required_scopes = list(fm.get("context_scopes_required") or [])
    allowed = ctx.session.allowed_scopes
    missing = [s for s in required_scopes if s not in allowed]
    if missing:
        await _emit_failed(
            runtime,
            ctx,
            pack_id_str=pack_id_str,
            pack_version=pack_version,
            failure_class="scope_insufficient",
            error_detail=f"session missing required scopes: {missing}",
            provider_attempted=None,
            duration_ms=0,
        )
        raise SkillScopeInsufficient(f"session missing required scopes: {missing}")

    # Step 5 — observation / dry-run short-circuit.
    outbound_affecting = bool(fm.get("outbound_affecting", False))
    suppress_reason: str | None = None
    if ctx.dry_run:
        suppress_reason = "dry_run"
    elif ctx.observation_mode_active and outbound_affecting:
        suppress_reason = "observation_mode_active"
    if suppress_reason is not None:
        await _emit_suppressed(
            runtime,
            ctx,
            pack_id_str=pack_id_str,
            pack_version=pack_version,
            reason=suppress_reason,
            would_have_sent={"inputs": inputs},
        )
        defensive_default = fm.get("on_failure") or {}
        return SkillResult(
            output=defensive_default if isinstance(defensive_default, dict) else {},
            openclaw_invocation_id=None,
            provider="suppressed",
            input_tokens=None,
            output_tokens=None,
            cost_usd=None,
            duration_ms=0,
        )

    if compatible_prior is not None:
        assert invocation_identity is not None
        prior, compatible_version, compatible_identity = compatible_prior
        return await _compatible_recorded_result(
            runtime,
            pack,
            ctx,
            inputs,
            prior,
            compatible_version,
            compatible_identity,
            compatible_receipts,
        )

    # Step 6: one POST; OpenClaw owns provider/model routing and fallback.
    token = _resolve_gateway_token(runtime)
    timeout_seconds = int(fm.get("timeout_seconds", 15))
    session_key = _derive_session_key(ctx)
    started_at = time.perf_counter()

    body = _build_request_body(pack=pack, inputs=inputs, session_key=session_key)
    outcome = await _post_one_provider(
        runtime=runtime,
        body=body,
        token=token,
        timeout_seconds=timeout_seconds,
    )
    chosen_provider = outcome.provider_model or "openclaw-routed"
    if outcome.kind == "malformed":
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        await _emit_failed(
            runtime,
            ctx,
            pack_id_str=pack_id_str,
            pack_version=pack_version,
            failure_class="openclaw_malformed_response",
            error_detail=outcome.error_detail,
            provider_attempted=None,
            duration_ms=duration_ms,
        )
        raise OpenClawResponseMalformed(outcome.error_detail)
    if outcome.kind != "ok":
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        failure_class = (
            "openclaw_timeout" if outcome.kind == "timeout" else "openclaw_unreachable"
        )
        exc_cls: type[Exception] = (
            OpenClawTimeout if outcome.kind == "timeout" else OpenClawUnreachable
        )
        await _emit_failed(
            runtime,
            ctx,
            pack_id_str=pack_id_str,
            pack_version=pack_version,
            failure_class=failure_class,
            error_detail=outcome.error_detail,
            provider_attempted=None,
            duration_ms=duration_ms,
        )
        error = exc_cls(outcome.error_detail)
        error.skill_version = pack_version  # type: ignore[attr-defined]
        raise error
    raw_response = outcome.raw_result

    # Step 7 — optional handler.post_process.
    if pack.handler_post_process is not None:
        try:
            processed = pack.handler_post_process(raw_response, inputs, ctx)
        except Exception as exc:  # noqa: BLE001
            _save_validation_failure(runtime, raw_response)
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            await _emit_failed(
                runtime,
                ctx,
                pack_id_str=pack_id_str,
                pack_version=pack_version,
                failure_class="handler_raised",
                error_detail=f"handler post_process raised: {exc}",
                provider_attempted=chosen_provider,
                duration_ms=duration_ms,
            )
            if ctx.require_clean_result:
                handler_error = SkillHandlerError(str(exc))
                handler_error.skill_version = pack_version  # type: ignore[attr-defined]
                raise handler_error from exc
            defensive_default = fm.get("on_failure")
            if isinstance(defensive_default, dict):
                processed = defensive_default
            else:
                raise SkillHandlerError(str(exc)) from exc
    else:
        processed = raw_response

    # Step 8 — validate output against output.schema.json.
    try:
        Draft202012Validator(pack.output_schema).validate(processed)
    except JsonSchemaValidationError as exc:
        _save_validation_failure(runtime, raw_response)
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        await _emit_failed(
            runtime,
            ctx,
            pack_id_str=pack_id_str,
            pack_version=pack_version,
            failure_class="output_invalid",
            error_detail=f"output schema rejected response: {exc.message}",
            provider_attempted=chosen_provider,
            duration_ms=duration_ms,
        )
        if ctx.require_clean_result:
            output_error = SkillOutputInvalid(str(exc))
            output_error.skill_version = pack_version  # type: ignore[attr-defined]
            raise output_error from exc
        defensive_default = fm.get("on_failure")
        if isinstance(defensive_default, dict):
            processed = defensive_default
        else:
            raise SkillOutputInvalid(str(exc)) from exc

    duration_ms = int((time.perf_counter() - started_at) * 1000)

    # Step 9 — emit skill.call.recorded with size-capped inputs.
    inputs_payload = _spill_inputs_if_large(runtime, inputs)
    try:
        recorded_event_id = await _emit_recorded(
            runtime,
            ctx,
            pack_id_str=pack_id_str,
            pack_version=pack_version,
            invocation_id=outcome.invocation_id,
            inputs_payload=inputs_payload,
            outputs=processed,
            provider=chosen_provider,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
            cost_usd=outcome.cost_usd,
            duration_ms=duration_ms,
        )
    except IdempotencyConflict:
        # Two exact invocations can both pass the initial lookup before either
        # result is durable. The append-only log chooses the first writer. A
        # losing concurrent caller must reuse that canonical result instead of
        # turning a successful language assessment into a terminal failure.
        if idempotency_key is None or runtime.event_log is None:
            raise
        recorded = await runtime.event_log.get_by_idempotency(
            ctx.session.tenant_id, idempotency_key
        )
        if recorded is None or recorded.get("type") != "skill.call.recorded":
            raise
        return _recorded_skill_result(recorded)

    return SkillResult(
        output=processed,
        openclaw_invocation_id=outcome.invocation_id,
        provider=chosen_provider,
        input_tokens=outcome.input_tokens,
        output_tokens=outcome.output_tokens,
        cost_usd=outcome.cost_usd,
        duration_ms=duration_ms,
        skill_version=pack_version,
        recorded_event_id=recorded_event_id,
    )


# ---------------------------------------------------------------------------
# pack-root resolution + small helpers
# ---------------------------------------------------------------------------


def _resolve_pack_root(skill_id: str) -> Path:
    """Resolve a skill_id to a pack root directory.

    Three forms accepted:
    - absolute path → used directly
    - bare slug ("classify_test") → resolved relative to repo `packs/skills/`
      (test convenience) or instance packs_dir if available
    - "namespace:name" form → mapped to `<packs_dir>/<name>`

    Production callers should prefer absolute paths derived from
    `InstanceConfig.packs_dir` so this resolution never sees a slug.
    """
    p = Path(skill_id)
    if p.is_absolute() and p.exists():
        return p
    # Try repo-relative for tests.
    repo_packs = Path(__file__).resolve().parents[3] / "packs" / "skills"
    if (repo_packs / skill_id).exists():
        return repo_packs / skill_id
    # Try strip "skill:" prefix or "namespace:name".
    if ":" in skill_id:
        _, name = skill_id.rsplit(":", 1)
        if (repo_packs / name).exists():
            return repo_packs / name
    raise FileNotFoundError(f"could not resolve pack root for skill_id={skill_id!r}")


_SENS_ORDER = {"normal": 0, "sensitive": 1, "privileged": 2}


def _max_sensitivity(values: Any) -> str:
    best = "normal"
    best_rank = 0
    for v in values:
        rank = _SENS_ORDER.get(str(v), 0)
        if rank > best_rank:
            best = str(v)
            best_rank = rank
    return best


def _derive_session_key(ctx: SkillContext) -> str:
    """OpenClaw `sessionKey` derived from the AdministrateMe Session.

    OpenClaw requires the canonical ``agent:<agent-id>:<opaque-key>`` form to
    resolve the SQLite session store that owns a tool invocation.  The opaque
    portion keeps AdministrateMe's tenant, authenticated-member, and DM-scope
    isolation boundary; no provider credential is embedded in the key.
    """
    sess = ctx.session
    return (
        f"agent:{OPENCLAW_AGENT_ID}:adminme:"
        f"{sess.tenant_id}:{sess.auth_member_id}:{sess.dm_scope}"
    )


__all__ = [
    "OPENCLAW_AGENT_ID",
    "OPENCLAW_GATEWAY_URL",
    "OpenClawResponseMalformed",
    "OpenClawTimeout",
    "OpenClawUnreachable",
    "SkillContext",
    "SkillHandlerError",
    "SkillInputInvalid",
    "SkillOutputInvalid",
    "SkillResult",
    "SkillScopeInsufficient",
    "SkillSensitivityRefused",
    "run_skill",
    "set_default_event_sink",
]
