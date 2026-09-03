"""Pinned, loopback-only BlueBubbles Server read transport.

The selected compatibility target is BlueBubbles Server 1.9.9 at the exact
reviewed source commit below.  This module deliberately exposes only three
read operations.  BlueBubbles' send/reaction/chat mutation routes are not
represented, so the bridge adapter cannot call them accidentally.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from dataclasses import dataclass
from typing import Any, Mapping, Protocol
from urllib.parse import unquote, urlsplit

import httpx


SELECTED_TRANSPORT_VERSION = "1.9.9"
SELECTED_TRANSPORT_COMMIT = "ba31cd1cf6c03e154c18ffa2cd6da47a934c3be1"
SELECTED_ARM64_DMG_SHA256 = (
    "fafd650c883f52e7494a6625e45249f2144d197378a4d57143ccf6198bb2e862"
)
SELECTED_ARM64_DMG_SIZE = 300_676_880
SELECTED_RELEASE_URL = (
    "https://github.com/BlueBubblesApp/bluebubbles-server/releases/tag/v1.9.9"
)
_MAX_RESPONSE_BYTES = 10 * 1024 * 1024


class _RedactHttpxQueryFilter(logging.Filter):
    """Keep BlueBubbles query credentials out of httpx request logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            redacted: list[Any] = []
            for argument in record.args:
                if isinstance(argument, httpx.URL) and argument.query:
                    argument = argument.copy_with(query=b"credential=%3Credacted%3E")
                redacted.append(argument)
            record.args = tuple(redacted)
        return True


_httpx_logger = logging.getLogger("httpx")
if not any(isinstance(item, _RedactHttpxQueryFilter) for item in _httpx_logger.filters):
    _httpx_logger.addFilter(_RedactHttpxQueryFilter())


class BlueBubblesTransportError(RuntimeError):
    """Content-free transport failure safe to surface in adapter status."""


class BlueBubblesAuthenticationError(BlueBubblesTransportError):
    """Local credential resolution or BlueBubbles authentication failed."""


class CredentialResolver(Protocol):
    """Resolve one secret locally without persisting it in bridge config."""

    async def resolve(self, reference: str) -> str: ...


