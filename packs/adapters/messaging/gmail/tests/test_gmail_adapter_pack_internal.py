"""Gmail adapter pack — manifest + auth canaries.

C2 ships:
  1. Pack-load canary — manifest parses, sentinel fields match.
  2. Service-account auth flow (mocked) emits ``adapter.authenticated``.
  3. Sensitivity-floor downgrade attempt is refused per [§6 invariant 10].

F-3 (10c-iii): basename is unique
(``test_gmail_adapter_pack_internal.py``) so collection from the repo
root does not collide with sibling packs.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from adminme.adapters.base import Adapter, AdapterContext

PACK_ROOT = Path(__file__).resolve().parents[1]


def _load_adapter_module() -> Any:
    """Load ``adapter.py`` from the pack root via importlib — the same
    file-path import pattern the pipeline pack-loader uses. Packs are
    not a top-level Python package under poetry (only ``adminme`` is)."""
    spec = importlib.util.spec_from_file_location(
        "gmail_adapter_under_test", PACK_ROOT / "adapter.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Loaded dynamically — packs/ is not a Python package under poetry's
# package config. mypy gets ``Any`` (the alternative is a TYPE_CHECKING
# import path that doesn't resolve, since packs/adapters/messaging/gmail
# is not on the import path).
_adapter_mod: Any = _load_adapter_module()
GmailAdapter: Any = _adapter_mod.GmailAdapter
GMAIL_READONLY_SCOPE: str = _adapter_mod.GMAIL_READONLY_SCOPE


# ---------------------------------------------------------------------------
# 1) pack-load canary
# ---------------------------------------------------------------------------


def test_pack_manifest_loads_cleanly() -> None:
    """``pack.yaml`` parses, declares ``kind: adapter``, names the
    GmailAdapter class, and lists the four event types the adapter
    depends on / emits."""
    manifest_path = PACK_ROOT / "pack.yaml"
    with manifest_path.open() as f:
        manifest = yaml.safe_load(f)

    assert manifest["pack"]["id"] == "adapter:messaging-gmail-api"
    assert manifest["pack"]["kind"] == "adapter"
    assert manifest["pack"]["version"] == "1.0.0"
    assert manifest["runtime"]["entrypoint"] == "adapter.py"
    assert manifest["runtime"]["class"] == "GmailAdapter"
    assert manifest["family"] == "messaging"
    assert manifest["triggers"]["kind"] == "external_poll"
    assert manifest["triggers"]["schedule"] == "*/2 * * * *"

    declared = set(manifest["depends_on"]["event_types"])
    for required in (
        "adapter.error",
        "adapter.authenticated",
        "adapter.cursor_advanced",
        "messaging.received",
    ):
        assert required in declared, f"manifest missing {required!r}"

    # The two sibling config files exist and are referenced.
    assert (PACK_ROOT / manifest["config"]["schema"]).exists()
    assert (PACK_ROOT / manifest["config"]["example"]).exists()


def test_gmail_adapter_satisfies_protocol() -> None:
    """The runtime class implements the L1 Adapter Protocol —
    the supervisor's loader will not accept an instance that doesn't."""
    adapter = GmailAdapter(
        config={
            "service_account_key_ref": "op://test/vault/item/field",
            "user_email": "sandbox@example.test",
        },
    )
    assert isinstance(adapter, Adapter)
    assert adapter.name == "messaging-gmail-api"
    assert adapter.family == "messaging"
    assert adapter.version == "1.0.0"
    assert adapter.sensitivity_floor == "normal"


# ---------------------------------------------------------------------------
# 2) service-account auth flow (mocked)
# ---------------------------------------------------------------------------


class _FakeEventLog:
    """Captures synchronous append calls for assertion."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def append_sync(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def _ctx(event_log: _FakeEventLog) -> AdapterContext:
    return AdapterContext(
        event_log=event_log,  # type: ignore[arg-type]
        config={},
        correlation_id_factory=lambda: "corr-test",
        now_factory=lambda: datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc),
    )


