"""Tests for :mod:`adminme.lib.pack_registry.parser`.

Covers backward-compatibility (existing pack.yaml files parse cleanly),
forward-compatibility (new manifests with capabilities populate the
model), and error reporting (missing file, malformed yaml, non-mapping
top level).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from adminme.lib.pack_registry import (
    AdapterManifest,
    ManifestParseError,
    parse_adapter_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[4]


# ---------- Backward-compat: existing pack.yaml files parse cleanly ----------

def test_parse_existing_gmail_pack_yaml_yields_empty_capabilities() -> None:
    """Gmail pack.yaml predates the framework — capabilities default []."""
    pack_dir = REPO_ROOT / "packs" / "adapters" / "messaging" / "gmail"
    manifest = parse_adapter_manifest(pack_dir)
    assert isinstance(manifest, AdapterManifest)
    assert manifest.capabilities == []
    # All other extension fields default; no incidental fields polluted.
    assert manifest.write_capabilities == []
    assert manifest.sensitivity_default == "normal"


def test_parse_existing_plaid_pack_yaml_yields_cat_c_capability() -> None:
    """Plaid pack.yaml was retrofitted in prompt 11r-b: the manifest now
    declares one Cat-C capability with ``money_flow.plaid_synced`` in
    event_families. ``sensitivity_default: privileged`` is now declared
    in the manifest (matches the hardcoded class-attribute floor — the
    manifest layer of the triple-lock per [§6 invariant 10])."""
    pack_dir = REPO_ROOT / "packs" / "adapters" / "financial" / "plaid"
    manifest = parse_adapter_manifest(pack_dir)
    assert len(manifest.capabilities) == 1
    cap = manifest.capabilities[0]
    assert cap.kind == "Cat-C"
    assert cap.runtime == "central"
    assert cap.event_families == ["money_flow.plaid_synced"]
    assert manifest.sensitivity_default == "privileged"


def test_parse_existing_reminders_pack_yaml_yields_empty_capabilities() -> None:
    pack_dir = REPO_ROOT / "packs" / "adapters" / "reminders" / "apple-eventkit"
    manifest = parse_adapter_manifest(pack_dir)
    assert manifest.capabilities == []
    assert manifest.write_capabilities == []


# ---------- Forward-compat: new manifests populate capabilities ----------

def test_parse_new_manifest_with_capabilities(tmp_path: Path) -> None:
    pack_yaml = tmp_path / "pack.yaml"
    pack_yaml.write_text(
        """\
pack:
  id: adapter:test
  version: 1.0.0
  kind: adapter
capabilities:
  - kind: Cat-B
    runtime: central
    event_families: [reminder, list_item]
write_capabilities:
  - create_list
  - toggle_completion
sensitivity_default: sensitive
sharing_model_discriminator: list_owner_scope
""",
        encoding="utf-8",
    )
    manifest = parse_adapter_manifest(tmp_path)
    assert len(manifest.capabilities) == 1
    assert manifest.capabilities[0].kind == "Cat-B"
    assert manifest.capabilities[0].runtime == "central"
    assert manifest.write_capabilities == ["create_list", "toggle_completion"]
    assert manifest.sensitivity_default == "sensitive"
    assert manifest.sharing_model_discriminator == "list_owner_scope"


def test_parse_multi_capability_manifest_pm_35(tmp_path: Path) -> None:
    """Apple Notes-checklists shape: Cat-D + Cat-B."""
    (tmp_path / "pack.yaml").write_text(
        """\
pack:
  id: adapter:notes-checklists
  version: 1.0.0
  kind: adapter
capabilities:
  - kind: Cat-D
    runtime: bridge
    event_families: [note]
  - kind: Cat-B
    runtime: bridge
    event_families: [list_item]
write_capabilities: [toggle_checklist_item]
owner_scope_overridability_at_install: false
""",
        encoding="utf-8",
    )
    manifest = parse_adapter_manifest(tmp_path)
    assert {c.kind for c in manifest.capabilities} == {"Cat-D", "Cat-B"}
    assert all(c.runtime == "bridge" for c in manifest.capabilities)
    assert manifest.owner_scope_overridability_at_install is False


# ---------- Error reporting ----------

def test_missing_pack_yaml_raises(tmp_path: Path) -> None:
    with pytest.raises(ManifestParseError, match="not found"):
        parse_adapter_manifest(tmp_path)


def test_malformed_yaml_raises(tmp_path: Path) -> None:
    (tmp_path / "pack.yaml").write_text(
        ": this : is : not : valid : yaml :", encoding="utf-8"
    )
    with pytest.raises(ManifestParseError, match="failed to read"):
        parse_adapter_manifest(tmp_path)


def test_non_mapping_yaml_raises(tmp_path: Path) -> None:
    (tmp_path / "pack.yaml").write_text(
        "- just\n- a\n- list\n", encoding="utf-8"
    )
    with pytest.raises(ManifestParseError, match="did not parse to a mapping"):
        parse_adapter_manifest(tmp_path)


def test_invalid_capability_kind_raises(tmp_path: Path) -> None:
    (tmp_path / "pack.yaml").write_text(
        """\
capabilities:
  - kind: Cat-Z
    runtime: central
""",
        encoding="utf-8",
    )
    with pytest.raises(ManifestParseError, match="failed validation"):
        parse_adapter_manifest(tmp_path)
