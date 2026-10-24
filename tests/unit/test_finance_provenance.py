"""Adversarial synthetic checks; no private source or native acceptance claim."""

from copy import deepcopy
from dataclasses import replace
import hashlib
import json

import pytest

from adminme.finance.publication import (
    PublicationError,
    validate_publication,
    validate_transition,
)
from adminme.finance.publication_provenance import (
    CONTRACT_PATH,
    CONTRACT_SHA256,
    digest,
    freeze_package,
    namespace_root,
    parse_blob,
    verify_package,
)
from tests.fixtures.finance_provenance import (
    native_rows,
    package_fixture,
    seal_publication,
)


def test_exact_safe_contract_and_complete_typed_lines():
    assert hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest() == CONTRACT_SHA256
    assert (
        digest(json.loads(CONTRACT_PATH.read_bytes()))
        == "4851c2e74ee8054a670d97fe16b3a461b86ea5e8116a5178fdca821d5e626d8c"
    )
    p, package = package_fixture()
    assert validate_publication(p) == p
    proof = verify_package(p, package)
    assert len(proof.line_units) == 4
    assert proof.unit_ids == frozenset(p["accepted_journal_revision_ids"])


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "extra",
        "duplicate",
        "held_revision",
        "held_event",
        "blank_generated",
        "changed_run",
    ],
)
def test_native_identity_fields_and_semantic_exclusions(fault):
    rows = native_rows()
    if fault == "missing":
        rows[0][0].pop("description")
    if fault == "extra":
        rows[0][0]["_row"] = 42
    if fault == "duplicate":
        rows[1][0]["line_id"] = rows[0][0]["line_id"]
    if fault == "held_revision":
        rows[2][0]["source_revision_id"] = "revision-pending"
    if fault == "held_event":
        rows[0][0]["canonical_event_id"] = "held-event"
    if fault == "blank_generated":
        rows[0] = (rows[0][0], "committed_generated")
    if fault == "changed_run":
        rows[0][0]["line_id"] = ""
    p, package = package_fixture(rows=rows)
    with pytest.raises(PublicationError):
        verify_package(p, package)


@pytest.mark.parametrize("flag", [False, True, 0, 2, "1", None])
def test_no_boolean_coercion_or_formula_substitution(flag):
    p, package = package_fixture(evaluated=flag)
    with pytest.raises(PublicationError):
        verify_package(p, package)


def test_null_empty_numeric_string_and_formula_bytes_remain_distinct():
    values = [None, "", 1, 1.0, True, "1", "=1"]
    ids = []
    for value in values:
        rows = native_rows()
        rows[0][0]["description"] = value
        p, package = package_fixture(rows=rows)
        ids.append(verify_package(p, package).line_units["line-0"])
    assert len(set(ids)) == len(values)


def test_closed_bytes_and_independent_evidence_are_required():
    p, package = package_fixture()
    for mutant in [
        replace(package, approved_evidence=frozenset()),
        replace(package, blobs={}),
        replace(package, blobs={**package.blobs, "f" * 64: b"{}"}),
        replace(package, index=package.index + b" "),
        replace(package, blobs={k: (v + b" ") for k, v in package.blobs.items()}),
    ]:
        with pytest.raises(PublicationError):
            verify_package(p, mutant)
    with pytest.raises(PublicationError):
        parse_blob(b'{"a":1,"a":1}', 100)
    with pytest.raises(PublicationError):
        parse_blob(b'{"a":NaN}', 100)
    with pytest.raises(PublicationError):
        freeze_package(replace(package, index=b"x" * 262145))
    with pytest.raises(PublicationError):
        freeze_package(replace(package, blobs={"a" * 64: b"x" * 1048577}))
    frozen = freeze_package(package)
    package.blobs.clear()
    assert verify_package(p, frozen).unit_ids


