"""Bridge daemon lifecycle for opt-in Cat-B and Cat-D connector packs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import signal
import subprocess
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from adminme.tailscale_local import tailscale_executable
from adminme.events.envelope import EventEnvelope
from adminme.apple_service_permissions import (
    PROVIDERS as APPLE_SERVICE_PROVIDERS,
    digest as apple_digest,
    validate_edge_proof,
    write_policy,
)
from adminme.bridge_authorization import BRIDGE_PACK_POLICIES, BridgePackPolicy
from adminme.messages_reply import (
    REPLY_CAPABILITY_VERSION,
    REPLY_CONTENT_GUARD,
    REPLY_CONTRACT,
    REPLY_POLICY_VERSION,
    REPLY_TRANSPORT,
    body_sha256,
    canonical_sha256,
    validate_exact_body,
)

from adminme_bridge.adapters.base import AdapterStatus, BridgeAdapter, BridgeContext
from adminme_bridge.config import BridgeConfig, load_config
from adminme_bridge.ingest_client import IngestClient
from adminme_bridge.packs import (
    COMMUNICATION_SOURCE_SUBKIND,
    PackHandle,
    PackLoadError,
    discover_packs,
    instantiate_pack,
)


DEFAULT_CONFIG_PATH = Path("~/.adminme-bridge/config.yaml").expanduser()

logger = logging.getLogger(__name__)


class _CommandLedger:
    """Durable result envelope cache preventing duplicate create side effects."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS bridge_command_results ("
            "command_id TEXT PRIMARY KEY, envelope_json TEXT NOT NULL)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS bridge_command_attempts ("
            "command_id TEXT PRIMARY KEY, request_fingerprint TEXT NOT NULL, "
            "started_at TEXT NOT NULL)"
        )

    def get(
        self, command_id: str, request_fingerprint: str | None = None
    ) -> dict | None:
        row = self._conn.execute(
            "SELECT envelope_json FROM bridge_command_results WHERE command_id = ?",
            (command_id,),
        ).fetchone()
        if row is None:
            return None
        envelope = json.loads(row[0])
        cached_fingerprint = (envelope.get("payload") or {}).get("request_fingerprint")
        if (
            request_fingerprint is not None
            and cached_fingerprint != request_fingerprint
        ):
            raise ValueError("cached command result fingerprint mismatch")
        return envelope

    def put(self, command_id: str, envelope: dict) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO bridge_command_results VALUES (?, ?)",
            (command_id, json.dumps(envelope, separators=(",", ":"), sort_keys=True)),
        )

    def attempt_status(self, command_id: str, request_fingerprint: str) -> str | None:
        """Read prior uncertainty before a permission change can mask it."""
        row = self._conn.execute(
            "SELECT request_fingerprint FROM bridge_command_attempts WHERE command_id = ?",
            (command_id,),
        ).fetchone()
        if row is None:
            return None
        return "ambiguous" if str(row[0]) == request_fingerprint else "conflict"

    def begin(self, command_id: str, request_fingerprint: str) -> str:
        """Durably claim one effect before contacting a provider.

        A matching row without a result means a prior process could have
        reached the transport.  Returning ``ambiguous`` prevents a restart
        from guessing that retry is safe.
        """

        try:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute(
                "SELECT request_fingerprint FROM bridge_command_attempts "
                "WHERE command_id = ?",
                (command_id,),
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO bridge_command_attempts VALUES (?, ?, ?)",
                    (
                        command_id,
                        request_fingerprint,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                outcome = "new"
            elif str(row[0]) == request_fingerprint:
                outcome = "ambiguous"
            else:
                outcome = "conflict"
            self._conn.execute("COMMIT")
            return outcome
        except Exception:
            if self._conn.in_transaction:
                self._conn.execute("ROLLBACK")
            raise

    def abandon_before_provider_call(self, command_id: str) -> None:
        """Clear a claim only when an adapter proves no provider call began."""

        self._conn.execute(
            "DELETE FROM bridge_command_attempts WHERE command_id = ?",
            (command_id,),
        )

    def close(self) -> None:
        self._conn.close()


def configured_packs(config: BridgeConfig) -> list[PackHandle]:
    """Combine installed packs with explicitly enabled built-in packs."""

    installed = {
        handle.name: handle for handle in discover_packs(config.resolved_packs_dir())
    }
    built_in_root = Path(__file__).parent / "connector_packs"
    built_ins = {handle.name: handle for handle in discover_packs(built_in_root)}
    enabled = set(config.enabled_packs)
    if len(enabled) != len(config.enabled_packs):
        raise PackLoadError("enabled bridge packs must be unique")
    if not enabled <= set(config.configured_packs):
        raise PackLoadError("enabled bridge packs not found in configured set")
    unknown = set(config.configured_packs) - (set(installed) | set(built_ins))
    if unknown:
        raise PackLoadError(f"configured bridge packs not found: {sorted(unknown)}")
    selected: dict[str, PackHandle] = {}
    for name in enabled:
        if name in installed:
            selected[name] = installed[name]
        elif name in built_ins:
            selected[name] = built_ins[name]
    return [selected[name] for name in sorted(selected)]


class BridgeSupervisor:
    """Async supervisor for separately enrolled member-bridge adapters."""

    def __init__(
        self,
        *,
        config: BridgeConfig,
        ingest_client: IngestClient,
        packs: list[PackHandle] | None = None,
        adapters: list[BridgeAdapter] | None = None,
        device_identity_sha256: str = "",
    ) -> None:
        self._config = config
        self._device_identity_sha256 = device_identity_sha256
        self._ingest_client = ingest_client
        self._packs = list(packs or [])
        if adapters is None:
            self._adapters = [
                instantiate_pack(
                    handle,
                    config_override={
                        **self._config.pack_config.get(handle.name, {}),
                        **(
                            {
                                "cursor_db_path": str(
                                    self._config.resolved_cursor_db_path()
                                )
                            }
                            if handle.subkind
                            in {"knowledge-source", COMMUNICATION_SOURCE_SUBKIND}
                            else {}
                        ),
                    },
                )
                for handle in self._packs
            ]
        else:
            self._adapters = list(adapters)
        self._tasks: list[asyncio.Task[None]] = []
        self._stopping = False
        self._command_ledger = _CommandLedger(config.resolved_cursor_db_path())

    @property
    def adapters(self) -> list[BridgeAdapter]:
        return list(self._adapters)

    def make_context(self) -> BridgeContext:
        configuration_sha256 = hashlib.sha256(
            json.dumps(
                self._config.pack_config,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        device_identities = {
            str(pack_config.get("device_identity_sha256"))
            for name, pack_config in self._config.pack_config.items()
            if name in self._config.enabled_packs
            and pack_config.get("device_identity_sha256")
        }
        device_identity_sha256 = (
            next(iter(device_identities)) if len(device_identities) == 1 else ""
        )
        return BridgeContext(
            member_id=self._config.member_id,
            ingest_client=self._ingest_client,
            correlation_id_factory=lambda: str(uuid.uuid4()),
            now_factory=lambda: datetime.now(timezone.utc),
            tenant_id=self._config.tenant_id,
            enrollment_id=self._config.enrollment_id,
            configuration_sha256=configuration_sha256,
            device_identity_sha256=device_identity_sha256,
        )

    async def start(self) -> None:
        for adapter in self._adapters:
            self._tasks.append(
                asyncio.create_task(
                    adapter.start(ctx=self.make_context()),
                    name=f"bridge-adapter:{type(adapter).__name__}",
                )
            )
        if any(
            getattr(adapter, "provider", None)
            and callable(getattr(adapter, "apply_write", None))
            for adapter in self._adapters
        ) and callable(getattr(self._ingest_client, "fetch_commands", None)):
            self._tasks.append(
                asyncio.create_task(
                    self._command_loop(), name="bridge-command-dispatcher"
                )
            )

    async def stop(self) -> None:
        self._stopping = True
        for adapter in self._adapters:
            try:
                await adapter.stop()
            except Exception:  # noqa: BLE001 — best-effort
                logger.exception("bridge adapter stop() raised")
        # Give in-flight startup/emission work a bounded chance to commit
        # before cancellation. Bridge sinks can be waiting on SQLCipher or
        # an HTTP response when shutdown begins.
        _done, pending = (
            await asyncio.wait(self._tasks, timeout=5.0)
            if self._tasks
            else (set(), set())
        )
        for task in pending:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tasks.clear()
        self._command_ledger.close()

    async def _command_loop(self) -> None:
        adapters: dict[str, Any] = {
            str(getattr(adapter, "provider")): adapter
            for adapter in self._adapters
            if getattr(adapter, "provider", None)
            and callable(getattr(adapter, "apply_write", None))
        }
        while not self._stopping:
            try:
                response = await self._ingest_client.fetch_commands(list(adapters))
                for command in response["commands"]:
                    command_id = str(command["command_id"])
                    try:
                        result_envelope = self._command_ledger.get(
                            command_id, str(command["request_fingerprint"])
                        )
                    except ValueError as exc:
                        result_envelope = self._result_envelope(
                            command, error=exc, provider_called=False
                        )
                    if result_envelope is None:
                        previous_attempt = self._command_ledger.attempt_status(
                            command_id, str(command["request_fingerprint"])
                        )
                        if previous_attempt == "ambiguous":
                            result_envelope = self._result_envelope(
                                command,
                                result={
                                    "outcome": "ambiguous",
                                    "applied": False,
                                    "suppressed": False,
                                    "reason": "prior_bridge_attempt_has_no_terminal_receipt",
                                    "automatic_retry": False,
                                },
                                provider_called=True,
                            )
                        elif previous_attempt == "conflict":
                            result_envelope = self._result_envelope(
                                command,
                                error=ValueError(
                                    "command id reused with a different fingerprint"
                                ),
                                provider_called=False,
                            )
                        if result_envelope is not None:
                            self._command_ledger.put(command_id, result_envelope)
                    if result_envelope is None:
                        if (
                            command.get("provider") in APPLE_SERVICE_PROVIDERS
                            and not command.get("apple_authorization_denied")
                            and (command.get("payload") or {}).get(
                                "apple_service_permission"
                            )
                        ):
                            # Recheck revocation/binding just before claiming the
                            # local attempt. Transport uncertainty means no effect.
                            command[
                                "apple_service_authorization"
                            ] = await self._ingest_client.authorize_apple_command(
                                command, apple_digest(self._config.pack_config)
                            )
                        try:
                            adapter = self._authorized_command_adapter(
                                command, adapters
                            )
                        except ValueError as exc:
                            result_envelope = self._result_envelope(
                                command, error=exc, provider_called=False
                            )
                        else:
                            attempt = self._command_ledger.begin(
                                command_id, str(command["request_fingerprint"])
                            )
                            if attempt == "conflict":
                                result_envelope = self._result_envelope(
                                    command,
                                    error=ValueError(
                                        "command id reused with a different fingerprint"
                                    ),
                                    provider_called=False,
                                )
                            elif attempt == "ambiguous":
                                result_envelope = self._result_envelope(
                                    command,
                                    result={
                                        "outcome": "ambiguous",
                                        "applied": False,
                                        "suppressed": False,
                                        "reason": "prior_bridge_attempt_has_no_terminal_receipt",
                                        "automatic_retry": False,
                                    },
                                    provider_called=True,
                                )
                            else:
                                provider_called = True
                                try:
                                    provider_payload = dict(
                                        command.get("payload") or {}
                                    )
                                    if (
                                        command.get("apple_service_authorization")
                                        is not None
                                    ):
                                        provider_payload[
                                            "apple_service_authorization_expires_at"
                                        ] = command["apple_service_authorization"][
                                            "expires_at"
                                        ]
                                        provider_payload[
                                            "apple_service_allowed_container_ids"
                                        ] = list(
                                            command["apple_service_authorization"][
                                                "grant"
                                            ]["allowed_container_ids"]
                                        )
                                    result = await getattr(adapter, "apply_write")(
                                        str(command["operation"]),
                                        provider_payload,
                                        command.get("target_external_id"),
                                    )
                                except RuntimeError as exc:
                                    if "has not finished starting" in str(exc):
                                        self._command_ledger.abandon_before_provider_call(
                                            command_id
                                        )
                                        continue
                                    result_envelope = self._result_envelope(
                                        command,
                                        error=exc,
                                        provider_called=provider_called,
                                    )
                                except Exception as exc:  # noqa: BLE001
                                    result_envelope = self._result_envelope(
                                        command,
                                        error=exc,
                                        provider_called=provider_called,
                                    )
                                else:
                                    result_envelope = self._result_envelope(
                                        command,
                                        result=result,
                                        provider_called=provider_called,
                                    )
                        self._command_ledger.put(command_id, result_envelope)
                    submit = await self._ingest_client.submit(result_envelope)
                    if not submit.ok:
                        logger.warning(
                            "bridge command result upload failed for %s: %s",
                            command_id,
                            submit.error,
                        )
            except asyncio.CancelledError:
                return
            except Exception:  # noqa: BLE001 - polling supervisor retries
                logger.exception("bridge command poll failed")
            try:
                await asyncio.sleep(self._config.command_poll_interval_s)
            except asyncio.CancelledError:
                return

    @staticmethod
    def _command_container(
        policy: BridgePackPolicy, command: dict[str, Any]
    ) -> str | None:
        payload = command.get("payload")
        if not isinstance(payload, dict):
            return None
        aliases = {
            "calendar_identifier": (
                "calendar_identifier",
                "calendar_external_id",
                "calendar_id",
            ),
            "target_list_identifier": (
                "target_list_identifier",
                "target_list_id",
                "list_external_id",
                "list_identifier",
            ),
            "container_external_id": ("container_external_id",),
            "folder_external_id": ("folder_external_id",),
            "protected_source_id": ("protected_source_id",),
        }.get(str(policy.container_key), ())
        for key in aliases:
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    def _authorized_command_adapter(
        self, command: dict[str, Any], adapters: dict[str, Any]
    ) -> Any:
        """Validate the central command again at the provider-process edge."""

        apple_grant = None
        if command.get("apple_authorization_denied"):
            raise ValueError(
                "Apple permission was revoked or changed; this action cannot run"
            )
        if command.get("provider") in APPLE_SERVICE_PROVIDERS and (
            self._config.observation_only is True
            or self._config.provider_writes == "deny"
            or command.get("provider") == "apple_notes"
            or (command.get("payload") or {}).get("apple_service_permission")
        ):
            apple_grant = validate_edge_proof(
                self._config,
                command,
                device_identity_sha256=self._device_identity_sha256,
            )
        elif (
            self._config.observation_only is True
            or self._config.provider_writes == "deny"
        ):
            raise ValueError(
                "member bridge enrollment is read-only; provider writes denied"
            )
        violations: list[str] = []
        if int(command.get("schema_version") or 0) != 2:
            violations.append("schema_version")
        if command.get("member_id") != self._config.member_id:
            violations.append("member_id")
        provider = command.get("provider")
        matching = [
            BRIDGE_PACK_POLICIES[pack]
            for pack in self._config.enabled_packs
            if BRIDGE_PACK_POLICIES[pack].provider == provider
        ]
        policy = (
            write_policy(str(provider))
            if apple_grant is not None
            else matching[0]
            if len(matching) == 1
            else None
        )
        if policy is None:
            violations.append("provider")
        config = (
            apple_grant
            if apple_grant is not None
            else self._config.pack_config.get(policy.pack, {})
            if policy
            else {}
        )
        if command.get("provider_account_id") != config.get("provider_account_id"):
            violations.append("provider_account_id")
        if policy is None or command.get("operation") not in policy.write_operations:
            violations.append("operation")
        if policy is not None and policy.container_key is not None:
            container = self._command_container(policy, command)
            allowed_raw = config.get("allowed_container_ids")
            allowed_containers = (
                {str(value) for value in allowed_raw}
                if isinstance(allowed_raw, list)
                else set()
            )
            if container not in allowed_containers:
                violations.append("container")
        for key in (
            "command_id",
            "event_id",
            "domain_approval_event_id",
            "openclaw_approval_event_id",
        ):
            if not isinstance(command.get(key), str) or not command[key]:
                violations.append(key)
        fingerprint = command.get("request_fingerprint")
        if (
            not isinstance(fingerprint, str)
            or len(fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in fingerprint)
        ):
            violations.append("request_fingerprint")
        adapter = adapters.get(str(provider))
        if adapter is None:
            violations.append("adapter")
        payload = command.get("payload")
        if provider == "bluebubbles":
            if not isinstance(payload, dict):
                violations.append("payload")
            else:
                expected_fields = {
                    "contract",
                    "command_id",
                    "idempotency_key",
                    "member_owner_id",
                    "actor_principal_id",
                    "operator_principal_id",
                    "bridge_enrollment_id",
                    "provider_account_id",
                    "protected_source_id",
                    "native_chat_id",
                    "in_reply_to_native_message_id",
                    "interaction_id",
                    "expected_last_event_id",
                    "expected_native_revision",
                    "participant_ids",
                    "participant_fingerprint",
                    "is_group",
                    "body",
                    "body_fingerprint",
                    "capability_version",
                    "policy_version",
                    "setting_revision",
                    "approval_basis",
                    "approval_expires_at",
                    "correlation_id",
                    "causation_id",
                    "observation_mode_disposition",
                    "command_authentication",
                    "content_privacy_guard",
                }
                if set(payload) != expected_fields:
                    violations.append("payload_fields")
                if payload.get("contract") != REPLY_CONTRACT:
                    violations.append("reply_contract")
                if payload.get("command_id") != command.get("command_id"):
                    violations.append("payload_command_id")
                if payload.get("idempotency_key") != (
                    f"messages-reply:{self._config.member_id}:"
                    f"{command.get('command_id')}"
                ):
                    violations.append("idempotency_key")
                if payload.get("member_owner_id") != self._config.member_id:
                    violations.append("payload_member_id")
                if (
                    payload.get("actor_principal_id") != self._config.member_id
                    or payload.get("operator_principal_id") != self._config.member_id
                ):
                    violations.append("member_principal")
                if payload.get("bridge_enrollment_id") != self._config.enrollment_id:
                    violations.append("bridge_enrollment_id")
                if payload.get("provider_account_id") != config.get(
                    "provider_account_id"
                ):
                    violations.append("payload_provider_account_id")
                if payload.get("protected_source_id") != config.get(
                    "protected_source_id"
                ):
                    violations.append("protected_source_id")
                if payload.get("capability_version") != REPLY_CAPABILITY_VERSION:
                    violations.append("capability_version")
                if payload.get("policy_version") != REPLY_POLICY_VERSION:
                    violations.append("policy_version")
                if payload.get("approval_basis") != (
                    "authenticated_member_exact_preview"
                ):
                    violations.append("approval_basis")
                if payload.get("observation_mode_disposition") not in {
                    "suppress_without_transport_call",
                    "dispatch_if_all_gates_pass",
                }:
                    violations.append("observation_mode_disposition")
                if (
                    config.get("reply_transport") != REPLY_TRANSPORT
                    or config.get("reply_capability_version")
                    != REPLY_CAPABILITY_VERSION
                    or config.get("reply_permission_verified") is not True
                ):
                    violations.append("local_reply_capability")
                if adapter is not None:
                    status = adapter.status()
                    if status.state != "running" or not bool(
                        getattr(adapter, "_probe_verified", False)
                    ):
                        violations.append("local_transport_readiness")
                authentication = payload.get("command_authentication")
                if not isinstance(authentication, dict) or authentication != {
                    "scheme": "protected-central-event-log-v1",
                    "audience_bridge_enrollment_id": self._config.enrollment_id,
                }:
                    violations.append("command_authentication")
                guard = payload.get("content_privacy_guard")
                if (
                    not isinstance(guard, dict)
                    or guard.get("policy") != REPLY_CONTENT_GUARD
                    or guard.get("plain_text_only") is not True
                    or guard.get("attachments") != []
                    or guard.get("quoted_history_added") is not False
                    or guard.get("signature_added") is not False
                ):
                    violations.append("content_privacy_guard")
                try:
                    exact_body = validate_exact_body(payload.get("body"))
                except ValueError:
                    violations.append("body")
                else:
                    if payload.get("body_fingerprint") != body_sha256(exact_body):
                        violations.append("body_fingerprint")
                participants = payload.get("participant_ids")
                if (
                    not isinstance(participants, list)
                    or len(participants) < 2
                    or participants != sorted(set(participants))
                    or payload.get("participant_fingerprint")
                    != canonical_sha256(participants)
                ):
                    violations.append("participants")
                if (
                    not isinstance(payload.get("native_chat_id"), str)
                    or not payload.get("native_chat_id")
                    or payload.get("in_reply_to_native_message_id")
                    != command.get("target_external_id")
                ):
                    violations.append("native_target")
                try:
                    expires_at = datetime.fromisoformat(
                        str(payload.get("approval_expires_at")).replace("Z", "+00:00")
                    )
                except ValueError:
                    violations.append("approval_expiry")
                else:
                    if expires_at.tzinfo is None or expires_at <= datetime.now(
                        timezone.utc
                    ):
                        violations.append("approval_expired")
        if violations:
            raise ValueError(
                "bridge command contract denied: " + ", ".join(sorted(set(violations)))
            )
        return adapter

    def _result_envelope(
        self,
        command: dict,
        *,
        result: dict | None = None,
        error: Exception | None = None,
        provider_called: bool,
    ) -> dict:
        now = datetime.now(timezone.utc)
        if int(command.get("schema_version") or 1) != 2:
            raise ValueError("member bridge commands require external_sync v2")
        if error is None:
            event_type = "external_sync.write_completed"
            payload = {
                "command_id": command["command_id"],
                "member_id": self._config.member_id,
                "provider": command["provider"],
                "operation": command["operation"],
                "external_id": (result or {}).get("external_id"),
                "provider_revision": (result or {}).get("provider_revision"),
                "result": result or {},
                "completed_at": now.isoformat(),
                "suppressed": bool((result or {}).get("suppressed", False)),
            }
        else:
            event_type = "external_sync.write_failed"
            payload = {
                "command_id": command["command_id"],
                "member_id": self._config.member_id,
                "provider": command["provider"],
                "operation": command["operation"],
                "error_kind": (
                    "conflict"
                    if "conflict" in type(error).__name__.casefold()
                    or "conflict" in str(error).casefold()
                    else "not_found"
                    if isinstance(error, LookupError)
                    else "validation"
                    if isinstance(error, ValueError)
                    else "provider"
                ),
                "message": f"{type(error).__name__}: {error}",
                "retryable": not isinstance(error, (LookupError, ValueError)),
                "failed_at": now.isoformat(),
            }
        payload.update(
            {
                "command_event_id": command["event_id"],
                "domain_approval_event_id": command["domain_approval_event_id"],
                "openclaw_approval_event_id": command["openclaw_approval_event_id"],
                "provider_account_id": command["provider_account_id"],
                "request_fingerprint": command["request_fingerprint"],
                "provider_called": provider_called,
                "origin_proposal_id": command.get("origin_proposal_id"),
            }
        )
        return EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id=self._config.tenant_id,
            type=event_type,
            schema_version=2,
            occurred_at=now.isoformat(),
            source_adapter="adminme-bridge-command-dispatcher",
            source_account_id=str(command["provider_account_id"]),
            owner_scope=f"private:{self._config.member_id}",
            visibility_scope=f"private:{self._config.member_id}",
            sensitivity="normal",
            causation_id=str(command["event_id"]),
            idempotency_key=f"external-sync-result:{command['command_id']}",
            payload=payload,
        ).model_dump(mode="json")

    def snapshot(self) -> dict:
        return {
            "supervisor": {
                "stopping": self._stopping,
                "adapter_count": len(self._adapters),
                "packs_discovered": len(self._packs),
            },
            "adapters": [
                _status_to_dict(a.status() if hasattr(a, "status") else AdapterStatus())
                for a in self._adapters
            ],
        }


def _status_to_dict(status: AdapterStatus) -> dict:
    return {
        "state": status.state,
        "last_error": status.last_error,
        "last_cursor_advance_at": (
            status.last_cursor_advance_at.isoformat()
            if status.last_cursor_advance_at is not None
            else None
        ),
        "started_at": (
            status.started_at.isoformat() if status.started_at is not None else None
        ),
        "restart_count": status.restart_count,
        "extra": dict(status.extra),
    }


async def _run_until_signal(supervisor: BridgeSupervisor) -> None:
    """Wait for SIGTERM / SIGINT; then stop the supervisor cleanly."""
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            pass
    await supervisor.start()
    try:
        await stop_event.wait()
    finally:
        await supervisor.stop()


def _local_device_identity(config: BridgeConfig, config_path: Path) -> str:
    """Read existing local enrollment plus current Tailscale identity; never grant."""
    if config.acquisition_policy is None:
        return ""
    path = config_path.parent / "enrollment-receipt.json"
    try:
        if (
            path.is_symlink()
            or path.stat().st_uid != os.getuid()
            or path.stat().st_mode & 0o077
        ):
            return ""
        receipt = json.loads(path.read_text())
        if any(
            receipt.get(key) != getattr(config, key)
            for key in ("member_id", "enrollment_id", "instance_id")
        ):
            return ""
        status = subprocess.run(
            [tailscale_executable(), "status", "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if status.returncode:
            return ""
        value = json.loads(status.stdout)
        device = value["Self"]
        node = device.get("ID") or device.get("DNSName")
        login = value["User"][str(device["UserID"])]["LoginName"]
        if (
            not isinstance(node, str)
            or node != receipt.get("bridge_node_id")
            or login.casefold() != config.tailscale_identity.casefold()
        ):
            return ""
        return hashlib.sha256(node.encode()).hexdigest()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return ""


async def main_async(config_path: Path = DEFAULT_CONFIG_PATH) -> None:
    config = load_config(config_path)
    packs = configured_packs(config)
    ingest_client = IngestClient(
        central_endpoint_url=config.central_endpoint,
        tailscale_identity_header=config.tailscale_identity,
        enrollment_id=config.enrollment_id,
    )
    supervisor = BridgeSupervisor(
        config=config,
        ingest_client=ingest_client,
        packs=packs,
        device_identity_sha256=_local_device_identity(config, config_path),
    )
    try:
        await _run_until_signal(supervisor)
    finally:
        await ingest_client.aclose()


def run() -> None:  # pragma: no cover — script entrypoint shape only
    logging.basicConfig(level=logging.INFO)
    configured = Path(
        os.environ.get("ADMINME_BRIDGE_CONFIG", str(DEFAULT_CONFIG_PATH))
    ).expanduser()
    asyncio.run(main_async(configured))


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "BridgeSupervisor",
    "configured_packs",
    "main_async",
    "run",
]


if __name__ == "__main__":  # pragma: no cover - launchd module entrypoint
    run()
