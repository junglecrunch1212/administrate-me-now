"""Secret-safe launchd target for the one AdministrateMe Python process."""

from __future__ import annotations

import json
import os
from pathlib import Path

import uvicorn

from adminme.runtime.application import RuntimeSettings, create_application
from bootstrap.contracts import DeploymentPaths, repository_root
from bootstrap.openclaw_activation import model_provider_configured
from bootstrap.secrets import secret_store


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
    actor_identities = adapter_config.get("openclaw_actor_identities", {})
    store = secret_store(paths)
    shared = store.get(references["openclaw_shared"]).decode("ascii")
    settings = RuntimeSettings.credential_free(
        paths.instance_dir,
        encryption_key=store.get(references["event_log"]),
        repository_root=Path(
            os.environ.get("ADMINME_REPOSITORY_ROOT", str(repository_root()))
        ),
        member_identities=member_identities,
        member_roles=roles,
        deterministic_openclaw_fixture=False,
        openclaw_enabled=bool(actor_identities),
        openclaw_shared_secret=shared,
        openclaw_actor_identities=actor_identities,
        openclaw_gateway_token=shared,
        openclaw_model_configured=model_provider_configured(paths),
        member_chat_enabled_members=frozenset(
            adapter_config.get("member_chat_enabled_members", [])
        ),
        child_chat_enabled_members=frozenset(
            adapter_config.get("child_chat_enabled_members", [])
        ),
    )
    uvicorn.run(
        create_application(settings),
        host="127.0.0.1",
        port=3330,
        access_log=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
