"""Synthetic-only early identity, before application imports; same owned child."""

from __future__ import annotations

import json
from pathlib import Path
import re
import resource
import secrets
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# This module and its package initializers have only standard-library imports.
from adminme.finance.worker_protocol import read_frame, write_json

STARTUP_LIMIT = 2048
NONCE = re.compile(r"[0-9a-f]{16}")
TENANT = re.compile(r"tenant-synthetic-store-[0-9a-f]{16}")


def store_name(nonce):
    if not isinstance(nonce, str) or NONCE.fullmatch(nonce) is None:
        raise ValueError("synthetic_namespace_invalid")
    return "synthetic-store-" + nonce


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("synthetic_startup_duplicate_key")
        result[key] = value
    return result


class StartupFrames:
    def __init__(self):
        self.tenant = None
        self.ready = False
        self.count = 0

    def read(self, stream):
        if self.ready:
            raise ValueError("synthetic_startup_duplicate_frame")
        value = json.loads(
            read_frame(stream, STARTUP_LIMIT), object_pairs_hook=unique_object
        )
        if not isinstance(value, dict):
            raise ValueError("synthetic_startup_object_required")
        if self.tenant is None:
            if (
                set(value) != {"fixture_coordinates", "tenant"}
                or type(value["fixture_coordinates"]) is not int
                or value["fixture_coordinates"] != 1
                or not isinstance(value["tenant"], str)
                or TENANT.fullmatch(value["tenant"]) is None
            ):
                raise ValueError("synthetic_startup_coordinates_invalid")
            self.tenant = value["tenant"]
        else:
            if (
                set(value) != {"ready", "url", "tenant"}
                or value["ready"] is not True
                or value["tenant"] != self.tenant
                or not isinstance(value["url"], str)
            ):
                raise ValueError("synthetic_startup_readiness_invalid")
            address = urlsplit(value["url"])
            if (
                address.scheme != "http"
                or address.hostname != "127.0.0.1"
                or address.username is not None
                or address.password is not None
                or address.port is None
                or not 1 <= address.port <= 65535
                or address.path
                or address.query
                or address.fragment
            ):
                raise ValueError("synthetic_startup_url_invalid")
            self.ready = True
        self.count += 1
        return value

    def control_result(self, value):
        if not self.ready or not isinstance(value, dict):
            raise ValueError("synthetic_startup_not_complete")
        if "fixture_coordinates" in value or "ready" in value:
            raise ValueError("synthetic_startup_duplicate_frame")
        return value


def launch(arguments):
    if len(arguments) != 2 or arguments[0] != "measured_lifetime":
        raise ValueError("synthetic_launcher_arguments")
    root = Path(arguments[1])
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ValueError("synthetic_launcher_root")
    if any(root.iterdir()):
        raise ValueError("synthetic_launcher_nonempty_root")
    resource.setrlimit(resource.RLIMIT_CPU, (90, 90))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    nonce = secrets.token_hex(8)
    tenant = "tenant-" + store_name(nonce)
    write_json(sys.stdout.buffer, {"fixture_coordinates": 1, "tenant": tenant})

    # No directory/key is accepted over the pipe or command line. The existing
    # constructor still exclusively creates storage and generates its test key.
    import asyncio
    from tests.fixtures.finance_storage_measurement import serve

    asyncio.run(serve(root, namespace_nonce=nonce, announced_tenant=tenant))


if __name__ == "__main__":
    launch(sys.argv[1:])
