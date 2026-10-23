"""Enroll personal Google adapters with installed-user OAuth credentials."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable, Sequence

from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore[import-untyped]

from bootstrap.contracts import DeploymentPaths, atomic_write
from bootstrap.google_oauth_contract import (
    GOOGLE_READONLY_ADAPTER_SCOPES,
    credential_member_digest,
    member_google_secret_name,
)
from bootstrap.secrets import secret_store
from adminme.adapters.credentials import credential_service_digest
from adminme.adapters.cos_google_workspace import PURPOSES, grant_scopes
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID

COS_GOOGLE_SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
)


def _assert_exact_grant(credentials: object, requested_scopes: Sequence[str]) -> None:
    requested = set(requested_scopes)
    credential_scopes = set(getattr(credentials, "scopes", None) or ())
    granted_scopes = set(getattr(credentials, "granted_scopes", None) or ())
    if credential_scopes != requested or (
        granted_scopes and granted_scopes != requested
    ):
        raise RuntimeError(
            "Google OAuth grant does not exactly match the requested scopes"
        )


def _resolve_google_account_id(credentials: object) -> str:
    """Resolve the consented account through the granted Gmail read scope."""
    from googleapiclient.discovery import build

    profile = (
        build("gmail", "v1", credentials=credentials, cache_discovery=False)
        .users()
        .getProfile(userId="me")
        .execute()
    )
    account_id = profile.get("emailAddress") if isinstance(profile, dict) else None
    if not isinstance(account_id, str) or not account_id.strip():
        raise RuntimeError("Google did not return the authorized account identity")
    return account_id.strip().casefold()


def _resolve_workspace_account_id(credentials: object) -> str:
    """Prove the OAuth account without adding Gmail access to workspace grants."""
    from googleapiclient.discovery import build

    profile = (
        build("oauth2", "v2", credentials=credentials, cache_discovery=False)
        .userinfo()
        .get()
        .execute()
    )
    if not isinstance(profile, dict) or profile.get("verified_email") is not True:
        raise RuntimeError("Google did not return a verified account identity")
    email = profile.get("email")
    if not isinstance(email, str) or not email.strip():
        raise RuntimeError("Google did not return the authorized account identity")
    return email.strip().casefold()


def enroll(
    client_file: Path,
    *,
    member_id: str | None = None,
    service_principal_id: str | None = None,
    expected_account_id: str,
    purpose: str = "gmail-read-send",
    account_resolver: Callable[[object], str] | None = None,
) -> str:
    """Enroll one exact grant; workspace purposes never replace mail/member keys."""
    if bool(member_id) == bool(service_principal_id):
        raise ValueError("exactly one credential owner must be selected")
    if service_principal_id and service_principal_id != COS_SERVICE_PRINCIPAL_ID:
        raise ValueError("unsupported service credential owner")
    if purpose != "gmail-read-send" and (
        not service_principal_id or purpose not in PURPOSES
    ):
        raise ValueError("named workspace grants require the CoS service owner")
    expected_account = expected_account_id.strip().casefold()
    if not expected_account:
        raise ValueError("expected_account_id must be a non-empty string")
    paths = DeploymentPaths.default()
    paths.run_dir.mkdir(parents=True, exist_ok=True)
    authorization_url_file = paths.run_dir / "google-oauth-authorization-url"
    scopes = list(
        grant_scopes(purpose)
        if purpose != "gmail-read-send"
        else COS_GOOGLE_SCOPES
        if service_principal_id
        else GOOGLE_READONLY_ADAPTER_SCOPES
    )
    flow = InstalledAppFlow.from_client_secrets_file(str(client_file), scopes=scopes)
    token_uri = flow.client_config.get("token_uri")
    if not isinstance(token_uri, str) or not token_uri:
        raise RuntimeError("Google OAuth client configuration has no token URI")
    original_authorization_url = flow.authorization_url

    def authorization_url(**kwargs: object) -> tuple[str, str]:
        url, state = original_authorization_url(**kwargs)
        atomic_write(authorization_url_file, url.encode("utf-8"), mode=0o600)
        return url, state

    flow.authorization_url = authorization_url  # type: ignore[method-assign]
    authorization_options: dict[str, Any] = (
        {"include_granted_scopes": "false"} if purpose != "gmail-read-send" else {}
    )
    try:
        credentials = flow.run_local_server(
            host="127.0.0.1",
            port=0,
            authorization_prompt_message="",
            success_message=(
                "AdministrateMe authorization complete. You may close this tab."
            ),
            open_browser=False,
            access_type="offline",
            prompt="consent",
            **authorization_options,
        )
    finally:
        authorization_url_file.unlink(missing_ok=True)
    if not credentials.refresh_token:
        raise RuntimeError("Google did not return a refresh token")
    _assert_exact_grant(credentials, scopes)
    resolver = account_resolver or (
        _resolve_workspace_account_id
        if purpose != "gmail-read-send"
        else _resolve_google_account_id
    )
    resolved_account = resolver(credentials)
    if not isinstance(resolved_account, str) or not resolved_account.strip():
        raise RuntimeError("Google did not return the authorized account identity")
    actual_account = resolved_account.strip().casefold()
    if actual_account != expected_account:
        raise RuntimeError(
            "authorized Google account does not match the reviewed account"
        )
    document = {
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "provider_account_id": actual_account,
        "refresh_token": credentials.refresh_token,
        "token_uri": token_uri,
        "scopes": sorted(scopes),
    }
    if service_principal_id:
        digest = credential_service_digest(service_principal_id)
        document.update(
            {
                "owner_kind": "service_principal",
                "owner_service_principal_digest": digest,
            }
        )
        secret_name = f"google-oauth-service-{digest}"
        if purpose != "gmail-read-send":
            document["grant_purpose"] = purpose
            secret_name += f"-{purpose}"
    else:
        assert member_id is not None
        document["owner_member_digest"] = credential_member_digest(member_id)
        secret_name = member_google_secret_name(member_id)
    reference = secret_store(paths).put(
        secret_name,
        json.dumps(document, sort_keys=True).encode("utf-8"),
    )
    if service_principal_id:
        from datetime import datetime, timezone
        import hashlib

        digest = credential_service_digest(service_principal_id)
        atomic_write(
            paths.run_dir
            / (
                "cos-google-enrollment.json"
                if purpose == "gmail-read-send"
                else f"cos-google-{purpose}-enrollment.json"
            ),
            json.dumps(
                {
                    "contract": "cos-google-enrollment-v1",
                    "grant_purpose": purpose,
                    "credential_reference": reference,
                    "owner_service_principal_digest": digest,
                    "provider_account_sha256": hashlib.sha256(
                        actual_account.encode()
                    ).hexdigest(),
                    "scopes": sorted(scopes),
                    "profile_verified": True,
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "provider_effect_count": 0,
                },
                sort_keys=True,
            ).encode(),
            mode=0o600,
        )
    return reference


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("client_file", type=Path)
    owner = parser.add_mutually_exclusive_group(required=True)
    owner.add_argument(
        "--member-id",
        help="opaque AdministrateMe member id from the local member mapping",
    )
    owner.add_argument(
        "--cos-service",
        action="store_true",
        help="enroll a separately named grant for the CoS actual Google account",
    )
    parser.add_argument(
        "--purpose",
        choices=["gmail-read-send", *PURPOSES],
        default="gmail-read-send",
        help="separate CoS purpose; defaults to the existing Gmail read/send grant",
    )
    args = parser.parse_args(argv)
    if not args.cos_service and args.purpose != "gmail-read-send":
        parser.error("--purpose requires --cos-service")
    expected_account_id = input("Expected Google account (local input only): ")
    reference = enroll(
        args.client_file,
        member_id=args.member_id,
        service_principal_id=COS_SERVICE_PRINCIPAL_ID if args.cos_service else None,
        expected_account_id=expected_account_id,
        purpose=args.purpose,
    )
    print(f"Google adapter credentials stored at {reference}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
