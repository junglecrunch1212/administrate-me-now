from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from bootstrap.contracts import DeploymentPaths
from bootstrap.openclaw_cron import (
    CronReconciliationError,
    OpenClawCronReconciler,
    load_desired_jobs,
)


REPO_ROOT = Path(__file__).resolve().parents[3]


class FakeOpenClawCron:
    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = [
            {
                "id": "foreign-1",
                "name": "personal-weekly-review",
                "schedule": {"expr": "0 9 * * 1", "tz": "Etc/UTC"},
                "payload": {"message": "foreign"},
                "sessionTarget": "isolated",
                "delivery": {"mode": "none"},
            }
        ]
        self.commands: list[list[str]] = []
        self.fail_on_add_number: int | None = None
        self._adds = 0

    @staticmethod
    def _option(arguments: list[str], name: str) -> str:
        return arguments[arguments.index(name) + 1]

    def __call__(
        self, command: list[str], **_kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        arguments = command[2:]
        action = arguments[0]
        if action == "list":
            return subprocess.CompletedProcess(
                command, 0, json.dumps({"jobs": self.jobs}), ""
            )
        if action == "add":
            self._adds += 1
            if self.fail_on_add_number == self._adds:
                return subprocess.CompletedProcess(
                    command, 1, "", "simulated add failure"
                )
            job = {
                "id": f"managed-{self._adds}",
                "name": self._option(arguments, "--name"),
                "schedule": {
                    "expr": self._option(arguments, "--cron"),
                    "tz": self._option(arguments, "--tz"),
                },
                "payload": {"message": self._option(arguments, "--message")},
                "sessionTarget": self._option(arguments, "--session"),
                "delivery": {"mode": "none"},
            }
            self.jobs.append(job)
            return subprocess.CompletedProcess(command, 0, json.dumps({"job": job}), "")
        if action == "edit":
            identifier = arguments[1]
            job = next(item for item in self.jobs if item["id"] == identifier)
            job["schedule"] = {
                "expr": self._option(arguments, "--cron"),
                "tz": self._option(arguments, "--tz"),
            }
            job["payload"] = {"message": self._option(arguments, "--message")}
            job["sessionTarget"] = self._option(arguments, "--session")
            job["delivery"] = {"mode": "none"}
            return subprocess.CompletedProcess(command, 0, json.dumps({"job": job}), "")
        if action == "remove":
            identifier = arguments[1]
            self.jobs = [item for item in self.jobs if item["id"] != identifier]
            return subprocess.CompletedProcess(command, 0, "{}", "")
        raise AssertionError(command)


def test_reconcile_adds_updates_and_preserves_foreign_jobs(
    tmp_path: Path,
) -> None:
    paths = DeploymentPaths.from_root(tmp_path / "instance")
    fake = FakeOpenClawCron()
    reconciler = OpenClawCronReconciler(paths, root=REPO_ROOT, runner=fake)

    first = reconciler.reconcile("Etc/UTC")
    assert first["added"] == 5
    assert first["updated"] == 0
    assert {
        job["name"] for job in fake.jobs if job["name"].startswith("administrateme-")
    } == {job.name for job in load_desired_jobs(REPO_ROOT, "Etc/UTC")}
    assert any(job["id"] == "foreign-1" for job in fake.jobs)

    command_count = len(fake.commands)
    second = reconciler.reconcile("Etc/UTC")
    assert second["added"] == second["updated"] == 0
    assert all(command[2] == "list" for command in fake.commands[command_count:])

    managed = next(
        job for job in fake.jobs if job["name"] == "administrateme-morning-digest"
    )
    managed["schedule"]["expr"] = "5 5 * * *"
    third = reconciler.reconcile("Etc/UTC")
    assert third["updated"] == 1
    assert managed["schedule"]["expr"] == "0 7 * * *"
    assert reconciler.status("Etc/UTC")["ok"] is True
    edit_commands = [command for command in fake.commands if command[2] == "edit"]
    assert edit_commands
    assert all("--json" not in command for command in edit_commands)
    add_commands = [command for command in fake.commands if command[2] == "add"]
    assert add_commands
    assert all("--json" in command for command in add_commands)

    fake.jobs = [
        job for job in fake.jobs if job["name"] != "administrateme-morning-digest"
    ]
    drift = reconciler.status("Etc/UTC")
    assert drift["ok"] is False
    assert drift["live_checked"] is True


def test_reconcile_rolls_back_new_jobs_when_openclaw_fails(tmp_path: Path) -> None:
    paths = DeploymentPaths.from_root(tmp_path / "instance")
    fake = FakeOpenClawCron()
    fake.fail_on_add_number = 3
    reconciler = OpenClawCronReconciler(paths, root=REPO_ROOT, runner=fake)

    with pytest.raises(CronReconciliationError, match="failed"):
        reconciler.reconcile("Etc/UTC")

    assert fake.jobs == [
        {
            "id": "foreign-1",
            "name": "personal-weekly-review",
            "schedule": {"expr": "0 9 * * 1", "tz": "Etc/UTC"},
            "payload": {"message": "foreign"},
            "sessionTarget": "isolated",
            "delivery": {"mode": "none"},
        }
    ]
    assert not reconciler.state_path.exists()


def test_duplicate_managed_names_fail_closed(tmp_path: Path) -> None:
    paths = DeploymentPaths.from_root(tmp_path / "instance")
    fake = FakeOpenClawCron()
    duplicate = {
        "id": "duplicate-a",
        "name": "administrateme-morning-digest",
        "schedule": {"expr": "0 7 * * *", "tz": "Etc/UTC"},
        "payload": {"message": "one"},
        "sessionTarget": "isolated",
        "delivery": {"mode": "none"},
    }
    fake.jobs.extend([duplicate, {**duplicate, "id": "duplicate-b"}])

    with pytest.raises(CronReconciliationError, match="duplicate"):
        OpenClawCronReconciler(paths, root=REPO_ROOT, runner=fake).reconcile("Etc/UTC")

    assert not any(command[2] in {"add", "edit", "remove"} for command in fake.commands)
