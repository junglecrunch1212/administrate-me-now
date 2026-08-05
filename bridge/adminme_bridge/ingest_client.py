"""HTTP client that ships envelopes to the central :3330 API namespace.

Per memo 04 §2.2: bridge → central over Tailscale; ``Tailscale-User-Login``
header is the identity binding. The central router enforces F-3
``owner_scope`` stamping; this client merely forwards envelopes.

Retry policy: 2xx return; 4xx no-retry; 429 honors ``Retry-After`` once;
5xx exponential backoff (1s/2s/4s, cap 60s, max ``MAX_ATTEMPTS``)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx


INITIAL_BACKOFF_S = 1.0
MAX_BACKOFF_S = 60.0
MAX_ATTEMPTS = 6
BATCH_CHUNK_SIZE = 100


@dataclass(frozen=True)
class SubmitResult:
    ok: bool
    event_id: Any
    status_code: int
    error: str | None = None


@dataclass(frozen=True)
class SubmitBatchResult:
    ok: bool
    event_ids: list[str]
    chunks_sent: int
    error: str | None = None
    error_chunk_offset: int | None = None


class IngestClient:
    """Async HTTP client to central bridge ingest. One per daemon process."""

    def __init__(
        self,
        *,
        central_endpoint_url: str,
        tailscale_identity_header: str,
        client: httpx.AsyncClient | None = None,
        sleep: Any = asyncio.sleep,
    ) -> None:
        self._endpoint = central_endpoint_url.rstrip("/")
        self._identity = tailscale_identity_header
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        self._sleep = sleep

    async def aclose(self) -> None:
        await self._client.aclose()

    async def submit(self, envelope: dict[str, Any]) -> SubmitResult:
        return await self._post_with_retry(
            url=f"{self._endpoint}/api/bridge/ingest",
            payload=envelope,
            mode="single",
        )

    async def submit_batch(self, envelopes: list[dict[str, Any]]) -> SubmitBatchResult:
        all_ids: list[str] = []
        chunks_sent = 0
        for chunk_offset, start in enumerate(
            range(0, len(envelopes), BATCH_CHUNK_SIZE)
        ):
            chunk = envelopes[start : start + BATCH_CHUNK_SIZE]
            result = await self._post_with_retry(
                url=f"{self._endpoint}/api/bridge/ingest/batch",
                payload={"envelopes": chunk},
                mode="batch",
            )
            chunks_sent += 1
            if not result.ok:
                return SubmitBatchResult(
                    ok=False,
                    event_ids=all_ids,
                    chunks_sent=chunks_sent,
                    error=result.error,
                    error_chunk_offset=chunk_offset,
                )
            all_ids.extend(result.event_id or [])
        return SubmitBatchResult(ok=True, event_ids=all_ids, chunks_sent=chunks_sent)

    async def fetch_commands(self, providers: list[str]) -> dict[str, Any]:
        response = await self._client.get(
            f"{self._endpoint}/api/bridge/commands",
            params={"providers": ",".join(sorted(providers))},
            headers={"Tailscale-User-Login": self._identity},
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or not isinstance(body.get("commands"), list):
            raise RuntimeError("central command response was malformed")
        return body

    async def _post_with_retry(
        self, *, url: str, payload: Any, mode: str
    ) -> SubmitResult:
        backoff = INITIAL_BACKOFF_S
        last_error: str | None = None
        last_status = 0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = await self._client.post(
                    url, json=payload, headers={"Tailscale-User-Login": self._identity}
                )
            except httpx.HTTPError as exc:
                last_error = f"transport_error: {type(exc).__name__}: {exc}"
                last_status = 0
                if attempt < MAX_ATTEMPTS:
                    await self._sleep(backoff)
                    backoff = min(backoff * 2, MAX_BACKOFF_S)
                continue
            last_status = resp.status_code
            if 200 <= resp.status_code < 300:
                data = resp.json()
                if mode == "batch":
                    return SubmitResult(
                        True, data.get("event_ids", []), resp.status_code
                    )
                return SubmitResult(True, data.get("event_id"), resp.status_code)
            if 400 <= resp.status_code < 500 and resp.status_code != 429:
                return SubmitResult(
                    False, None, resp.status_code, f"client_error: {resp.text[:200]}"
                )
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", backoff))
                last_error = f"rate_limited: {resp.text[:200]}"
                if attempt < MAX_ATTEMPTS:
                    await self._sleep(retry_after)
                continue
            last_error = f"server_error: {resp.text[:200]}"
            if attempt < MAX_ATTEMPTS:
                await self._sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_S)
        return SubmitResult(
            False, None, last_status, last_error or "max_attempts_exceeded"
        )


__all__ = [
    "BATCH_CHUNK_SIZE",
    "INITIAL_BACKOFF_S",
    "IngestClient",
    "MAX_ATTEMPTS",
    "MAX_BACKOFF_S",
    "SubmitBatchResult",
    "SubmitResult",
]
