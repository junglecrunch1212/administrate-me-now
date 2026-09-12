"""Boundary tests for the disposable model Gateway launcher; no model calls."""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from scripts.acceptance import run_triage_model_lab as lab


@pytest.mark.parametrize(
    "system,runner", [("Darwin", "github-hosted"), ("Linux", "self-hosted")]
)
def test_gateway_refuses_family_or_self_hosted_execution(monkeypatch, system, runner):
    monkeypatch.setattr(lab.platform, "system", lambda: system)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", runner)
    with pytest.raises(ValueError, match="hosted Linux"):
        lab.validate_host()


def test_gateway_refuses_an_unapproved_revision(monkeypatch):
    monkeypatch.setattr(lab.platform, "system", lambda: "Linux")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.delenv("ADMINME_TEST_MODE", raising=False)
    monkeypatch.setenv("ADMINME_MODEL_LAB_APPROVED_SHA", "b" * 40)
    monkeypatch.setattr(
        lab.subprocess, "check_output", lambda *args, **kwargs: "a" * 40
    )
    with pytest.raises(ValueError, match="revision"):
        lab.validate_host()


def test_gateway_configuration_has_only_the_reviewed_lab_route(tmp_path: Path):
    config = lab.gateway_config(tmp_path, "synthetic-gateway-token")
    assert config["agents"]["list"] == [
        {"id": "main", "tools": {"allow": ["llm-task"]}}
    ]
    assert config["plugins"]["allow"] == ["llm-task"]
    route = config["plugins"]["entries"]["llm-task"]["config"]
    assert "defaultProvider" not in route
    assert "defaultModel" not in route
    assert config["plugins"]["entries"]["llm-task"]["llm"] == {
        "allowedCompletionModels": [lab.MODEL]
    }
    assert config["agents"]["defaults"]["model"] == {"primary": lab.MODEL}
    assert config["channels"] == {}
    assert config["cron"]["enabled"] is False
    assert config["agents"]["defaults"]["heartbeat"] == {"every": "0m"}
    assert config["browser"]["enabled"] is False
    assert config["gateway"]["bind"] == "loopback"
    assert "OPENAI_API_KEY" not in str(config)


def test_gateway_failure_is_retained_without_secrets(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "bootstrap").mkdir(parents=True)
    archive = tmp_path / "openclaw-source.tar.gz"
    archive.write_bytes(b"synthetic-archive")
    source = tmp_path / ("openclaw-" + "c" * 40)
    source.mkdir()
    (source / "openclaw.mjs").touch()
    pin = {
        "commit": "c" * 40,
        "version": "fixture",
        "source_archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    }
    (repo / "bootstrap/versions.json").write_text(json.dumps({"openclaw": pin}))
    output = tmp_path / "report.json"
    monkeypatch.setattr(lab, "REPO_ROOT", repo)
    monkeypatch.setattr(lab, "validate_host", lambda **kwargs: "a" * 40)
    monkeypatch.setattr(
        lab.secrets, "token_urlsafe", lambda count: "synthetic-gateway-secret"
    )
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-provider-secret")
    monkeypatch.setattr(
        lab.sys, "argv", ["lab", "--source", str(source), "--output", str(output)]
    )

    def fail_config(*args, **kwargs):
        raise subprocess.CalledProcessError(
            1,
            args[0],
            output="synthetic-gateway-secret",
            stderr="synthetic-provider-secret",
        )

    monkeypatch.setattr(lab.subprocess, "run", fail_config)
    assert lab.main() == 1
    text = output.read_text()
    report = json.loads(text)
    assert report["gateway_evidence"]["failure"]["phase"] == "configuration_validation"
    assert report["gateway_evidence"]["runtime_registered_llm_task"] is False
    assert "synthetic-gateway-secret" not in text
    assert "synthetic-provider-secret" not in text
    assert output.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob("triage-gateway-*"))
