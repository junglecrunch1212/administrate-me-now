"""Tests for :mod:`~.observation_mode_wrapping`.

The check AST-walks ``adapter.py`` content; stubs only need to parse
cleanly, not run as importable modules. The canonical observation
function name is :func:`adminme.lib.observation.outbound` (verified at
c3 start against on-main).
"""

from __future__ import annotations

from pathlib import Path

from adminme.lib.pack_registry.models import AdapterManifest
from adminme.lib.pack_registry.validator.observation_mode_wrapping import (
    check_observation_mode_wrapping,
)


def _write_adapter(pack_dir: Path, body: str) -> None:
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "adapter.py").write_text(body, encoding="utf-8")


def test_cat_a_send_calls_outbound_via_direct_import(tmp_path: Path) -> None:
    _write_adapter(
        tmp_path,
        "from adminme.lib.observation import outbound\n"
        "\n"
        "class FakeMessaging:\n"
        "    async def send(self, message):\n"
        "        return await outbound(\n"
        "            session=None, action='m', payload={}, action_fn=None,\n"
        "            manager=None, event_log=None,\n"
        "        )\n",
    )
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-A",
                    "runtime": "central",
                    "event_families": ["messaging.received"],
                }
            ]
        }
    )
    assert check_observation_mode_wrapping(tmp_path, manifest) == []


def test_cat_a_send_missing_outbound_call_yields_error(tmp_path: Path) -> None:
    _write_adapter(
        tmp_path,
        "class FakeMessaging:\n"
        "    async def send(self, message):\n"
        "        return await self._send_direct(message)\n",
    )
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-A",
                    "runtime": "central",
                    "event_families": ["messaging.received"],
                }
            ]
        }
    )
    findings = check_observation_mode_wrapping(tmp_path, manifest)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity == "error"
    assert finding.check == "observation_mode_wrapping"
    assert "Cat-A" in finding.message
    assert "send" in finding.message


def test_cat_e_execute_action_calls_outbound_via_module_alias(
    tmp_path: Path,
) -> None:
    _write_adapter(
        tmp_path,
        "from adminme.lib import observation\n"
        "\n"
        "class FakeAction:\n"
        "    async def execute_action(self, request):\n"
        "        return await observation.outbound(\n"
        "            session=None, action='a', payload={}, action_fn=None,\n"
        "            manager=None, event_log=None,\n"
        "        )\n",
    )
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-E",
                    "runtime": "central",
                    "event_families": ["outbound.dispatched"],
                }
            ],
            "observation_mode_required": True,
        }
    )
    assert check_observation_mode_wrapping(tmp_path, manifest) == []


def test_cat_e_missing_execute_action_yields_error(tmp_path: Path) -> None:
    _write_adapter(
        tmp_path,
        "class FakeAction:\n    pass\n",
    )
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-E",
                    "runtime": "central",
                    "event_families": ["outbound.dispatched"],
                }
            ],
            "observation_mode_required": True,
        }
    )
    findings = check_observation_mode_wrapping(tmp_path, manifest)
    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert "execute_action" in findings[0].message


def test_cat_b_capability_skips_check(tmp_path: Path) -> None:
    _write_adapter(tmp_path, "class FakeMirror:\n    pass\n")
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-B",
                    "runtime": "central",
                    "event_families": ["reminder.synced"],
                }
            ]
        }
    )
    assert check_observation_mode_wrapping(tmp_path, manifest) == []


def test_cat_c_and_cat_d_capabilities_skip_check(tmp_path: Path) -> None:
    _write_adapter(tmp_path, "class FakeMixed:\n    pass\n")
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-C",
                    "runtime": "bridge",
                    "event_families": ["home_assistant.state_changed"],
                },
                {
                    "kind": "Cat-D",
                    "runtime": "bridge",
                    "event_families": ["note.added"],
                },
            ],
            "owner_scope_overridability_at_install": False,
        }
    )
    assert check_observation_mode_wrapping(tmp_path, manifest) == []


def test_fully_qualified_outbound_call_is_recognized(tmp_path: Path) -> None:
    _write_adapter(
        tmp_path,
        "import adminme.lib.observation\n"
        "\n"
        "class FakeMessaging:\n"
        "    async def send(self, message):\n"
        "        return await adminme.lib.observation.outbound(\n"
        "            session=None, action='m', payload={}, action_fn=None,\n"
        "            manager=None, event_log=None,\n"
        "        )\n",
    )
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-A",
                    "runtime": "central",
                    "event_families": ["messaging.received"],
                }
            ]
        }
    )
    assert check_observation_mode_wrapping(tmp_path, manifest) == []


def test_aliased_outbound_import_is_recognized(tmp_path: Path) -> None:
    """``from adminme.lib.observation import outbound as do_send`` is recognized."""
    _write_adapter(
        tmp_path,
        "from adminme.lib.observation import outbound as do_send\n"
        "\n"
        "class FakeMessaging:\n"
        "    async def send(self, message):\n"
        "        return await do_send(\n"
        "            session=None, action='m', payload={}, action_fn=None,\n"
        "            manager=None, event_log=None,\n"
        "        )\n",
    )
    manifest = AdapterManifest.model_validate(
        {
            "capabilities": [
                {
                    "kind": "Cat-A",
                    "runtime": "central",
                    "event_families": ["messaging.received"],
                }
            ]
        }
    )
    assert check_observation_mode_wrapping(tmp_path, manifest) == []
