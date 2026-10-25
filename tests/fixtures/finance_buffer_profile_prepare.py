"""Export and verify exact read-only source inputs before the measured processes."""

import hashlib
import io
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tarfile
import time

PINS = {
    "before": (
        "7419175f5508cf21bc7b86e046f65c5c2893269e",
        "7b89c73a0420eb73925119b9f0550629389586c6",
    ),
    "after": (
        "49331292a06ce7cab9eb0504ad197a55684fae88",
        "1873e65d8a36d8af74ed5608b49b62dac6934d73",
    ),
}
SOURCE_CONTENTS = {"before": 52135208, "after": 52145692}
SOURCE_DISK_LIMIT = 128 * 1024 * 1024
PREPARATION_ADDRESS_SPACE_LIMIT = 512 * 1024 * 1024
LOCK = "cc2102f6e9dd2fd029bd636566806493c1df91dc"
FIXTURES = {
    "tests/fixtures/finance_import_load.py": "79fb003c716a5485e6f5e1cf18ae5e6b9f0aa445c6a32ec82cd7fc6fbe537a09",
    "tests/fixtures/finance_provenance.py": "b8d410ad7832ef629e5f8bbe26f00f2a7e5b39993bf51f4ec6fcd88d30f69dfd",
    "tests/fixtures/finance_planning.py": "b1c14038604d9d6a4077b45135b1ea206ad49432450655eddbbb8f015fa84ca0",
    "tests/fixtures/finance_activation.py": "ee5197d7d92258de0ff093e1c21dff0912213080bb525d353c9c721ed5a3e698",
    "tests/acceptance/test_finance_publications.py": "c4b9640b7e0198b2291334272093c545f4773370749645592f6751c417b9cebe",
}


def main(repository, sources, evidence):
    started = time.monotonic()
    resource.setrlimit(resource.RLIMIT_AS, (PREPARATION_ADDRESS_SPACE_LIMIT,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    calls = []
    evidence.mkdir(parents=True, exist_ok=False)
    sources.mkdir(parents=True, exist_ok=False)
    report = {"success": False, "git_processes": calls, "fixture_sha256": FIXTURES}

    def git(*arguments):
        begin = time.monotonic()
        call = {"arguments": arguments, "state": "launching"}
        calls.append(call)
        try:
            result = subprocess.run(
                ["git", "-C", str(repository), *arguments],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
            )
            call.update(state="reaped", returncode=result.returncode)
            return result.stdout
        except BaseException as exc:
            call.update(state="failed", failure=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            call["wall_seconds"] = time.monotonic() - begin

    try:
        assert sys.version_info[:3] == (3, 14, 7)
        report["candidate"] = git("rev-parse", "HEAD").decode().strip()
        changed = (
            git(
                "diff",
                "--name-only",
                PINS["before"][0],
                PINS["after"][0],
                "--",
                "adminme",
            )
            .decode()
            .splitlines()
        )
        assert changed == [
            "adminme/finance/dated_projection.py",
            "adminme/finance/publication_provenance.py",
        ]
        for label, (revision, tree) in PINS.items():
            actual = (
                git(
                    "rev-parse",
                    revision,
                    f"{revision}:adminme",
                    f"{revision}:poetry.lock",
                )
                .decode()
                .splitlines()
            )
            assert actual == [revision, tree, LOCK]
            destination = sources / label
            destination.mkdir()
            archive = git("archive", "--format=tar", revision)
            assert len(archive) <= 64 * 1024 * 1024, "Source tar size bound"
            with tarfile.open(fileobj=io.BytesIO(archive)) as source:
                assert all(
                    item.isfile() or item.isdir() for item in source.getmembers()
                ), "No source links or special files"
                members = source.getmembers()
                assert sum(item.size for item in members) == SOURCE_CONTENTS[label], (
                    "Pinned source contents size"
                )
                existing = sum(
                    path.stat().st_blocks * 512 for path in sources.rglob("*")
                )
                estimated = sum(
                    ((item.size + 4095) // 4096) * 4096 if item.isfile() else 4096
                    for item in members
                )
                assert existing + estimated + 16384 <= SOURCE_DISK_LIMIT, (
                    "Prepared source disk bound"
                )
                source.extractall(destination, filter="data")
            del archive
            for name, expected in FIXTURES.items():
                assert (
                    hashlib.sha256((destination / name).read_bytes()).hexdigest()
                    == expected
                )
            assert (destination / "poetry.lock").read_bytes() == (
                repository / "poetry.lock"
            ).read_bytes()
            assert (destination / "pyproject.toml").read_bytes() == (
                repository / "pyproject.toml"
            ).read_bytes()
            (destination / ".probe-source.json").write_text(
                json.dumps(
                    {"revision": revision, "production_tree": tree, "lock": LOCK}
                )
                + "\n"
            )
            for path in sorted(destination.rglob("*"), reverse=True):
                path.chmod(0o555 if path.is_dir() else 0o444)
            destination.chmod(0o555)
            assert (
                sum(path.stat().st_blocks * 512 for path in sources.rglob("*"))
                <= SOURCE_DISK_LIMIT
            )
        report.update(
            success=True,
            pins=PINS,
            lock=LOCK,
            preparation_limits={
                "source_contents_bytes": SOURCE_CONTENTS,
                "source_disk_bytes": SOURCE_DISK_LIMIT,
                "per_process_address_space_bytes": PREPARATION_ADDRESS_SPACE_LIMIT,
                "per_process_cpu_seconds": 30,
                "git_call_wall_seconds": 30,
                "workflow_step_wall_seconds": 60,
                "scope": "Separate from32MiB evidence scratch and2MiB artifact limits; source tar stays in memory",
            },
            prepared_source_allocated_bytes=sum(
                path.stat().st_blocks * 512 for path in sources.rglob("*")
            ),
            maximum_active_owned_processes=2,
            git_processes_created=len(calls),
            python=sys.version,
            platform=dict(
                zip(("system", "node", "release", "version", "machine"), os.uname())
            ),
            image_os=os.environ.get("ImageOS"),
            image_version=os.environ.get("ImageVersion"),
            scope="Preparation only; six sequential Git children, separate from self-check and measurement process counts",
        )
    except BaseException as exc:
        report["failure"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        own = resource.getrusage(resource.RUSAGE_SELF)
        children = resource.getrusage(resource.RUSAGE_CHILDREN)
        report.update(
            wall_seconds=time.monotonic() - started,
            supervisor_cpu_seconds=own.ru_utime + own.ru_stime,
            git_children_cpu_seconds=children.ru_utime + children.ru_stime,
            supervisor_high_water_rss_bytes=own.ru_maxrss * 1024,
            git_children_high_water_rss_bytes=children.ru_maxrss * 1024,
        )
        (evidence / "preparation.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main(*(Path(x).resolve() for x in sys.argv[1:]))
