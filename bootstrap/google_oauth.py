"""Enroll personal Google adapters with installed-user OAuth credentials."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable, Sequence

from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore[import-untyped]

from bootstrap.contracts import DeploymentPaths
from bootstrap.google_oauth_contract import (
    GOOGLE_READONLY_ADAPTER_SCOPES,
    credential_member_digest,
    member_google_secret_name,
)
from bootstrap.secrets import secret_store


def _assert_exact_grant(credentials: object, requested_scopes: Sequence[str]) -> None:
    requested = set(requested_scopes)
    credential_scopes = set(getattr(credentials, "scopes", None) or ())
    granted_scopes = set(getattr(credentials, "granted_scopes", None) or ())
    if credential_scopes != requested or (
        granted_scopes and granted_scopes != requested
    ):
        raise RuntimeError(
            "Google OAuth grant does not exactly match the requested read-only scopes"
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


def enroll(
    client_file: Path,
    *,
    member_id: str,
    expected_account_id: str,
    account_resolver: Callable[[object], str] = _resolve_google_account_id,
) -> str:
    """Run exact read-only consent and retain refresh credentials in Keychain."""
    paths = DeploymentPaths.default()
    paths.run_dir.mkdir(parents=True, exist_ok=True)
    authorization_url_file = paths.run_dir / "google-oauth-authorization-url"
    scopes = list(GOOGLE_READONLY_ADAPTER_SCOPES)
    flow = InstalledAppFlow.from_client_secrets_file(str(client_file), scopes=scopes)
    token_uri = flow.client_config.get("token_uri")
    if not isinstance(token_uri, str) or not token_uri:
        raise RuntimeError("Google OAuth client configuration has no token URI")
    original_authorization_url = flow.authorization_url

    def authorization_url(**kwargs: object) -> tuple[str, str]:
        url, state = original_authorization_url(**kwargs)
        authorization_url_file.write_text(url, encoding="utf-8")
        authorization_url_file.chmod(0o600)
        return url, state

    flow.authorization_url = authorization_url  # type: ignore[method-assign]
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
        )
    finally:
        authorization_url_file.unlink(missing_ok=True)
    if not credentials.refresh_token:
        raise RuntimeError("Google did not return a refresh token")
    _assert_exact_grant(credentials, scopes)
    expected_account = expected_account_id.strip().casefold()
    if not expected_account:
        raise ValueError("expected_account_id must be a non-empty string")
    resolved_account = account_resolver(credentials)
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
        "owner_member_digest": credential_member_digest(member_id),
        "provider_account_id": actual_account,
        "refresh_token": credentials.refresh_token,
        "token_uri": token_uri,
        "scopes": sorted(scopes),
    }
    return secret_store(paths).put(
        member_google_secret_name(member_id),
        json.dumps(document, sort_keys=True).encode("utf-8"),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("client_file", type=Path)
    parser.add_argument(
        "--member-id",
        required=True,
        help="opaque AdministrateMe member id from the local member mapping",
    )
    args = parser.parse_args(argv)
    expected_account_id = input("Expected Google account (local input only): ")
    reference = enroll(
        args.client_file,
        member_id=args.member_id,
        expected_account_id=expected_account_id,
    )
    print(f"Google adapter credentials stored at {reference}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
