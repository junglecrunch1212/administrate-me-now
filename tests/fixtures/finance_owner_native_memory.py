"""Read aggregate counters from already-loaded pinned SQLCipher and glibc.

No new library image/connection, raw connection pointers, counter resets,
allocator changes or application hooks. This is not performance qualification.
"""

import ctypes
import hashlib
from importlib.metadata import version
import os
from pathlib import Path
import re
import sys
import time

BOUNDARIES = ("preimport", "postimport", "postreplay", "postload")
EXTENSION_SHA256 = "3ce2148af3fcabf04c4eed45b5c11dc92f1959a8b7cb9e708f65c611c5b1cd40"
WHEEL_SHA256 = "e00988174ecd67ecd4537504c3df55bf8daeb75fce98401f099dff8e22c43ae1"
SOURCE_ID = rb"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} [a-f0-9]{60}(?:[a-f0-9]{4}|alt[0-9])(?: alt[0-9])?"
MALLINFO_FIELDS = (
    "arena",
    "ordblks",
    "smblks",
    "hblks",
    "hblkhd",
    "usmblks",
    "fsmblks",
    "uordblks",
    "fordblks",
    "keepcost",
)


class Mallinfo2(ctypes.Structure):
    # glibc-2.39 malloc/malloc.h: ten size_t fields, returned by value.
    _fields_ = [(name, ctypes.c_size_t) for name in MALLINFO_FIELDS]


class Observations:
    def __init__(self):
        if sys.platform != "linux" or os.uname().machine != "x86_64":
            raise ValueError("native_observation_requires_pinned_linux_x86_64")
        if sys.version_info[:3] != (3, 14, 7) or version("sqlcipher3") != "0.6.2":
            raise ValueError("native_observation_version_mismatch")
        if (
            ctypes.sizeof(ctypes.c_size_t) != 8
            or ctypes.sizeof(ctypes.c_void_p) != 8
            or ctypes.sizeof(Mallinfo2) != 80
            or ctypes.alignment(Mallinfo2) != 8
            or [getattr(Mallinfo2, name).offset for name in MALLINFO_FIELDS]
            != list(range(0, 80, 8))
        ):
            raise ValueError("mallinfo2_abi_mismatch")
        libc = ctypes.CDLL(
            "libc.so.6", mode=os.RTLD_NOLOAD | os.RTLD_NOW | os.RTLD_LOCAL
        )
        libc.gnu_get_libc_version.argtypes = []
        libc.gnu_get_libc_version.restype = ctypes.c_char_p
        glibc_version = libc.gnu_get_libc_version()
        if glibc_version != b"2.39":
            raise ValueError("glibc_version_mismatch")
        libc.mallinfo2.argtypes = []
        libc.mallinfo2.restype = Mallinfo2
        self.libc = libc
        # The application imports this module first. Never import/load a second
        # SQLCipher image merely to make observation available.
        extension = sys.modules.get("sqlcipher3._sqlite3")
        package = sys.modules.get("sqlcipher3")
        if extension is None or package is None:
            raise ValueError("sqlcipher_extension_not_already_imported")
        path = Path(extension.__file__).resolve()
        if (
            not path.is_relative_to(Path(sys.prefix).resolve())
            or path.name != "_sqlite3.cpython-314-x86_64-linux-gnu.so"
            or not 0 < path.stat().st_size <= 32 * 1024**2
        ):
            raise ValueError("sqlcipher_extension_path_or_size")
        digest, compiled_ids, previous = hashlib.sha256(), set(), b""
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(65536), b""):
                digest.update(block)
                combined = previous + block
                compiled_ids.update(re.findall(SOURCE_ID + rb"(?=\x00)", combined))
                previous = combined[-128:]
        if digest.hexdigest() != EXTENSION_SHA256 or not 1 <= len(compiled_ids) <= 8:
            raise ValueError("sqlcipher_extension_hash_or_compiled_source_id")
        library = ctypes.CDLL(
            str(path), mode=os.RTLD_NOLOAD | os.RTLD_NOW | os.RTLD_LOCAL
        )
        signatures = {
            "sqlite3_memory_used": ([], ctypes.c_longlong),
            "sqlite3_memory_highwater": ([ctypes.c_int], ctypes.c_longlong),
            "sqlite3_libversion": ([], ctypes.c_char_p),
            "sqlite3_sourceid": ([], ctypes.c_char_p),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(library, name)
            function.argtypes, function.restype = arguments, result
        native_version = library.sqlite3_libversion()
        source_id = library.sqlite3_sourceid()
        if (
            native_version != package.sqlite_version.encode("ascii")
            or source_id not in compiled_ids
        ):
            raise ValueError("loaded_sqlcipher_version_or_source_id_mismatch")
        self.library = library
        self.pins = {
            "package_version": "0.6.2",
            "locked_wheel_sha256": WHEEL_SHA256,
            "extension_name": path.name,
            "extension_sha256": EXTENSION_SHA256,
            "sqlite_version": native_version.decode("ascii"),
            "sqlite_source_id": source_id.decode("ascii"),
            "source_id_matches_hashed_binary_literal": True,
            "already_loaded_only": True,
            "glibc_version": glibc_version.decode("ascii"),
            "mallinfo2_abi": "linux-x86_64; zero arguments; ten size_t fields; 8-byte size_t; 80-byte struct; 8-byte alignment",
            "mallinfo2_field_order": MALLINFO_FIELDS,
        }
        self.index = 0
        self.baseline = self.previous = None

    def capture(self, boundary):
        if self.index >= len(BOUNDARIES) or boundary != BOUNDARIES[self.index]:
            raise ValueError("native_observation_boundary_order")
        started, cpu_started = time.monotonic(), time.process_time()
        used = self.library.sqlite3_memory_used()
        highwater = self.library.sqlite3_memory_highwater(0)
        allocator = self.libc.mallinfo2()
        # Zero may mean disabled memory accounting; do not report it as proof
        # of no live SQLCipher allocations in this running application.
        if not 0 < used <= highwater:
            raise ValueError("sqlcipher_memory_accounting_unavailable")
        row = {
            "schema": "finance-owner-mixed-memory-observation-v1",
            "qualification": False,
            "boundary": boundary,
            "pid": os.getpid(),
            "pins": self.pins,
            "started_at": started,
            "sqlite_live_bytes": used,
            "sqlite_highwater_since_process_start_bytes": highwater,
            "glibc_mallinfo2_raw": {
                name: getattr(allocator, name) for name in MALLINFO_FIELDS
            },
            "glibc_field_units": "ordblks/smblks/hblks: chunk or region counts; all other fields: bytes",
            "glibc_coverage": "glibc allocator aggregates across arenas plus allocator mmap accounting; sequential arena locks, not an atomic process snapshot. Free-bin counts exclude thread caches; in-use is not live application-object ownership. Does not account for all RSS, direct mappings or other allocators. Allocator counters and SQLCipher counters overlap and must not be added.",
            "reset_counters": False,
            "live_delta_from_preimport_bytes": None
            if self.baseline is None
            else used - self.baseline,
            "live_delta_from_previous_bytes": None
            if self.previous is None
            else used - self.previous,
            "interpretation": "Aggregate allocations counted by this SQLCipher image across the runtime. Does not identify a connection/statement, include all native allocations, or explain allocator-retained RSS. No latency/resource qualification.",
            "finished_at": time.monotonic(),
            "observation_cpu_seconds": time.process_time() - cpu_started,
        }
        row["observation_wall_seconds"] = row["finished_at"] - started
        if self.baseline is None:
            self.baseline = used
        self.previous = used
        self.index += 1
        return row
