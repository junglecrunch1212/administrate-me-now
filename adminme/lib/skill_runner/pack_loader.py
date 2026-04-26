"""Skill pack loader.

Parses the canonical pack shape per [REFERENCE_EXAMPLES.md §3] and
[BUILD.md §L4-continued]:

    <pack_root>/
      pack.yaml                    # manifest (id, version, model, etc.)
      SKILL.md                     # YAML frontmatter + markdown body
      schemas/input.schema.json
      schemas/output.schema.json
      prompt.jinja2                # rendered to llm-task `prompt` arg
      handler.py                   # optional; provides post_process(...)

The loader returns a `LoadedPack` dataclass; the wrapper consumes it.
Caches by `(pack_id, version)` so repeated calls inside one process don't
re-read disk for every skill invocation. Tests can call
`invalidate_cache()` between runs.
"""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError


class PackLoadError(RuntimeError):
    """Raised when a pack cannot be loaded — malformed yaml, missing
    required files, invalid schemas, or a bad handler.py module."""


@dataclass(frozen=True)
class LoadedPack:
    pack_id: str
    version: str
    manifest: dict[str, Any]
    skill_frontmatter: dict[str, Any]
    skill_body: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    prompt_template: str
    handler_post_process: Callable[..., Any] | None
    pack_root: Path


_cache: dict[tuple[str, str], LoadedPack] = {}


def invalidate_cache() -> None:
    """Test hook — drop every cached pack."""
    _cache.clear()


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open() as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as exc:
        raise PackLoadError(f"failed to read yaml at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PackLoadError(f"yaml at {path} did not parse to a mapping")
    return data


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise PackLoadError(f"failed to read json at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PackLoadError(f"json at {path} did not parse to an object")
    return data


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a markdown file with optional `---`-fenced YAML frontmatter
    into (frontmatter_dict, body_str). Per [cheatsheet Q5] frontmatter
    keys are single-line; we just lean on PyYAML for the parse."""
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return {}, text
    # Locate the closing '---' on its own line after the opening.
    after_open = stripped[3:]
    # Find the next line starting with '---'.
    lines = after_open.splitlines(keepends=True)
    yaml_lines: list[str] = []
    body_start: int | None = None
    for i, line in enumerate(lines):
        if line.strip() == "---":
            body_start = i + 1
            break
        yaml_lines.append(line)
    if body_start is None:
        raise PackLoadError("SKILL.md frontmatter unterminated (missing closing ---)")
    yaml_block = "".join(yaml_lines)
    try:
        meta = yaml.safe_load(yaml_block) or {}
    except yaml.YAMLError as exc:
        raise PackLoadError(f"SKILL.md frontmatter not valid yaml: {exc}") from exc
    if not isinstance(meta, dict):
        raise PackLoadError("SKILL.md frontmatter did not parse to a mapping")
    body = "".join(lines[body_start:])
    return meta, body


def _load_handler(handler_path: Path, pack_id: str) -> Callable[..., Any]:
    spec = importlib.util.spec_from_file_location(
        f"adminme.skill_handlers.{pack_id.replace(':', '_').replace('/', '_')}",
        handler_path,
    )
    if spec is None or spec.loader is None:
        raise PackLoadError(f"could not build import spec for {handler_path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 — surface any handler-import error
        raise PackLoadError(f"handler import failed at {handler_path}: {exc}") from exc
    fn = getattr(module, "post_process", None)
    if fn is None or not callable(fn):
        raise PackLoadError(
            f"handler at {handler_path} does not expose post_process(raw, inputs, ctx)"
        )
    return fn


def load_pack(pack_root: Path) -> LoadedPack:
    """Load a pack from disk, returning a cached `LoadedPack` if the
    pack has been loaded before (keyed on pack_id + version)."""
    pack_root = Path(pack_root)
    pack_yaml = pack_root / "pack.yaml"
    skill_md = pack_root / "SKILL.md"
    input_schema_path = pack_root / "schemas" / "input.schema.json"
    output_schema_path = pack_root / "schemas" / "output.schema.json"
    prompt_path = pack_root / "prompt.jinja2"
    handler_path = pack_root / "handler.py"

    for required in (pack_yaml, skill_md, input_schema_path, output_schema_path, prompt_path):
        if not required.exists():
            raise PackLoadError(f"missing required pack file: {required}")

    manifest = _read_yaml(pack_yaml)
    pack_block = manifest.get("pack")
    if not isinstance(pack_block, dict):
        raise PackLoadError("pack.yaml: top-level `pack:` block missing")
    pack_id = pack_block.get("id")
    version = pack_block.get("version")
    if not isinstance(pack_id, str) or not pack_id:
        raise PackLoadError("pack.yaml: pack.id missing or not a string")
    if not isinstance(version, str) or not version:
        raise PackLoadError("pack.yaml: pack.version missing or not a string")

    cache_key = (pack_id, version)
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        skill_text = skill_md.read_text(encoding="utf-8")
    except OSError as exc:
        raise PackLoadError(f"failed to read SKILL.md: {exc}") from exc
    frontmatter, body = _split_frontmatter(skill_text)

    input_schema = _read_json(input_schema_path)
    output_schema = _read_json(output_schema_path)
    for label, schema in (
        ("input.schema.json", input_schema),
        ("output.schema.json", output_schema),
    ):
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise PackLoadError(f"{label} is not a valid JSON Schema: {exc}") from exc

    try:
        prompt_template = prompt_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PackLoadError(f"failed to read prompt.jinja2: {exc}") from exc

    post_process: Callable[..., Any] | None = None
    if handler_path.exists():
        post_process = _load_handler(handler_path, pack_id)

    loaded = LoadedPack(
        pack_id=pack_id,
        version=version,
        manifest=manifest,
        skill_frontmatter=frontmatter,
        skill_body=body,
        input_schema=input_schema,
        output_schema=output_schema,
        prompt_template=prompt_template,
        handler_post_process=post_process,
        pack_root=pack_root,
    )
    _cache[cache_key] = loaded
    return loaded


__all__ = [
    "LoadedPack",
    "PackLoadError",
    "invalidate_cache",
    "load_pack",
]
