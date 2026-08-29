"""Secret-safe launchd target for the one AdministrateMe Python process."""

from __future__ import annotations

import json
import os
from pathlib import Path

import uvicorn

from adminme.runtime.application import RuntimeSettings, create_application
from bootstrap.candidate_readiness import (
    CandidateReadinessError,
    capture_deployment_snapshot,
)
from bootstrap.contracts import DeploymentPaths, repository_root
from bootstrap.openclaw_activation import model_provider_configured
from bootstrap.secrets import secret_store
from bootstrap.topology_readiness import (
    load_deployment_evidence,
    sample_host_topology,
)


def main() -> None:
    root_value = os.environ.get("ADMINME_INSTANCE_ROOT")
    if not root_value:
        raise RuntimeError("ADMINME_INSTANCE_ROOT is required")
    paths = DeploymentPaths.from_root(Path(root_value), production=True)
    references = json.loads(
        (paths.instance_dir / "config" / "secret-references.json").read_text("utf-8")
    )["references"]
    mapping = json.loads(
        (paths.instance_dir / "config" / "member-mapping.json").read_text("utf-8")
    )
    adapter_config = json.loads(
        (paths.instance_dir / "config" / "adapters.json").read_text("utf-8")
    )
    roles = {member["member_id"]: member["role"] for member in mapping["members"]}
    member_identities = {
        member["tailscale_identity"]: member["member_id"]
        for member in mapping["members"]
        if member.get("tailscale_identity")
    }
    normalized_member_identities = {
        identity.casefold(): member_id
        for identity, member_id in member_identities.items()
    }
    bridge_mapping_path = paths.instance_dir / "bridges" / "member-mapping.json"
    bridge_enrollments = (
        json.loads(bridge_mapping_path.read_text("utf-8"))
        if bridge_mapping_path.is_file()
        else {}
    )
    for bridge_identity, record in bridge_enrollments.items():
        member_id = record.get("member_id") if isinstance(record, dict) else None
        if (
            not isinstance(bridge_identity, str)
            or not isinstance(member_id, str)
            or normalized_member_identities.get(bridge_identity.casefold()) != member_id
            or record.get("owner_scope") != f"private:{member_id}"
            or record.get("role") != roles.get(member_id)
        ):
            raise RuntimeError(
                "bridge enrollment mapping disagrees with the reviewed household identity map"
            )
    actor_identities = adapter_config.get("openclaw_actor_identities", {})
    chat_members = frozenset(adapter_config.get("member_chat_enabled_members", []))
    store = secret_store(paths)
    shared = store.get(references["openclaw_shared"]).decode("ascii")
    host_topology = sample_host_topology(
        paths,
        member_identities=member_identities,
    )
    try:
        deployment_snapshot = capture_deployment_snapshot(paths)
        host_topology["deployment_binding"] = {
            "release_commit": deployment_snapshot.predecessor_commit,
            "instance_fingerprint": deployment_snapshot.instance_fingerprint,
            "host_fingerprint": deployment_snapshot.host_fingerprint,
            "configuration_fingerprint": (
                deployment_snapshot.configuration_fingerprint
            ),
        }
    except CandidateReadinessError:
        # Missing/invalid immutable-release evidence blocks activation through
        # the shared readiness dimension; it does not suppress safe reads.
        host_topology["deployment_binding"] = {}
    deployment_evidence = load_deployment_evidence(paths)
    settings = RuntimeSettings.credential_free(
        paths.instance_dir,
        encryption_key=store.get(references["event_log"]),
        repository_root=Path(
            os.environ.get("ADMINME_REPOSITORY_ROOT", str(repository_root()))
        ),
        member_identities=member_identities,
        household_mapping=mapping,
        bridge_enrollments=bridge_enrollments,
        member_roles=roles,
        deterministic_openclaw_fixture=False,
        openclaw_enabled=bool(actor_identities or chat_members),
        openclaw_shared_secret=shared,
        openclaw_actor_identities=actor_identities,
        openclaw_gateway_token=shared,
        openclaw_model_configured=model_provider_configured(paths),
        openclaw_runtime_config_path=(
            paths.instance_dir / "openclaw" / "runtime" / "openclaw.json"
        ),
        member_chat_enabled_members=chat_members,
        child_chat_enabled_members=frozenset(
            adapter_config.get("child_chat_enabled_members", [])
        ),
        adapter_config=adapter_config,
        host_topology=host_topology,
        deployment_evidence=deployment_evidence,
        deployment_evidence_required=True,
    )
    uvicorn.run(
        create_application(settings),
        host="127.0.0.1",
        port=3330,
        # Tailscale Serve connects over loopback and supplies the authenticated
        # identity header. Do not rewrite request.client from forwarded headers;
        # the auth boundary must continue proving the immediate peer is local.
        proxy_headers=False,
        access_log=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
