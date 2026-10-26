"""Independent tampering and current-result checks for deterministic cards."""

import json
from copy import deepcopy

import pytest

from adminme.finance.answers import FinanceAnswerError, answer_reference, verify_answer
from adminme.finance.calculators import calculate
from tests.unit.test_finance_calculators import inputs


def receipt(identifier="reserve_coverage"):
    result = calculate(identifier, "1", inputs()[identifier])
    result["answer_ref"] = answer_reference(result)
    return result, {
        "result_sha256": result["answer_ref"],
        "result_json": json.dumps(result),
    }


def selected(ref):
    return json.dumps({"finance_answer": {"result_refs": [ref]}})


@pytest.mark.parametrize("identifier", list(inputs()))
def test_every_model_renders_its_current_typed_result_and_required_limits(identifier):
    result, proof = receipt(identifier)
    rendered = verify_answer(selected(result["answer_ref"]), [proof])
    assert result["basis"] in rendered
    assert result["period"] in rendered
    assert result["answer_ref"] in rendered
    assert "Caller-supplied inputs" in rendered
    for caveat in result["exclusions"] + result["assumptions"]:
        assert caveat in rendered


@pytest.mark.parametrize(
    "alteration", ["value", "unit", "period", "basis", "model", "caveat"]
)
def test_changed_numeric_or_semantic_evidence_cannot_match_its_receipt(alteration):
    result, proof = receipt()
    changed = deepcopy(result)
    if alteration == "value":
        changed["values"]["reference_months_covered"] = "400.000000"
    elif alteration == "unit":
        changed["metrics"][0]["unit"] = "USD"
    elif alteration == "period":
        changed["period"] = "another year"
    elif alteration == "basis":
        changed["basis"] = "accepted_actual"
    elif alteration == "model":
        changed["model_version"] = "unreviewed"
    else:
        changed["exclusions"] = []
    proof["result_json"] = json.dumps(changed)
    with pytest.raises(FinanceAnswerError, match="integrity"):
        verify_answer(selected(result["answer_ref"]), [proof])


def test_no_prose_extra_claims_duplicate_keys_or_stale_reference():
    result, proof = receipt()
    for text in (
        "You can safely spend 400.",
        selected(result["answer_ref"]) + " Buy now.",
        json.dumps(
            {"finance_answer": {"result_refs": [result["answer_ref"]], "value": 400}}
        ),
        '{"finance_answer":{},"finance_answer":{}}',
    ):
        with pytest.raises(FinanceAnswerError):
            verify_answer(text, [proof])
    with pytest.raises(FinanceAnswerError, match="current_result"):
        verify_answer(selected("b" * 64), [proof])
