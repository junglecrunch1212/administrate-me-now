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
HELPER_COMPONENT_PATHS = frozenset(
    {
        "bootstrap/install-member-bridge.sh",
        "bootstrap/member_bridge_helper_app.py",
        "bootstrap/member_bridge_component.py",
        "bootstrap/member_bridge_permission_controller.py",
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
    return selected


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
        if compatibility_contract == PERMISSION_RECOVERY_COMPATIBILITY:
            # This is a mixed central/helper release, never a smaller CI scope.
            # The public runtime fingerprint remains conservative. Only these
            # versioned central protocol implementation paths may differ; the
            # delivered archive still contains the exact retained bytes.
            for path in sorted(component_inventory.keys() | runtime_inventory.keys()):
                candidate = component_inventory.get(path)
                retained = runtime_inventory.get(path)
                if candidate == retained or not any(
                    is_runtime_input(path, mode=entry["mode"])
                    for entry in (candidate, retained)
                    if entry is not None
                ):
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
                _retained_edge_inventory(runtime_inventory)
            )
            if retained_edge_fingerprint != _fingerprint(
                _retained_edge_inventory(component_inventory)
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
