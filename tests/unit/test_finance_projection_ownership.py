"""Owned decoding retains full validation while caller inputs stay detached."""

from collections import OrderedDict
from copy import deepcopy
import gc
import json
import tracemalloc

import pytest

from adminme.finance import dated_projection as module
from adminme.finance.publication import PublicationError, canonical_bytes
from tests.acceptance.test_finance_publications import publication
from tests.fixtures.finance_planning import shared_projection


def test_owned_and_public_validation_have_identical_semantics_and_detachment(
    monkeypatch,
):
    value = shared_projection(publication())
    expected = deepcopy(value)
    monkeypatch.setattr(module, "_validation_receipts", OrderedDict())
    public = module.validate_projection(value)
    owned = module._validate_owned_projection(json.loads(canonical_bytes(value)))
    assert public == owned == expected
    value["contexts"][0]["opening"]["lower_minor"] = -1
    assert public == expected
    for validate in (module.validate_projection, module._validate_owned_projection):
        with pytest.raises(PublicationError):
            validate(value)


@pytest.mark.parametrize(
    "key", ["schema_sha256", "content_sha256", "shared_inventory_sha256"]
)
def test_owned_receipt_cannot_bypass_changed_bytes(monkeypatch, key):
    value = shared_projection(publication())
    module._validate_owned_projection(value)
    value[key] = "0" * 64
    with pytest.raises(PublicationError):
        module._validate_owned_projection(value)


def test_owned_validation_releases_the_redundant_container_copy(tmp_path):
    value = shared_projection(publication())
    raw = canonical_bytes(value)
    # Both paths use the same validated-byte receipt. This isolates allocation,
    # not cold validation CPU or the end-to-end acceptance memory threshold.
    module.validate_projection(value)
    results = {}
    for name, validate in (
        ("public_copy", module.validate_projection),
        ("owned_decode", module._validate_owned_projection),
    ):
        source = json.loads(raw)
        gc.collect()
        tracemalloc.start()
        result = validate(source)
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert canonical_bytes(result) == raw
        results[name] = {"retained_python_bytes": current, "peak_python_bytes": peak}
        del source, result
    assert (
        results["owned_decode"]["peak_python_bytes"]
        < results["public_copy"]["peak_python_bytes"]
    )
    assert (
        results["owned_decode"]["retained_python_bytes"]
        < results["public_copy"]["retained_python_bytes"]
    )
    import os
    from pathlib import Path

    output = Path(os.environ.get("FINANCE_FOCUSED_OUTPUT", str(tmp_path)))
    output.mkdir(exist_ok=True, parents=True)
    (output / "projection-allocation.json").write_text(
        json.dumps(results, indent=2) + "\n"
    )
