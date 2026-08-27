"""Production loader for built-in adapter packs.

Adapter packs are intentionally not importable Python packages because their
directory names mirror provider product names.  This loader resolves a
configured pack manifest, validates its JSON configuration schema, and loads
the declared entrypoint without teaching the runtime about each provider.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from adminme.adapters.base import Adapter
from adminme.provider_bindings import (
    MEMBER_BRIDGE_ONLY_ADAPTER_KINDS,
    validate_provider_bindings,
)


def load_builtin_adapters(
    config: dict[str, Any],
    *,
    repository_root: Path,
    member_roles: dict[str, str] | None = None,
    household_mapping: dict[str, Any] | None = None,
    service_principal_ids: set[str] | None = None,
    service_principal_provider_authorizations: dict[str, frozenset[str]] | None = None,
    validate_bindings: bool = False,
) -> list[Adapter]:
    if validate_bindings:
        validate_provider_bindings(
            config,
            member_roles=member_roles or {},
            household_mapping=household_mapping or {},
            service_principal_ids=service_principal_ids or set(),
            service_principal_provider_authorizations=(
                service_principal_provider_authorizations or {}
            ),
        )
    catalog = _catalog(repository_root / "packs" / "adapters")
    adapters: list[Adapter] = []
    for index, entry in enumerate(config.get("adapters") or []):
        if not isinstance(entry, dict):
            raise ValueError(f"adapter entry {index} must be a mapping")
        if entry.get("enabled") is False:
            continue
        kind = str(entry.get("kind") or "")
        normalized_kind = kind.removeprefix("adapter:")
        if normalized_kind in MEMBER_BRIDGE_ONLY_ADAPTER_KINDS:
            raise ValueError(
                f"configured adapter pack is member-bridge-only and cannot run "
                f"in the central supervisor: {kind!r}"
            )
        resolved = catalog.get(kind)
        if resolved is None:
            raise ValueError(f"configured adapter pack not found: {kind!r}")
        pack_dir, manifest = resolved
        targets = manifest.get("deployment_targets")
        capabilities = manifest.get("capabilities")
        bridge_only = targets == ["bridge"] or (
            isinstance(capabilities, list)
            and bool(capabilities)
            and all(
                isinstance(capability, dict) and capability.get("runtime") == "bridge"
                for capability in capabilities
            )
        )
        if bridge_only:
            raise ValueError(
                f"configured adapter pack is bridge-only and cannot run in the "
                f"central supervisor: {kind!r}"
            )
        adapter_config = dict(entry.get("config") or {})
        schema_path = pack_dir / "config.schema.json"
        if schema_path.exists():
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            errors = sorted(
                Draft202012Validator(schema).iter_errors(adapter_config),
                key=lambda error: list(error.path),
            )
            if errors:
                rendered = "; ".join(error.message for error in errors[:5])
                raise ValueError(f"invalid config for {kind}: {rendered}")
        runtime = manifest.get("runtime") or {}
        entrypoint = str(runtime.get("entrypoint") or "adapter.py")
        class_name = str(runtime.get("class") or "Adapter")
        module_name = "_adminme_builtin_adapter_" + "_".join(
            part.replace("-", "_") for part in pack_dir.parts[-3:]
        )
        module = sys.modules.get(module_name)
        if module is None:
            spec = importlib.util.spec_from_file_location(
                module_name, pack_dir / entrypoint
            )
            if spec is None or spec.loader is None:
                raise ImportError(
                    f"cannot load adapter entrypoint {pack_dir / entrypoint}"
                )
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        adapter_class = getattr(module, class_name)
        adapter = adapter_class(config=adapter_config)
        account = (
            adapter_config.get("provider_account_id")
            or adapter_config.get("subject_email")
            or adapter_config.get("user_email")
        )
        member = adapter_config.get("member_id")
        configured_id = entry.get("id") or ":".join(
            str(value)
            for value in (kind, member, account, index)
            if value not in (None, "")
        )
        setattr(adapter, "instance_name", str(configured_id))
        adapters.append(adapter)
    return adapters


def _catalog(root: Path) -> dict[str, tuple[Path, dict[str, Any]]]:
    result: dict[str, tuple[Path, dict[str, Any]]] = {}
    for manifest_path in sorted(root.rglob("pack.yaml")):
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        if not isinstance(manifest, dict):
            continue
        pack = manifest.get("pack") or {}
        identifiers = {
            manifest_path.parent.name,
            str(pack.get("id") or ""),
            str(pack.get("name") or ""),
        }
        identifiers |= {
            identifier.removeprefix("adapter:") for identifier in identifiers
        }
        for identifier in identifiers - {""}:
            result[identifier] = (manifest_path.parent, manifest)
    return result


__all__ = ["load_builtin_adapters"]
