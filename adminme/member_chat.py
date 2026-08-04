"""Narrow OpenClaw chat relay for the authenticated member portal.

OpenClaw remains the only conversation runtime and transcript owner.  This
module implements only the loopback Gateway protocol client needed to relay a
member-visible stream.  It deliberately has no model selection, retry router,
token accounting, prompt storage, or AdministrateMe transcript persistence.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from typing import Any, Protocol


OPENCLAW_PROTOCOL_VERSION = 4
OPENCLAW_VERSION = "2026.7.2"

_DIRECTIVE_RE = re.compile(r"\[\[(?:reply_to_[^\]]+|audio_as_voice)\]\]", re.I)
_TOOL_XML_RE = re.compile(
    r"<(?:tool_call|function_call|tool_calls|function_calls)\b[^>]*>.*?"
    r"</(?:tool_call|function_call|tool_calls|function_calls)>",
    re.I | re.S,
)
_CONTROL_TOKEN_RE = re.compile(
    r"(?:<\|(?:system|assistant|user|endoftext)[^|]*\|>|"
    r"＜\|(?:system|assistant|user|endoftext)[^｜]*\|＞)",
    re.I,
)


class MemberChatError(RuntimeError):
    """A safe, typed chat transport failure."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class MemberChatChunk:
    """The complete browser-visible stream vocabulary."""

    kind: str
    text: str | None = None
    replace: bool = False
    phase: str | None = None
    code: str | None = None
    retryable: bool = False

    def public(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


class MemberChatTransport(Protocol):
    def stream(
        self,
        *,
        session_key: str,
        message: str,
        idempotency_key: str,
    ) -> AsyncIterator[MemberChatChunk]: ...

    async def history(self, *, session_key: str, limit: int) -> list[dict[str, str]]: ...

    async def abort(self, *, session_key: str) -> bool: ...


def _safe_visible_text(value: str) -> str:
    text = _DIRECTIVE_RE.sub("", value)
    text = _TOOL_XML_RE.sub("", text)
    text = _CONTROL_TOKEN_RE.sub("", text)
    stripped = text.strip()
    if stripped.lower() == "no_reply":
        return ""
    return text


def _assistant_text(message: Any) -> str:
    """Extract only rendered assistant text from a display-projected message."""

    if not isinstance(message, dict):
        return ""
    role = message.get("role")
    if role not in {None, "assistant"}:
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return _safe_visible_text(content)
    if not isinstance(content, list):
        text = message.get("text")
        return _safe_visible_text(text) if isinstance(text, str) else ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict) or item.get("type") not in {"text", "output_text"}:
            continue
        text = item.get("text")
        if isinstance(text, str):
            parts.append(text)
    return _safe_visible_text("".join(parts))


