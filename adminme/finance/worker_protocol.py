"""Private framed IPC for one credential-free Finance calculation child.

No pickle, executable input, file names, database handles or credentials cross
this pipe. Limits apply before allocating or decoding a received frame.
"""

from __future__ import annotations

from collections.abc import Iterator
import hashlib
import json
import struct
from typing import Any, IO

FRAME_LIMIT = 32 * 1024 * 1024
UPLOAD_CHUNK = 64 * 1024
CONTROL_LIMIT = 64 * 1024
FINANCE_TYPES = frozenset(
    {
        "finance.provenance_archived",
        "finance.publication_accepted",
        "finance.projection_verified",
    }
)


def read_frame(stream: IO[bytes], limit: int = FRAME_LIMIT) -> bytes:
    header = stream.read(4)
    if len(header) != 4:
        raise ValueError("finance_worker_pipe_closed")
    size = struct.unpack("!I", header)[0]
    if size > limit:
        raise ValueError("finance_worker_frame_limit")
    first = stream.read(size)
    if len(first) == size:
        return first
    parts = bytearray(first)
    while len(parts) < size:
        chunk = stream.read(size - len(parts))
        if not chunk:
            raise ValueError("finance_worker_pipe_closed")
        parts.extend(chunk)
    return bytes(parts)


def write_frame(stream: IO[bytes], data: bytes) -> None:
    if len(data) > FRAME_LIMIT:
        raise ValueError("finance_worker_frame_limit")
    stream.write(struct.pack("!I", len(data)))
    stream.write(data)
    stream.flush()


def utf8_chunks(text: str) -> Iterator[bytes]:
    # At most four UTF-8 bytes per code point; closed JSON is normally ASCII.
    step = UPLOAD_CHUNK if text.isascii() else UPLOAD_CHUNK // 4
    for offset in range(0, len(text), step):
        yield text[offset : offset + step].encode("utf-8")


def text_sha256(text: str) -> str:
    digest = hashlib.sha256()
    for chunk in utf8_chunks(text):
        digest.update(chunk)
    return digest.hexdigest()


def write_text_frame(stream: IO[bytes], text: str) -> None:
    # Keep the existing wire format and reject oversize/invalid UTF-8 before
    # writing a header, without allocating a whole second payload in the parent.
    size = len(text)
    if size > FRAME_LIMIT:
        raise ValueError("finance_worker_frame_limit")
    if not text.isascii():
        size = 0
        for chunk in utf8_chunks(text):
            size += len(chunk)
            if size > FRAME_LIMIT:
                raise ValueError("finance_worker_frame_limit")
    stream.write(struct.pack("!I", size))
    for chunk in utf8_chunks(text):
        stream.write(chunk)
    stream.flush()


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode()


def read_json(stream: IO[bytes], limit: int = CONTROL_LIMIT) -> Any:
    return json.loads(read_frame(stream, limit))


def write_json(stream: IO[bytes], value: Any) -> None:
    write_frame(stream, json_bytes(value))
