"""Stable multiprocessing targets for backup-rehearsal process tests.

Spawned disposable controllers create a guardian, which creates another phase
worker.  Keeping every target in an importable module gives each spawn level a
stable module identity on both macOS and Linux instead of repickling helpers
from pytest's transient ``__mp_main__`` module.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from bootstrap.backup_restore import BackupManager


def guardian_extract_fixture(
    source: str,
    staging_root: str,
    expected_artifact_sha256: str,
    key_pipe: Any,
    result_pipe: Any,
) -> None:
    del source, expected_artifact_sha256
    key_pipe.recv_bytes()
    state = Path(staging_root) / "state"
    state.mkdir(parents=True)
    (state / "fixture.db").write_bytes(b"encrypted-fixture")
    result_pipe.send({"status": "healthy", "event_log_sha256": "e" * 64})


def guardian_replay_fixture(
    staged_state: str,
    key_pipe: Any,
    result_pipe: Any,
) -> None:
    key_pipe.recv_bytes()
    (Path(staged_state) / "replay-complete").write_text(
        "healthy",
        encoding="utf-8",
    )
    result_pipe.send({"status": "healthy", "event_log_sha256": "e" * 64})


def guardian_hung_extract_fixture(
    source: str,
    staging_root: str,
    expected_artifact_sha256: str,
    key_pipe: Any,
    result_pipe: Any,
) -> None:
    del source, expected_artifact_sha256, result_pipe
    key_pipe.recv_bytes()
    marker = Path(staging_root) / "hung-extraction.pid"
    marker.write_text(str(os.getpid()), encoding="ascii")
    time.sleep(30)


def guardian_hung_replay_fixture(
    staged_state: str,
    key_pipe: Any,
    result_pipe: Any,
) -> None:
    del result_pipe
    key_pipe.recv_bytes()
    marker = Path(staged_state) / "hung-replay.pid"
    marker.write_text(str(os.getpid()), encoding="ascii")
    time.sleep(30)


def run_guardian_from_disposable_parent(stage: str, mode: str) -> None:
    extraction_target = (
        guardian_hung_extract_fixture
        if mode == "hung-extraction"
        else guardian_extract_fixture
    )
    replay_target = (
        guardian_hung_replay_fixture
        if mode == "hung-replay"
        else guardian_replay_fixture
    )
    BackupManager._run_rehearsal_guardian(  # noqa: SLF001 - process fixture
        "fixture-source",
        stage,
        "a" * 64,
        10,
        b"k" * 64,
        timeout_s=30,
        extraction_target=extraction_target,
        replay_target=replay_target,
    )
