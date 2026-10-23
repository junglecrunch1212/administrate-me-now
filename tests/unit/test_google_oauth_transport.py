"""Member Google OAuth boundaries without network access or real credentials."""

from __future__ import annotations

import base64
import hashlib
import json
from types import SimpleNamespace
from typing import Any

import pytest

from adminme import google_oauth_transport as oauth
from adminme.adapters.credentials import (
    assert_installed_user_binding,
    credential_member_digest,
)

ORIGIN = "https://console.fixture-tail.ts.net"
REDIRECT = ORIGIN + oauth.CALLBACK_PATH
MEMBER = "member-fixture-b"
ACCOUNT = "member@example.invalid"
OWNER = credential_member_digest(MEMBER)
SECRET_NAME = f"google-oauth-member-{OWNER}-request-123"
VERIFIER = "A" * 64
STATE = "B" * 43
SCOPES = [*oauth.IDENTITY_SCOPES, "https://www.googleapis.com/auth/calendar.readonly"]
CLIENT_REF = "keychain://fixture/google-member-web-client-fixture"


def client_document() -> dict[str, Any]:
    return {
        "web": {
            "client_id": "client-fixture.apps.googleusercontent.com",
            "client_secret": "fixture-client-secret",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": oauth.TOKEN_URI,
            "redirect_uris": [REDIRECT],
            "javascript_origins": [ORIGIN],
        }
    }


class Store:
    def __init__(self) -> None:
        self.values = {CLIENT_REF: json.dumps(client_document()).encode()}
        self.puts: list[tuple[str, bytes]] = []

    def get(self, reference: str) -> bytes:
        return self.values[reference]

    def put(self, name: str, value: bytes) -> str:
        self.puts.append((name, value))
        reference = f"keychain://fixture/{name}"
        self.values[reference] = value
        return reference

    def has(self, reference: str) -> bool:
        return reference in self.values


class Session:
    def __init__(self) -> None:
        self.profile: object = {
            "email": ACCOUNT,
            "email_verified": True,
            "sub": "subject-fixture",
        }
        self.status = 200
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.closed = False

    def __enter__(self) -> Session:
        return self

    def __exit__(self, *_args: object) -> None:
        self.closed = True

    def get(self, url: str, **kwargs: object) -> object:
        self.calls.append((url, kwargs))
        return SimpleNamespace(
            status_code=self.status, content=b"{}", json=lambda: self.profile
        )


class FakeFlow:
    def __init__(self) -> None:
        self.credentials = SimpleNamespace(
            scopes=list(SCOPES),
            granted_scopes=list(SCOPES),
            refresh_token="fixture-refresh-secret",
            token="fixture-access-secret",
            client_id="client-fixture.apps.googleusercontent.com",
            client_secret="fixture-client-secret",
        )
        self.session = Session()
        self.authorize_calls: list[dict[str, object]] = []
        self.fetch_calls: list[dict[str, object]] = []
        self.error: Exception | None = None

    def authorization_url(self, **kwargs: object) -> tuple[str, object]:
        self.authorize_calls.append(kwargs)
        return oauth.AUTH_URI + "?state=fixture", kwargs["state"]

    def fetch_token(self, **kwargs: object) -> None:
        self.fetch_calls.append(kwargs)
        if self.error:
            raise self.error

    def authorized_session(self) -> Session:
        return self.session