class KeychainCredentialResolver:
    """Read ``keychain://service/account`` from the member Mac Keychain."""

    async def resolve(self, reference: str) -> str:
        parsed = urlsplit(reference)
        service = unquote(parsed.netloc)
        account = unquote(parsed.path.lstrip("/"))
        if (
            parsed.scheme != "keychain"
            or not service
            or not account
            or "/" in account
            or any(ord(character) < 32 for character in service + account)
        ):
            raise BlueBubblesAuthenticationError(
                "BlueBubbles credential reference is invalid"
            )

        def _read() -> str:
            result = subprocess.run(  # noqa: S603 - fixed executable, no shell
                [
                    "/usr/bin/security",
                    "find-generic-password",
                    "-w",
                    "-s",
                    service,
                    "-a",
                    account,
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            secret = result.stdout.rstrip("\r\n")
            if result.returncode != 0 or not secret:
                raise BlueBubblesAuthenticationError(
                    "BlueBubbles credential is unavailable"
                )
            return secret

        return await asyncio.to_thread(_read)


@dataclass(frozen=True)
class MessagePage:
    messages: tuple[Mapping[str, Any], ...]
    offset: int
    limit: int
    total: int


class BlueBubblesReadTransport(Protocol):
    """Read operations used by :class:`BlueBubblesMessagesAdapter` only."""

    async def server_info(self) -> Mapping[str, Any]: ...

    async def query_messages(
        self,
        *,
        sort: str,
        limit: int,
        offset: int = 0,
        after_ms: int | None = None,
        before_ms: int | None = None,
    ) -> MessagePage: ...

    async def chat(self, native_chat_id: str) -> Mapping[str, Any]: ...

    async def aclose(self) -> None: ...


class BlueBubblesRestTransport:
    """BlueBubbles v1 REST reads restricted to this Mac's loopback interface."""

    def __init__(
        self,
        *,
        base_url: str,
        credential_ref: str,
        credential_resolver: CredentialResolver | None = None,
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        parsed = urlsplit(base_url)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError(
                "BlueBubbles base URL must be an exact loopback HTTP origin"
            ) from exc
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or port is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError(
                "BlueBubbles base URL must be an exact loopback HTTP origin"
            )
        if not credential_ref.startswith("keychain://"):
            raise ValueError("BlueBubbles password must use a local Keychain reference")
        if timeout_s <= 0 or timeout_s > 60:
            raise ValueError("BlueBubbles timeout_s must be in (0, 60]")
        self._base_url = base_url.rstrip("/") + "/api/v1"
        self._credential_ref = credential_ref
        self._resolver = credential_resolver or KeychainCredentialResolver()
        self._client = client or httpx.AsyncClient(timeout=timeout_s, trust_env=False)
        self._owns_client = client is None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        query: Mapping[str, str] | None = None,
    ) -> Mapping[str, Any]:
        # BlueBubbles 1.9.9 authenticates protected REST routes using a query
        # password.  Never call raise_for_status(): its exception renders the
        # complete URL, which would disclose that query secret.
        secret = await self._resolver.resolve(self._credential_ref)
        try:
            response = await self._client.request(
                method,
                self._base_url + path,
                params={**dict(query or {}), "password": secret},
                json=dict(body) if body is not None else None,
            )
        except httpx.HTTPError:
            raise BlueBubblesTransportError(
                "BlueBubbles loopback request failed"
            ) from None
        finally:
            secret = ""  # Drop the only method-local reference promptly.
        if response.status_code in {401, 403}:
            raise BlueBubblesAuthenticationError(
                "BlueBubbles rejected the local credential"
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise BlueBubblesTransportError(
                f"BlueBubbles returned HTTP {response.status_code}"
            )
        if len(response.content) > _MAX_RESPONSE_BYTES:
            raise BlueBubblesTransportError(
                "BlueBubbles response exceeded safety limit"
            )
        try:
            decoded = response.json()
        except ValueError as exc:
            raise BlueBubblesTransportError(
                "BlueBubbles returned malformed JSON"
            ) from exc
        if not isinstance(decoded, dict):
            raise BlueBubblesTransportError(
                "BlueBubbles response was not a JSON object"
            )
        return decoded

    @staticmethod
    def _data(response: Mapping[str, Any]) -> Any:
        if "data" not in response:
            raise BlueBubblesTransportError("BlueBubbles response omitted data")
        return response["data"]

    async def server_info(self) -> Mapping[str, Any]:
        data = self._data(await self._request("GET", "/server/info"))
        if not isinstance(data, dict):
            raise BlueBubblesTransportError("BlueBubbles server info was malformed")
        return data

    async def query_messages(
        self,
        *,
        sort: str,
        limit: int,
        offset: int = 0,
        after_ms: int | None = None,
        before_ms: int | None = None,
    ) -> MessagePage:
        if sort not in {"ASC", "DESC"}:
            raise ValueError("BlueBubbles query sort must be ASC or DESC")
        if limit < 1 or limit > 1000 or offset < 0:
            raise ValueError("BlueBubbles query bounds are invalid")
        body: dict[str, Any] = {
            "with": ["chats", "attachments"],
            "convertAttachments": False,
            "sort": sort,
            "offset": offset,
            "limit": limit,
        }
        if after_ms is not None:
            body["after"] = max(0, int(after_ms))
        if before_ms is not None:
            body["before"] = max(1, int(before_ms))
        response = await self._request("POST", "/message/query", body=body)
        data = self._data(response)
        metadata = response.get("metadata")
        if not isinstance(data, list) or not isinstance(metadata, dict):
            raise BlueBubblesTransportError("BlueBubbles message page was malformed")
        if any(not isinstance(message, dict) for message in data):
            raise BlueBubblesTransportError(
                "BlueBubbles message page contained a malformed record"
            )
        try:
            returned_offset = int(metadata["offset"])
            returned_limit = int(metadata["limit"])
            total = int(metadata["total"])
            count = int(metadata["count"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BlueBubblesTransportError(
                "BlueBubbles page metadata was malformed"
            ) from exc
        if (
            returned_offset != offset
            or returned_limit != limit
            or count != len(data)
            or total < count
        ):
            raise BlueBubblesTransportError(
                "BlueBubbles page metadata contradicted the response"
            )
        return MessagePage(tuple(data), returned_offset, returned_limit, total)

    async def chat(self, native_chat_id: str) -> Mapping[str, Any]:
        # httpx quotes the GUID as a path segment. The adapter independently
        # validates the returned chat GUID before trusting participants.
        from urllib.parse import quote

        data = self._data(
            await self._request(
                "GET",
                f"/chat/{quote(native_chat_id, safe='')}",
                query={"with": "participants"},
            )
        )
        if not isinstance(data, dict):
            raise BlueBubblesTransportError("BlueBubbles chat response was malformed")
        return data

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


__all__ = [
    "BlueBubblesAuthenticationError",
    "BlueBubblesReadTransport",
    "BlueBubblesRestTransport",
    "BlueBubblesTransportError",
    "CredentialResolver",
    "KeychainCredentialResolver",
    "MessagePage",
    "SELECTED_ARM64_DMG_SHA256",
    "SELECTED_ARM64_DMG_SIZE",
    "SELECTED_RELEASE_URL",
    "SELECTED_TRANSPORT_COMMIT",
    "SELECTED_TRANSPORT_VERSION",
]
