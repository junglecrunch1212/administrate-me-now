"""Idempotent ownership-aware OpenClaw cron reconciliation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from bootstrap.contracts import DeploymentPaths, atomic_write, repository_root


class CronReconciliationError(RuntimeError):
    pass


@dataclass(frozen=True)
class DesiredCronJob:
    managed_id: str
    program_id: str
    name: str
    schedule: str
    timezone: str
    message: str

    def public(self) -> dict[str, str]:
        return {
            "managed_id": self.managed_id,
            "program_id": self.program_id,
            "name": self.name,
            "schedule": self.schedule,
            "timezone": self.timezone,
            "message": self.message,
            "session": "isolated",
            "delivery": "none",
        }


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _canonical_digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _validate_timezone(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CronReconciliationError("an explicit IANA household timezone is required")
    timezone = value.strip()
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise CronReconciliationError(
            f"household timezone is not a recognized IANA zone: {timezone}"
        ) from exc
    return timezone


def load_desired_jobs(root: Path, timezone: str) -> list[DesiredCronJob]:
    """Load both declarations and refuse any drift between them."""

    timezone = _validate_timezone(timezone)
    automation_path = root / "openclaw" / "administrateme" / "automations.json"
    sidecar_path = root / "bootstrap" / "openclaw" / "cron.yaml"
    try:
        automations = json.loads(automation_path.read_text("utf-8"))
        sidecar = yaml.safe_load(sidecar_path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise CronReconciliationError(
            "OpenClaw automation declarations are invalid"
        ) from exc
    entries = automations.get("entries")
    sidecar_entries = sidecar.get("entries") if isinstance(sidecar, dict) else None
    if automations.get("schema_version") != 1 or not isinstance(entries, list):
        raise CronReconciliationError("automations.json has an unsupported schema")
    if not isinstance(sidecar_entries, list):
        raise CronReconciliationError(
            "bootstrap cron sidecar has an unsupported schema"
        )
    sidecar_by_program = {
        item.get("program_id"): item
        for item in sidecar_entries
        if isinstance(item, dict) and isinstance(item.get("program_id"), str)
    }
    desired: list[DesiredCronJob] = []
    seen_ids: set[str] = set()
    seen_programs: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise CronReconciliationError("automation entries must be objects")
        managed_id = entry.get("id")
        program_id = entry.get("program_id")
        schedule = entry.get("schedule")
        message = entry.get("message")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (managed_id, program_id, schedule, message)
        ):
            raise CronReconciliationError("automation entries have missing fields")
        assert isinstance(managed_id, str)
        assert isinstance(program_id, str)
        assert isinstance(schedule, str)
        assert isinstance(message, str)
        if not re.fullmatch(r"administrateme-[a-z0-9-]+", managed_id):
            raise CronReconciliationError(
                f"automation id is outside ownership: {managed_id}"
            )
        if managed_id in seen_ids or program_id in seen_programs:
            raise CronReconciliationError("automation ids and programs must be unique")
        sidecar_entry = sidecar_by_program.get(program_id)
        if not sidecar_entry or (
            sidecar_entry.get("cron") != schedule
            or sidecar_entry.get("message") != message
        ):
            raise CronReconciliationError(
                f"automation declaration drift for program: {program_id}"
            )
        seen_ids.add(managed_id)
        seen_programs.add(program_id)
        desired.append(
            DesiredCronJob(
                managed_id=managed_id,
                program_id=program_id,
                name=managed_id,
                schedule=schedule,
                timezone=timezone,
                message=message,
            )
        )
    if set(sidecar_by_program) != seen_programs:
        raise CronReconciliationError("cron sidecar and automations.json disagree")
    return sorted(desired, key=lambda item: item.managed_id)


def _jobs_from_output(output: str) -> list[dict[str, Any]]:
    try:
        value = json.loads(output)
    except json.JSONDecodeError as exc:
        raise CronReconciliationError("OpenClaw cron returned non-JSON output") from exc
    jobs_value: Any
    if isinstance(value, list):
        jobs_value = value
    elif isinstance(value, dict):
        jobs_value = value.get("jobs", value.get("entries", []))
    else:
        jobs_value = []
    if not isinstance(jobs_value, list) or not all(
        isinstance(item, dict) for item in jobs_value
    ):
        raise CronReconciliationError("OpenClaw cron list JSON has an unknown shape")
    return jobs_value


def _job_id(job: dict[str, Any]) -> str:
    value = job.get("id", job.get("jobId"))
    return value if isinstance(value, str) else ""


def _job_name(job: dict[str, Any]) -> str:
    value = job.get("name")
    return value if isinstance(value, str) else ""


def _nested(job: dict[str, Any], key: str) -> dict[str, Any]:
    value = job.get(key)
    return value if isinstance(value, dict) else {}


def _normalized(job: dict[str, Any]) -> dict[str, str]:
    schedule = _nested(job, "schedule")
    payload = _nested(job, "payload")
    delivery = _nested(job, "delivery")
    return {
        "name": _job_name(job),
        "schedule": str(
            schedule.get("expr", schedule.get("cron", job.get("cron", "")))
        ),
        "timezone": str(
            schedule.get("tz", schedule.get("timezone", job.get("tz", "")))
        ),
        "message": str(payload.get("message", job.get("message", ""))),
        "session": str(
            job.get("sessionTarget", job.get("session", payload.get("session", "")))
        ),
        "delivery": str(delivery.get("mode", job.get("deliveryMode", ""))),
    }


def _matches(job: dict[str, Any], desired: DesiredCronJob) -> bool:
    actual = _normalized(job)
    expected = desired.public()
    return all(actual[key] == expected[key] for key in expected if key in actual)


class OpenClawCronReconciler:
    """Own only AdministrateMe jobs and leave every foreign job untouched."""

    def __init__(
        self,
        paths: DeploymentPaths,
        *,
        root: Path | None = None,
        gateway_token: str | None = None,
        test_mode: bool = False,
        runner: Runner = subprocess.run,
    ) -> None:
        self.paths = paths
        self.root = (root or repository_root()).resolve()
        self.gateway_token = gateway_token
        self.test_mode = test_mode
        self.runner = runner

    @property
    def state_path(self) -> Path:
        return self.paths.instance_dir / "openclaw" / "cron-reconciliation.json"

    def _run(self, arguments: list[str]) -> str:
        environment = os.environ.copy()
        if self.gateway_token:
            environment["OPENCLAW_GATEWAY_TOKEN"] = self.gateway_token
        completed = self.runner(
            ["openclaw", "cron", *arguments],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        if completed.returncode:
            detail = (completed.stderr or completed.stdout).strip()[:500]
            raise CronReconciliationError(
                f"OpenClaw cron command failed without applying a verified state: {detail}"
            )
        return completed.stdout

    def _list(self) -> list[dict[str, Any]]:
        return _jobs_from_output(self._run(["list", "--json"]))

    @staticmethod
    def _arguments(
        desired: DesiredCronJob, *, add: bool, job_id: str = ""
    ) -> list[str]:
        arguments = ["add"] if add else ["edit", job_id]
        if add:
            arguments.extend(["--name", desired.name])
        arguments.extend(
            [
                "--cron",
                desired.schedule,
                "--tz",
                desired.timezone,
                "--session",
                "isolated",
                "--message",
                desired.message,
                "--no-deliver",
            ]
        )
        if add:
            arguments.append("--json")
        return arguments

    def reconcile(self, timezone: str) -> dict[str, Any]:
        desired = load_desired_jobs(self.root, timezone)
        desired_public = [job.public() for job in desired]
        desired_digest = _canonical_digest(desired_public)
        if self.test_mode:
            installed_fixture = [
                {**job.public(), "job_id": f"fixture-{job.managed_id}"}
                for job in desired
            ]
            result = {
                "schema_version": 1,
                "status": "reconciled",
                "desired_digest": desired_digest,
                "jobs": installed_fixture,
                "added": len(installed_fixture),
                "updated": 0,
                "removed": 0,
                "foreign_jobs_untouched": True,
            }
            atomic_write(self.state_path, json.dumps(result, indent=2).encode())
            return result

        current = self._list()
        managed_by_name: dict[str, list[dict[str, Any]]] = {}
        for current_job in current:
            name = _job_name(current_job)
            if name.startswith("administrateme-"):
                managed_by_name.setdefault(name, []).append(current_job)
        duplicates = sorted(
            name for name, jobs in managed_by_name.items() if len(jobs) > 1
        )
        if duplicates:
            raise CronReconciliationError(
                f"duplicate AdministrateMe cron ownership detected: {duplicates}"
            )
        prior_state = (
            json.loads(self.state_path.read_text("utf-8"))
            if self.state_path.exists()
            else {}
        )
        prior_ids: dict[str, str] = {}
        for item in prior_state.get("jobs", []):
            if not isinstance(item, dict):
                continue
            prior_managed_id = item.get("managed_id")
            prior_job_id = item.get("job_id")
            if isinstance(prior_managed_id, str) and isinstance(prior_job_id, str):
                prior_ids[prior_managed_id] = prior_job_id
        desired_names = {job.name for job in desired}
        added_ids: list[str] = []
        edited: list[tuple[str, dict[str, Any]]] = []
        removed: list[dict[str, Any]] = []
        added = updated = removed_count = 0
        try:
            for desired_job in desired:
                candidates = managed_by_name.get(desired_job.name, [])
                existing: dict[str, Any] | None = candidates[0] if candidates else None
                if existing is None:
                    self._run(self._arguments(desired_job, add=True))
                    refreshed = [
                        item
                        for item in self._list()
                        if _job_name(item) == desired_job.name
                    ]
                    if len(refreshed) != 1 or not _job_id(refreshed[0]):
                        raise CronReconciliationError(
                            f"OpenClaw did not return one verifiable job for {desired_job.name}"
                        )
                    added_ids.append(_job_id(refreshed[0]))
                    managed_by_name[desired_job.name] = refreshed
                    added += 1
                elif not _matches(existing, desired_job):
                    identifier = _job_id(existing)
                    if not identifier:
                        raise CronReconciliationError(
                            f"managed job has no id: {desired_job.name}"
                        )
                    edited.append((identifier, existing))
                    self._run(
                        self._arguments(
                            desired_job,
                            add=False,
                            job_id=identifier,
                        )
                    )
                    updated += 1
            for managed_id, identifier in prior_ids.items():
                if not isinstance(managed_id, str) or managed_id in desired_names:
                    continue
                match = next(
                    (
                        current_job
                        for current_job in current
                        if _job_id(current_job) == identifier
                    ),
                    None,
                )
                if match is not None:
                    removed.append(match)
                    self._run(["remove", str(identifier), "--json"])
                    removed_count += 1
            verified = self._list()
            installed_jobs: list[dict[str, str]] = []
            for desired_job in desired:
                matches = [
                    item for item in verified if _job_name(item) == desired_job.name
                ]
                if len(matches) != 1 or not _matches(matches[0], desired_job):
                    raise CronReconciliationError(
                        f"post-reconciliation verification failed for {desired_job.name}"
                    )
                installed_jobs.append(
                    {**desired_job.public(), "job_id": _job_id(matches[0])}
                )
        except Exception:
            for identifier in reversed(added_ids):
                try:
                    self._run(["remove", identifier, "--json"])
                except CronReconciliationError:
                    pass
            for identifier, snapshot in reversed(edited):
                normalized = _normalized(snapshot)
                try:
                    prior = DesiredCronJob(
                        managed_id=normalized["name"],
                        program_id=normalized["name"],
                        name=normalized["name"],
                        schedule=normalized["schedule"],
                        timezone=normalized["timezone"],
                        message=normalized["message"],
                    )
                    self._run(self._arguments(prior, add=False, job_id=identifier))
                except CronReconciliationError:
                    pass
            for snapshot in removed:
                normalized = _normalized(snapshot)
                try:
                    prior = DesiredCronJob(
                        managed_id=normalized["name"],
                        program_id=normalized["name"],
                        name=normalized["name"],
                        schedule=normalized["schedule"],
                        timezone=normalized["timezone"],
                        message=normalized["message"],
                    )
                    self._run(self._arguments(prior, add=True))
                except CronReconciliationError:
                    pass
            raise
        result = {
            "schema_version": 1,
            "status": "reconciled",
            "desired_digest": desired_digest,
            "jobs": installed_jobs,
            "added": added,
            "updated": updated,
            "removed": removed_count,
            "foreign_jobs_untouched": True,
        }
        atomic_write(self.state_path, json.dumps(result, indent=2).encode())
        return result

    def status(self, timezone: str) -> dict[str, Any]:
        desired_jobs = load_desired_jobs(self.root, timezone)
        desired = [job.public() for job in desired_jobs]
        expected = _canonical_digest(desired)
        try:
            state = json.loads(self.state_path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"ok": False, "detail": "cron reconciliation marker is missing"}
        jobs = state.get("jobs", [])
        marker_ok = (
            state.get("status") == "reconciled"
            and state.get("desired_digest") == expected
            and len(jobs) == len(desired)
            and all(isinstance(job, dict) and job.get("job_id") for job in jobs)
        )
        if self.test_mode:
            return {
                "ok": marker_ok,
                "detail": f"{len(jobs)}/{len(desired)} managed jobs reconciled (fixture)",
                "desired_digest": expected,
                "live_checked": False,
            }
        current = self._list()
        managed = [
            job for job in current if _job_name(job).startswith("administrateme-")
        ]
        unexpected = sorted(
            _job_name(job)
            for job in managed
            if _job_name(job) not in {desired_job.name for desired_job in desired_jobs}
        )
        matched = sum(
            len(matches) == 1
            and bool(_job_id(matches[0]))
            and _matches(matches[0], desired_job)
            for desired_job in desired_jobs
            if (
                matches := [
                    job for job in managed if _job_name(job) == desired_job.name
                ]
            )
        )
        live_ok = matched == len(desired_jobs) and not unexpected
        ok = marker_ok and live_ok
        return {
            "ok": ok,
            "detail": (
                f"{matched}/{len(desired)} managed jobs match the live scheduler"
            ),
            "desired_digest": expected,
            "live_checked": True,
            "unexpected_owned_names": unexpected,
        }


__all__ = [
    "CronReconciliationError",
    "DesiredCronJob",
    "OpenClawCronReconciler",
    "load_desired_jobs",
]
