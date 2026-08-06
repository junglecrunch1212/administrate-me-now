"""Enroll personal Google adapters with installed-user OAuth credentials."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore[import-untyped]

from bootstrap.contracts import DeploymentPaths
from bootstrap.secrets import secret_store

GOOGLE_ADAPTER_SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
)


def enroll(client_file: Path, *, secret_name: str) -> str:
    """Run browser consent and retain only refresh credentials in Keychain."""
    paths = DeploymentPaths.default()
    paths.run_dir.mkdir(parents=True, exist_ok=True)
    authorization_url_file = paths.run_dir / "google-oauth-authorization-url"
    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_file), scopes=list(GOOGLE_ADAPTER_SCOPES)
    )
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
            authorization_prompt_message=None,
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
    document = {
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "scopes": sorted(credentials.scopes or GOOGLE_ADAPTER_SCOPES),
    }
    return secret_store(paths).put(
        secret_name, json.dumps(document, sort_keys=True).encode("utf-8")
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("client_file", type=Path)
    parser.add_argument("--secret-name", default="google-oauth-primary")
    args = parser.parse_args(argv)
    reference = enroll(args.client_file, secret_name=args.secret_name)
    print(f"Google adapter credentials stored at {reference}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
