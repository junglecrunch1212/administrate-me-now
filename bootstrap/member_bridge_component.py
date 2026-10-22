"""Fixed-source compatibility for a helper component and retained bridge runtime.

Unknown tracked paths are runtime inputs. Exclusions are deliberately narrow:
the helper's packaging implementation, test/CI inputs, and non-executable
governing prose directly under docs/refactor plus AGENTS.md. Package READMEs,
locks, configuration, interface code and other documents remain runtime inputs.
An explicit versioned mixed-release contract may retain an older exact archive
while the central protocol route differs; it never changes that CI fingerprint.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile
import tempfile

from bootstrap.source_identity import (
    GitTreeEntry,
    load_git_tree,
    materialize_git_tree,
    reviewed_candidate_commit,
)


HELPER_RUNTIME_COMPATIBILITY_CONTRACT = "administrateme-helper-runtime-compatibility-v1"
PERMISSION_RECOVERY_COMPATIBILITY = "bridge-permission-recovery-v1"
PERMISSION_RECOVERY_PROTOCOL = "administrateme-bridge-permission-recovery-v1"
PERMISSION_RECOVERY_CENTRAL_PATHS = frozenset(
    {"adminme/products/bridge/routers/ingest.py"}
)
PERMISSION_RECOVERY_SHARED_MODULE = "adminme/bridge_enrollment.py"
PERMISSION_RECOVERY_METHOD_RULE = "exact-reviewed-enrollment-coordinator-pair-v1"
PERMISSION_RECOVERY_RETAINED_MODULE_SHA256 = (
    "96caaabe2320f19a2201d94dd53bf93b06f2d12957d634519637cb1cbd6f220a"
)
PERMISSION_RECOVERY_CANDIDATE_MODULE_SHA256 = (
    "a6aa140fea3b3bdf63437eb818f1f9974b9cdd5f23fc09cb3352ee6b467a21e9"
)
# Independent hosted-probe correction; this is not part of the PR457 overlay.
PERMISSION_RECOVERY_GATEWAY_PROBE_PATH = (
    "openclaw/administrateme/scripts/check-cos-approval-gateway.mjs"
)
PERMISSION_RECOVERY_GATEWAY_PROBE_RULE = "hosted-gateway-startup-probe-456-v1"
PERMISSION_RECOVERY_GATEWAY_PROBE_RETAINED_SHA256 = (
    "d17351be538d5c8dd44b5fdbdf9b54f505da70018d56257daf60e633b832b901"
)
PERMISSION_RECOVERY_GATEWAY_PROBE_CANDIDATE_SHA256 = (
    "76ff3412dcf925d662377c5850f89f367f32392e355780f87f40c4f517766107"
)
# Closed reviewed central overlay. The retained edge never receives these bytes.
# Every path/presence/mode/hash must match as one coherent group before reuse.
PERMISSION_RECOVERY_COS_OVERLAY_CONTRACT = "cos-standing-replies-457-hooks-458-v1"
PERMISSION_RECOVERY_COS_RETAINED_COMMIT = "043ac68915b185be4ada771f6893a4d69c3f994b"
PERMISSION_RECOVERY_COS_CENTRAL_COMMIT = "6035a997614b52f537bb9ce91d24d01464d878a2"
PERMISSION_RECOVERY_COS_OVERLAY: dict[str, tuple[str | None, str]] = {
    "adminme/cos_channel_operation.py": (
        "dad867bca64ff6f294d8079fa98f33d6fe55f0c3c86ee9a6d312ed45c4180266",
        "bd7ca21ee320f289731a13bba6662b345f6c1d75382855d504987c643a395288",
    ),
    "adminme/cos_response_assurance.py": (
        "74531b1a4b21879ca6b9a7c290977c6ee8e58e0a127b34b1df41eb19d37d1489",
        "7f7fa9abc4698ef692e3788449eece25aac1ba7d54790cc6f3ae4c752554005c",
    ),
    "adminme/cos_self_replies.py": (
        None,
        "c667b67bbb91ae7f4670d342fddf19099ad93a6651cf34db9d1c7739a90b2a3e",
    ),
    "adminme/events/schemas/cos_channels.py": (
        "da0dd6e94e5192ed55dd678a0e9104bced5e464e37f18d4695410cf11802e832",
        "f0a3d53b5d04b2e4131115a65ba2257441718f149170a5175079a929e9bbe291",
    ),
    "adminme/events/schemas/cos_responses.py": (
        "0c73204ad9e3b2d1d6210ee4bff933d2b275d9df62ced3d5f3c7a8efd371ce5b",
        "ba3857a1fd740f6be29684ba3bf8030f666d156db0f87d7127e9628a73c8e18c",
    ),
    "adminme/historical_attribution.py": (
        "cd72392cf807cc6849555c7d404b85b69cf2be1e11c28d3d30be9fafcd091d67",
        "e13c3db91d65a52f19fbd6d2b6f08eb6e5d0797077535e8407cfbc75d7941404",
    ),
    "adminme/lifecycle_catalog.py": (
        "2238b47e2f7ed69d0f1ef7906f81a26423ef4dfe31684dc49fa4621fc8475b87",
        "f9558df42f530769ab1efcbb983378d7a60d051108ba5f0e3985d4fb3af8daff",
    ),
    "adminme/products/openclaw/cos_responses.py": (
        "8c70b96e7c62435805bd076657de3f51b568674ee6b85c66245640dd2249f65f",
        "2d99eb38bb11eaabd46c5afd8b4010a3f5800d70184e48b563698064767eac96",
    ),
    "adminme/products/openclaw/cos_self_replies.py": (
        None,
        "2ab96c84bcc80e06e51ddb2e4aed2d078a99e8a60e78f1cad7121eeeb6b98e8e",
    ),
    "adminme/runtime/application.py": (
        "eda3671a4cfdbda08a565ce521ab2afa13878d1198ca00378780e7b74a016422",
        "f66d009de9158221d7e4551f219a1208e69af936e851c381bcd89d47fd8cb9e1",
    ),
    "adminme/source_assurance.py": (
        "0592edb883f25d8d6e6d116cdd5707331351c3c4e32677f93a1aa05b3216ee6d",
        "78297d066998bff698b8d690d2d5eae8afff5780f4219c4ccda73c3b943daf4a",
    ),
    "bootstrap/cos_channel_activation.py": (
        "e686393e3baf0b444ed74cf1fbd0eee72c1e906e32d00a997031b5e19557d796",
        "6f66ecc10df1dbfb6551a3a1548be93a1657220cff1a19a54e91643d4c3046b5",
    ),
    "bootstrap/cos_reply_hooks.py": (
        None,
        "8b17bb5b50ae361d58f37565990971c6a7551941e770f3d0dcd9d32368e91890",
    ),
    "bootstrap/openclaw_tools.py": (
        "4ede4ef79721ad0ab2076c3d394b11ced3e91f9c23b602e47a88a57e990eb4dc",
        "8181443acb595849cfefa7dd0c7bab4c9f61477cfb110a4b8d5cd67de6d12812",
    ),
    "docs/refactor/evidence/event-lifecycle-catalog.json": (
        "b32ea2a138a21ffd08fc8e647de25847fc3c9e19821e8b76d8c44fa63e953434",
        "eaedd34e2d87dcddc77ddb093a0326ea3955295aff63fee146f4728a403a9d42",
    ),
    "openclaw/administrateme/openclaw.plugin.json": (
        "8ac97090995fc5afc2e87bc35ee0f69bd90b732157c06a56f380621ce7ccd52a",
        "6c7bdccf285af12aabb75a7eb220333ebf726a650ae61aeb4e08a72c5faa1d0e",
    ),
    "openclaw/administrateme/scripts/check-cos-imessage-text.mjs": (
        "5d8a0df6dcb0c25d615d9c280df8f4ffc9818dc91d72d07a6a637fc05a7fb2c8",
        "651ca7faab4398a724de606dfe8d24cb7d2a02fa20b61d25410f16e27ccb699a",
    ),
    "openclaw/administrateme/skills/administrateme-household/SKILL.md": (
        "d5623c9587e6e32e94b2647b84f0cd769950f08be46fa6a83aba2b9868831208",
        "35390c16a80ca0fcee50a1cc3c2de2fdccf970e3e402fe95395f2ae1551e8b82",
    ),
    "openclaw/administrateme/src/config.ts": (
        "2f1ac4d33f0d9357f973798b663a5a1914a26bc11bf0bfa0ad232473b13cdb08",
        "b6abe66ea858851824b87fbacb5320d58a8cc2d7ff9843298351ded01b448d73",
    ),
    "openclaw/administrateme/src/contract.ts": (
        "34ddb06bb18b3952425fb9cd76e40ba030aae490bc7cbcdf5bb34fb76495cda8",
        "d88c6bbb38452ad8aa2f5f3a3e6323b3a3e1c3bd27272aba589cfb03626ea505",
    ),
    "openclaw/administrateme/src/hooks.ts": (
        "c925395af1be5cebd132627ba0dcc8b820b83580ebf31a3d4866580b119a5b85",
        "6a4bcb0b7c4831843b67c016814c2c4f7c67dcac2ab3c0aacff4e8fb50439e08",
    ),
    "openclaw/administrateme/src/self-replies.ts": (
        None,
        "f8e44083e13975c967d806fb4e5a8a79ce40bbf4778b4dc9e1f93d8e3525ed42",
    ),
    "openclaw/administrateme/test/fixtures/openclaw-2026.7.2/plugin-entry.d.ts": (
        "5a8ef3477b9987d830548f4c4eac6a6df47e6a1a9ccb81cd7b96ddebe7c09633",
        "b8382bf2ef0d59959591adce4d7d24271957f7d3a7635894f0b5f504387c93eb",
    ),
    "openclaw/administrateme/test/self-replies.test.ts": (
        None,
        "20a32bb7e6f59d58a2ca4313504df0bf7196826241d46f41ba84b2b71914e926",
    ),
}

HELPER_COMPONENT_PATHS = frozenset(
    {
        "bootstrap/install-member-bridge.sh",
        "bootstrap/member_bridge_helper_app.py",
        "bootstrap/member_bridge_component.py",
        "bootstrap/member_bridge_permission_controller.py",
        "bootstrap/member_bridge_launch.py",
    }
)
NON_RUNTIME_PATHS = frozenset({"scripts/release_scope.py"})
NON_RUNTIME_PREFIXES = (".github/", "tests/", "bridge/tests/")
NON_RUNTIME_PROSE_PATHS = frozenset({"AGENTS.md"})
NON_RUNTIME_PROSE_DIRECTORY = "docs/refactor"


def is_runtime_input(path: str, *, mode: str = "100644") -> bool:
    prose = PurePosixPath(path)
    if mode == "100644" and (
        path in NON_RUNTIME_PROSE_PATHS
        or (
            prose.parent.as_posix() == NON_RUNTIME_PROSE_DIRECTORY
            and prose.suffix == ".md"
        )
    ):
        return False
    return (
        path not in HELPER_COMPONENT_PATHS
        and path not in NON_RUNTIME_PATHS
        and not path.startswith(NON_RUNTIME_PREFIXES)
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _inventory(
    source: Path, entries: tuple[GitTreeEntry, ...]
) -> dict[str, dict[str, str]]:
    return {
        entry.path: {"mode": entry.mode, "sha256": _sha256(source / entry.path)}
        for entry in entries
    }


def _fingerprint(inventory: dict[str, dict[str, str]]) -> str:
    selected = {
        path: coordinate
        for path, coordinate in inventory.items()
        if is_runtime_input(path, mode=coordinate["mode"])
    }
    return hashlib.sha256(
        json.dumps(selected, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _retained_edge_inventory(
    inventory: dict[str, dict[str, str]],
    *,
    reviewed_differences: dict[str, dict[str, str] | None],
) -> dict[str, dict[str, str]]:
    selected = {
        path: dict(coordinate)
        for path, coordinate in inventory.items()
        if path not in PERMISSION_RECOVERY_CENTRAL_PATHS
    }
    if PERMISSION_RECOVERY_SHARED_MODULE in selected:
        coordinate = selected[PERMISSION_RECOVERY_SHARED_MODULE]
        if coordinate["mode"] != "100644" or coordinate["sha256"] not in {
            PERMISSION_RECOVERY_RETAINED_MODULE_SHA256,
            PERMISSION_RECOVERY_CANDIDATE_MODULE_SHA256,
        }:
            raise RuntimeError(
                "retained edge compatibility changes unsupported runtime inputs"
            )
        # Only this reviewed pair differs solely inside the central coordinator's
        # configure_capability body. No generic class or source exemption exists.
        coordinate["sha256"] = PERMISSION_RECOVERY_RETAINED_MODULE_SHA256
    # This normalization is supplied only after every reviewed pair is checked.
    # Newly central-only files are absent from the exact retained archive.
    for path, retained in reviewed_differences.items():
        if retained is None:
            selected.pop(path, None)
        else:
            selected[path] = dict(retained)
    return selected


def _reviewed_cos_overlay(
    candidate: dict[str, dict[str, str]],
    retained: dict[str, dict[str, str]],
) -> dict[str, dict[str, str] | None]:
    """Allow only the complete reviewed old-to-new central source group."""

    if all(
        candidate.get(path) == retained.get(path)
        for path in PERMISSION_RECOVERY_COS_OVERLAY
    ):
        return {}
    normalization: dict[str, dict[str, str] | None] = {}
    for path, (old_sha, new_sha) in PERMISSION_RECOVERY_COS_OVERLAY.items():
        previous = {"mode": "100644", "sha256": old_sha} if old_sha else None
        current = {"mode": "100644", "sha256": new_sha}
        if retained.get(path) != previous or candidate.get(path) != current:
            raise RuntimeError(
                "retained edge compatibility changes unsupported runtime inputs: "
                "reviewed central overlay is incomplete or changed"
            )
        normalization[path] = previous
    return normalization


def _reviewed_gateway_probe(
    candidate: dict[str, dict[str, str]],
    retained: dict[str, dict[str, str]],
) -> dict[str, dict[str, str] | None]:
    """Permit one exact hosted-only probe correction, independently of PR457."""

    path = PERMISSION_RECOVERY_GATEWAY_PROBE_PATH
    if candidate.get(path) == retained.get(path):
        return {}
    previous = {
        "mode": "100644",
        "sha256": PERMISSION_RECOVERY_GATEWAY_PROBE_RETAINED_SHA256,
    }
    current = {
        "mode": "100644",
        "sha256": PERMISSION_RECOVERY_GATEWAY_PROBE_CANDIDATE_SHA256,
    }
    if retained.get(path) != previous or candidate.get(path) != current:
        raise RuntimeError(
            "retained edge compatibility changes unsupported runtime inputs: "
            "reviewed hosted Gateway probe changed"
        )
    return {path: previous}


def runtime_interface_fingerprint(repository: Path, revision: str) -> str:
    """Hash fixed Git bytes and executable modes, never archive export filters."""

    entries, _ = load_git_tree(repository, revision)
    with tempfile.TemporaryDirectory(prefix="adminme-helper-interface-") as temporary:
        source = Path(temporary) / "source"
        materialize_git_tree(repository, revision, source)
        return _fingerprint(_inventory(source, entries))


@dataclass(frozen=True)
class HelperComponentInputs:
    compatibility: dict[str, object]
    component_sources: dict[str, dict[str, str]]
    component_payloads: dict[str, bytes]
    runtime_inventory: dict[str, dict[str, str]]


def inspect_helper_component(
    repository: Path,
    component_commit: str,
    runtime_commit: str,
    *,
    compatibility_contract: str | None = None,
) -> HelperComponentInputs:
    """Verify one clean source coordinate and its unchanged retained runtime.

    The default requires complete runtime-input equality. The explicit recovery
    contract permits only its fixed central protocol deltas and retains all edge
    inputs. This proves source integrity, not GitHub approval or live protocol
    support; those require protected delivery and authenticated negotiation.
    """

    if compatibility_contract not in {None, PERMISSION_RECOVERY_COMPATIBILITY}:
        raise RuntimeError("unsupported retained runtime compatibility contract")
    if reviewed_candidate_commit(repository) != component_commit:
        raise RuntimeError("helper component source does not match the clean checkout")
    component_entries, _ = load_git_tree(repository, component_commit)
    runtime_entries, _ = load_git_tree(
        repository, runtime_commit, retained_by_commit=component_commit
    )
    with tempfile.TemporaryDirectory(prefix="adminme-helper-component-") as temporary:
        component_source = Path(temporary) / "component"
        runtime_source = Path(temporary) / "runtime"
        materialize_git_tree(repository, component_commit, component_source)
        materialize_git_tree(repository, runtime_commit, runtime_source)
        component_inventory = _inventory(component_source, component_entries)
        runtime_inventory = _inventory(runtime_source, runtime_entries)
        component_fingerprint = _fingerprint(component_inventory)
        runtime_fingerprint = _fingerprint(runtime_inventory)
        central_differences: list[dict[str, object]] = []
        retained_edge_fingerprint: str | None = None
        reviewed_overlay: dict[str, dict[str, str] | None] = {}
        reviewed_probe: dict[str, dict[str, str] | None] = {}
        if compatibility_contract == PERMISSION_RECOVERY_COMPATIBILITY:
            # This compatibility rule preserves retained edge bytes; the separate
            # release selector decides the required central/helper CI scope.
            # The public runtime fingerprint remains conservative. Only these
            # versioned central protocol implementation paths may differ; the
            # delivered archive still contains the exact retained bytes.
            reviewed_overlay = _reviewed_cos_overlay(
                component_inventory, runtime_inventory
            )
            reviewed_probe = _reviewed_gateway_probe(
                component_inventory, runtime_inventory
            )
            reviewed_differences = reviewed_overlay | reviewed_probe
            for path in sorted(component_inventory.keys() | runtime_inventory.keys()):
                candidate = component_inventory.get(path)
                retained = runtime_inventory.get(path)
                if candidate == retained or not any(
                    is_runtime_input(path, mode=entry["mode"])
                    for entry in (candidate, retained)
                    if entry is not None
                ):
                    continue
                if path in reviewed_differences:
                    central_differences.append(
                        {
                            "path": path,
                            "retained": retained,
                            "central_candidate": candidate,
                            "compatibility_rule": (
                                PERMISSION_RECOVERY_COS_OVERLAY_CONTRACT
                                if path in reviewed_overlay
                                else PERMISSION_RECOVERY_GATEWAY_PROBE_RULE
                            ),
                        }
                    )
                    continue
                if (
                    path not in PERMISSION_RECOVERY_CENTRAL_PATHS
                    and path != PERMISSION_RECOVERY_SHARED_MODULE
                ) or (
                    candidate is None
                    or retained is None
                    or candidate["mode"] != "100644"
                    or retained["mode"] != "100644"
                ):
                    raise RuntimeError(
                        "retained edge compatibility changes unsupported runtime inputs"
                    )
                difference: dict[str, object] = {
                    "path": path,
                    "retained": retained,
                    "central_candidate": candidate,
                }
                if path == PERMISSION_RECOVERY_SHARED_MODULE:
                    if (
                        retained["sha256"] != PERMISSION_RECOVERY_RETAINED_MODULE_SHA256
                        or candidate["sha256"]
                        != PERMISSION_RECOVERY_CANDIDATE_MODULE_SHA256
                    ):
                        raise RuntimeError(
                            "retained edge compatibility changes unsupported runtime inputs"
                        )
                    difference.update(
                        {
                            "compatibility_rule": PERMISSION_RECOVERY_METHOD_RULE,
                            "reviewed_central_method_body": "BridgeEnrollmentCoordinator.configure_capability",
                        }
                    )
                central_differences.append(difference)
            retained_edge_fingerprint = _fingerprint(
                _retained_edge_inventory(
                    runtime_inventory, reviewed_differences=reviewed_differences
                )
            )
            if retained_edge_fingerprint != _fingerprint(
                _retained_edge_inventory(
                    component_inventory, reviewed_differences=reviewed_differences
                )
            ):
                raise RuntimeError("retained edge inputs differ")
        elif component_fingerprint != runtime_fingerprint:
            raise RuntimeError("helper component changes retained runtime inputs")
        if not HELPER_COMPONENT_PATHS.issubset(component_inventory):
            raise RuntimeError("helper component source is incomplete")
        provenance_paths = HELPER_COMPONENT_PATHS | {
            "bootstrap/__init__.py",
            "bootstrap/contracts.py",
            "bootstrap/source_identity.py",
        }
        if not provenance_paths.issubset(component_inventory):
            raise RuntimeError("helper build dependencies are incomplete")
        payloads = {
            path: (component_source / path).read_bytes() for path in provenance_paths
        }
        for path, coordinate in component_inventory.items():
            local = repository / path
            metadata = local.lstat()
            if (
                not local.is_file()
                or local.is_symlink()
                or any(
                    parent.is_symlink()
                    for parent in local.parents
                    if parent != repository and repository in parent.parents
                )
                or _sha256(local) != coordinate["sha256"]
                or bool(metadata.st_mode & 0o111) != (coordinate["mode"] == "100755")
            ):
                raise RuntimeError(
                    "helper checkout bytes or modes differ from its commit"
                )
        sources = {
            path: component_inventory[path] for path in sorted(HELPER_COMPONENT_PATHS)
        }
        source_digest = hashlib.sha256(
            json.dumps(sources, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        compatibility: dict[str, object] = {
            "contract": HELPER_RUNTIME_COMPATIBILITY_CONTRACT,
            "helper_component_commit": component_commit,
            "runtime_commit": runtime_commit,
            "runtime_input_sha256": runtime_fingerprint,
            "runtime_input_count": sum(
                is_runtime_input(path, mode=coordinate["mode"])
                for path, coordinate in runtime_inventory.items()
            ),
            "helper_component_source_sha256": source_digest,
            "excluded_component_paths": sorted(HELPER_COMPONENT_PATHS),
            "excluded_non_runtime_paths": sorted(NON_RUNTIME_PATHS),
            "excluded_non_runtime_prefixes": list(NON_RUNTIME_PREFIXES),
            "excluded_non_runtime_prose_paths": sorted(NON_RUNTIME_PROSE_PATHS),
            "excluded_non_runtime_prose_directory": NON_RUNTIME_PROSE_DIRECTORY,
            "excluded_non_runtime_prose_rule": "direct-child-markdown-mode-100644",
            "runtime_inputs_unchanged": component_fingerprint == runtime_fingerprint,
        }
        if compatibility_contract == PERMISSION_RECOVERY_COMPATIBILITY:
            compatibility.update(
                {
                    "contract": "administrateme-helper-retained-edge-compatibility-v1",
                    "compatibility_mode": compatibility_contract,
                    "required_central_protocol": PERMISSION_RECOVERY_PROTOCOL,
                    "required_central_release_commit": component_commit,
                    "central_candidate_runtime_input_sha256": component_fingerprint,
                    "retained_edge_input_sha256": retained_edge_fingerprint,
                    "retained_edge_inputs_unchanged": True,
                    "allowed_central_difference_paths": sorted(
                        PERMISSION_RECOVERY_CENTRAL_PATHS
                    ),
                    "allowed_shared_module_hash_pair": {
                        "path": PERMISSION_RECOVERY_SHARED_MODULE,
                        "retained_sha256": PERMISSION_RECOVERY_RETAINED_MODULE_SHA256,
                        "central_candidate_sha256": PERMISSION_RECOVERY_CANDIDATE_MODULE_SHA256,
                    },
                    "central_differences": central_differences,
                }
            )
            if reviewed_overlay:
                compatibility["reviewed_central_overlay"] = {
                    "contract": PERMISSION_RECOVERY_COS_OVERLAY_CONTRACT,
                    "retained_reference_commit": PERMISSION_RECOVERY_COS_RETAINED_COMMIT,
                    "central_reference_commit": PERMISSION_RECOVERY_COS_CENTRAL_COMMIT,
                    "paths": sorted(reviewed_overlay),
                    "complete_group_verified": True,
                }
            if reviewed_probe:
                compatibility["reviewed_hosted_gateway_probe"] = {
                    "contract": PERMISSION_RECOVERY_GATEWAY_PROBE_RULE,
                    "path": PERMISSION_RECOVERY_GATEWAY_PROBE_PATH,
                    "retained_sha256": PERMISSION_RECOVERY_GATEWAY_PROBE_RETAINED_SHA256,
                    "central_candidate_sha256": PERMISSION_RECOVERY_GATEWAY_PROBE_CANDIDATE_SHA256,
                }
    if reviewed_candidate_commit(repository) != component_commit:
        raise RuntimeError("helper component source changed during inspection")
    return HelperComponentInputs(compatibility, sources, payloads, runtime_inventory)


def verify_runtime_archive(
    archive_path: Path, inventory: dict[str, dict[str, str]]
) -> None:
    """Reject exported omissions, substitutions, links and executable-mode drift."""

    seen: set[str] = set()
    seen_directories: set[str] = set()
    expected_directories = {
        parent.as_posix()
        for path in inventory
        for parent in PurePosixPath(path).parents
        if parent != PurePosixPath(".")
    }
    with tarfile.open(archive_path, mode="r:") as archive:
        for member in archive:
            if member.isdir():
                directory = member.name.rstrip("/")
                if (
                    directory not in expected_directories
                    or member.name not in {directory, directory + "/"}
                    or directory in seen_directories
                    or member.mode not in {0o755, 0o775}
                ):
                    raise RuntimeError(
                        "runtime archive contains an unexpected directory"
                    )
                seen_directories.add(directory)
                continue
            expected = inventory.get(member.name)
            if (
                not member.isfile()
                or expected is None
                or member.name in seen
                or member.mode
                not in (
                    {0o755, 0o775} if expected["mode"] == "100755" else {0o644, 0o664}
                )
            ):
                raise RuntimeError(
                    "runtime archive does not match the fixed runtime tree"
                )
            source = archive.extractfile(member)
            if source is None:
                raise RuntimeError("runtime archive member is unavailable")
            digest = hashlib.sha256()
            with source:
                while block := source.read(1024 * 1024):
                    digest.update(block)
            if digest.hexdigest() != expected["sha256"]:
                raise RuntimeError(
                    "runtime archive bytes differ from the fixed runtime tree"
                )
            seen.add(member.name)
    if seen != inventory.keys() or seen_directories != expected_directories:
        raise RuntimeError("runtime archive omits fixed runtime inputs")


__all__ = [
    "HELPER_COMPONENT_PATHS",
    "HELPER_RUNTIME_COMPATIBILITY_CONTRACT",
    "PERMISSION_RECOVERY_COMPATIBILITY",
    "PERMISSION_RECOVERY_PROTOCOL",
    "PERMISSION_RECOVERY_CENTRAL_PATHS",
    "NON_RUNTIME_PATHS",
    "NON_RUNTIME_PREFIXES",
    "NON_RUNTIME_PROSE_DIRECTORY",
    "NON_RUNTIME_PROSE_PATHS",
    "HelperComponentInputs",
    "inspect_helper_component",
    "is_runtime_input",
    "runtime_interface_fingerprint",
    "verify_runtime_archive",
]
