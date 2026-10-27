"""Serialized validation inputs expire before proof work without changing results."""

from collections import OrderedDict
from copy import deepcopy
from dataclasses import replace

import pytest

from adminme.finance import dated_projection as dated
from adminme.finance import publication_provenance as proof
from adminme.finance.publication import PublicationError, canonical_bytes
from tests.acceptance.test_finance_publications import publication
from tests.fixtures.finance_planning import projection
from tests.fixtures.finance_provenance import package_fixture


class LifetimeBytes(bytes):
    """Observe byte ownership without retaining the observed buffer."""

    def __new__(cls, raw, released, label):
        value = super().__new__(cls, raw)
        value.released = released
        value.label = label
        return value

    def __del__(self):
        self.released.append(self.label)


@pytest.mark.parametrize("owned", [False, True])
@pytest.mark.parametrize("warm", [False, True])
@pytest.mark.parametrize("invalid", [False, True])
def test_projection_buffer_lifetime_preserves_cold_hit_and_error_paths(
    monkeypatch, owned, warm, invalid
):
    value = projection(publication())
    expected = deepcopy(value)
    validate = dated._validate_owned_projection if owned else dated.validate_projection
    monkeypatch.setattr(dated, "_validation_receipts", OrderedDict())
    if warm:
        assert validate(value) == expected
    if invalid:
        value["content_sha256"] = "0" * 64
    input_bytes = canonical_bytes(value)
    released, created, lookups, validations = [], [], [], []
    encode = dated.canonical_bytes
    compile_validator = dated._validator

    def observed_bytes(candidate):
        raw = encode(candidate)
        if not created:
            assert candidate == value
            created.append("projection")
            return LifetimeBytes(raw, released, "projection")
        return raw

    class CheckedReceipts(OrderedDict):
        def __contains__(self, key):
            lookups.append(key)
            assert released == ["projection"]
            return super().__contains__(key)

    class ObservedValidator:
        def __init__(self, schema_bytes):
            self.validator = compile_validator(schema_bytes)

        def validate(self, candidate):
            assert released == ["projection"]
            validations.append(1)
            self.validator.validate(candidate)

    receipts = CheckedReceipts(dated._validation_receipts)
    before_count = len(receipts)
    monkeypatch.setattr(dated, "_validation_receipts", receipts)
    monkeypatch.setattr(dated, "canonical_bytes", observed_bytes)
    monkeypatch.setattr(dated, "_validator", ObservedValidator)
    if invalid:
        with pytest.raises(PublicationError, match="projection_content_mismatch"):
            validate(value)
        assert len(receipts) == before_count
    else:
        result = validate(value)
        assert result == expected and canonical_bytes(result) == input_bytes
        assert (result is value) is owned
        if not owned:
            value["contexts"][0]["opening"]["lower_minor"] = -1
            assert result == expected
        assert len(receipts) == 1
    assert created == released == ["projection"] and len(lookups) == 1
    assert len(validations) == int(invalid or not warm)
    if owned or invalid:
        assert canonical_bytes(value) == input_bytes


@pytest.mark.parametrize("with_previous", [False, True])
@pytest.mark.parametrize("warm", [False, True])
@pytest.mark.parametrize("invalid", [False, True])
def test_proof_buffers_expire_before_cold_hit_and_error_paths(
    monkeypatch, with_previous, warm, invalid
):
    value, package = package_fixture()
    expected = proof._verify_package(value, package, None)
    previous = deepcopy(expected) if with_previous else None
    original_publication = canonical_bytes(value)
    original_previous = proof._proof_bytes(previous)
    monkeypatch.setattr(proof, "_proof_receipts", OrderedDict())
    if warm:
        assert proof.verify_package(value, package, previous) == expected
    if invalid:
        package = replace(package, approved_evidence=frozenset())
    released, created, lookups, validations = [], [], [], []
    encode = proof.canonical_bytes
    verify = proof._verify_package

    def observed_bytes(candidate):
        raw = encode(candidate)
        label = (
            "publication"
            if candidate is value
            else "previous"
            if candidate is None
            or (isinstance(candidate, dict) and "line_units" in candidate)
            else None
        )
        if label is not None and label not in created:
            created.append(label)
            return LifetimeBytes(raw, released, label)
        return raw

    class CheckedReceipts(OrderedDict):
        def get(self, key, default=None):
            lookups.append(key)
            assert set(released) == {"publication", "previous"}
            return super().get(key, default)

    def observed_verify(*args):
        assert set(released) == {"publication", "previous"}
        validations.append(1)
        return verify(*args)

    receipts = CheckedReceipts(proof._proof_receipts)
    before_count = len(receipts)
    monkeypatch.setattr(proof, "_proof_receipts", receipts)
    monkeypatch.setattr(proof, "canonical_bytes", observed_bytes)
    monkeypatch.setattr(proof, "_verify_package", observed_verify)
    if invalid:
        with pytest.raises(PublicationError, match="independent_evidence_required"):
            proof.verify_package(value, package, previous)
        assert len(receipts) == before_count
    else:
        result = proof.verify_package(value, package, previous)
        assert result == expected
        assert proof._proof_bytes(result) == proof._proof_bytes(expected)
        result.line_units.clear()
        assert proof.verify_package(value, package, previous) == expected
        assert len(receipts) == 1
    assert created == ["publication", "previous"]
    assert sorted(released) == ["previous", "publication"]
    assert len(lookups) == (1 if invalid else 2)
    assert len(validations) == int(invalid or not warm)
    assert canonical_bytes(value) == original_publication
    assert proof._proof_bytes(previous) == original_previous