def test_publication_derivation_order_and_retained_history():
    p, package = package_fixture()
    prior = verify_package(p, package)
    mutated = deepcopy(p)
    mutated["published_at"] = "2026-01-31T11:59:59Z"
    with pytest.raises(PublicationError):
        namespace_root(mutated)
    mutated = deepcopy(p)
    mutated["accepted_journal_revision_ids"].reverse()
    seal_publication(mutated)
    with pytest.raises(PublicationError):
        namespace_root(mutated)
    rows = native_rows()
    rows[0][0]["description"] = "changed historical bytes"
    changed, closure = package_fixture(rows=rows)
    with pytest.raises(PublicationError, match="historical"):
        verify_package(changed, closure, prior)
    with pytest.raises(PublicationError, match="historical"):
        validate_transition(p, changed)
    appended, closure = package_fixture(rows=native_rows(5))
    appended["prior_publication_id"] = p["publication_id"]
    seal_publication(appended)
    validate_transition(p, appended)
    assert len(verify_package(appended, closure, prior).unit_ids) == 5


@pytest.mark.parametrize(
    "fault",
    [
        "formula",
        "wildcard",
        "two_runs",
        "no_run",
        "wrong_status",
        "row_changed",
        "wrong_cell",
        "missing_line",
        "extra_line",
        "future_capture",
    ],
)
def test_exact_native_committed_count_predicate_and_pinned_witnesses(fault):
    from tests.fixtures.finance_provenance import rewrite_evidence

    rows = native_rows()
    if fault == "formula":
        rows[0][0]["committed_flag"] = "=TRUE"
    if fault == "wildcard":
        rows[0][0]["import_run_id"] = "run-*"
    p, package = package_fixture(rows=rows)
    if fault == "two_runs":
        p, package = rewrite_evidence(
            p, package, "committed_runs", lambda d: d["runs"].append(dict(d["runs"][0]))
        )
    if fault == "no_run":
        p, package = rewrite_evidence(
            p, package, "committed_runs", lambda d: d["runs"].clear()
        )
    if fault == "wrong_status":
        p, package = rewrite_evidence(
            p,
            package,
            "committed_runs",
            lambda d: d["runs"][0].update(status="PREPARED"),
        )
    if fault == "row_changed":
        p, package = rewrite_evidence(
            p,
            package,
            "readback",
            lambda d: d["lines"][0].update(native_row_sha256="b" * 64),
        )
    if fault == "wrong_cell":
        p, package = rewrite_evidence(
            p,
            package,
            "readback",
            lambda d: d["lines"][0].update(effective_value={"boolValue": True}),
        )
    if fault == "missing_line":
        p, package = rewrite_evidence(
            p, package, "readback", lambda d: d["lines"].pop()
        )
    if fault == "extra_line":
        p, package = rewrite_evidence(
            p,
            package,
            "readback",
            lambda d: d["lines"].append({**d["lines"][0], "native_line_id": "unused"}),
        )
    if fault == "future_capture":
        p, package = rewrite_evidence(
            p,
            package,
            "readback",
            lambda d: d.update(captured_at="2099-01-01T00:00:00Z"),
        )
    with pytest.raises(PublicationError):
        verify_package(p, package)


def test_retained_hold_cannot_be_repointed_to_accept_its_original_event():
    from tests.fixtures.finance_provenance import rewrite_evidence

    p, package = package_fixture()
    prior = verify_package(p, package)
    successor, package = rewrite_evidence(
        p,
        package,
        "held_observations",
        lambda d: d["observations"][0].update(
            canonical_event_id="different-held-event"
        ),
    )
    successor["prior_publication_id"] = p["publication_id"]
    seal_publication(successor)
    with pytest.raises(PublicationError, match="historical"):
        verify_package(successor, package, prior)


def test_archive_schema_errors_do_not_render_private_blob_content():
    from pydantic import ValidationError
    from adminme.finance.publication import canonical_bytes
    from adminme.finance.provenance_store import ProvenanceBlobArchivedV1

    marker = "synthetic-private-proof-marker"
    with pytest.raises(ValidationError) as caught:
        ProvenanceBlobArchivedV1.model_validate(
            {
                "owner_id": "owner-synthetic",
                "book_id": "book-synthetic",
                "root_sha256": "a" * 64,
                "content_sha256": "b" * 64,
                "kind": "chunk",
                "canonical_json": canonical_bytes({"private": marker}).decode(),
                "approved_evidence": [],
            }
        )
    assert marker not in str(caught.value)
