"""Render finance answers from current private receipts, never model arithmetic.

The model selects up to three opaque result references. It cannot supply values,
units, dates, scenarios, caveats or narrative conclusions. Those are rendered
from the same bounded typed results that crossed the authorized tool boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from adminme.finance.budgets import json_bytes


class FinanceAnswerError(ValueError):
    pass


def answer_reference(result: dict[str, Any]) -> str:
    return hashlib.sha256(
        json_bytes({k: v for k, v in result.items() if k != "answer_ref"})
    ).hexdigest()


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FinanceAnswerError("finance_answer_duplicate_key")
        result[key] = value
    return result


def _value(value: Any) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (dict, list)):
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
    return str(value)


def render_result(result: dict[str, Any]) -> str:
    """No optional removal of required qualifications or source semantics."""
    lines: list[str] = []
    if "values" in result and "calculator_id" in result:
        lines.append(
            "Qualified hypothetical calculation: "
            + result["calculator_id"].replace("_", " ")
        )
        lines.append(
            f"Model: {result['calculator_id']} v{result['model_version']}. Basis: {result['basis']}. Period: {result['period']}."
        )
        lines.append("Caller-supplied inputs; accepted household truth is unchanged.")
        units = {
            row["metric_id"].removeprefix(result["result_id"] + ":"): row["unit"]
            for row in result["metrics"]
        }
        for key, value in result["values"].items():
            unit = " " + units[key] if key in units else ""
            lines.append(f"{key.replace('_', ' ')}: {_value(value)}{unit}.")
        for label, key in (("Assumption", "assumptions"), ("Limit", "exclusions")):
            lines.extend(f"{label}: {item}" for item in result[key])
        if result["professional_review"]:
            lines.append(
                "Professional review is required before a consequential decision."
            )
        lines.append(
            "Next step: confirm the selected inputs and the model's limits; this authorizes no financial action."
        )
    elif result.get("result_kind") == "owner_choices":
        lines.append("Your explicitly saved private choices.")
        lines.append(
            "Basis: explicit owner intent. Amounts are targets in the stated currency's minor units; dates and priorities are your selections."
        )
        for choice in result["choices"]:
            lines.append(_value(choice))
        if not result["choices"]:
            lines.append("No saved choices on this page; no preference is inferred.")
        if result.get("next_offset") is not None:
            lines.append("More saved choices are available; this page is incomplete.")
        lines.append("Next step: " + result["next_step"])
    elif result.get("report") is not None:
        report = result["report"]
        lines.append(f"Finance report: {result['state']}.")
        lines.append(
            f"Publication: {result.get('publication_id', 'unavailable')}. Model: {report['model_id']} v{report['model_version']}."
        )
        lines.append(
            f"Basis: {report['basis']}. Period: {report['period_start']}/{report['period_end']}."
        )
        # Each metric retains its basis, period, unit, scenario and source refs.
        # Opaque source IDs are evidence coordinates, never public Drive URLs.
        lines.extend(_value(metric) for metric in result.get("metrics", []))
        lines.append("Quality: " + _value(report["quality"]))
        lines.append("Missing evidence: " + _value(report["required_evidence"]))
        lines.append(
            "Accepted historical snapshot; current freshness and complete coverage are not established."
        )
        if result.get("next_offset") is not None:
            lines.append("More results are available; this page is incomplete.")
        lines.append(
            "Next step: resolve missing evidence before a financial conclusion."
        )
    else:
        state = result.get("readiness", result.get("state", "qualified"))
        lines.append(f"Finance status: {state}.")
        # Structured preparation remains a limitation/checklist, never a verdict.
        for key in (
            "label",
            "reason",
            "missing_evidence",
            "required_evidence",
            "unavailable_models",
            "report_availability",
            "bundle",
            "dependencies",
            "next_step",
        ):
            if result.get(key):
                lines.append(f"{key.replace('_', ' ')}: {_value(result[key])}")
        if result.get("publication"):
            lines.append("Publication: " + _value(result["publication"]))
        if result.get("input_schema"):
            lines.append(
                "The selected hypothetical model's input contract is available. No calculation has been made."
            )
        lines.append("This status grants no financial action or disclosure authority.")
    lines.append("Evidence result: " + result["answer_ref"])
    return "\n".join(lines)


def verify_answer(text: str, receipts: list[dict[str, Any]]) -> str:
    if not text or len(text.encode("utf-8")) > 6000:
        raise FinanceAnswerError("finance_answer_contract_required")
    try:
        body = json.loads(text, object_pairs_hook=_object)
    except (ValueError, TypeError, RecursionError):
        raise FinanceAnswerError("finance_answer_contract_required") from None
    if not isinstance(body, dict) or set(body) != {"finance_answer"}:
        raise FinanceAnswerError("finance_answer_contract_required")
    selected = body["finance_answer"]
    if not isinstance(selected, dict) or set(selected) != {"result_refs"}:
        raise FinanceAnswerError("finance_answer_contract_required")
    refs = selected["result_refs"]
    if (
        not isinstance(refs, list)
        or not 1 <= len(refs) <= 3
        or any(
            not isinstance(ref, str) or not re.fullmatch(r"[0-9a-f]{64}", ref)
            for ref in refs
        )
        or len(set(refs)) != len(refs)
    ):
        raise FinanceAnswerError("finance_answer_contract_required")
    available = {receipt["result_sha256"]: receipt for receipt in receipts}
    cards = []
    for ref in refs:
        receipt = available.get(ref)
        if receipt is None or not receipt.get("result_json"):
            raise FinanceAnswerError("finance_answer_current_result_required")
        result = json.loads(receipt["result_json"])
        if result.get("answer_ref") != ref or answer_reference(result) != ref:
            raise FinanceAnswerError("finance_answer_result_integrity")
        cards.append(render_result(result))
    answer = "\n\n".join(cards)
    if len(answer.encode("utf-8")) > 6000:
        raise FinanceAnswerError("finance_answer_smaller_selection_required")
    return answer
