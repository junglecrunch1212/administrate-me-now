"""Synthetic production owner-control and exact-package API setup for test labs."""

from copy import deepcopy
from uuid import uuid4
import httpx

from adminme.finance.publication import content_hash
from tests.acceptance.test_finance_activation import packet_for


def witness_projection(value):
    witness = deepcopy(value["evidence"][0])
    witness.update(
        evidence_id="hbsource1:" + "a" * 64, artifact_ref="hbsource1:" + "a" * 64
    )
    value["evidence"].append(witness)
    value["evidence_ids"].append(witness["evidence_id"])
    value["content_sha256"] = content_hash(value)
    return value


async def activate(
    app, headers, publication, planned, package, *, import_data=True, advisor=True
):
    """No legacy static enable flags and no direct private ingress calls."""
    assert not app.state.runtime.settings.finance_publications_enabled
    assert not app.state.runtime.settings.finance_model_members
    manifest, packet = packet_for(publication, planned, package)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://synthetic"
    ) as client:
        control = await client.put(
            "/api/member/v1/settings/finance",
            headers=headers,
            json={
                "request_id": str(uuid4()),
                "expected_revision": 0,
                "binding": manifest["binding"],
                "books_enabled": True,
                "cos_analysis_enabled": advisor,
            },
        )
        assert control.status_code == 200, control.text
        if import_data:
            approval = await client.post(
                "/api/member/v1/finance/import-approvals",
                headers=headers,
                json={
                    "request_id": str(uuid4()),
                    "expected_policy_revision": control.json()["revision"],
                    "manifest": manifest,
                },
            )
            assert approval.status_code == 200, approval.text
            result = await client.post(
                "/api/member/v1/finance/imports/"
                + approval.json()["approval_event_id"],
                headers=headers,
                json=packet,
            )
            assert result.status_code == 200, result.text
    return manifest, packet
