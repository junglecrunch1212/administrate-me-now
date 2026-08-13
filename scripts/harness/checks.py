"""Reviewed test selection and exact-receipt execution for harness tiers."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import struct
import subprocess
import sys
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core import (
    GATE7_MIN_ASSERTIONS,
    GATE7_MIN_SCREENSHOTS,
    HarnessError,
    _validate_browser_evidence,
    clean_environment,
    ensure_ephemeral_run_dir,
    load_json,
    sha256_file,
    utc_now,
    write_json,
)


@dataclass(frozen=True)
class CheckSpec:
    check_id: str
    tier: str
    command: str


STATIC_CHECKS = (
    CheckSpec(
        "harness-unit",
        "static",
        "PYTHONPATH=bridge PYTHONDONTWRITEBYTECODE=1 poetry run pytest -p no:cacheprovider -q tests/unit/harness",
    ),
    CheckSpec(
        "harness-consistency",
        "static",
        "PYTHONPATH=bridge poetry run python -m scripts.harness.cli consistency --check-generated",
    ),
    CheckSpec("invariants", "static", "bash scripts/verify_invariants.sh"),
    CheckSpec("diff-hygiene", "static", "git diff --check"),
)

JOURNEY_CHECKS = (
    CheckSpec(
        "navigation-production-composition",
        "journey",
        "PYTHONPATH=bridge PYTHONDONTWRITEBYTECODE=1 poetry run pytest -p no:cacheprovider -q tests/integration/test_harness_member_navigation.py tests/acceptance/test_gate7_household_experience.py::test_ten_surfaces_three_modes_and_independent_route_denial",
    ),
)

BREADTH_CHECKS = (
    CheckSpec("poetry-sync", "breadth", "poetry sync --with dev"),
    CheckSpec("root-suite", "breadth", "PYTHONPATH=bridge poetry run pytest -q"),
    CheckSpec(
        "acceptance-runxfail",
        "breadth",
        "PYTHONPATH=bridge poetry run pytest tests/acceptance --runxfail -q",
    ),
    CheckSpec(
        "ruff-supported",
        "breadth",
        "poetry run ruff check adminme bridge/adminme_bridge",
    ),
    CheckSpec(
        "mypy-supported",
        "breadth",
        "poetry run mypy adminme bridge/adminme_bridge",
    ),
    CheckSpec("invariants", "breadth", "bash scripts/verify_invariants.sh"),
    CheckSpec(
        "openclaw-install",
        "breadth",
        "npm --prefix openclaw/administrateme ci",
    ),
    CheckSpec(
        "openclaw-check",
        "breadth",
        "npm --prefix openclaw/administrateme run check",
    ),
    CheckSpec(
        "openclaw-test",
        "breadth",
        "npm --prefix openclaw/administrateme test",
    ),
)

RELEASE_CHECKS = (
    CheckSpec(
        "release-candidate",
        "release",
        "bash scripts/acceptance/release-candidate.sh",
    ),
)

PATH_RULES: tuple[tuple[tuple[str, ...], tuple[CheckSpec, ...]], ...] = (
    (
        ("scripts/harness/", "schemas/harness/", ".agents/skills/adminme-"),
        (
            CheckSpec(
                "harness-focused",
                "focused",
                "PYTHONPATH=bridge PYTHONDONTWRITEBYTECODE=1 poetry run pytest -p no:cacheprovider -q tests/unit/harness tests/integration/test_harness_*.py",
            ),
        ),
    ),
    (
        (
            "adminme/products/member/navigation.py",
            "adminme/products/member/surfaces.py",
            "adminme/products/member/main.py",
            "tests/unit/harness/test_member_navigation_characterization.py",
            "tests/integration/test_harness_member_navigation.py",
        ),
        (
            CheckSpec(
                "member-navigation-characterization",
                "focused",
                "PYTHONPATH=bridge PYTHONDONTWRITEBYTECODE=1 poetry run pytest -p no:cacheprovider -q tests/unit/harness/test_member_navigation_characterization.py tests/integration/test_harness_member_navigation.py",
            ),
            CheckSpec(
                "member-navigation-head-baseline",
                "focused",
                "PYTHONPATH=bridge PYTHONDONTWRITEBYTECODE=1 poetry run python -m scripts.harness.baseline_navigation",
            ),
            CheckSpec(
                "member-navigation-acceptance",
                "focused",
                "PYTHONPATH=bridge PYTHONDONTWRITEBYTECODE=1 poetry run pytest -p no:cacheprovider -q tests/acceptance/test_gate7_household_experience.py::test_ten_surfaces_three_modes_and_independent_route_denial tests/acceptance/test_triage_vertical_slice.py::test_proposal_requires_member_triage_before_official_task",
            ),
        ),
    ),
    (
        ("docs/refactor/", ".github/workflows/"),
        (
            CheckSpec(
                "constitutional-consistency",
                "focused",
                "PYTHONPATH=bridge poetry run python -m scripts.harness.cli consistency --check-generated",
            ),
        ),
    ),
)


def focused_checks(changed_paths: list[str]) -> list[CheckSpec]:
    selected: dict[str, CheckSpec] = {}
    for prefixes, checks in PATH_RULES:
        if any(
            path == prefix.rstrip("/") or path.startswith(prefix)
            for path in changed_paths
            for prefix in prefixes
        ):
            selected.update({check.check_id: check for check in checks})
    if not selected:
        fallback = CheckSpec(
            "focused-fallback",
            "focused",
            "PYTHONPATH=bridge PYTHONDONTWRITEBYTECODE=1 poetry run pytest -p no:cacheprovider -q tests/unit",
        )
        selected[fallback.check_id] = fallback
    return list(selected.values())


def tier_checks(tier: str, changed_paths: list[str] | None = None) -> list[CheckSpec]:
    if tier == "static":
        return list(STATIC_CHECKS)
    if tier == "focused":
        return focused_checks(changed_paths or [])
    if tier == "journey":
        return list(JOURNEY_CHECKS)
    if tier == "breadth":
        return list(BREADTH_CHECKS)
    if tier == "release":
        return list(RELEASE_CHECKS)
    raise HarnessError(f"unknown check tier: {tier}")


def run_check(
    root: Path,
    run_dir: Path,
    spec: CheckSpec,
    *,
    environment: dict[str, str] | None = None,
    toolchain: dict[str, str] | None = None,
) -> dict[str, Any]:
    ensure_ephemeral_run_dir(run_dir, root)
    logs = run_dir / "check-logs"
    logs.mkdir(exist_ok=True)
    started = utc_now()
    begin = time.monotonic()
    process = subprocess.run(
        spec.command,
        cwd=root,
        shell=True,
        executable="/bin/bash",
        capture_output=True,
        text=True,
        env=clean_environment(environment),
    )
    duration = time.monotonic() - begin
    log_path = logs / f"{spec.tier}-{spec.check_id}.log"
    log_path.write_text(
        "\n".join(
            (
                f"started_at={started}",
                f"finished_at={utc_now()}",
                f"command={spec.command}",
                f"toolchain={json.dumps(toolchain, sort_keys=True) if toolchain else 'not-recorded'}",
                f"exit_code={process.returncode}",
                "stdout:",
                process.stdout,
                "stderr:",
                process.stderr,
            )
        ),
        encoding="utf-8",
    )
    check = {
        "check_id": spec.check_id,
        "tier": spec.tier,
        "command": spec.command,
        "status": "PASS" if process.returncode == 0 else "FAIL",
        "exit_code": process.returncode,
        "duration_seconds": round(duration, 3),
        "evidence_class": "local_candidate",
    }
    receipt_path = run_dir / "check-receipts" / f"{spec.tier}-{spec.check_id}.json"
    receipt_path.parent.mkdir(exist_ok=True)
    receipt_value = {
        "schema_version": 1,
        "binding": _current_run_binding(run_dir),
        "check": check,
        "log": {
            "path": log_path.relative_to(run_dir).as_posix(),
            "sha256": sha256_file(log_path),
        },
    }
    if toolchain is not None:
        receipt_value["toolchain"] = toolchain
    write_json(receipt_path, receipt_value)
    return {**check, "receipt": receipt_path.relative_to(run_dir).as_posix()}


def run_tier(
    root: Path,
    run_dir: Path,
    tier: str,
    *,
    changed_paths: list[str] | None = None,
    environment: dict[str, str] | None = None,
    node_bin: str | None = None,
) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    node_environment: dict[str, str] | None = None
    node_toolchain: dict[str, str] | None = None
    for spec in tier_checks(tier, changed_paths):
        check_environment = environment
        toolchain = None
        if _is_openclaw_check(spec):
            if node_environment is None or node_toolchain is None:
                node_environment, node_toolchain = _supported_toolchain(
                    node_bin=node_bin,
                    environment=environment,
                    purpose="OpenClaw breadth checks",
                )
            check_environment = node_environment
            toolchain = node_toolchain
        receipt = run_check(
            root,
            run_dir,
            spec,
            environment=check_environment,
            toolchain=toolchain,
        )
        receipts.append(receipt)
        if receipt["status"] != "PASS":
            break
    return receipts


def run_browser(
    root: Path,
    run_dir: Path,
    *,
    node_bin: str | None = None,
    label: str = "current",
) -> dict[str, Any]:
    """Run the existing credential-free Gate 7 harness into this temp run."""

    ensure_ephemeral_run_dir(run_dir, root)
    if not label.replace("-", "").isalnum():
        raise HarnessError(f"invalid browser evidence label: {label}")
    evidence_dir = run_dir / ("browser" if label == "current" else f"browser-{label}")
    evidence_dir.mkdir(exist_ok=True)
    write_json(evidence_dir / "harness-binding.json", _current_run_binding(run_dir))
    browser_environment = {
        "ADMINME_GATE7_EVIDENCE_DIR": str(evidence_dir),
        "ADMINME_GATE7_PYTHON": sys.executable,
        "ADMINME_GATE7_VISUAL_NOW": "2026-08-13T14:00:00Z",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    environment, toolchain = _supported_toolchain(
        node_bin=node_bin,
        environment=browser_environment,
        purpose="browser proof",
    )
    spec = CheckSpec(
        "gate7-browser" if label == "current" else f"gate7-browser-{label}",
        "journey",
        "npm --prefix tools/console-reference-harness run test:gate7",
    )
    receipt = run_check(
        root,
        run_dir,
        spec,
        environment=environment,
        toolchain=toolchain,
    )
    report_path = evidence_dir / "visual-results.json"
    browser: dict[str, Any] = {
        "status": receipt["status"],
        "output_directory": str(evidence_dir),
        "report_path": None,
        "report_sha256": None,
        "assertion_count": 0,
        "browser_engine": None,
        "desktop": [],
        "narrow": [],
        "console_errors": [],
        "page_errors": [],
        "request_failures": [],
    }
    if not report_path.is_file():
        browser["status"] = "FAIL" if receipt["status"] == "FAIL" else "UNDETERMINED"
        return {"receipt": receipt, "browser": browser}
    report = load_json(report_path)
    browser.update(_browser_summary(evidence_dir, report_path, report))
    if receipt["status"] != "PASS":
        browser["status"] = "FAIL"
    if browser["status"] == "PASS":
        _validate_browser_evidence(
            run_dir,
            _current_run_binding(run_dir),
            browser,
        )
    return {"receipt": receipt, "browser": browser}


def _is_openclaw_check(spec: CheckSpec) -> bool:
    return spec.command.startswith("npm --prefix openclaw/administrateme ")


def _supported_toolchain(
    *,
    node_bin: str | None,
    environment: dict[str, str] | None,
    purpose: str,
) -> tuple[dict[str, str], dict[str, str]]:
    """Resolve and attest the reviewed Python/Node/npm candidate toolchain."""

    base_path = (environment or {}).get("PATH", os.environ.get("PATH", ""))
    candidates: list[Path] = []
    if node_bin:
        candidates.append(Path(node_bin))
    else:
        found = shutil.which("node", path=base_path)
        if found:
            candidates.append(Path(found))
        candidates.extend(
            (
                Path("/opt/homebrew/opt/node@24/bin/node"),
                Path("/usr/local/opt/node@24/bin/node"),
            )
        )

    observed: list[str] = []
    node_path: Path | None = None
    node_version: str | None = None
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            observed.append(f"{resolved}=not-executable")
            continue
        candidate_environment = dict(environment or {})
        candidate_environment["PATH"] = f"{resolved.parent}:{base_path}"
        version = _tool_version(resolved, candidate_environment)
        match = re.fullmatch(r"v24\.(\d+)\.(\d+)", version)
        if match is not None and int(match.group(1)) >= 15:
            node_path = resolved
            node_version = version
            break
        observed.append(f"{resolved}={version}")
        if node_bin:
            break
    if node_path is None or node_version is None:
        detail = ", ".join(observed) or "no executable Node candidate"
        raise HarnessError(f"{purpose} requires Node >=24.15.0 <25; resolved {detail}")

    resolved_environment = dict(environment or {})
    resolved_environment["PATH"] = f"{node_path.parent}:{base_path}"
    npm_found = shutil.which("npm", path=resolved_environment["PATH"])
    if npm_found is None:
        raise HarnessError(f"{purpose} requires npm beside the reviewed Node runtime")
    npm_path = Path(npm_found).resolve()
    try:
        npm_path.relative_to(node_path.parent.parent)
    except ValueError as exc:
        raise HarnessError(
            f"{purpose} requires npm from the reviewed Node runtime"
        ) from exc
    npm_version = _tool_version(npm_path, resolved_environment)
    npm_match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", npm_version)
    if (
        npm_match is None
        or int(npm_match.group(1)) != 11
        or int(npm_match.group(2)) < 6
    ):
        raise HarnessError(
            f"{purpose} requires npm >=11.6.0 <12; resolved {npm_path} as {npm_version}"
        )

    python_path = Path(sys.executable).resolve()
    python_version = _tool_version(python_path, resolved_environment)
    if not re.fullmatch(r"Python 3\.14\.\d+", python_version):
        raise HarnessError(
            f"{purpose} requires Python >=3.14.0 <3.15; "
            f"resolved {python_path} as {python_version}"
        )
    toolchain = {
        "node_path": str(node_path),
        "node_version": node_version,
        "node_sha256": sha256_file(node_path),
        "npm_path": str(npm_path),
        "npm_version": npm_version,
        "npm_sha256": sha256_file(npm_path),
        "python_path": str(python_path),
        "python_version": python_version,
        "python_sha256": sha256_file(python_path),
    }
    return resolved_environment, toolchain


def _tool_version(path: Path, environment: dict[str, str]) -> str:
    process = subprocess.run(
        [str(path), "--version"],
        check=False,
        capture_output=True,
        text=True,
        env=clean_environment(environment),
    )
    version = process.stdout.strip() or process.stderr.strip()
    if process.returncode != 0 or not version:
        raise HarnessError(f"cannot resolve tool version for {path}: {version}")
    return version


def _browser_summary(
    evidence_dir: Path, report_path: Path, report: dict[str, Any]
) -> dict[str, Any]:
    """Build browser metadata only from files that hash and fully decode now."""

    screenshots = report.get("screenshots", {})
    if not isinstance(screenshots, dict):
        raise HarnessError("browser visual report screenshots must be an object")
    desktop: list[dict[str, Any]] = []
    narrow: list[dict[str, Any]] = []
    for name, metadata in screenshots.items():
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or not name.endswith(".png")
        ):
            raise HarnessError(f"unsafe browser screenshot name: {name}")
        if not isinstance(metadata, dict):
            raise HarnessError(f"invalid browser screenshot metadata: {name}")
        screenshot = evidence_dir / name
        actual_hash = sha256_file(screenshot) if screenshot.is_file() else None
        if actual_hash is None or actual_hash != metadata.get("sha256"):
            raise HarnessError(f"browser screenshot hash mismatch: {name}")
        width, height, _channels, _rows = _png_pixels(screenshot)
        item = {
            "path": str(screenshot),
            "sha256": actual_hash,
            "width": width,
            "height": height,
        }
        (narrow if "narrow" in name else desktop).append(item)
    results = report.get("results", [])
    return {
        "status": report.get("status", "UNDETERMINED"),
        "output_directory": str(evidence_dir),
        "report_path": str(report_path),
        "report_sha256": sha256_file(report_path),
        "assertion_count": len(results) if isinstance(results, list) else 0,
        "browser_engine": report.get("browser"),
        "desktop": desktop,
        "narrow": narrow,
        "console_errors": report.get("console_errors", []),
        "page_errors": report.get("page_errors", []),
        "request_failures": report.get("request_failures", []),
    }


def compare_browser_evidence(
    root: Path,
    run_dir: Path,
    *,
    before_label: str = "current",
    after_label: str = "after",
) -> dict[str, Any]:
    """Compare semantic results and pixels while ignoring report timestamps."""

    ensure_ephemeral_run_dir(run_dir, root)
    before_dir = run_dir / (
        "browser" if before_label == "current" else f"browser-{before_label}"
    )
    after_dir = run_dir / (
        "browser" if after_label == "current" else f"browser-{after_label}"
    )
    before_path = before_dir / "visual-results.json"
    after_path = after_dir / "visual-results.json"
    if not before_path.is_file() or not after_path.is_file():
        raise HarnessError("both before and after browser reports are required")
    current_binding = _current_run_binding(run_dir)
    for evidence_dir in (before_dir, after_dir):
        manifest = evidence_dir / "harness-binding.json"
        if not manifest.is_file() or load_json(manifest) != current_binding:
            raise HarnessError(
                f"browser evidence is missing or stale for current binding: {evidence_dir}"
            )
    before = json.loads(before_path.read_text("utf-8"))
    after = json.loads(after_path.read_text("utf-8"))
    mismatches: list[str] = []
    for label, report in ((before_label, before), (after_label, after)):
        if report.get("status") != "PASS":
            mismatches.append(f"{label}_status_not_pass")
        for field in ("console_errors", "page_errors", "request_failures"):
            if report.get(field):
                mismatches.append(f"{label}_{field}")
        if report.get("status") == "PASS":
            evidence_dir = before_dir if label == before_label else after_dir
            report_path = before_path if label == before_label else after_path
            try:
                summary = _browser_summary(evidence_dir, report_path, report)
                _validate_browser_evidence(run_dir, current_binding, summary)
            except HarnessError as exc:
                mismatches.append(f"{label}_incomplete:{exc}")
    for field in (
        "status",
        "browser",
        "viewports",
        "states",
        "console_errors",
        "page_errors",
        "request_failures",
    ):
        if before.get(field) != after.get(field):
            mismatches.append(field)

    def normalize_fixture_value(value: Any) -> Any:
        if isinstance(value, str):
            value = re.sub(
                r"/member/quests/view_[0-9a-f]+",
                "/member/quests/<fixture-id>",
                value,
            )
            value = re.sub(
                r"tenant-adminme-gate7-visual-[0-9]+-[A-Za-z0-9_-]+",
                "tenant-adminme-gate7-visual-<fixture>",
                value,
            )
            return value
        if isinstance(value, list):
            return [normalize_fixture_value(item) for item in value]
        if isinstance(value, dict):
            return {key: normalize_fixture_value(item) for key, item in value.items()}
        return value

    def assertion_identity(item: dict[str, Any]) -> tuple[str, Any, Any]:
        name = re.sub(
            r"/member/quests/view_[0-9a-f]+",
            "/member/quests/<fixture-id>",
            str(item.get("name")),
        )
        return name, item.get("status"), normalize_fixture_value(item.get("detail"))

    before_assertions = [assertion_identity(item) for item in before.get("results", [])]
    after_assertions = [assertion_identity(item) for item in after.get("results", [])]
    if before_assertions != after_assertions:
        mismatches.append("browser_assertions")
    before_shots = before.get("screenshots", {})
    after_shots = after.get("screenshots", {})
    pixel_differences: list[str] = []
    pixel_comparisons: dict[str, dict[str, Any]] = {}
    structure_differences: list[str] = []
    if set(before_shots) != set(after_shots):
        mismatches.append("screenshot_names")
    else:
        for name in before_shots:
            before_png = before_dir / name
            after_png = after_dir / name
            if not before_png.is_file() or not after_png.is_file():
                mismatches.append(f"screenshot_missing:{name}")
                continue
            before_hash = sha256_file(before_png)
            after_hash = sha256_file(after_png)
            if before_shots[name].get("sha256") != before_hash:
                mismatches.append(f"screenshot_hash:{before_label}:{name}")
            if after_shots[name].get("sha256") != after_hash:
                mismatches.append(f"screenshot_hash:{after_label}:{name}")
            comparison = _png_pixel_delta(before_png, after_png)
            pixel_comparisons[name] = comparison
            if comparison["changed_pixels"] != 0:
                pixel_differences.append(name)
                mismatches.append(f"screenshot_pixels:{name}")
        for name in before_shots:
            before_png = before_dir / name
            after_png = after_dir / name
            if _png_dimensions(before_png) != _png_dimensions(after_png):
                structure_differences.append(name)
        if structure_differences:
            mismatches.append("screenshot_dimensions")
    check = {
        "check_id": "gate7-browser-equivalence",
        "tier": "journey",
        "command": "deterministic comparison of pre-change and post-change Gate 7 reports",
        "status": "PASS" if not mismatches else "FAIL",
        "exit_code": 0 if not mismatches else 1,
        "duration_seconds": 0,
        "evidence_class": "local_candidate",
    }
    report = {
        "schema_version": 1,
        "binding": current_binding,
        "check": check,
        "status": "PASS" if not mismatches else "FAIL",
        "before": str(before_path),
        "after": str(after_path),
        "mismatches": mismatches,
        "pixel_differences": pixel_differences,
        "pixel_comparisons": pixel_comparisons,
        "screenshot_structure_differences": structure_differences,
        "classification": "EXACT" if not mismatches else "REGRESSION_OR_UNDETERMINED",
        "required_minimums": {
            "assertions": GATE7_MIN_ASSERTIONS,
            "screenshots": GATE7_MIN_SCREENSHOTS,
        },
    }
    comparison_path = run_dir / "browser-comparison.json"
    write_json(comparison_path, report)
    receipt_path = run_dir / "check-receipts/journey-gate7-browser-equivalence.json"
    receipt_path.parent.mkdir(exist_ok=True)
    write_json(
        receipt_path,
        {
            "schema_version": 1,
            "binding": current_binding,
            "check": check,
            "log": {
                "path": comparison_path.relative_to(run_dir).as_posix(),
                "sha256": sha256_file(comparison_path),
            },
        },
    )
    return {**check, "receipt": receipt_path.relative_to(run_dir).as_posix()}


def _current_run_binding(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "outcome-contract.json"
    if not path.is_file():
        raise HarnessError("run lacks outcome-contract.json binding")
    value = load_json(path).get("binding")
    if not isinstance(value, dict):
        raise HarnessError("run outcome binding is invalid")
    return value


def _png_dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()[:24]
    if len(data) != 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise HarnessError(f"invalid PNG screenshot: {path}")
    return struct.unpack(">II", data[16:24])


def _png_pixels(path: Path) -> tuple[int, int, int, list[bytes]]:
    """Decode non-interlaced 8-bit RGB/RGBA screenshots without extra deps."""

    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise HarnessError(f"invalid PNG screenshot: {path}")
    position = 8
    compressed: list[bytes] = []
    header: tuple[int, int, int, int, int, int, int] | None = None
    while position < len(data):
        length = struct.unpack(">I", data[position : position + 4])[0]
        kind = data[position + 4 : position + 8]
        value = data[position + 8 : position + 8 + length]
        position += length + 12
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", value)
        elif kind == b"IDAT":
            compressed.append(value)
    if header is None:
        raise HarnessError(f"PNG lacks IHDR: {path}")
    width, height, depth, color, compression, filtering, interlace = header
    if (depth, color, compression, filtering, interlace) not in {
        (8, 2, 0, 0, 0),
        (8, 6, 0, 0, 0),
    }:
        raise HarnessError(f"unsupported PNG screenshot encoding: {path}")
    channels = 3 if color == 2 else 4
    stride = width * channels
    raw = zlib.decompress(b"".join(compressed))
    rows: list[bytes] = []
    previous = bytearray(stride)
    cursor = 0
    for _ in range(height):
        filter_kind = raw[cursor]
        cursor += 1
        row = bytearray(raw[cursor : cursor + stride])
        cursor += stride
        for index in range(stride):
            left = row[index - channels] if index >= channels else 0
            above = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_kind == 1:
                predictor = left
            elif filter_kind == 2:
                predictor = above
            elif filter_kind == 3:
                predictor = (left + above) // 2
            elif filter_kind == 4:
                estimate = left + above - upper_left
                distances = (
                    abs(estimate - left),
                    abs(estimate - above),
                    abs(estimate - upper_left),
                )
                predictor = (
                    left
                    if distances[0] <= distances[1] and distances[0] <= distances[2]
                    else above
                    if distances[1] <= distances[2]
                    else upper_left
                )
            elif filter_kind == 0:
                predictor = 0
            else:
                raise HarnessError(f"unsupported PNG filter {filter_kind}: {path}")
            row[index] = (row[index] + predictor) & 255
        rows.append(bytes(row))
        previous = row
    return width, height, channels, rows


def _png_pixel_delta(before: Path, after: Path) -> dict[str, Any]:
    left = _png_pixels(before)
    right = _png_pixels(after)
    if left[:3] != right[:3]:
        raise HarnessError("pixel comparison requires equal PNG dimensions and format")
    width, height, channels = left[:3]
    changed = 0
    max_delta = 0
    for before_row, after_row in zip(left[3], right[3], strict=True):
        for offset in range(0, len(before_row), channels):
            delta = max(
                abs(first - second)
                for first, second in zip(
                    before_row[offset : offset + channels],
                    after_row[offset : offset + channels],
                    strict=True,
                )
            )
            if delta:
                changed += 1
                max_delta = max(max_delta, delta)
    return {
        "changed_pixels": changed,
        "total_pixels": width * height,
        "changed_pixel_ratio": changed / (width * height),
        "max_channel_delta": max_delta,
        "required_changed_pixels": 0,
        "required_max_channel_delta": 0,
    }


def checks_as_json(checks: list[CheckSpec]) -> str:
    return json.dumps(
        [
            {"check_id": item.check_id, "tier": item.tier, "command": item.command}
            for item in checks
        ],
        indent=2,
        sort_keys=True,
    )


def shell_preview(checks: list[CheckSpec]) -> str:
    return "\n".join(shlex.join(["bash", "-c", item.command]) for item in checks)
