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


def load_builtin_adapters(
    config: dict[str, Any], *, repository_root: Path
) -> list[Adapter]:
    catalog = _catalog(repository_root / "packs" / "adapters")
    adapters: list[Adapter] = []
    for index, entry in enumerate(config.get("adapters") or []):
        if not isinstance(entry, dict):
            raise ValueError(f"adapter entry {index} must be a mapping")
        kind = str(entry.get("kind") or "")
        resolved = catalog.get(kind)
        if resolved is None:
            raise ValueError(f"configured adapter pack not found: {kind!r}")
        pack_dir, manifest = resolved
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
        account = adapter_config.get("provider_account_id") or adapter_config.get(
            "subject_email"
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
