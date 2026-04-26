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
- `set_default_event_log(log)` — module-level injection used by service
  start (08b's outbound() uses the same pattern).
- Four exceptions: `SkillInputInvalid`, `SkillSensitivityRefused`,
  `SkillScopeInsufficient`, `OpenClawResponseMalformed`.

The wrapper is the SINGLE EMIT SEAM for `skill.call.recorded`,
`skill.call.failed`, `skill.call.suppressed`. `verify_invariants.sh`
allowlists these emits to this file only.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

from adminme.events.envelope import EventEnvelope
from adminme.lib.skill_runner.pack_loader import LoadedPack, load_pack

if TYPE_CHECKING:
    from adminme.events.log import EventLog
    from adminme.lib.session import Session

_log = logging.getLogger(__name__)

OPENCLAW_GATEWAY_URL = "http://127.0.0.1:18789/tools/invoke"
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
    """Every provider in `provider_preferences` returned 5xx / network
    failure."""


class OpenClawTimeout(Exception):
    """Every provider in `provider_preferences` timed out."""


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


@dataclass(frozen=True)
class SkillResult:
    output: dict
    openclaw_invocation_id: str | None
    provider: str
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    duration_ms: int


# ---------------------------------------------------------------------------
# EventLog DI (matches 08b's ObservationManager pattern)
# ---------------------------------------------------------------------------


_DEFAULT_EVENT_LOG: "EventLog | None" = None


def set_default_event_log(log: "EventLog") -> None:
    """Wire the module-level event log used by `run_skill()` when no
    per-call override is supplied. Bootstrap §7 calls this at service
    start; tests inject a per-test log via `_runtime`."""
    global _DEFAULT_EVENT_LOG
    _DEFAULT_EVENT_LOG = log


@dataclass(frozen=True)
class _Runtime:
    """Test-injectable bundle. Production callers do not construct this
    directly; production wiring lives in bootstrap."""

    event_log: "EventLog"
    raw_events_dir: Path
    httpx_client_factory: Any = None  # callable() -> httpx.AsyncClient | None
    gateway_url: str = OPENCLAW_GATEWAY_URL


_RUNTIME_OVERRIDE: _Runtime | None = None


def _set_runtime_for_tests(runtime: _Runtime | None) -> None:
    global _RUNTIME_OVERRIDE
    _RUNTIME_OVERRIDE = runtime


def _resolve_runtime() -> _Runtime:
    if _RUNTIME_OVERRIDE is not None:
        return _RUNTIME_OVERRIDE
    if _DEFAULT_EVENT_LOG is None:
        raise RuntimeError(
            "skill_runner: no default EventLog configured; "
            "call set_default_event_log(...) at service start"
        )
    # Production resolves raw_events_dir via InstanceConfig at bootstrap.
    # If we land here without an override AND no instance dir, surface
    # the gap loudly rather than guess.
    instance_dir = os.environ.get("ADMINME_INSTANCE_DIR")
    if not instance_dir:
        raise RuntimeError(
            "skill_runner: ADMINME_INSTANCE_DIR unset; cannot resolve "
            "raw_events_dir per [§15]"
        )
    return _Runtime(
        event_log=_DEFAULT_EVENT_LOG,
        raw_events_dir=Path(instance_dir) / "data" / "raw_events",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_gateway_token(session: "Session") -> str | None:
    return os.environ.get(OPENCLAW_GATEWAY_TOKEN_ENV)


def _build_envelope(
    *,
    tenant_id: str,
    event_type: str,
    schema_version: int,
    payload: dict,
    actor_identity: str,
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
        owner_scope="shared:household",
        visibility_scope="shared:household",
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
    )
    await runtime.event_log.append(envelope, correlation_id=correlation)


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
    )
    await runtime.event_log.append(envelope, correlation_id=correlation)


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
) -> None:
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
    )
    await runtime.event_log.append(envelope, correlation_id=correlation)


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


def _split_provider_model(provider_model: str) -> tuple[str, str]:
    if "/" not in provider_model:
        return provider_model, ""
    provider, model = provider_model.split("/", 1)
    return provider, model


