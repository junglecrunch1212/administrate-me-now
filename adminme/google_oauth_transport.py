"""Exact-scope, member-owned Google web OAuth transport.

The caller owns authenticated request state and one-time callback settlement.
This module never persists authorization codes, access tokens or PKCE verifiers.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Protocol, Sequence
from urllib.parse import urlsplit

from google_auth_oauthlib.flow import Flow  # type: ignore[import-untyped]

from adminme.adapters.credentials import credential_member_digest

CALLBACK_PATH = "/api/member/v1/settings/google-connections/callback"
AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
USERINFO_URI = "https://openidconnect.googleapis.com/v1/userinfo"
IDENTITY_SCOPES = ("openid", "https://www.googleapis.com/auth/userinfo.email")
SERVICE_SCOPES = frozenset(
    f"https://www.googleapis.com/auth/{suffix}"
    for suffix in (
        "gmail.readonly",
        "gmail.modify",
        "gmail.send",
        "calendar.readonly",
        "calendar",
        "tasks.readonly",
        "tasks",
    )
)
_SCOPE_ALIASES = {"email": IDENTITY_SCOPES[1]}


class OAuthSecretStore(Protocol):
    def get(self, reference: str) -> bytes: ...

    def put(self, name: str, value: bytes) -> str: ...

    def has(self, reference: str) -> bool: ...


class GoogleOAuthError(RuntimeError):
    """A bounded, secret-free connection failure suitable for a Settings result."""


@dataclass(frozen=True)
class GoogleOAuthGrant:
    credential_ref: str
    account_id: str
    subject: str
    scopes: tuple[str, ...]


def dashboard_redirect_uri(origin: str) -> str:
    """Bind authorization to an operator-configured canonical Tailscale origin."""
    try:
        parsed = urlsplit(origin)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname is not None
            and re.fullmatch(
                r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.[a-z0-9-]+\.ts\.net",
                parsed.hostname,
            )
            and parsed.netloc == parsed.hostname
            and parsed.path in ("", "/")
            and not parsed.query
            and not parsed.fragment
            and origin == origin.strip()
        )
    except ValueError:
        raise GoogleOAuthError(
            "Google OAuth requires the canonical Tailscale HTTPS origin"
        ) from None
    if not valid:
        raise GoogleOAuthError(
            "Google OAuth requires the canonical Tailscale HTTPS origin"
        )
    return f"https://{parsed.hostname}{CALLBACK_PATH}"


def validate_web_client(document: object, redirect_uri: str) -> dict[str, Any]:
    """Return only reviewed Google web-client fields; reject endpoint substitution."""
    try:
        parsed = urlsplit(redirect_uri)
    except ValueError:
        raise GoogleOAuthError("Google OAuth callback is invalid") from None
    if redirect_uri != dashboard_redirect_uri(f"{parsed.scheme}://{parsed.netloc}"):
        raise GoogleOAuthError(
            "Google OAuth callback does not match the Settings route"
        )
    if not isinstance(document, dict) or set(document) != {"web"}:
        raise GoogleOAuthError("Google OAuth needs a Web application client")
    web = document["web"]
    if not isinstance(web, dict):
        raise GoogleOAuthError("Google OAuth Web application client is invalid")
    client_id, client_secret = web.get("client_id"), web.get("client_secret")
    redirects = web.get("redirect_uris")
    if (
        not isinstance(client_id, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]+\.apps\.googleusercontent\.com", client_id)
        or len(client_id) > 512
        or not isinstance(client_secret, str)
        or not client_secret
        or len(client_secret) > 4096
        or web.get("auth_uri")
        not in {AUTH_URI, "https://accounts.google.com/o/oauth2/auth"}
        or web.get("token_uri") != TOKEN_URI
        or not isinstance(redirects, list)
        or redirect_uri not in redirects
        or any(not isinstance(value, str) for value in redirects)
    ):
        raise GoogleOAuthError(
            "Google OAuth client must register the exact callback and Google endpoints"
        )
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": AUTH_URI,
            "token_uri": TOKEN_URI,
            "redirect_uris": [redirect_uri],
        }
    }


def _scope_set(scopes: object) -> set[str]:
    if isinstance(scopes, str):
        scopes = scopes.split()
    if not isinstance(scopes, (list, tuple, set, frozenset)):
        raise GoogleOAuthError("Google did not confirm the requested permissions")
    result: set[str] = set()
    for scope in scopes:
        if not isinstance(scope, str) or not scope:
            raise GoogleOAuthError("Google did not confirm the requested permissions")
        result.add(_SCOPE_ALIASES.get(scope, scope))
    return result


def requested_scopes(scopes: Sequence[str]) -> tuple[str, ...]:
    result = _scope_set(scopes)
    if (
        not set(IDENTITY_SCOPES) <= result
        or not result & SERVICE_SCOPES
        or not result <= SERVICE_SCOPES | set(IDENTITY_SCOPES)
    ):
        raise GoogleOAuthError(
            "Google OAuth permissions are outside the supported services"
        )
    return tuple(sorted(result))


def _verifier(verifier: str) -> str:
    if not isinstance(verifier, str) or not re.fullmatch(
        r"[A-Za-z0-9._~-]{43,128}", verifier
    ):
        raise GoogleOAuthError("Google OAuth verifier is invalid")
    return verifier


class GoogleWebOAuth:
    def __init__(
        self, secret_store: OAuthSecretStore, client_ref: str, redirect_uri: str
    ) -> None:
        self.secret_store = secret_store
        self.client_ref = client_ref
        self.redirect_uri = redirect_uri

    def _client(self) -> dict[str, Any]:
        try:
            raw = self.secret_store.get(self.client_ref)
            if len(raw) > 65536:
                raise ValueError("oversized client")
            document = json.loads(raw)
        except Exception:
            raise GoogleOAuthError("Google OAuth client is unavailable") from None
        return validate_web_client(document, self.redirect_uri)

    def _flow(self, verifier: str, scopes: Sequence[str]) -> Any:
        client = self._client()
        scope_list = list(requested_scopes(scopes))
        try:
            return Flow.from_client_config(
                client,
                scopes=scope_list,
                redirect_uri=self.redirect_uri,
                code_verifier=_verifier(verifier),
                autogenerate_code_verifier=False,
            )
        except Exception:
            raise GoogleOAuthError(
                "Google OAuth authorization could not be prepared"
            ) from None

    def start(self, *, state: str, verifier: str, scopes: list[str]) -> str:
        if not isinstance(state, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{32,256}", state
        ):
            raise GoogleOAuthError("Google OAuth request state is invalid")
        flow = self._flow(verifier, scopes)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
            .decode("ascii")
            .rstrip("=")
        )
        try:
            url, returned_state = flow.authorization_url(
                state=state,
                access_type="offline",
                prompt="consent select_account",
                include_granted_scopes="false",
                code_challenge=challenge,
                code_challenge_method="S256",
            )
        except Exception:
            raise GoogleOAuthError(
                "Google OAuth authorization could not be prepared"
            ) from None
        target = urlsplit(url)
        if (
            returned_state != state
            or f"{target.scheme}://{target.netloc}{target.path}" != AUTH_URI
        ):
            raise GoogleOAuthError("Google OAuth authorization target is invalid")
        return str(url)

    def exchange(
        self,
        *,
        code: str,
        verifier: str,
        scopes: list[str],
        expected_account_id: str,
        member_id: str,
        secret_name: str,
    ) -> GoogleOAuthGrant:
        """Exchange once; store only a verified account's exact, fresh member grant."""
        expected = expected_account_id.strip().casefold()
        if (
            not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", expected)
            or len(expected) > 320
            or not isinstance(code, str)
            or not code
            or len(code) > 8192
            or any(character in code for character in "\r\n")
            or not re.fullmatch(r"google-oauth-member-[a-z0-9-]{24,160}", secret_name)
        ):
            raise GoogleOAuthError("Google OAuth exchange coordinates are invalid")
        owner_digest = credential_member_digest(member_id)
        if not secret_name.startswith(f"google-oauth-member-{owner_digest}-"):
            raise GoogleOAuthError(
                "Google OAuth credential destination is not member-owned"
            )
        wanted = requested_scopes(scopes)
        flow = self._flow(verifier, scopes)
        try:
            flow.fetch_token(code=code, timeout=20, allow_redirects=False)
            credentials = flow.credentials
        except Exception:
            raise GoogleOAuthError(
                "Google authorization could not be exchanged; begin a new connection"
            ) from None
        if not credentials.refresh_token:
            raise GoogleOAuthError(
                "Google did not return offline access; reconnect with consent"
            )
        actual = _scope_set(getattr(credentials, "granted_scopes", None))
        if actual != set(wanted) or _scope_set(credentials.scopes) != set(wanted):
            raise GoogleOAuthError(
                "Google did not grant exactly the selected permissions"
            )
        try:
            with flow.authorized_session() as session:
                response = session.get(USERINFO_URI, timeout=20, allow_redirects=False)
                if response.status_code != 200 or len(response.content) > 16384:
                    raise ValueError("invalid identity response")
                profile = response.json()
        except Exception:
            raise GoogleOAuthError(
                "Google account identity could not be verified"
            ) from None
        if not isinstance(profile, dict) or profile.get("email_verified") is not True:
            raise GoogleOAuthError("Google did not return a verified account identity")
        email, subject = profile.get("email"), profile.get("sub")
        if (
            not isinstance(email, str)
            or email.strip().casefold() != expected
            or not isinstance(subject, str)
            or not subject
            or len(subject) > 255
            or any(character.isspace() for character in subject)
        ):
            raise GoogleOAuthError(
                "Authorized Google account does not match the selected account"
            )
        document = {
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "provider_account_id": expected,
            "provider_subject": subject,
            "owner_member_digest": owner_digest,
            "refresh_token": credentials.refresh_token,
            "token_uri": TOKEN_URI,
            "scopes": list(wanted),
        }
        try:
            # Each settled request gets its own name; never replace another grant.
            prefix = self.client_ref.rsplit("/", 1)[0]
            if self.secret_store.has(f"{prefix}/{secret_name}"):
                raise GoogleOAuthError(
                    "Google OAuth credential destination is already in use"
                )
            reference = self.secret_store.put(
                secret_name, json.dumps(document, sort_keys=True).encode("utf-8")
            )
            if reference != f"{prefix}/{secret_name}":
                raise GoogleOAuthError(
                    "Google OAuth credential destination belongs to another instance"
                )
        except GoogleOAuthError:
            raise
        except Exception:
            raise GoogleOAuthError(
                "Google credentials could not be stored securely"
            ) from None
        return GoogleOAuthGrant(reference, expected, subject, wanted)
