"""Pack-internal tests for the Apple Reminders dual-deployment landed in
prompt 11b-ii-α-2 (c1 manifest + c1 config schema + c2 adapter branching +
c3 bridge connector-pack scaffold).

Sits alongside ``test_apple_eventkit_adapter_pack_internal.py`` (the
pre-retrofit pack-internal suite), ``test_apple_eventkit_dual_emit_pack_internal.py``
(11b-i pack-side dual-emit suite), and
``test_apple_eventkit_external_state_mirror_retrofit_pack_internal.py``
(11r-c retrofit suite). This file's three tests close prompt 11b-ii-α-2 §7
c4 deliverable 1 — the pack-side guards that the dual-deployment shape
landed in c1+c2 stays wired correctly: the manifest declares the new
top-level ``subkind`` + ``deployment_targets`` fields, the config schema
accepts the ``deployment_target`` enum with the central default, and the
11-β six-check validator continues to PASS on the dual-deployment pack.

Three load-bearing assertions:

1. ``test_pack_manifest_declares_subkind_state_mirror_and_deployment_targets``
   — ``pack.yaml`` declares top-level ``subkind: state-mirror`` (matching
   α-1's bridge loader allow-list constant
   ``STATE_MIRROR_SUBKIND = "state-mirror"`` at
   ``bridge/adminme_bridge/packs/__init__.py``) AND top-level
   ``deployment_targets: [central, bridge]`` per memo 04 §2 / memo 05 §5.4.

2. ``test_config_schema_accepts_deployment_target_central_and_bridge_with_central_default``
   — ``config.schema.json`` adds the ``deployment_target`` enum
   ``["central", "bridge"]`` with ``default: "central"`` so pre-α-2
   instances (which omit the field entirely) continue to validate per F-2
   backwards-compat.

3. ``test_pack_validator_accepts_dual_deployment_pack`` — the 11-β
   six-check validator at
   :func:`adminme.lib.pack_registry.validator.validate_adapter_pack`
   exits with no error findings on the dual-deployment pack: the
   ``capabilities[*].runtime`` Cat-B central entry coheres with the
   `deployment_targets` top-level switch (memo 05 §3.1 — α-2 keeps the
   capability-list row at ``runtime: central`` per §1 item 1; the
   deployment switch lives in the manifest's top-level + config enum,
   not in the capability list). NO ``advisory_pending_retrofit`` per
   ``base_class_conformance`` (the retrofit landed in 11r-c).

F-5 (HARD/standing): basename ``test_apple_eventkit_dual_deployment_pack_internal.py``
is unique against ``test_apple_eventkit_adapter_pack_internal.py``,
``test_apple_eventkit_dual_emit_pack_internal.py``, and
``test_apple_eventkit_external_state_mirror_retrofit_pack_internal.py``.
Pytest collection from the repo root does not collide.

F-6 (HARD/from 11a c4): NO ``__init__.py`` in
``packs/adapters/reminders/apple-eventkit/tests/`` — pack-internal test
dirs are bare directories so the supervisor's importlib-by-file loader
contract is preserved (the framework test dir
``tests/unit/adapters/reminders/`` DOES have one because it is a real
Python package).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import json
import yaml

PACK_ROOT = Path(__file__).resolve().parents[1]


def test_pack_manifest_declares_subkind_state_mirror_and_deployment_targets() -> None:
    """Pack manifest pin (§7 c1 deliverable 1 + §1 item 1).

    ``subkind: state-mirror`` at the TOP LEVEL (not under ``pack:``) so
    the bridge daemon's ``discover_packs()`` at
    ``bridge/adminme_bridge/packs/__init__.py`` reads it directly via the
    ``data.get("subkind")`` lookup against the
    ``STATE_MIRROR_SUBKIND = "state-mirror"`` allow-list constant.

    ``deployment_targets: [central, bridge]`` at the TOP LEVEL enumerates
    the two deployment runtimes the adapter supports per [D18] (Reminders
    dual-deployment per memo 05 §5.4) + [D22] (Apple Calendar
    dual-deployment lineage). The runtime switch the central adapter's
    ``start()`` reads is ``config.deployment_target`` (not this list);
    this list is the *declarative* surface validator + loader tooling
    inspect to know the pack supports both.
    """
    with (PACK_ROOT / "pack.yaml").open(encoding="utf-8") as fh:
        manifest: Any = yaml.safe_load(fh)
    assert isinstance(manifest, dict)
    # Top-level subkind matches α-1's STATE_MIRROR_SUBKIND allow-list.
    assert manifest.get("subkind") == "state-mirror"
    # Top-level deployment_targets enumerates BOTH supported runtimes.
    deployment_targets = manifest.get("deployment_targets")
    assert isinstance(deployment_targets, list)
    assert "central" in deployment_targets
    assert "bridge" in deployment_targets
    # 11r-c retrofit's capabilities[*].runtime row stays "central" per
    # memo 05 §3.1 — α-2 does NOT promote the capability-list row to
    # ``runtime: dual``; the deployment switch lives in the top-level
    # + config.schema.json enum, not in the capability list. The bridge
    # connector-pack at ``bridge/adminme_bridge/connector_packs/
    # reminders_apple_eventkit_bridge/`` is the bridge-side runtime.
    capabilities = manifest.get("capabilities")
    assert isinstance(capabilities, list)
    assert len(capabilities) == 1
    assert capabilities[0]["kind"] == "Cat-B"
    assert capabilities[0]["runtime"] == "central"


def test_config_schema_accepts_deployment_target_central_and_bridge_with_central_default() -> None:
    """Config schema pin (§7 c1 deliverable 2 + §1 item 2 + F-2).

    ``deployment_target`` is an OPTIONAL string enum
    ``["central", "bridge"]`` with ``default: "central"``. Optional +
    central-default is the F-2 backwards-compat guarantee: instances
    persisted before α-2 do not carry ``deployment_target`` in their
    config and the schema validator continues to accept them. The
    central adapter's ``start()`` reads
    ``ctx.config.get("deployment_target", "central")`` so the runtime
    default is identical to the schema default.
    """
    with (PACK_ROOT / "config.schema.json").open(encoding="utf-8") as fh:
        schema: Any = json.load(fh)
    assert isinstance(schema, dict)
    properties = schema.get("properties")
    assert isinstance(properties, dict)
    dt = properties.get("deployment_target")
    assert isinstance(dt, dict), "config.schema.json must declare deployment_target"
    assert dt.get("type") == "string"
    assert dt.get("enum") == ["central", "bridge"]
    assert dt.get("default") == "central"
    # F-2 backwards-compat: NOT in the required list, so pre-α-2
    # configs that omit it validate cleanly and pick up the default.
    required = schema.get("required") or []
    assert "deployment_target" not in required
    # Existing required fields preserved unchanged (target_list_identifier
    # + member_id from 11b-i + 11r-c).
    assert "target_list_identifier" in required
    assert "member_id" in required


def test_pack_validator_accepts_dual_deployment_pack() -> None:
    """11-β six-check validator passes on the dual-deployment pack
    (§7 c4 verification — ``adminme adapters validate
    packs/adapters/reminders/apple-eventkit`` exits 0 with no error
    findings + no ``advisory_pending_retrofit``).

    The validator at :func:`validate_adapter_pack` runs six checks:
    capability-runtime coherence, event-schema registration, projection-
    subscription compatibility, base-class conformance, write-capabilities
    presence, observation-mode wrapping. The dual-deployment shape adds
    NEW top-level fields (``subkind`` + ``deployment_targets``) which the
    validator's ``AdapterManifest`` parser tolerates (extra-fields-allowed
    on top-level), and the capability-list row stays ``runtime: central``
    per the prompt §1 item 1 design choice — so none of the six checks
    surface a NEW error for α-2.
    """
    from adminme.lib.pack_registry.validator import validate_adapter_pack

    result = validate_adapter_pack(PACK_ROOT)
    # No error findings — install-time gate would PASS.
    errors = [f for f in result.findings if f.severity == "error"]
    assert errors == [], (
        "validate_adapter_pack surfaced unexpected error findings on the "
        f"dual-deployment pack: {[(f.check, f.message) for f in errors]}"
    )
    # No advisory_pending_retrofit (closed by 11r-c step 3 of 3 per memo
    # 08 §3.3 — base_class_conformance must not surface a retrofit
    # advisory on α-2). The retrofit advisory carries the literal phrase
    # ``advisory_pending_retrofit`` in its message (see
    # adminme/lib/pack_registry/validator/base_class_conformance.py).
    retrofit_advisories = [
        f for f in result.findings
        if f.severity == "advisory"
        and "advisory_pending_retrofit" in f.message
    ]
    assert retrofit_advisories == [], (
        "advisory_pending_retrofit must stay closed post-11r-c; α-2 does "
        "not regress base_class_conformance. Findings: "
        f"{[(f.check, f.message) for f in result.findings]}"
    )