@pytest.fixture
def setup(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[oauth.GoogleWebOAuth, Store, FakeFlow, list[dict[str, Any]]]:
    store, flow = Store(), FakeFlow()
    constructions: list[dict[str, Any]] = []

    def create(document: object, **kwargs: Any) -> FakeFlow:
        constructions.append({"document": document, **kwargs})
        return flow

    monkeypatch.setattr(oauth.Flow, "from_client_config", create)
    return oauth.GoogleWebOAuth(store, CLIENT_REF, REDIRECT), store, flow, constructions


def exchange(
    transport: oauth.GoogleWebOAuth, **overrides: Any
) -> oauth.GoogleOAuthGrant:
    return transport.exchange(
        **{
            "code": "fixture-authorization-code",
            "verifier": VERIFIER,
            "scopes": SCOPES,
            "expected_account_id": ACCOUNT,
            "member_id": MEMBER,
            "secret_name": SECRET_NAME,
            **overrides,
        }
    )


def test_authorize_uses_exact_redirect_pkce_and_separate_offline_consent(
    setup: tuple[Any, ...],
) -> None:
    transport, store, flow, constructions = setup
    assert transport.start(state=STATE, verifier=VERIFIER, scopes=SCOPES).startswith(
        oauth.AUTH_URI
    )
    assert constructions[0]["redirect_uri"] == REDIRECT
    assert constructions[0]["code_verifier"] == VERIFIER
    assert constructions[0]["autogenerate_code_verifier"] is False
    assert constructions[0]["scopes"] == sorted(SCOPES)
    assert flow.authorize_calls == [
        {
            "state": STATE,
            "access_type": "offline",
            "prompt": "consent select_account",
            "include_granted_scopes": "false",
            "code_challenge": base64.urlsafe_b64encode(
                hashlib.sha256(VERIFIER.encode()).digest()
            )
            .decode()
            .rstrip("="),
            "code_challenge_method": "S256",
        }
    ]
    assert store.puts == []


def test_exchange_stores_only_verified_exact_member_grant(
    setup: tuple[Any, ...],
) -> None:
    transport, store, flow, _ = setup
    grant = exchange(transport, expected_account_id=ACCOUNT.upper())
    assert grant.account_id == ACCOUNT
    assert grant.subject == "subject-fixture"
    assert set(grant.scopes) == set(SCOPES)
    name, payload = store.puts[0]
    document = json.loads(payload)
    assert name == SECRET_NAME
    assert document["provider_subject"] == grant.subject
    assert document["scopes"] == sorted(SCOPES)
    assert document["token_uri"] == oauth.TOKEN_URI
    assert "token" not in document
    assert b"fixture-access-secret" not in payload
    assert b"fixture-authorization-code" not in payload
    assert VERIFIER.encode() not in payload
    assert_installed_user_binding(
        document, member_id=MEMBER, provider_account_id=ACCOUNT
    )
    with pytest.raises(RuntimeError, match="binding"):
        assert_installed_user_binding(
            document, member_id="different-member", provider_account_id=ACCOUNT
        )
    assert flow.fetch_calls == [
        {"code": "fixture-authorization-code", "timeout": 20, "allow_redirects": False}
    ]
    assert flow.session.calls == [
        (oauth.USERINFO_URI, {"timeout": 20, "allow_redirects": False})
    ]
    assert flow.session.closed


@pytest.mark.parametrize(
    "profile",
    [
        {
            "email": "other@example.invalid",
            "email_verified": True,
            "sub": "subject-fixture",
        },
        {"email": ACCOUNT, "email_verified": False, "sub": "subject-fixture"},
        {"email": ACCOUNT, "email_verified": "true", "sub": "subject-fixture"},
        {"email": ACCOUNT, "email_verified": True},
        {"email": ACCOUNT, "email_verified": True, "sub": ""},
        [],
    ],
)
def test_wrong_unverified_or_missing_subject_never_stores(
    setup: tuple[Any, ...], profile: object
) -> None:
    transport, store, flow, _ = setup
    flow.session.profile = profile
    with pytest.raises(oauth.GoogleOAuthError):
        exchange(transport)
    assert store.puts == []


@pytest.mark.parametrize(
    "granted",
    [None, [], SCOPES[:-1], [*SCOPES, "https://www.googleapis.com/auth/tasks"]],
)
def test_missing_partial_or_broader_grant_never_stores(
    setup: tuple[Any, ...], granted: object
) -> None:
    transport, store, flow, _ = setup
    flow.credentials.granted_scopes = granted
    with pytest.raises(oauth.GoogleOAuthError):
        exchange(transport)
    assert store.puts == []
    assert flow.session.calls == []


def test_identity_scope_alias_matches_without_widening(setup: tuple[Any, ...]) -> None:
    transport, _, flow, _ = setup
    flow.credentials.granted_scopes = (
        "openid email https://www.googleapis.com/auth/calendar.readonly"
    )
    assert set(exchange(transport).scopes) == set(SCOPES)


def test_missing_refresh_token_and_redirected_userinfo_fail_closed(
    setup: tuple[Any, ...],
) -> None:
    transport, store, flow, _ = setup
    flow.credentials.refresh_token = None
    with pytest.raises(oauth.GoogleOAuthError, match="offline"):
        exchange(transport)
    flow.credentials.refresh_token = "fixture-refresh-secret"
    flow.session.status = 302
    with pytest.raises(oauth.GoogleOAuthError, match="identity"):
        exchange(transport)
    assert store.puts == []


def test_transport_errors_cannot_disclose_codes_or_credentials(
    setup: tuple[Any, ...],
) -> None:
    transport, store, flow, _ = setup
    flow.error = RuntimeError("fixture-authorization-code fixture-refresh-secret")
    with pytest.raises(oauth.GoogleOAuthError, match="begin a new connection") as error:
        exchange(transport)
    assert "fixture" not in str(error.value)
    assert error.value.__suppress_context__ is True
    assert store.puts == []


def test_existing_grant_is_never_overwritten(setup: tuple[Any, ...]) -> None:
    transport, store, _, _ = setup
    prior = b"existing-grant"
    store.values[f"keychain://fixture/{SECRET_NAME}"] = prior
    with pytest.raises(oauth.GoogleOAuthError, match="already in use"):
        exchange(transport)
    assert store.values[f"keychain://fixture/{SECRET_NAME}"] == prior
    assert store.puts == []


def test_wrong_member_secret_destination_rejected_before_exchange(
    setup: tuple[Any, ...],
) -> None:
    transport, store, flow, _ = setup
    with pytest.raises(oauth.GoogleOAuthError, match="member-owned"):
        exchange(transport, member_id="different-member")
    assert flow.fetch_calls == []
    assert store.puts == []


@pytest.mark.parametrize(
    "origin",
    [
        "http://console.fixture-tail.ts.net",
        "https://console.fixture-tail.ts.net:443",
        "https://console.fixture-tail.ts.net/path",
        "https://console.fixture-tail.ts.net?next=x",
        "https://console.fixture-tail.ts.net#x",
        "https://other.example.invalid",
        "https://" + "user" + "@" + "console.fixture-tail.ts.net",
        "https://console.fixture-tail.ts.net.evil.invalid",
        "https://127.0.0.1",
        "https://[::1]",
        "https://console.fixture-tail.ts.net/ ",
    ],
)
def test_only_canonical_tls_origin_accepted(origin: str) -> None:
    with pytest.raises(oauth.GoogleOAuthError):
        oauth.dashboard_redirect_uri(origin)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("token_uri", "https://evil.example.invalid/token"),
        ("auth_uri", "https://evil.example.invalid/auth"),
        ("redirect_uris", [ORIGIN + "/wrong"]),
        ("client_id", "not-a-google-client"),
        ("client_secret", ""),
    ],
)
def test_client_endpoints_and_exact_redirect_are_pinned(
    key: str, value: object
) -> None:
    document = client_document()
    document["web"][key] = value
    with pytest.raises(oauth.GoogleOAuthError):
        oauth.validate_web_client(document, REDIRECT)


