"""Known retained tooling rejection; no selected Doctor or history is rewritten."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from bootstrap.contracts import DeploymentPaths, atomic_write
from bootstrap import doctor_tooling as tooling
from bootstrap import service_settlement as settlement

ROOT = Path(__file__).resolve().parents[3]
OLD = "471d20ec71d40d59490ff763be524d73a3c3cb7d"
CURRENT = "c" * 40
OBSERVED = {
    "homebrew": "Homebrew 7.0.11",
    "node": "24.22.1",
    "npm": "11.19.1",
    "openclaw": "OpenClaw 2026.7.2",
    "python": "3.14.7",
}


def report(*, synthetic: bool = False):
    names = tooling.RETAINED_CHECKS | (
        {"canonical_synthetic_round_trip"} if synthetic else set()
    )
    return {
        "schema_version": 1,
        "status": "failed",
        "mode": "observation",
        "safe_to_attach": True,
        "live_provider_claims": [],
        "checks": [
            {
                "name": name,
                "ok": name != "runtime_and_package_versions",
                "detail": "retained diagnostic",
                "remediation": None,
                "evidence": copy.deepcopy(OBSERVED)
                if name == "runtime_and_package_versions"
                else {},
            }
            for name in sorted(names)
        ],
    }


def _fixture(tmp_path, monkeypatch):
    paths = DeploymentPaths.from_root(tmp_path / "instance", production=False)
    paths.create()
    release = paths.app_dir / "releases" / OLD
    atomic_write(
        release / "bootstrap/versions.json",
        subprocess.check_output(
            ["git", "show", f"{OLD}:bootstrap/versions.json"], cwd=ROOT
        ),
    )
    monkeypatch.setattr(
        tooling, "verify_retained_tooling_source", lambda *_args: CURRENT
    )
    return paths, release


@pytest.mark.parametrize("synthetic", [False, True])
def test_complete_original_failed_report_is_retained_in_distinct_decision(
    tmp_path, monkeypatch, synthetic
):
    paths, release = _fixture(tmp_path, monkeypatch)
    original = report(synthetic=synthetic)
    before = copy.deepcopy(original)
    result = tooling.retained_tooling_report(
        paths, release, ROOT, original, include_synthetic_proof=synthetic
    )
    assert original == before and original["status"] == "failed"
    assert result["status"] == "healthy"
    receipt = result.pop(tooling.COMPATIBILITY_KEY)
    assert receipt["original_report"] == original
    assert receipt["original_doctor_status"] == "failed"
    assert receipt["decision"] == "compatible"
    assert receipt["controller_commit"] == CURRENT and receipt["release_commit"] == OLD
    assert (
        receipt["original_report_sha256"]
        == hashlib.sha256(
            json.dumps(original, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    for old, new in zip(original["checks"], result["checks"], strict=True):
        if old["name"] == "runtime_and_package_versions":
            assert new["ok"] is True and new["evidence"] == old["evidence"]
        else:
            assert new == old


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "additional_failure",
        "unknown_check",
        "duplicate",
        "boolean",
        "synthetic_missing",
        "synthetic_failed",
        "schema",
        "claim",
        "python",
        "node",
        "npm",
        "homebrew",
        "openclaw",
        "unknown_release",
    ],
)
def test_other_faults_or_incomplete_reports_never_qualify(
    tmp_path, monkeypatch, mutation
):
    paths, release = _fixture(tmp_path, monkeypatch)
    original = report(synthetic=mutation == "synthetic_failed")
    synthetic = mutation in {"synthetic_missing", "synthetic_failed"}
    if mutation in OBSERVED:
        values = {
            "python": "3.15.0",
            "node": "26.0.0",
            "npm": "12.0.0",
            "homebrew": "Homebrew 8.0.0",
            "openclaw": "OpenClaw unknown",
        }
        next(
            c for c in original["checks"] if c["name"] == "runtime_and_package_versions"
        )["evidence"][mutation] = values[mutation]
    elif mutation == "missing":
        original["checks"].pop()
    elif mutation == "additional_failure":
        next(
            c
            for c in original["checks"]
            if c["name"] == "encrypted_event_log_and_migrations"
        )["ok"] = False
    elif mutation == "unknown_check":
        original["checks"].append({"name": "unknown", "ok": True})
    elif mutation == "duplicate":
        original["checks"].append(copy.deepcopy(original["checks"][0]))
    elif mutation == "boolean":
        original["checks"][0]["ok"] = 1
    elif mutation == "schema":
        original["schema_version"] = True
    elif mutation == "claim":
        original["live_provider_claims"] = ["unsupported"]
    elif mutation == "synthetic_failed":
        next(
            c
            for c in original["checks"]
            if c["name"] == "canonical_synthetic_round_trip"
        )["ok"] = False
    elif mutation == "unknown_release":
        release = release.parent / ("f" * 40)
    assert (
        tooling.retained_tooling_report(
            paths, release, ROOT, original, include_synthetic_proof=synthetic
        )
        is original
    )


@pytest.mark.parametrize("version", ["6.0.0", "7.0.11"])
def test_current_tooling_rule_accepts_only_compatible_tuple(version):
    assert tooling.tooling_versions_supported(
        {**OBSERVED, "homebrew": "Homebrew " + version}, "2026.7.2"
    )
    assert not tooling.tooling_versions_supported(
        {**OBSERVED, "node": None}, "2026.7.2"
    )


def test_immutable_predecessors_really_have_complete_contract_and_old_ceiling():
    for commit in tooling.RETAINED_DOCTORS:
        source = subprocess.check_output(
            ["git", "show", f"{commit}:bootstrap/doctor.py"], cwd=ROOT
        )
        assert hashlib.sha256(source).hexdigest() == tooling.RETAINED_DOCTOR_SHA256
        tree = ast.parse(source)
        names = {
            node.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "DoctorCheck"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        }
        assert names == tooling.RETAINED_CHECKS | {"canonical_synthetic_round_trip"}
        predicate = next(
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "version_ok" for t in node.targets
            )
        )
        # Execute only this archived pure expression, using the genuine failing
        # observed tuple. No archived runtime, service or provider is invoked.
        namespace = {
            "python_version": (3, 14, 7),
            "node_version": (24, 22, 1),
            "npm_version": (11, 19, 1),
            "brew_version": (7, 0, 11),
            "observed_versions": OBSERVED,
            "versions": {"openclaw": {"version": "2026.7.2"}},
        }
        assert (
            eval(
                compile(
                    ast.Expression(predicate), "archived-tooling-predicate", "eval"
                ),
                {},
                namespace,
            )
            is False
        )


def test_source_identity_and_predicate_hash_remain_mandatory(tmp_path, monkeypatch):
    paths = DeploymentPaths.from_root(tmp_path / "instance", production=False)
    paths.create()
    release = paths.app_dir / "releases" / OLD
    atomic_write(release / "bootstrap/doctor.py", b"changed")
    monkeypatch.setattr(tooling, "reviewed_candidate_commit", lambda _root: CURRENT)
    seen = []
    monkeypatch.setattr(
        tooling,
        "verify_release_source_identity",
        lambda *args, **kwargs: seen.append(kwargs),
    )
    with pytest.raises(ValueError, match="predicate source differs"):
        tooling.verify_retained_tooling_source(paths, release, ROOT)
    assert seen[0]["require_tracked_tree"] is True
    assert seen[0]["retained_by_commit"] == CURRENT
    with pytest.raises(ValueError, match="not qualified"):
        tooling.verify_retained_tooling_source(paths, release.parent / ("f" * 40), ROOT)


def test_selected_process_report_is_qualified_without_replacing_runtime(
    tmp_path, monkeypatch
):
    paths, release = _fixture(tmp_path, monkeypatch)
    atomic_write(release / ".venv/bin/python", b"fixture")
    original = report(synthetic=True)
    seen = []

    def run(argv, **kwargs):
        seen.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(original))

    monkeypatch.setattr(settlement.subprocess, "run", run)
    monkeypatch.setattr(
        settlement, "service_environment_path", lambda *_args: "/fixture/bin"
    )
    actual = settlement.run_release_doctor(
        paths, release, include_synthetic_proof=True, controller_root=ROOT
    )
    assert actual[tooling.COMPATIBILITY_KEY]["original_report"] == original
    assert seen[0][0][0] == str(release / ".venv/bin/python")
    assert seen[0][1]["cwd"] == release
    assert seen[0][1]["env"]["ADMINME_REPOSITORY_ROOT"] == str(release)


@pytest.mark.parametrize("bad", [False, True])
def test_preflight_reads_real_selected_tools_and_rejects_before_pause(
    tmp_path, monkeypatch, bad
):
    paths, release = _fixture(tmp_path, monkeypatch)
    atomic_write(release / "bootstrap/doctor.py", b"retained")
    monkeypatch.setattr(
        settlement, "service_environment_path", lambda *_args: "/selected/bin"
    )
    monkeypatch.setattr(
        settlement, "verify_retained_tooling_source", lambda *_args: CURRENT
    )
    seen = []

    def run(argv, **kwargs):
        seen.append((argv, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {**OBSERVED, "homebrew": "Homebrew 8.0.0"} if bad else OBSERVED
            ),
        )

    monkeypatch.setattr(settlement.subprocess, "run", run)
    if bad:
        with pytest.raises(ValueError, match="before service pause"):
            settlement.probe_release_tooling(paths, release, ROOT)
    else:
        assert (
            settlement.probe_release_tooling(paths, release, ROOT)["versions"]
            == OBSERVED
        )
    assert seen[0][0][0] == str(release / ".venv/bin/python")
    assert "Doctor(" not in seen[0][0][-1]
    assert seen[0][1]["env"]["PATH"] == "/selected/bin"