def _build_request_body(
    *,
    pack: LoadedPack,
    inputs: dict,
    provider_model: str,
    session_key: str,
) -> dict:
    """Translate pack manifest → llm-task tool args per [ADR-0002]."""
    fm = pack.skill_frontmatter
    provider, model = _split_provider_model(provider_model)
    timeout_seconds = int(fm.get("timeout_seconds", 15))
    args: dict[str, Any] = {
        "prompt": pack.prompt_template,
        "input": inputs,
        "schema": pack.output_schema,
        "provider": provider,
        "model": model,
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
    parsed = result.get("details", {}).get("json") if isinstance(result.get("details"), dict) else None
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
    return _OpenClawCallOutcome(
        kind="ok",
        raw_result=parsed,
        invocation_id=invocation_id if isinstance(invocation_id, str) else None,
        input_tokens=result.get("tokens_in") if isinstance(result.get("tokens_in"), int) else None,
        output_tokens=result.get("tokens_out") if isinstance(result.get("tokens_out"), int) else None,
        cost_usd=result.get("cost_usd") if isinstance(result.get("cost_usd"), (int, float)) else None,
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
    # Wall-clock margin per the prompt: skill manifest's timeout +2s.
    httpx_timeout = httpx.Timeout(timeout_seconds + 2.0)

    if runtime.httpx_client_factory is not None:
        client = runtime.httpx_client_factory()
        owns_client = False
    else:
        client = httpx.AsyncClient(timeout=httpx_timeout)
        owns_client = True

    try:
        try:
            response = await client.post(
                runtime.gateway_url, json=body, headers=headers
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
) -> SkillResult:
    """Validate + invoke an OpenClaw skill. See module docstring."""
    runtime = _resolve_runtime()

    # Step 1 — load pack.
    pack = load_pack(Path(skill_id) if Path(skill_id).is_absolute() else _resolve_pack_root(skill_id))
    pack_id_str = pack.pack_id
    pack_version = pack.version
    fm = pack.skill_frontmatter

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
            raise SkillSensitivityRefused(
                "skill refuses privileged inputs"
            )

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
        raise SkillScopeInsufficient(
            f"session missing required scopes: {missing}"
        )

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

    # Step 6 — POST to OpenClaw, iterating provider_preferences.
    provider_preferences = list(fm.get("provider_preferences") or [])
    if not provider_preferences:
        await _emit_failed(
            runtime,
            ctx,
            pack_id_str=pack_id_str,
            pack_version=pack_version,
            failure_class="openclaw_unreachable",
            error_detail="pack manifest declares no provider_preferences",
            provider_attempted=None,
            duration_ms=0,
        )
        raise OpenClawUnreachable("pack manifest declares no provider_preferences")

    token = _resolve_gateway_token(ctx.session)
    timeout_seconds = int(fm.get("timeout_seconds", 15))
    session_key = _derive_session_key(ctx)
    started_at = time.perf_counter()

    last_outcome: _OpenClawCallOutcome | None = None
    successful: _OpenClawCallOutcome | None = None
    chosen_provider = ""
    for provider_model in provider_preferences:
        body = _build_request_body(
            pack=pack,
            inputs=inputs,
            provider_model=provider_model,
            session_key=session_key,
        )
        outcome = await _post_one_provider(
            runtime=runtime,
            body=body,
            token=token,
            timeout_seconds=timeout_seconds,
        )
        last_outcome = outcome
        if outcome.kind == "ok":
            successful = outcome
            chosen_provider = provider_model
            break
        if outcome.kind == "malformed":
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            await _emit_failed(
                runtime,
                ctx,
                pack_id_str=pack_id_str,
                pack_version=pack_version,
                failure_class="openclaw_malformed_response",
                error_detail=outcome.error_detail,
                provider_attempted=provider_model,
                duration_ms=duration_ms,
            )
            raise OpenClawResponseMalformed(outcome.error_detail)
        if outcome.kind == "deterministic_4xx":
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            await _emit_failed(
                runtime,
                ctx,
                pack_id_str=pack_id_str,
                pack_version=pack_version,
                failure_class="openclaw_unreachable",
                error_detail=outcome.error_detail,
                provider_attempted=provider_model,
                duration_ms=duration_ms,
            )
            raise OpenClawUnreachable(outcome.error_detail)
        # transient or timeout — try next provider
    if successful is None:
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        last = last_outcome or _OpenClawCallOutcome(kind="transient", error_detail="no providers attempted")
        if last.kind == "timeout":
            failure_class = "openclaw_timeout"
            exc_cls: type[Exception] = OpenClawTimeout
        else:
            failure_class = "openclaw_unreachable"
            exc_cls = OpenClawUnreachable
        await _emit_failed(
            runtime,
            ctx,
            pack_id_str=pack_id_str,
            pack_version=pack_version,
            failure_class=failure_class,
            error_detail=last.error_detail,
            provider_attempted=provider_preferences[-1] if provider_preferences else None,
            duration_ms=duration_ms,
        )
        raise exc_cls(last.error_detail)

    raw_response = successful.raw_result

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
        defensive_default = fm.get("on_failure")
        if isinstance(defensive_default, dict):
            processed = defensive_default
        else:
            raise SkillOutputInvalid(str(exc)) from exc

    duration_ms = int((time.perf_counter() - started_at) * 1000)

    # Step 9 — emit skill.call.recorded with size-capped inputs.
    inputs_payload = _spill_inputs_if_large(runtime, inputs)
    await _emit_recorded(
        runtime,
        ctx,
        pack_id_str=pack_id_str,
        pack_version=pack_version,
        invocation_id=successful.invocation_id,
        inputs_payload=inputs_payload,
        outputs=processed,
        provider=chosen_provider,
        input_tokens=successful.input_tokens,
        output_tokens=successful.output_tokens,
        cost_usd=successful.cost_usd,
        duration_ms=duration_ms,
    )

    return SkillResult(
        output=processed,
        openclaw_invocation_id=successful.invocation_id,
        provider=chosen_provider,
        input_tokens=successful.input_tokens,
        output_tokens=successful.output_tokens,
        cost_usd=successful.cost_usd,
        duration_ms=duration_ms,
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

    For 09a we mint a stable per-session key from auth_member + dm_scope.
    The wizard prompt 16 will replace this with the configured main key.
    """
    sess = ctx.session
    return f"adminme:{sess.tenant_id}:{sess.auth_member_id}:{sess.dm_scope}"


__all__ = [
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
    "set_default_event_log",
]