class OpenClawGatewayChatTransport:
    """Minimal protocol-v4 Gateway client for chat/history/abort only."""

    def __init__(
        self,
        *,
        url: str,
        token: str,
        request_timeout_s: float = 30.0,
    ) -> None:
        if url != "ws://127.0.0.1:18789" and url != "ws://[::1]:18789":
            raise ValueError("member chat Gateway must use the pinned loopback endpoint")
        if not token:
            raise ValueError("member chat Gateway token is required")
        self._url = url
        self._token = token
        self._request_timeout_s = request_timeout_s

    async def _connect(self) -> Any:
        try:
            from websockets.asyncio.client import connect  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - dependency gate
            raise MemberChatError("transport_unavailable", "Chat transport is unavailable") from exc
        socket: Any | None = None
        try:
            connected = await connect(
                self._url,
                open_timeout=5,
                close_timeout=2,
                max_queue=8,
                max_size=1_000_000,
                ping_interval=20,
                ping_timeout=20,
            )
            socket = connected
            raw = await asyncio.wait_for(connected.recv(), timeout=5)
            challenge = self._decode(raw)
            if challenge.get("type") != "event" or challenge.get("event") != "connect.challenge":
                raise MemberChatError("protocol_error", "Chat transport handshake failed")
            nonce = (challenge.get("payload") or {}).get("nonce")
            if not isinstance(nonce, str) or not nonce:
                raise MemberChatError("protocol_error", "Chat transport handshake failed")
            hello = await self._request(
                connected,
                "connect",
                {
                    "minProtocol": OPENCLAW_PROTOCOL_VERSION,
                    "maxProtocol": OPENCLAW_PROTOCOL_VERSION,
                    "client": {
                        "id": "gateway-client",
                        "displayName": "AdministrateMe member relay",
                        "version": "2.0.0-gate7",
                        "platform": "python",
                        "mode": "backend",
                    },
                    "caps": ["session-scoped-events"],
                    "role": "operator",
                    "scopes": ["operator.read", "operator.write"],
                    "auth": {"token": self._token},
                },
            )
            server = hello.get("server") if isinstance(hello, dict) else None
            methods = ((hello.get("features") or {}).get("methods") if isinstance(hello, dict) else None)
            if not isinstance(server, dict) or server.get("version") != OPENCLAW_VERSION:
                raise MemberChatError("incompatible", "Chat transport version is incompatible")
            required = {"chat.send", "chat.abort", "chat.history"}
            if not isinstance(methods, list) or not required.issubset(set(methods)):
                raise MemberChatError("incompatible", "Chat transport methods are incompatible")
            return connected
        except MemberChatError:
            if socket is not None:
                await socket.close()
            raise
        except (OSError, asyncio.TimeoutError) as exc:
            if socket is not None:
                await socket.close()
            raise MemberChatError(
                "transport_unavailable", "Chat is temporarily unavailable", retryable=True
            ) from exc

    @staticmethod
    def _decode(raw: Any) -> dict[str, Any]:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="strict")
        if not isinstance(raw, str):
            raise MemberChatError("protocol_error", "Chat transport returned an invalid frame")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MemberChatError("protocol_error", "Chat transport returned an invalid frame") from exc
        if not isinstance(value, dict):
            raise MemberChatError("protocol_error", "Chat transport returned an invalid frame")
        return value

    async def _request(self, socket: Any, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        await socket.send(
            json.dumps(
                {"type": "req", "id": request_id, "method": method, "params": params},
                separators=(",", ":"),
            )
        )
        while True:
            raw = await asyncio.wait_for(socket.recv(), timeout=self._request_timeout_s)
            frame = self._decode(raw)
            if frame.get("type") != "res" or frame.get("id") != request_id:
                continue
            if frame.get("ok") is not True:
                error_value = frame.get("error")
                error: dict[str, Any] = (
                    error_value if isinstance(error_value, dict) else {}
                )
                retryable = error.get("retryable") is True
                code = "rate_limited" if error.get("code") == "RATE_LIMITED" else "gateway_rejected"
                raise MemberChatError(code, "Chat request was not accepted", retryable=retryable)
            payload = frame.get("payload")
            return payload if isinstance(payload, dict) else {}

    async def stream(
        self,
        *,
        session_key: str,
        message: str,
        idempotency_key: str,
    ) -> AsyncIterator[MemberChatChunk]:
        socket = await self._connect()
        terminal = False
        run_id: str | None = None
        saw_delta = False
        try:
            request_id = str(uuid.uuid4())
            await socket.send(
                json.dumps(
                    {
                        "type": "req",
                        "id": request_id,
                        "method": "chat.send",
                        "params": {
                            "sessionKey": session_key,
                            "message": message,
                            "deliver": False,
                            "suppressCommandInterpretation": True,
                            "idempotencyKey": idempotency_key,
                        },
                    },
                    separators=(",", ":"),
                )
            )
            while True:
                raw = await asyncio.wait_for(socket.recv(), timeout=self._request_timeout_s)
                frame = self._decode(raw)
                if frame.get("type") == "res" and frame.get("id") == request_id:
                    if frame.get("ok") is not True:
                        raise MemberChatError("gateway_rejected", "Chat request was not accepted")
                    ack_value = frame.get("payload")
                    ack: dict[str, Any] = (
                        ack_value if isinstance(ack_value, dict) else {}
                    )
                    value = ack.get("runId")
                    if not isinstance(value, str) or not value:
                        raise MemberChatError("protocol_error", "Chat transport omitted its run identifier")
                    run_id = value
                    yield MemberChatChunk(kind="status", phase="starting_model")
                    continue
                if frame.get("type") != "event" or frame.get("event") != "chat":
                    continue
                payload = frame.get("payload")
                if not isinstance(payload, dict):
                    continue
                if payload.get("sessionKey") != session_key:
                    continue
                if run_id is not None and payload.get("runId") != run_id:
                    continue
                state = payload.get("state")
                if state == "status":
                    phase = payload.get("phase")
                    yield MemberChatChunk(
                        kind="status", phase=phase if isinstance(phase, str) else "preparing_context"
                    )
                elif state == "delta":
                    text = payload.get("deltaText")
                    if isinstance(text, str):
                        text = _safe_visible_text(text)
                        if text:
                            saw_delta = True
                            yield MemberChatChunk(
                                kind="delta", text=text, replace=payload.get("replace") is True
                            )
                elif state == "final":
                    if not saw_delta:
                        text = _assistant_text(payload.get("message"))
                        if text:
                            yield MemberChatChunk(kind="delta", text=text)
                    terminal = True
                    yield MemberChatChunk(kind="final")
                    return
                elif state == "aborted":
                    terminal = True
                    yield MemberChatChunk(kind="aborted", code="cancelled")
                    return
                elif state == "error":
                    terminal = True
                    kind = payload.get("errorKind")
                    safe_code = kind if kind in {"refusal", "timeout", "rate_limit", "context_length"} else "unavailable"
                    yield MemberChatChunk(
                        kind="error",
                        code=safe_code,
                        retryable=safe_code in {"timeout", "rate_limit", "unavailable"},
                    )
                    return
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError as exc:
            raise MemberChatError("timeout", "Chat response timed out", retryable=True) from exc
        finally:
            if not terminal:
                try:
                    await self._request(
                        socket,
                        "chat.abort",
                        {"sessionKey": session_key, **({"runId": run_id} if run_id else {})},
                    )
                except Exception:
                    pass
            await socket.close()

    async def history(self, *, session_key: str, limit: int) -> list[dict[str, str]]:
        socket = await self._connect()
        try:
            payload = await self._request(
                socket,
                "chat.history",
                {"sessionKey": session_key, "limit": min(max(limit, 1), 50), "maxChars": 40_000},
            )
        finally:
            await socket.close()
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return []
        visible: list[dict[str, str]] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            if role == "assistant":
                text = _assistant_text(message)
            elif role == "user":
                content = message.get("content")
                text = _safe_visible_text(content) if isinstance(content, str) else ""
            else:
                continue
            if text:
                visible.append({"role": role, "text": text})
        return visible[-limit:]

    async def abort(self, *, session_key: str) -> bool:
        socket = await self._connect()
        try:
            await self._request(socket, "chat.abort", {"sessionKey": session_key})
            return True
        finally:
            await socket.close()


class DeterministicOpenClawChatTransport:
    """Credential-free OpenClaw-boundary fixture; state never enters AdminMe."""

    def __init__(self) -> None:
        self._transcripts: dict[str, list[dict[str, str]]] = defaultdict(list)
        self._cancelled: set[str] = set()

    async def stream(
        self,
        *,
        session_key: str,
        message: str,
        idempotency_key: str,
    ) -> AsyncIterator[MemberChatChunk]:
        del idempotency_key
        self._cancelled.discard(session_key)
        self._transcripts[session_key].append({"role": "user", "text": message})
        yield MemberChatChunk(kind="status", phase="starting_model")
        await asyncio.sleep(0)
        if session_key in self._cancelled:
            yield MemberChatChunk(kind="aborted", code="cancelled")
            return
        response = "I'm here. I can help you choose the next small step."
        self._transcripts[session_key].append({"role": "assistant", "text": response})
        yield MemberChatChunk(kind="delta", text=response)
        yield MemberChatChunk(kind="final")

    async def history(self, *, session_key: str, limit: int) -> list[dict[str, str]]:
        return [dict(item) for item in self._transcripts.get(session_key, [])[-limit:]]

    async def abort(self, *, session_key: str) -> bool:
        self._cancelled.add(session_key)
        return True


class UnavailableMemberChatTransport:
    async def stream(
        self,
        *,
        session_key: str,
        message: str,
        idempotency_key: str,
    ) -> AsyncIterator[MemberChatChunk]:
        del session_key, message, idempotency_key
        raise MemberChatError("not_configured", "Chat provider is not configured")
        yield  # pragma: no cover

    async def history(self, *, session_key: str, limit: int) -> list[dict[str, str]]:
        del session_key, limit
        raise MemberChatError("not_configured", "Chat provider is not configured")

    async def abort(self, *, session_key: str) -> bool:
        del session_key
        return False


__all__ = [
    "DeterministicOpenClawChatTransport",
    "MemberChatChunk",
    "MemberChatError",
    "MemberChatTransport",
    "OpenClawGatewayChatTransport",
    "UnavailableMemberChatTransport",
]
