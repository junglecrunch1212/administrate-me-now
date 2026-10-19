"""Hosted-only predecessor composition for fixed synthetic currentness cases."""

from __future__ import annotations

import copy
import json
import platform
import sys
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

import adminme.runtime.application as application
from adminme.events.envelope import EventEnvelope
from tests.fixtures.source_assessment_revision_worker import (
    AUTH,
    runtime_factory,
    settings,
    settle,
    snapshot,
)


class Model:
    def __init__(self, outputs):
        self.outputs = outputs
        self.calls = []

    async def respond(self, request, args):
        pack = args["input"]["context_pack"]
        alias = pack["current_source_id"]
        [current] = [
            message for message in pack["messages"] if message["source_id"] == alias
        ]
        output = copy.deepcopy(self.outputs[current["body_text"]])

        def resolve(value):
            if isinstance(value, dict):
                return {key: resolve(item) for key, item in value.items()}
            if isinstance(value, list):
                return [resolve(item) for item in value]
            return alias if value == "@current" else value

        output = resolve(output)
        self.calls.append(output)
        return httpx.Response(
            200,
            request=request,
            json={
                "ok": True,
                "result": {
                    "details": {
                        "json": output,
                        "provider": "fixture",
                        "model": "synthetic-currentness-predecessor",
                    }
                },
            },
        )


def main():
    root, instance, fixture, result = map(Path, sys.argv[1:])
    assert Path(application.__file__).resolve().is_relative_to(root.resolve())
    inputs = json.loads(fixture.read_text())
    model = Model(inputs["outputs"])
    application.build_fixture_runtime = runtime_factory(model)
    config = settings(instance, root, selected=inputs["selected"])
    with TestClient(application.create_application(config)) as client:
        runtime = client.app.state.runtime
        client.portal.call(settle, runtime)
        for value in inputs["sources"]:
            client.portal.call(
                runtime.events.write, EventEnvelope.model_validate(value)
            )
            client.portal.call(settle, runtime)
        captured = snapshot(client)
        captured["model_calls"] = model.calls
        captured["python_version"] = platform.python_version()
        if inputs.get("recovery_target"):
            recovery = client.post(
                "/api/system/source-assessment-recoveries",
                headers=AUTH,
                json={
                    "request_id": "fixture-currentness-predecessor-budget",
                    "targets": [inputs["recovery_target"]],
                },
            )
            captured["recovery"] = {
                "status": recovery.status_code,
                "detail": recovery.json().get("detail"),
            }
            assert snapshot(client)["events"] == captured["events"]
        captured["readiness"] = client.get("/api/system/ready").json()
        result.write_text(json.dumps(captured))


if __name__ == "__main__":
    main()
