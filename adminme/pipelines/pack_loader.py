"""
Pipeline pack loader.

Distinct from ``adminme.lib.skill_runner.pack_loader`` — pipeline packs
have a different shape (no SKILL.md, no schemas/, an importable handler
that exposes a class instead of a function). Mirrors the import-by-path
pattern of the skill loader so the two read consistently.

Pack shape (per [BUILD.md L4] lines 1259-1262 + [REFERENCE_EXAMPLES.md
§2]):

    <pack_root>/
      pipeline.yaml         # manifest
      handler.py            # module exposing runtime.class
      tests/                # optional

``pipeline.yaml`` minimum keys: ``pack.id``, ``pack.version``,
``pack.kind: pipeline``, ``runtime.entrypoint`` (file under pack_root),
``runtime.class`` (class name). Optional: ``triggers`` (events/schedule/
proactive), ``depends_on``, ``events_emitted``.

Caches by ``(pack_id, version)``; tests can call ``invalidate_cache()``
to drop the cache between runs.
"""

from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from adminme.pipelines.base import Pipeline, PipelinePackLoadError, Triggers

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoadedPipelinePack:
    pack_id: str
    version: str
    manifest: dict[str, Any]
    triggers: Triggers
    events_emitted: list[str]
    instance: Pipeline
    pack_root: Path


_cache: dict[tuple[str, str], LoadedPipelinePack] = {}


def invalidate_cache() -> None:
    """Test hook — drop every cached pack."""
    _cache.clear()


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open() as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as exc:
        raise PipelinePackLoadError(f"failed to read yaml at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PipelinePackLoadError(f"yaml at {path} did not parse to a mapping")
    return data


def _import_handler_module(handler_path: Path, pack_id: str) -> Any:
    spec = importlib.util.spec_from_file_location(
        f"adminme.pipeline_handlers.{pack_id.replace(':', '_').replace('/', '_')}",
        handler_path,
    )
    if spec is None or spec.loader is None:
        raise PipelinePackLoadError(f"could not build import spec for {handler_path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 — surface any handler-import error
        raise PipelinePackLoadError(
            f"handler import failed at {handler_path}: {exc}"
        ) from exc
    return module


def load_pipeline_pack(pack_root: Path) -> LoadedPipelinePack:
    """Load a pipeline pack from disk. Returns a cached ``LoadedPipelinePack``
    if the pack has been loaded before (keyed on ``pack_id`` + ``version``)."""
    pack_root = Path(pack_root)
    pipeline_yaml = pack_root / "pipeline.yaml"
    if not pipeline_yaml.exists():
        raise PipelinePackLoadError(f"missing pipeline.yaml: {pipeline_yaml}")

    manifest = _read_yaml(pipeline_yaml)
    pack_block = manifest.get("pack")
    if not isinstance(pack_block, dict):
        raise PipelinePackLoadError("pipeline.yaml: top-level `pack:` block missing")

    pack_id = pack_block.get("id")
    version = pack_block.get("version")
    kind = pack_block.get("kind")
    if not isinstance(pack_id, str) or not pack_id:
        raise PipelinePackLoadError("pipeline.yaml: pack.id missing or not a string")
    if not isinstance(version, str) or not version:
        raise PipelinePackLoadError("pipeline.yaml: pack.version missing or not a string")
    if kind != "pipeline":
        raise PipelinePackLoadError(
            f"pipeline.yaml: pack.kind must be 'pipeline', got {kind!r}"
        )

    cache_key = (pack_id, version)
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    runtime = manifest.get("runtime")
    if not isinstance(runtime, dict):
        raise PipelinePackLoadError("pipeline.yaml: top-level `runtime:` block missing")
    entrypoint = runtime.get("entrypoint")
    class_name = runtime.get("class")
    if not isinstance(entrypoint, str) or not entrypoint:
        raise PipelinePackLoadError(
            "pipeline.yaml: runtime.entrypoint missing or not a string"
        )
    if not isinstance(class_name, str) or not class_name:
        raise PipelinePackLoadError(
            "pipeline.yaml: runtime.class missing or not a string"
        )

    handler_path = pack_root / entrypoint
    if not handler_path.exists():
        raise PipelinePackLoadError(
            f"pipeline.yaml: runtime.entrypoint not found: {handler_path}"
        )

    module = _import_handler_module(handler_path, pack_id)
    cls = getattr(module, class_name, None)
    if cls is None:
        raise PipelinePackLoadError(
            f"runtime.class {class_name!r} not found in {handler_path}"
        )
    try:
        instance = cls()
    except Exception as exc:  # noqa: BLE001 — surface ctor errors as load errors
        raise PipelinePackLoadError(
            f"failed to instantiate {class_name!r} from {handler_path}: {exc}"
        ) from exc

    if not isinstance(instance, Pipeline):
        raise PipelinePackLoadError(
            f"runtime.class {class_name!r} does not implement Pipeline protocol "
            f"(missing async handle(event, ctx) and/or required attributes)"
        )

    triggers_block = manifest.get("triggers") or {}
    if not isinstance(triggers_block, dict):
        raise PipelinePackLoadError("pipeline.yaml: triggers must be a mapping")
    triggers: Triggers = {
        "events": triggers_block.get("events"),
        "schedule": triggers_block.get("schedule"),
        "proactive": triggers_block.get("proactive"),
    }

    events_emitted_raw = manifest.get("events_emitted") or []
    if not isinstance(events_emitted_raw, list):
        raise PipelinePackLoadError(
            "pipeline.yaml: events_emitted must be a list"
        )
    events_emitted = [str(x) for x in events_emitted_raw]

    loaded = LoadedPipelinePack(
        pack_id=pack_id,
        version=version,
        manifest=manifest,
        triggers=triggers,
        events_emitted=events_emitted,
        instance=instance,
        pack_root=pack_root,
    )
    _cache[cache_key] = loaded
    return loaded


__all__ = [
    "LoadedPipelinePack",
    "invalidate_cache",
    "load_pipeline_pack",
]