async def test_service_account_auth_emits_authenticated() -> None:
    """``start()`` resolves the service-account ref, builds a fake
    Gmail service, and emits ``adapter.authenticated`` v1 — then the
    C2 stub raises ``NotImplementedError`` for the polling loop."""
    resolved_credentials: list[str] = []
    built_services: list[tuple[dict[str, Any], list[str], str]] = []

    def fake_resolver(op_ref: str) -> dict[str, Any]:
        resolved_credentials.append(op_ref)
        return {"type": "service_account", "client_email": "fake@fake"}

    def fake_factory(
        creds: dict[str, Any], scopes: list[str], subject: str
    ) -> object:
        built_services.append((creds, scopes, subject))
        return object()

    adapter = GmailAdapter(
        config={
            "service_account_key_ref": "op://test/vault/gmail/sa",
            "user_email": "sandbox@example.test",
        },
        credentials_resolver=fake_resolver,
        service_factory=fake_factory,
    )
    event_log = _FakeEventLog()
    ctx = _ctx(event_log)

    with pytest.raises(NotImplementedError, match="commit 3"):
        await adapter.start(ctx=ctx)

    # Resolver was called with the configured op-ref.
    assert resolved_credentials == ["op://test/vault/gmail/sa"]
    # Service factory got the resolved creds + readonly scope + subject.
    assert len(built_services) == 1
    _creds, scopes, subject = built_services[0]
    assert scopes == [GMAIL_READONLY_SCOPE]
    assert subject == "sandbox@example.test"

    # adapter.authenticated was emitted exactly once at v1.
    auth_events = [c for c in event_log.calls if c["type"] == "adapter.authenticated"]
    assert len(auth_events) == 1
    payload = auth_events[0]["payload"]
    assert auth_events[0]["schema_version"] == 1
    assert payload["adapter_name"] == "messaging-gmail-api"
    assert payload["adapter_family"] == "messaging"
    assert payload["token_storage_ref"] == "op://test/vault/gmail/sa"
    assert payload["scopes"] == [GMAIL_READONLY_SCOPE]
    # token_storage_ref is the op-ref — NEVER the raw key.
    assert "PRIVATE KEY" not in payload["token_storage_ref"]
    assert auth_events[0]["correlation_id"] == "corr-test"


async def test_start_rejects_non_op_ref() -> None:
    """A non-op:// ``service_account_key_ref`` is refused at start()
    before any network call — defends against accidental raw-secret
    placement in config."""
    adapter = GmailAdapter(
        config={
            "service_account_key_ref": "ya29.A0AfH6SMB_RAW_TOKEN",
            "user_email": "sandbox@example.test",
        },
        credentials_resolver=lambda _r: {},
        service_factory=lambda _c, _s, _u: object(),
    )
    with pytest.raises(ValueError, match="1Password reference"):
        await adapter.start(ctx=_ctx(_FakeEventLog()))


# ---------------------------------------------------------------------------
# 3) sensitivity-floor invariant [§6 invariant 10]
# ---------------------------------------------------------------------------


class _PrivilegedGmailAdapter(GmailAdapter):
    """Hypothetical privileged-by-default Gmail subclass — e.g., the
    law-practice email pattern. Used only to exercise the downgrade
    refusal; real privileged adapters live in 11b/c/d or later."""

    HARDCODED_FLOOR = "privileged"  # type: ignore[assignment]


def test_sensitivity_floor_config_can_raise_to_privileged() -> None:
    """Raising the floor in config is allowed — config sets
    'privileged' on a 'normal'-default adapter."""
    adapter = GmailAdapter(
        config={
            "service_account_key_ref": "op://test/v/i/f",
            "user_email": "sandbox@example.test",
            "sensitivity_floor": "privileged",
        },
    )
    assert adapter.sensitivity_floor == "privileged"


def test_sensitivity_floor_cannot_be_lowered_from_privileged() -> None:
    """Lowering 'privileged' to 'normal' via config is refused per
    SYSTEM_INVARIANTS.md §6 invariant 10. The constructor raises before
    any auth / network work runs."""
    with pytest.raises(ValueError, match="downgrade"):
        _PrivilegedGmailAdapter(
            config={
                "service_account_key_ref": "op://test/v/i/f",
                "user_email": "sandbox@example.test",
                "sensitivity_floor": "normal",
            },
        )


def test_sensitivity_floor_rejects_unknown_value() -> None:
    """Unknown floor strings are refused — defends against typos /
    fabricated values bleeding into the supervisor's adapter set."""
    with pytest.raises(ValueError, match="must be"):
        GmailAdapter(
            config={
                "service_account_key_ref": "op://test/v/i/f",
                "user_email": "sandbox@example.test",
                "sensitivity_floor": "high",
            },
        )
