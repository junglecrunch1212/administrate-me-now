"""Transactional canonical-baseline and disposable-clone lifecycle."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import hmac
import json
import os
import platform
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from adminme.runtime.application import AdministrateMeRuntime
from adminme.events.envelope import EventEnvelope
from adminme.sandbox.contracts import (
    GENERATOR_VERSION,
    ManifestError,
    canonical_json,
    dominance_report,
    fingerprint,
    validate_manifest,
)
from adminme.sandbox.generator import (
    expected_coverage,
    fictional_privacy_corpus,
    measure_generated_coverage,
)
from adminme.synthetic_member import (
    SYNTHETIC_IDENTITY,
    build_synthetic_member_settings,
    prepare_synthetic_instance,
)


class SandboxError(RuntimeError):
    """A sandbox precondition failed and no test may run."""


@dataclass(frozen=True)
class DisposableInstance:
    path: Path
    encryption_key_path: Path
    canonical_fingerprint: str
    run_root: Path
    state_root: Path


def default_state_root() -> Path:
    return Path.home() / "Library" / "Application Support" / "AdministrateMe Sandbox"


def _atomic_write(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    if path.is_symlink():
        raise SandboxError(f"refusing to replace symlink: {path}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _safe_private_file(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise SandboxError(f"required owner-only file is missing: {path}")
    info = path.stat()
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise SandboxError(f"owner-only file has broad permissions: {path}")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise SandboxError(f"owner-only file has the wrong owner: {path}")
    return path.read_bytes()


def _state_file(path: Path, state_root: Path) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        raise SandboxError("sandbox artifacts must use absolute paths")
    resolved = (
        expanded.resolve()
        if expanded.exists()
        else expanded.parent.resolve() / expanded.name
    )
    if state_root.resolve() not in resolved.parents:
        raise SandboxError("sandbox artifacts must remain inside the state root")
    return resolved


def _state_root(path: Path, repository_root: Path) -> Path:
    root = path.expanduser()
    if not root.is_absolute():
        raise SandboxError("sandbox state root must be absolute")
    resolved = root.resolve()
    repository = repository_root.resolve()
    if (
        resolved in {Path(resolved.anchor), Path.home().resolve(), repository}
        or repository in resolved.parents
    ):
        raise SandboxError("sandbox state must use a dedicated directory outside Git")
    resolved.mkdir(parents=True, exist_ok=True, mode=0o700)
    resolved.chmod(0o700)
    return resolved


def _artifact_key(state_root: Path, *, create: bool) -> bytes:
    path = state_root / "keys" / "fidelity-artifact.key"
    if not path.exists() and create:
        _atomic_write(path, secrets.token_bytes(32))
    key = _safe_private_file(path)
    if len(key) != 32:
        raise SandboxError("fidelity artifact key must be exactly 32 bytes")
    return key


def _active_pointer(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise SandboxError("an immutable active-release pointer is required")
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SandboxError("active-release pointer is invalid") from exc
    commit = value.get("commit") if isinstance(value, dict) else None
    release_path = value.get("path") if isinstance(value, dict) else None
    if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise SandboxError("active-release pointer has no immutable commit")
    if not isinstance(release_path, str) or not Path(release_path).is_absolute():
        raise SandboxError("active-release pointer has no absolute release path")
    resolved_release = Path(release_path).resolve()
    releases_root = (path.parent / "releases").resolve()
    if (
        resolved_release.parent != releases_root
        or not resolved_release.is_dir()
        or Path(release_path).is_symlink()
    ):
        raise SandboxError(
            "active-release pointer does not name one immutable release directory"
        )
    return dict(value)


def _signed_payload(artifact: dict[str, Any]) -> bytes:
    return canonical_json(
        {key: value for key, value in artifact.items() if key != "signature"}
    )


def sign_artifact(
    manifest: dict[str, Any],
    privacy_proof: dict[str, Any],
    *,
    state_root: Path,
) -> dict[str, Any]:
    key = _artifact_key(state_root, create=True)
    artifact = {
        "contract": "administrateme-signed-fidelity-artifact-v1",
        "signed_at": datetime.now(timezone.utc).isoformat(),
        "manifest": manifest,
        "privacy_proof": privacy_proof,
    }
    artifact["signature"] = hmac.new(
        key, _signed_payload(artifact), hashlib.sha256
    ).hexdigest()
    return artifact


def load_artifact(path: Path, *, state_root: Path) -> dict[str, Any]:
    raw = _safe_private_file(_state_file(path, state_root))
    try:
        artifact = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SandboxError("signed fidelity artifact is invalid JSON") from exc
    if (
        not isinstance(artifact, dict)
        or artifact.get("contract") != "administrateme-signed-fidelity-artifact-v1"
    ):
        raise SandboxError("signed fidelity artifact contract is unsupported")
    signature = artifact.get("signature")
    if not isinstance(signature, str):
        raise SandboxError("signed fidelity artifact has no signature")
    expected = hmac.new(
        _artifact_key(state_root, create=False),
        _signed_payload(artifact),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise SandboxError("signed fidelity artifact authentication failed")
    privacy = artifact.get("privacy_proof")
    if not isinstance(privacy, dict) or privacy.get("passes") is not True:
        raise SandboxError("live/synthetic in-memory privacy comparison did not pass")
    try:
        validate_manifest(artifact.get("manifest") or {})
    except ManifestError as exc:
        raise SandboxError(str(exc)) from exc
    return artifact


def capture_profile(
    *,
    base_url: str,
    tailscale_identity: str,
    active_release_pointer: Path,
    state_root: Path,
    repository_root: Path,
    output: Path | None = None,
) -> Path:
    """Fetch fresh aggregates from the active runtime and sign them locally."""

    root = _state_root(state_root, repository_root)
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SandboxError("active runtime URL must be HTTP(S)")
    if parsed.scheme == "http" and parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise SandboxError("unencrypted active-runtime profiling is loopback-only")
    pointer = _active_pointer(active_release_pointer)
    headers = {"Tailscale-User-Login": tailscale_identity}
    with httpx.Client(
        base_url=base_url.rstrip("/"), headers=headers, timeout=120.0
    ) as client:
        response = client.get("/api/system/sandbox-fidelity")
        if response.status_code != 200:
            raise SandboxError(
                f"active runtime fidelity endpoint failed: HTTP {response.status_code}"
            )
        manifest = response.json()
        validated = validate_manifest(manifest)
        if validated["active_release"]["commit"] != pointer["commit"]:
            raise SandboxError("active runtime and immutable release pointer disagree")
        corpus = list(fictional_privacy_corpus(validated))
        comparison = client.post(
            "/api/system/sandbox-privacy-compare",
            json={"synthetic_values": corpus},
        )
        if comparison.status_code != 200:
            raise SandboxError(
                f"active runtime privacy comparison failed: HTTP {comparison.status_code}"
            )
        privacy_proof = comparison.json()
    artifact = sign_artifact(validated, privacy_proof, state_root=root)
    target = _state_file(output or (root / "profiles" / "active-fidelity.json"), root)
    _atomic_write(
        target, json.dumps(artifact, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    )
    return target


def _candidate_contract(repository_root: Path) -> dict[str, Any]:
    from adminme.events.registry import ensure_autoloaded, registry
    from adminme.projections.runner import DEFAULT_PROJECTION_CLASSES

    ensure_autoloaded()
    pipeline_versions: dict[str, str] = {}
    for path in sorted(
        (repository_root / "packs" / "pipelines").glob("*/pipeline.yaml")
    ):
        import yaml

        value = yaml.safe_load(path.read_text("utf-8")) or {}
        pack = value.get("pack") or {}
        pipeline_versions[str(pack.get("id") or path.parent.name)] = str(
            pack.get("version") or "unknown"
        )
    tracked = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            "adminme",
            "packs",
            "bootstrap/versions.json",
            "pyproject.toml",
            "poetry.lock",
        ],
        cwd=repository_root,
        text=True,
        check=True,
        capture_output=True,
    ).stdout.splitlines()
    tree = hashlib.sha256()
    for relative in tracked:
        path = repository_root / relative
        if path.is_file():
            tree.update(relative.encode("utf-8"))
            tree.update(path.read_bytes())
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository_root,
        text=True,
        check=True,
        capture_output=True,
    ).stdout.strip()
    return {
        "commit": commit,
        "source_tree_sha256": tree.hexdigest(),
        "generator_version": GENERATOR_VERSION,
        "event_schemas": {
            name: int(registry.latest_version(name) or 0)
            for name in registry.known_types()
        },
        "projections": {
            projection.name: projection.version
            for projection in DEFAULT_PROJECTION_CLASSES
        },
        "pipelines": pipeline_versions,
        "runtime_versions": json.loads(
            (repository_root / "bootstrap" / "versions.json").read_text("utf-8")
        ),
    }


def tree_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise SandboxError(f"sandbox state may not contain symlinks: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _make_private_tree(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_symlink():
            raise SandboxError(f"sandbox state may not contain symlinks: {path}")
        path.chmod(0o700 if path.is_dir() else 0o600)
    root.chmod(0o700)


def _settled_triage_evidence(connection: Any) -> dict[str, Any]:
    unresolved = "state NOT IN ('confirmed', 'dismissed', 'superseded', 'failed')"
    counts = connection.execute(
        "SELECT count(*), count(DISTINCT parent_intent_id) "
        f"FROM triage_items WHERE {unresolved}"
    ).fetchone()
    incomplete = connection.execute(
        "SELECT count(*) FROM triage_items "
        f"WHERE {unresolved} AND "
        "(route_json IS NULL OR route_json = '' OR route_json = 'null')"
    ).fetchone()
    kinds = connection.execute(
        "SELECT DISTINCT target_kind FROM triage_items "
        f"WHERE {unresolved} ORDER BY target_kind"
    ).fetchall()
    return {
        "triage_candidates": int(counts[0]),
        "triage_matters": int(counts[1]),
        "enrichment_or_destination_required": int(incomplete[0]),
        "candidate_kinds": [str(row[0]) for row in kinds],
    }


async def _build_runtime_evidence(
    instance_dir: Path,
    repository_root: Path,
    encryption_key: bytes,
) -> dict[str, Any]:
    settings = build_synthetic_member_settings(
        instance_dir,
        repository_root,
        encryption_key=encryption_key,
    )
    runtime = AdministrateMeRuntime(settings)
    await runtime.start()
    try:
        assert runtime.event_log is not None
        assert runtime.projections is not None
        assert runtime.pipelines is not None
        assert runtime.observation is not None
        external_effect_events = 0
        generated_source_facts = 0
        messages = 0
        threads: set[str] = set()
        participants: set[str] = set()
        triage_proposal_events = 0
        skill_calls = 0
        skill_failures = 0
        async for event in runtime.event_log.read_since():
            if event["type"].startswith("external_sync."):
                external_effect_events += 1
            if str(event.get("idempotency_key") or "").startswith("live-fidelity:v2:"):
                generated_source_facts += 1
            payload = event.get("payload") or {}
            if event["type"] in {"messaging.received", "messaging.sent"}:
                messages += 1
                thread_id = str(payload.get("thread_id") or "")
                if thread_id:
                    threads.add(thread_id)
                for value in (
                    payload.get("from_identifier"),
                    payload.get("to_identifier"),
                    *(payload.get("to_identifiers") or []),
                    *(payload.get("cc_identifiers") or []),
                    *(payload.get("bcc_identifiers") or []),
                ):
                    if isinstance(value, str) and value:
                        participants.add(value.casefold())
            if event["type"] == "triage.item.proposed":
                triage_proposal_events += 1
            if event["type"] == "skill.call.recorded":
                skill_calls += 1
            if event["type"] == "skill.call.failed":
                skill_failures += 1
        projection_status = await runtime.projections.status()
        pipeline_status = await runtime.pipelines.status()
        degraded_projections = sorted(
            name for name, status in projection_status.items() if status.get("degraded")
        )
        degraded_pipelines = sorted(
            name
            for name, status in pipeline_status.items()
            if (status.get("subscriber_status") or {}).get("degraded")
        )
        settled_triage = await runtime.projections.read(
            "triage", _settled_triage_evidence
        )
        evidence = {
            "contract": "administrateme-production-composition-evidence-v1",
            "generated_source_facts": generated_source_facts,
            "projection_count": len(runtime.projections.projection_names()),
            "pipeline_count": len(runtime.pipelines.registered_pack_ids()),
            "degraded_projections": degraded_projections,
            "degraded_pipelines": degraded_pipelines,
            "observation_active": await runtime.observation.is_active(),
            "real_adapter_instances": len(getattr(runtime.adapters, "_adapters", ())),
            "external_effect_events": external_effect_events,
            "deterministic_openclaw": settings.deterministic_openclaw_fixture,
            "skill_calls": skill_calls,
            "skill_failures": skill_failures,
            "measured": {
                "recent_messages": messages,
                "message_threads": len(threads),
                "participants": len(participants),
                "triage_proposal_events": triage_proposal_events,
                **settled_triage,
            },
            "passes": (
                not degraded_projections
                and not degraded_pipelines
                and await runtime.observation.is_active()
                and len(getattr(runtime.adapters, "_adapters", ())) == 0
                and external_effect_events == 0
                and skill_calls > 0
                and skill_failures == 0
            ),
        }
        if not evidence["passes"]:
            raise SandboxError("production-composed baseline did not settle safely")
        return evidence
    finally:
        await runtime.stop()


async def _verify_gateway_sample(
    instance_dir: Path,
    repository_root: Path,
    encryption_key: bytes,
    *,
    gateway_token: str,
    shared_secret: str,
) -> dict[str, Any]:
    """Send a bounded fictional stratum through the actual pinned Gateway."""

    settings = build_synthetic_member_settings(
        instance_dir,
        repository_root,
        encryption_key=encryption_key,
        openclaw_gateway_token=gateway_token,
        openclaw_shared_secret=shared_secret,
    )
    runtime = AdministrateMeRuntime(settings)
    await runtime.start()
    try:
        assert runtime.events is not None
        assert runtime.event_log is not None
        labels = (
            ["INBOX", "CATEGORY_PRIMARY"],
            ["INBOX", "CATEGORY_UPDATES"],
            ["CATEGORY_PROMOTIONS"],
            ["SPAM"],
        )
        envelopes: list[EventEnvelope] = []
        for index in range(12):
            occurred_at = f"2026-08-31T18:{index:02d}:00Z"
            body = (
                f"Please review fictional Gateway sample {index:02d} by Tuesday. "
                + ("Copper Finch Harbor Juniper Lantern Meadow. " * (1 + index))
            )
            envelopes.append(
                EventEnvelope(
                    event_at_ms=1_788_198_000_000 + index,
                    tenant_id=runtime.settings.instance.tenant_id,
                    type="messaging.received",
                    schema_version=4,
                    occurred_at=occurred_at,
                    source_adapter="sandbox-gateway-sample",
                    source_account_id="gateway-sample-fixture",
                    owner_scope="private:member_synthetic_principal",
                    visibility_scope="private:member_synthetic_principal",
                    sensitivity="normal",
                    actor_identity="member_synthetic_principal",
                    idempotency_key=f"live-fidelity-gateway-sample:v1:{index:02d}",
                    payload={
                        "source_channel": "fixture",
                        "from_identifier": f"gateway-sample-{index:02d}@fictional.invalid",
                        "to_identifier": "member_synthetic_principal@fictional.invalid",
                        "to_identifiers": [
                            "member_synthetic_principal@fictional.invalid"
                        ],
                        "thread_id": f"gateway-sample-thread-{index % 4}",
                        "subject": f"Fictional Gateway sample {index:02d}",
                        "body_text": body,
                        "body_html": None,
                        "received_at": occurred_at,
                        "attachments": [],
                        "native_message_id": f"gateway-sample-{index:02d}",
                        "member_id": "member_synthetic_principal",
                        "provider": "fixture",
                        "provider_account_id": "gateway-sample-fixture",
                        "workspace": "work" if index % 3 == 0 else "personal",
                        "cc_identifiers": [],
                        "bcc_identifiers": [],
                        "reply_to_identifier": None,
                        "rfc_message_id": (
                            f"<gateway-sample-{index:02d}@fictional.invalid>"
                        ),
                        "references": [],
                        "provider_labels": labels[index % len(labels)],
                        "unread": index % 2 == 0,
                        "archived": index % 5 == 0,
                        "starred": False,
                        "important": index % 3 == 0,
                        "in_reply_to": None,
                    },
                )
            )
        event_ids = await runtime.events.write_batch(envelopes)
        await runtime.wait_for_subscriber(
            "pipeline:pipeline:noise_filtering",
            event_ids[-1],
            timeout_s=120.0,
        )
        actual_gateway_calls = 0
        failed_calls = 0
        async for event in runtime.event_log.read_since(
            types=["skill.call.recorded", "skill.call.failed"]
        ):
            if event["type"] == "skill.call.failed":
                failed_calls += 1
                continue
            payload = event.get("payload") or {}
            if (
                payload.get("skill_name") == "skill:classify_message_nature"
                and payload.get("provider") != "fixture/deterministic-v1"
            ):
                actual_gateway_calls += 1
        evidence = {
            "contract": "administrateme-bounded-gateway-sample-v1",
            "sample_messages": len(envelopes),
            "strata": ["primary", "updates", "promotions", "spam"],
            "actual_gateway_calls": actual_gateway_calls,
            "failed_calls": failed_calls,
            "loopback_gateway": settings.openclaw_gateway_url == "ws://127.0.0.1:18789",
            "content_fictional": True,
            "passes": actual_gateway_calls >= len(envelopes)
            and failed_calls == 0
            and settings.openclaw_gateway_url == "ws://127.0.0.1:18789",
        }
        if evidence["passes"] is not True:
            raise SandboxError(
                "bounded pinned-Gateway sample did not produce current model evidence"
            )
        return evidence
    finally:
        await runtime.stop()


def _baseline_identity(
    manifest: dict[str, Any], repository_root: Path
) -> tuple[str, dict[str, Any]]:
    candidate = _candidate_contract(repository_root)
    identity = {
        "source_manifest_fingerprint": fingerprint(manifest),
        "active_release_commit": manifest["active_release"]["commit"],
        "candidate_contract": candidate,
    }
    return fingerprint(identity), identity


def reconcile(
    *,
    artifact_path: Path,
    active_release_pointer: Path,
    state_root: Path,
    repository_root: Path,
) -> dict[str, Any]:
    """Verify currency/privacy/dominance and transactionally publish a baseline."""

    root = _state_root(state_root, repository_root)
    artifact = load_artifact(artifact_path, state_root=root)
    manifest = artifact["manifest"]
    pointer = _active_pointer(active_release_pointer)
    if pointer["commit"] != manifest["active_release"]["commit"]:
        raise SandboxError("signed profile is not for the active immutable release")
    baseline_id, identity = _baseline_identity(manifest, repository_root)
    baselines = root / "baselines"
    destination = baselines / baseline_id
    metadata_path = destination / "baseline.json"
    if metadata_path.is_file() and not metadata_path.is_symlink():
        metadata = json.loads(metadata_path.read_text("utf-8"))
        instance = destination / "instance"
        if (
            metadata.get("identity") == identity
            and metadata.get("emitted_workload_dominance", {}).get("passes") is True
            and metadata.get("dominance", {}).get("passes") is True
            and metadata.get("privacy_proof", {}).get("passes") is True
            and metadata.get("production_composition", {}).get("passes") is True
            and metadata.get("instance_fingerprint") == tree_fingerprint(instance)
        ):
            pointer_value = {"baseline_id": baseline_id, "path": str(destination)}
            _atomic_write(
                root / "current.json",
                json.dumps(pointer_value, sort_keys=True).encode("utf-8") + b"\n",
            )
            return metadata

    planned_coverage = expected_coverage(manifest)
    coverage = measure_generated_coverage(manifest)
    emitted_dominance = dominance_report(manifest, coverage)
    if not emitted_dominance["passes"]:
        raise SandboxError(
            "measured emitted workload no longer dominates the fresh live profile"
        )
    privacy = artifact["privacy_proof"]
    baselines.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(
        prefix=f".{baseline_id}.stage-", dir=baselines
    ) as temporary:
        staging = Path(temporary)
        instance = staging / "instance"
        prepare_synthetic_instance(instance)
        _atomic_write(
            instance / "config" / "live-fidelity-source.json",
            json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n",
        )
        encryption_key = secrets.token_bytes(32)
        _atomic_write(staging / "encryption.key", encryption_key)
        evidence = asyncio.run(
            _build_runtime_evidence(instance, repository_root, encryption_key)
        )
        if evidence["generated_source_facts"] < int(
            planned_coverage["cardinalities"]["source_facts"]
        ):
            raise SandboxError(
                "production composition generated an under-complex source log"
            )
        measured_coverage = copy.deepcopy(coverage)
        measured_coverage["cardinalities"]["source_facts"] = evidence[
            "generated_source_facts"
        ]
        for name, measured in evidence["measured"].items():
            if name in measured_coverage["cardinalities"]:
                measured_coverage["cardinalities"][name] = measured
        measured_coverage["categories"]["candidate_kinds"] = sorted(
            set(measured_coverage["categories"]["candidate_kinds"])
            | set(evidence["measured"]["candidate_kinds"])
        )
        dominance = dominance_report(manifest, measured_coverage)
        if not dominance["passes"]:
            raise SandboxError(
                "production-composed baseline is under-complex after settlement"
            )
        _make_private_tree(instance)
        metadata = {
            "contract": "administrateme-canonical-baseline-v1",
            "baseline_id": baseline_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "identity": identity,
            "coverage": measured_coverage,
            "emitted_workload_dominance": emitted_dominance,
            "dominance": dominance,
            "privacy_proof": privacy,
            "production_composition": evidence,
            "canonical_mutable": False,
            "instance_fingerprint": tree_fingerprint(instance),
        }
        _atomic_write(
            staging / "baseline.json",
            json.dumps(metadata, indent=2, sort_keys=True).encode("utf-8") + b"\n",
        )
        if destination.exists():
            quarantine = baselines / f"{baseline_id}.quarantine-{uuid.uuid4().hex}"
            os.replace(destination, quarantine)
        os.replace(staging, destination)
    pointer_value = {"baseline_id": baseline_id, "path": str(destination)}
    _atomic_write(
        root / "current.json",
        json.dumps(pointer_value, sort_keys=True).encode("utf-8") + b"\n",
    )
    return json.loads((destination / "baseline.json").read_text("utf-8"))


def inspect_current(
    *,
    state_root: Path,
    repository_root: Path,
) -> dict[str, Any]:
    root = _state_root(state_root, repository_root)
    pointer = json.loads(_safe_private_file(root / "current.json"))
    baseline = Path(pointer["path"])
    if baseline.parent != root / "baselines" or baseline.name != pointer["baseline_id"]:
        raise SandboxError("canonical baseline pointer escapes the sandbox state root")
    metadata = json.loads(_safe_private_file(baseline / "baseline.json"))
    measured = tree_fingerprint(baseline / "instance")
    metadata["inspection"] = {
        "fingerprint_matches": measured == metadata.get("instance_fingerprint"),
        "dominance_passes": metadata.get("dominance", {}).get("passes") is True,
        "emitted_workload_dominance_passes": metadata.get(
            "emitted_workload_dominance", {}
        ).get("passes")
        is True,
        "privacy_passes": metadata.get("privacy_proof", {}).get("passes") is True,
        "production_composition_passes": metadata.get("production_composition", {}).get(
            "passes"
        )
        is True,
    }
    metadata["inspection"]["passes"] = all(metadata["inspection"].values())
    return metadata


def create_disposable(
    *,
    state_root: Path,
    repository_root: Path,
) -> DisposableInstance:
    root = _state_root(state_root, repository_root)
    metadata = inspect_current(state_root=root, repository_root=repository_root)
    if metadata["inspection"]["passes"] is not True:
        raise SandboxError("canonical baseline inspection failed")
    baseline = root / "baselines" / metadata["baseline_id"]
    runs = root / "runs"
    runs.mkdir(parents=True, exist_ok=True, mode=0o700)
    run_root = Path(tempfile.mkdtemp(prefix="sandbox-run-", dir=runs))
    run_root.chmod(0o700)
    destination = run_root / "instance"
    shutil.copytree(baseline / "instance", destination, copy_function=shutil.copy2)
    key_path = run_root / "encryption.key"
    _atomic_write(key_path, _safe_private_file(baseline / "encryption.key"))
    if tree_fingerprint(destination) != metadata["instance_fingerprint"]:
        raise SandboxError("disposable clone does not match the canonical baseline")
    return DisposableInstance(
        path=destination,
        encryption_key_path=key_path,
        canonical_fingerprint=metadata["instance_fingerprint"],
        run_root=run_root,
        state_root=root,
    )


def discard_disposable(instance: DisposableInstance) -> None:
    runs_parent = (instance.state_root / "runs").resolve()
    resolved = instance.run_root.resolve()
    if resolved.parent != runs_parent or not resolved.name.startswith("sandbox-run-"):
        raise SandboxError("refusing to discard an unrecognized sandbox run directory")
    shutil.rmtree(resolved)


_CREDENTIAL_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "API_KEY")


def _network_contained_command(
    command: list[str], *, require_gateway: bool
) -> list[str]:
    """Wrap a test process in an inherited OS network sandbox.

    The supported release host is macOS, where Seatbelt applies to the full
    descendant process tree.  Linux can provide a deny-all network namespace
    for deterministic runs, but it cannot safely punch the single Gateway
    exception without a separately reviewed firewall boundary.  Unknown or
    unsupported hosts therefore refuse to execute the requested command.
    """

    if os.environ.get("ADMINME_SANDBOX_NETWORK_CONTAINED") == "1":
        return command
    system = platform.system()
    if system == "Darwin":
        executable = shutil.which("sandbox-exec")
        if executable is None:
            raise SandboxError("macOS network containment is unavailable")
        profile = "(version 1) (allow default) (deny network*)"
        if require_gateway:
            profile += ' (allow network-outbound (remote tcp "localhost:18789"))'
        return [executable, "-p", profile, *command]
    if system == "Linux" and not require_gateway:
        executable = shutil.which("unshare")
        if executable is None:
            raise SandboxError("Linux network containment is unavailable")
        return [executable, "--user", "--map-root-user", "--net", "--", *command]
    raise SandboxError("this host cannot enforce the required sandbox network policy")


def _network_contained_serve_command(
    command: list[str], *, port: int, require_gateway: bool
) -> list[str]:
    """Permit only the browser-facing loopback listener and optional Gateway."""

    if platform.system() != "Darwin":
        raise SandboxError(
            "this host cannot enforce the loopback server network policy"
        )
    executable = shutil.which("sandbox-exec")
    if executable is None:
        raise SandboxError("macOS network containment is unavailable")
    profile = (
        "(version 1) (allow default) (deny network*) "
        f'(allow network-bind (local tcp "localhost:{port}")) '
        f'(allow network-inbound (local tcp "localhost:{port}"))'
    )
    if require_gateway:
        profile += ' (allow network-outbound (remote tcp "localhost:18789"))'
    return [executable, "-p", profile, *command]


def run_test(
    command: list[str],
    *,
    artifact_path: Path,
    active_release_pointer: Path,
    state_root: Path,
    repository_root: Path,
    require_gateway: bool = False,
    runtime_mode: str = "candidate",
) -> int:
    if not command:
        raise SandboxError("sandbox test requires a command after --")
    if runtime_mode not in {"candidate", "active"}:
        raise SandboxError("sandbox runtime mode must be 'candidate' or 'active'")
    gateway_token = os.environ.get("ADMINME_OPENCLAW_GATEWAY_TOKEN")
    gateway_secret = os.environ.get("ADMINME_OPENCLAW_SHARED_SECRET")
    if require_gateway and (not gateway_token or not gateway_secret):
        raise SandboxError(
            "Gateway-required test cannot run without the pinned loopback Gateway credentials"
        )
    reconcile(
        artifact_path=artifact_path,
        active_release_pointer=active_release_pointer,
        state_root=state_root,
        repository_root=repository_root,
    )
    active_pointer = _active_pointer(active_release_pointer)
    command_root = (
        repository_root
        if runtime_mode == "candidate"
        else Path(str(active_pointer["path"]))
    )
    disposable = create_disposable(
        state_root=state_root, repository_root=repository_root
    )
    if require_gateway:
        try:
            gateway_evidence = asyncio.run(
                _verify_gateway_sample(
                    disposable.path,
                    repository_root,
                    disposable.encryption_key_path.read_bytes(),
                    gateway_token=str(gateway_token),
                    shared_secret=str(gateway_secret),
                )
            )
            _atomic_write(
                disposable.run_root / "gateway-evidence.json",
                json.dumps(gateway_evidence, indent=2, sort_keys=True).encode("utf-8")
                + b"\n",
            )
        except BaseException:
            discard_disposable(disposable)
            raise
    environment = {
        key: value
        for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in _CREDENTIAL_MARKERS)
    }
    if require_gateway:
        environment["ADMINME_OPENCLAW_GATEWAY_TOKEN"] = str(gateway_token)
        environment["ADMINME_OPENCLAW_SHARED_SECRET"] = str(gateway_secret)
        environment["ADMINME_SANDBOX_MODEL_MODE"] = "gateway-sampled"
    else:
        environment["ADMINME_SANDBOX_MODEL_MODE"] = "deterministic-full-volume"
    sandbox_key = disposable.encryption_key_path.read_bytes()
    environment.update(
        {
            "ADMINME_SANDBOX": "1",
            "ADMINME_SANDBOX_INSTANCE_DIR": str(disposable.path),
            "ADMINME_SANDBOX_ENCRYPTION_KEY_FILE": str(disposable.encryption_key_path),
            "ADMINME_INSTANCE_DIR": str(disposable.path),
            "ADMINME_EVENT_LOG_KEY_HEX": sandbox_key.hex(),
            "ADMINME_REPOSITORY_ROOT": str(command_root),
            "ADMINME_OPENCLAW_MEMBER_ROLES_JSON": json.dumps(
                {
                    "member_synthetic_principal": "principal",
                    "member_synthetic_secondary": "principal",
                    "member_synthetic_dependent": "child",
                    "member_synthetic_dependent_secondary": "child",
                },
                sort_keys=True,
            ),
            "ADMINME_SANDBOX_PROVIDER_POLICY": "deny-all",
            "ADMINME_SANDBOX_NETWORK_POLICY": "deny-except-loopback-gateway"
            if require_gateway
            else "deny-all",
            "ADMINME_SYNTHETIC_IDENTITY": SYNTHETIC_IDENTITY,
            "ADMINME_SANDBOX_RUNTIME_MODE": runtime_mode,
            "ADMINME_SANDBOX_GATEWAY_EVIDENCE": str(
                disposable.run_root / "gateway-evidence.json"
            )
            if require_gateway
            else "",
        }
    )
    current_before = inspect_current(
        state_root=state_root, repository_root=repository_root
    )["instance_fingerprint"]
    try:
        contained_command = _network_contained_command(
            command, require_gateway=require_gateway
        )
        completed = subprocess.run(
            contained_command, cwd=command_root, env=environment, check=False
        )
        return int(completed.returncode)
    finally:
        current_after = inspect_current(
            state_root=state_root, repository_root=repository_root
        )["instance_fingerprint"]
        discard_disposable(disposable)
        if current_before != current_after:
            raise SandboxError(
                "canonical baseline changed during disposable test execution"
            )


__all__ = [
    "DisposableInstance",
    "SandboxError",
    "capture_profile",
    "create_disposable",
    "default_state_root",
    "discard_disposable",
    "inspect_current",
    "load_artifact",
    "reconcile",
    "run_test",
    "sign_artifact",
    "tree_fingerprint",
]