def test_installed_client_and_callback_query_are_rejected() -> None:
    with pytest.raises(oauth.GoogleOAuthError, match="Web application"):
        oauth.validate_web_client({"installed": client_document()["web"]}, REDIRECT)
    with pytest.raises(oauth.GoogleOAuthError, match="callback"):
        oauth.validate_web_client(client_document(), REDIRECT + "?redirect=evil")


@pytest.mark.parametrize(
    "scopes",
    [
        ["https://www.googleapis.com/auth/calendar.readonly"],
        list(oauth.IDENTITY_SCOPES),
        [*SCOPES, "https://www.googleapis.com/auth/drive"],
        [*SCOPES, "profile"],
    ],
)
def test_unsupported_permissions_rejected_before_network(
    setup: tuple[Any, ...], scopes: list[str]
) -> None:
    transport, store, flow, _ = setup
    with pytest.raises(oauth.GoogleOAuthError):
        transport.start(state=STATE, verifier=VERIFIER, scopes=scopes)
    assert flow.authorize_calls == []
    assert store.puts == []


@pytest.mark.parametrize(
    ("state", "verifier"),
    [
        ("short", VERIFIER),
        (STATE, "short"),
        (STATE, "a" * 129),
        (STATE, "/" * 64),
        ("state with spaces" * 4, VERIFIER),
    ],
)
def test_invalid_state_or_pkce_rejected_before_authorize(
    setup: tuple[Any, ...], state: str, verifier: str
) -> None:
    transport, store, flow, _ = setup
    with pytest.raises(oauth.GoogleOAuthError):
        transport.start(state=state, verifier=verifier, scopes=SCOPES)
    assert flow.authorize_calls == []
    assert store.puts == []


def test_successful_store_cannot_substitute_another_instance(
    setup: tuple[Any, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    transport, store, _, _ = setup
    monkeypatch.setattr(store, "put", lambda *_args: "keychain://other/grant")
    with pytest.raises(oauth.GoogleOAuthError, match="another instance"):
        exchange(transport)
