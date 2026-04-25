"""
Session — (current_user, requested_scopes) wrapper for every read and write.

Implemented in prompt 08a per ADMINISTRATEME_BUILD.md §L3-continued and
SYSTEM_INVARIANTS.md §6, [arch §6], CONSOLE_PATTERNS.md §§1-2,
DIAGRAMS.md §§4-5.

There is no global DB connection. Every read and every write happens under a
``Session(current_user, requested_scopes)`` object; no code imports
``sqlalchemy.orm.Session`` directly [§6.1].

Sessions carry BOTH an ``authMember`` (governs what you can do) and a
``viewMember`` (governs whose data you are reading):
- Only principals may set view-as; ambient entities cannot be viewed-as;
  children cannot view-as [§6.2, CONSOLE_PATTERNS.md §2, DIAGRAMS.md §4].
- Writes ALWAYS use authMember; viewMember never authorizes a write — a
  principal viewing-as another principal still writes under their own
  identity [§6.3].
- Two-member commitments record both ids separately
  (``approved_by=A``, ``owner=B``) — do not collapse [§6.3].

Three constructors:
- ``build_session_from_node(req, config)`` — Node console bridge; consumes
  Tailscale-injected identity per CONSOLE_PATTERNS.md §1.
- ``build_session_from_openclaw(request, config)`` — slash-command /
  standing-order dispatch.
- ``build_internal_session(actor, role, tenant_id)`` — bootstrap, migrations,
  CLI, daemons, forward xlsx workbook builders.

Source enum captures provenance; the value lands in event ``actor_identity``
through 08b's guardedWrite gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from adminme.lib.scope import ScopeViolation

AuthRole = Literal["principal", "child", "ambient", "coach_session", "device"]
ViewRole = Literal["principal", "child", "ambient", "device"]
DmScope = Literal["per_channel_peer", "shared"]
Source = Literal[
    "node_console",
    "product_api_internal",
    "openclaw_slash_command",
    "openclaw_standing_order",
    "bootstrap_wizard",
    "xlsx_workbooks",
    "xlsx_reverse_daemon",
]


class AuthError(Exception):
    """Raised when a session cannot be constructed because identity is
    missing, malformed, or the requested view-as is not permitted
    [DIAGRAMS.md §4]."""


_VALID_AUTH_ROLES: frozenset[str] = frozenset(
    {"principal", "child", "ambient", "coach_session", "device"}
)
_VALID_VIEW_ROLES: frozenset[str] = frozenset(
    {"principal", "child", "ambient", "device"}
)
_VALID_SOURCES: frozenset[str] = frozenset(
    {
        "node_console",
        "product_api_internal",
        "openclaw_slash_command",
        "openclaw_standing_order",
        "bootstrap_wizard",
        "xlsx_workbooks",
        "xlsx_reverse_daemon",
    }
)


@dataclass(frozen=True)
class Session:
    """Per-request context carrying the (auth_member, view_member, role,
    tenant) tuple. Frozen so it cannot be mutated mid-request — view-as
    state is set once at construction and read everywhere else
    [CONSOLE_PATTERNS.md §2]."""

    tenant_id: str
    auth_member_id: str
    auth_role: AuthRole
    view_member_id: str
    view_role: ViewRole
    dm_scope: DmScope
    source: Source
    correlation_id: str | None = None
    # Used by tests in 08a; 08b populates from authority.yaml. Frozen
    # default avoids mutable-default trap.
    child_forbidden_tags: frozenset[str] = field(
        default_factory=frozenset
    )

    @property
    def is_view_as(self) -> bool:
        """True iff the principal is rendering another member's surface
        [CONSOLE_PATTERNS.md §2]."""
        return self.view_member_id != self.auth_member_id

    @property
    def allowed_scopes(self) -> frozenset[str]:
        """The set of ``visibility_scope`` values this session may read
        [BUILD.md L3-continued, DIAGRAMS.md §5].

        Sensitivity / privileged-owner checks happen separately in
        ``scope.allowed_read``; this set is the visibility-scope axis only.
        """
        # Ambient: no surface at all.
        if self.auth_role == "ambient":
            return frozenset()

        scopes: set[str] = set()
        # Household-shared content is visible to principals, children,
        # devices, and coach sessions. Org-scoped content is visible to
        # principals and coach sessions (the latter for context build,
        # subject to coach_column_strip).
        if self.auth_role in ("principal", "child", "coach_session", "device"):
            scopes.add("shared:household")
        # Self-private content is always allowed. The view_member_id
        # carries through here for view-as: principal A viewing principal
        # B's surface gets B's private content but the privileged check
        # in scope.allowed_read still uses the auth_member to decide
        # privileged-owner status.
        scopes.add(f"private:{self.view_member_id}")
        # Principals (and coach sessions) may see org content.
        if self.auth_role in ("principal", "coach_session"):
            scopes.add("org:*")
        return frozenset(scopes)


def _validate(
    *,
    tenant_id: str,
    auth_member_id: str,
    auth_role: str,
    view_member_id: str,
    view_role: str,
    source: str,
) -> None:
    if not tenant_id:
        raise AuthError("missing_tenant_id")
    if not auth_member_id:
        raise AuthError("missing_auth_member_id")
    if auth_role not in _VALID_AUTH_ROLES:
        raise AuthError(f"invalid_auth_role:{auth_role}")
    if view_role not in _VALID_VIEW_ROLES:
        raise AuthError(f"invalid_view_role:{view_role}")
    if source not in _VALID_SOURCES:
        raise AuthError(f"invalid_source:{source}")
    if not view_member_id:
        raise AuthError("missing_view_member_id")
    # DIAGRAMS.md §4 view-as blocking matrix.
    if view_member_id != auth_member_id:
        # Only principals can view-as.
        if auth_role != "principal":
            raise AuthError("only_principals_can_view_as")
        # Ambient entities cannot be viewed-as.
        if view_role == "ambient":
            raise AuthError("view_target_has_no_surface")


def build_session_from_node(req: Any, config: Any) -> Session:
    """Construct a Session from a Node-console-bridged request.

    The console resolves Tailscale identity and the optional view-as
    target, packages them into ``req.identity`` and ``req.view_as``, and
    forwards via the HTTP bridge per CONSOLE_PATTERNS.md §1. Expected
    request shape::

        req.identity = {
            "member_id": "...",
            "role": "principal" | "child" | "ambient" | "coach_session"
                    | "device",
            "tailscale_login": "...",
        }
        req.view_as = {  # optional
            "member_id": "...",
            "role": "principal" | "child" | "ambient" | "device",
        }
        req.correlation_id = "..." | None

    [§6.2] Only principals may set view-as; ambient cannot be a view
    target; children cannot view-as. [§9.5] Tailscale-User-Login is the
    only auth seam; this constructor never falls back to a password.
    """
    identity = getattr(req, "identity", None)
    if not isinstance(identity, dict):
        raise AuthError("missing_identity")
    auth_member_id = identity.get("member_id")
    auth_role = identity.get("role")
    if not auth_member_id or not auth_role:
        raise AuthError("malformed_identity")
    if auth_role not in _VALID_AUTH_ROLES:
        raise AuthError(f"invalid_auth_role:{auth_role}")

    view = getattr(req, "view_as", None)
    raw_view_role: str
    if isinstance(view, dict) and view.get("member_id"):
        view_member_id = view["member_id"]
        raw_view_role = view.get("role") or auth_role
    else:
        view_member_id = auth_member_id
        raw_view_role = (
            "principal" if auth_role == "coach_session" else auth_role
        )

    if raw_view_role not in _VALID_VIEW_ROLES:
        raise AuthError(f"invalid_view_role:{raw_view_role}")
    view_role: ViewRole = raw_view_role  # type: ignore[assignment]

    correlation_id = getattr(req, "correlation_id", None)
    tenant_id = getattr(config, "tenant_id", None)
    if not tenant_id:
        raise AuthError("missing_tenant_id")

    _validate(
        tenant_id=tenant_id,
        auth_member_id=auth_member_id,
        auth_role=auth_role,
        view_member_id=view_member_id,
        view_role=view_role,
        source="node_console",
    )

    return Session(
        tenant_id=tenant_id,
        auth_member_id=auth_member_id,
        auth_role=auth_role,  # type: ignore[arg-type]
        view_member_id=view_member_id,
        view_role=view_role,
        dm_scope="per_channel_peer",
        source="node_console",
        correlation_id=correlation_id,
    )


def build_session_from_openclaw(request: Any, config: Any) -> Session:
    """Construct a Session from an OpenClaw slash-command or standing-order
    dispatch [§8.1, cheatsheet Q2].

    Expected request shape::

        request.invoking_member_id = "..."
        request.invoking_role = "principal" | "child" | ...
        request.kind = "slash_command" | "standing_order"
        request.correlation_id = "..." | None

    Standing orders run unattended; slash commands are typed by a
    member. Both paths land here; ``source`` differentiates downstream.
    """
    invoking_id = getattr(request, "invoking_member_id", None)
    invoking_role = getattr(request, "invoking_role", None)
    kind = getattr(request, "kind", None)
    if not invoking_id or not invoking_role:
        raise AuthError("malformed_openclaw_request")
    if kind not in ("slash_command", "standing_order"):
        raise AuthError(f"invalid_openclaw_kind:{kind}")

    tenant_id = getattr(config, "tenant_id", None)
    if not tenant_id:
        raise AuthError("missing_tenant_id")

    source: Source = (
        "openclaw_slash_command"
        if kind == "slash_command"
        else "openclaw_standing_order"
    )

    view_role_literal: ViewRole
    if invoking_role == "coach_session":
        view_role_literal = "principal"
    elif invoking_role in ("principal", "child", "ambient", "device"):
        view_role_literal = invoking_role  # type: ignore[assignment]
    else:
        raise AuthError(f"invalid_auth_role:{invoking_role}")
    view_role: ViewRole = view_role_literal

    _validate(
        tenant_id=tenant_id,
        auth_member_id=invoking_id,
        auth_role=invoking_role,
        view_member_id=invoking_id,
        view_role=view_role,
        source=source,
    )

    return Session(
        tenant_id=tenant_id,
        auth_member_id=invoking_id,
        auth_role=invoking_role,
        view_member_id=invoking_id,
        view_role=view_role,
        dm_scope="per_channel_peer",
        source=source,
        correlation_id=getattr(request, "correlation_id", None),
    )


def build_internal_session(
    actor: str,
    role: str,
    tenant_id: str,
    *,
    correlation_id: str | None = None,
) -> Session:
    """Construct a Session for a non-user-facing caller: bootstrap,
    migrations, CLI, daemons, forward xlsx workbook builders, skill runner.

    ``actor`` is a short string used as both ``auth_member_id`` and the
    actor identity carried into emitted events. ``role`` is one of the
    auth roles; ``device`` is the typical pick for system callers
    [§6, CONSOLE_PATTERNS.md §1].

    The corresponding ``source`` is inferred from ``actor``:
    ``xlsx_workbooks`` → ``xlsx_workbooks``; ``xlsx_reverse`` →
    ``xlsx_reverse_daemon``; otherwise ``product_api_internal``.
    """
    if role not in _VALID_AUTH_ROLES:
        raise AuthError(f"invalid_role:{role}")
    if role == "ambient":
        # Ambient is a profile role, not an internal-actor role; refuse
        # silently rather than mint a useless Session.
        raise AuthError("ambient_cannot_act_internally")

    source: Source
    if actor == "xlsx_workbooks":
        source = "xlsx_workbooks"
    elif actor == "xlsx_reverse":
        source = "xlsx_reverse_daemon"
    elif actor == "bootstrap":
        source = "bootstrap_wizard"
    else:
        source = "product_api_internal"

    view_role: ViewRole = "device" if role == "device" else (
        "principal" if role == "coach_session" else role  # type: ignore[assignment]
    )

    _validate(
        tenant_id=tenant_id,
        auth_member_id=actor,
        auth_role=role,
        view_member_id=actor,
        view_role=view_role,
        source=source,
    )

    return Session(
        tenant_id=tenant_id,
        auth_member_id=actor,
        auth_role=role,  # type: ignore[arg-type]
        view_member_id=actor,
        view_role=view_role,
        dm_scope="shared",
        source=source,
        correlation_id=correlation_id,
    )


def build_session_from_xlsx_reverse_daemon(
    detected_member_id: str | None,
    config: Any,
    *,
    correlation_id: str | None = None,
) -> Session:
    """Construct a Session for the xlsx reverse daemon's per-cycle work
    [§6, prompt 08b UT-7 closure].

    The reverse daemon detects principal-authored workbook edits and emits
    domain events on principal authority. Two regimes:

    - ``detected_member_id`` is None  → system-internal device-role session;
      the daemon couldn't pin a specific principal to the edit, so events
      attribute to a generic device actor (``actor_identity = "xlsx_reverse"``).
    - ``detected_member_id`` is set    → Session attributing that principal as
      ``auth_member`` so emitted events carry their principal_member_id in
      ``actor_identity``. The view_member equals auth_member (the daemon
      does not view-as).

    The caller — XlsxReverseDaemon — uses this helper inside
    ``_run_cycle_internal`` and passes the resulting Session into ``_append``
    so every emit is attributed correctly. UT-7 was OPEN through 07c-β with
    a literal ``"xlsx_reverse"`` actor_identity; 08b closes it by routing
    attribution through Session [arch §6].
    """
    tenant_id = getattr(config, "tenant_id", None)
    if not tenant_id:
        raise AuthError("missing_tenant_id")

    if detected_member_id is None:
        # System-internal device-role session. Same shape as
        # ``build_internal_session("xlsx_reverse", "device", tenant_id)``
        # — kept inline so this helper is the single seam the daemon
        # consults.
        return Session(
            tenant_id=tenant_id,
            auth_member_id="xlsx_reverse",
            auth_role="device",
            view_member_id="xlsx_reverse",
            view_role="device",
            dm_scope="shared",
            source="xlsx_reverse_daemon",
            correlation_id=correlation_id,
        )

    # Detected principal — attribute on principal authority. Role is
    # 'principal' by definition; ambient/child/coach members do not edit
    # workbooks under v1 [§6.2]. If a future household-config flag wants
    # to allow non-principal members to author xlsx changes, the
    # role-resolution lives here.
    _validate(
        tenant_id=tenant_id,
        auth_member_id=detected_member_id,
        auth_role="principal",
        view_member_id=detected_member_id,
        view_role="principal",
        source="xlsx_reverse_daemon",
    )
    return Session(
        tenant_id=tenant_id,
        auth_member_id=detected_member_id,
        auth_role="principal",
        view_member_id=detected_member_id,
        view_role="principal",
        dm_scope="per_channel_peer",
        source="xlsx_reverse_daemon",
        correlation_id=correlation_id,
    )


__all__ = [
    "AuthError",
    "AuthRole",
    "DmScope",
    "ScopeViolation",
    "Session",
    "Source",
    "ViewRole",
    "build_internal_session",
    "build_session_from_node",
    "build_session_from_openclaw",
    "build_session_from_xlsx_reverse_daemon",
]
