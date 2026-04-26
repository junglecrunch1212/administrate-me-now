"""Tests for `adminme.lib.skill_runner.pack_loader`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adminme.lib.skill_runner import pack_loader
from adminme.lib.skill_runner.pack_loader import PackLoadError, invalidate_cache, load_pack

REPO_ROOT = Path(__file__).resolve().parents[2]
CLASSIFY_TEST_PACK = REPO_ROOT / "packs" / "skills" / "classify_test"


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    invalidate_cache()


def test_load_classify_test_pack_manifest() -> None:
    pack = load_pack(CLASSIFY_TEST_PACK)
    assert pack.pack_id == "skill:classify_test"
    assert pack.version == "0.1.0"
    assert pack.manifest["pack"]["name"] == "Classify test"
    # Frontmatter from SKILL.md surfaces sensitivity + scope manifest fields.
    assert pack.skill_frontmatter["sensitivity_required"] == "normal"
    assert pack.skill_frontmatter["context_scopes_required"] == []
    assert pack.skill_frontmatter["outbound_affecting"] is False
    assert pack.skill_frontmatter["timeout_seconds"] == 5
    assert pack.skill_frontmatter["provider_preferences"] == [
        "anthropic/claude-haiku-4-5"
    ]


def test_load_classify_test_pack_schemas_validate_samples() -> None:
    from jsonschema import Draft202012Validator

    pack = load_pack(CLASSIFY_TEST_PACK)
    Draft202012Validator(pack.input_schema).validate({"text": "hi"})
    Draft202012Validator(pack.output_schema).validate(
        {"is_thing": True, "confidence": 0.5}
    )
    with pytest.raises(Exception):
        Draft202012Validator(pack.input_schema).validate({})  # missing text
    with pytest.raises(Exception):
        Draft202012Validator(pack.output_schema).validate(
            {"is_thing": True, "confidence": 1.5}  # out of range
        )


def test_load_classify_test_pack_no_handler_loaded() -> None:
    pack = load_pack(CLASSIFY_TEST_PACK)
    assert pack.handler_post_process is None


def test_load_returns_cached_instance_on_second_call() -> None:
    pack_a = load_pack(CLASSIFY_TEST_PACK)
    pack_b = load_pack(CLASSIFY_TEST_PACK)
    assert pack_a is pack_b


def test_invalidate_cache_forces_reload() -> None:
    pack_a = load_pack(CLASSIFY_TEST_PACK)
    invalidate_cache()
    pack_b = load_pack(CLASSIFY_TEST_PACK)
    assert pack_a is not pack_b
    # Same content, just a new dataclass instance.
    assert pack_a.pack_id == pack_b.pack_id


def test_malformed_pack_yaml_raises_pack_load_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad_pack"
    (bad / "schemas").mkdir(parents=True)
    (bad / "pack.yaml").write_text(": this : is : not : valid : yaml :", encoding="utf-8")
    (bad / "SKILL.md").write_text("---\nname: x\n---\n", encoding="utf-8")
    (bad / "schemas" / "input.schema.json").write_text(
        json.dumps({"type": "object"}), encoding="utf-8"
    )
    (bad / "schemas" / "output.schema.json").write_text(
        json.dumps({"type": "object"}), encoding="utf-8"
    )
    (bad / "prompt.jinja2").write_text("x", encoding="utf-8")
    with pytest.raises(PackLoadError):
        load_pack(bad)


def test_handler_loaded_when_handler_py_present(tmp_path: Path) -> None:
    pack = tmp_path / "with_handler"
    (pack / "schemas").mkdir(parents=True)
    (pack / "pack.yaml").write_text(
        "pack:\n  id: skill:test_handler\n  version: 0.0.1\n", encoding="utf-8"
    )
    (pack / "SKILL.md").write_text(
        "---\nname: test_handler\nsensitivity_required: normal\ncontext_scopes_required: []\nprovider_preferences:\n  - anthropic/claude-haiku-4-5\nmax_tokens: 50\ntemperature: 0.0\ntimeout_seconds: 5\n---\nbody\n",
        encoding="utf-8",
    )
    (pack / "schemas" / "input.schema.json").write_text(
        json.dumps({"type": "object"}), encoding="utf-8"
    )
    (pack / "schemas" / "output.schema.json").write_text(
        json.dumps({"type": "object"}), encoding="utf-8"
    )
    (pack / "prompt.jinja2").write_text("ignore", encoding="utf-8")
    (pack / "handler.py").write_text(
        "def post_process(raw, inputs, ctx):\n    return {'tag': 'ok', **raw}\n",
        encoding="utf-8",
    )
    loaded = load_pack(pack)
    assert loaded.handler_post_process is not None
    assert loaded.handler_post_process({"value": 1}, {}, None) == {
        "tag": "ok",
        "value": 1,
    }


def test_handler_without_post_process_raises(tmp_path: Path) -> None:
    pack = tmp_path / "broken_handler"
    (pack / "schemas").mkdir(parents=True)
    (pack / "pack.yaml").write_text(
        "pack:\n  id: skill:broken_handler\n  version: 0.0.1\n", encoding="utf-8"
    )
    (pack / "SKILL.md").write_text("---\nname: broken_handler\n---\n", encoding="utf-8")
    (pack / "schemas" / "input.schema.json").write_text(
        json.dumps({"type": "object"}), encoding="utf-8"
    )
    (pack / "schemas" / "output.schema.json").write_text(
        json.dumps({"type": "object"}), encoding="utf-8"
    )
    (pack / "prompt.jinja2").write_text("x", encoding="utf-8")
    (pack / "handler.py").write_text("# no post_process here\n", encoding="utf-8")
    with pytest.raises(PackLoadError, match="post_process"):
        load_pack(pack)


def test_invalid_json_schema_raises(tmp_path: Path) -> None:
    pack = tmp_path / "bad_schema"
    (pack / "schemas").mkdir(parents=True)
    (pack / "pack.yaml").write_text(
        "pack:\n  id: skill:bad_schema\n  version: 0.0.1\n", encoding="utf-8"
    )
    (pack / "SKILL.md").write_text("---\nname: bad_schema\n---\n", encoding="utf-8")
    # `type` must be a string or list of strings; an integer is invalid.
    (pack / "schemas" / "input.schema.json").write_text(
        json.dumps({"type": 7}), encoding="utf-8"
    )
    (pack / "schemas" / "output.schema.json").write_text(
        json.dumps({"type": "object"}), encoding="utf-8"
    )
    (pack / "prompt.jinja2").write_text("x", encoding="utf-8")
    with pytest.raises(PackLoadError, match="JSON Schema"):
        load_pack(pack)


def test_module_level_cache_is_visible() -> None:
    # Sanity: the cache mapping is module-level so tests across files
    # observe the same state. invalidate_cache() clears it.
    load_pack(CLASSIFY_TEST_PACK)
    assert pack_loader._cache  # populated
    invalidate_cache()
    assert not pack_loader._cache
