"""Bounded, deterministic finance models; no provider, model, or execution calls.

Inputs are explicit hypothetical facts. Resolving accepted publication references,
authorizing their use and verifying delivery belong to the calling service. This
module cannot bless a client-supplied value as accepted household truth.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, DecimalException, ROUND_HALF_UP, localcontext
from typing import Annotated, Any, Callable, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    ValidationError,
    model_validator,
)

MAX_MINOR = 10**12
MAX_RESULT_MINOR = 9_000_000_000_000_000


def _iso_date(value: Any) -> Any:
    if type(value) is date or (
        isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value)
    ):
        return value
    raise ValueError("explicit_iso_date_required")


def _confirmed(value: Any) -> bool:
    if value is not True:
        raise ValueError("explicit_confirmation_required")
    return True


IsoDate = Annotated[date, BeforeValidator(_iso_date)]
Confirmed = Annotated[Literal[True], BeforeValidator(_confirmed)]
Money = Annotated[int, Field(strict=True, ge=0, le=MAX_MINOR)]
PositiveMoney = Annotated[int, Field(strict=True, gt=0, le=MAX_MINOR)]
SignedMoney = Annotated[int, Field(strict=True, ge=-MAX_MINOR, le=MAX_MINOR)]
Months = Annotated[int, Field(strict=True, ge=1, le=720)]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")]
DecimalText = Annotated[
    str, Field(pattern=r"^-?[0-9]{1,12}(\.[0-9]{1,12})?$", max_length=26)
]


class CalculatorError(ValueError):
    """Safe public error: never contains financial input values or source text."""

    def __init__(self, code: str, *, missing_fields: tuple[str, ...] = ()) -> None:
        self.code = code
        self.missing_fields = missing_fields
        super().__init__(code)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CurrencyInput(Contract):
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


class Metric(Contract):
    metric_id: str
    value: int | str | None
    unit: str


class CalculationResult(Contract):
    result_id: str
    calculator_id: str
    model_version: Literal["1"] = "1"
    input_kind: Literal["explicit_hypothetical"] = "explicit_hypothetical"
    readiness: Literal["qualified"] = "qualified"
    currency: str
    basis: str
    period: str
    values: dict[str, Any]
    metrics: list[Metric]
    assumptions: list[str]
    exclusions: list[str]
    professional_review: bool
    provider_effect_count: Literal[0] = 0


def _minor(value: Decimal | int) -> int:
    rounded = int(Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if abs(rounded) > MAX_RESULT_MINOR:
        raise CalculatorError("result_exceeds_supported_money_range")
    return rounded


def _text(value: Decimal, places: int = 6) -> str:
    return format(
        value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP), "f"
    )


def _rate(value: str, *, minimum: Decimal = Decimal(0)) -> Decimal:
    rate = Decimal(value)
    if not minimum <= rate <= 1:
        raise ValueError("rate_outside_supported_range")
    return rate


class CashEvent(Contract):
    event_id: Identifier
    earliest_date: IsoDate
    latest_date: IsoDate
    direction: Literal["inflow", "outflow"]
    amount_minor: Money

    @model_validator(mode="after")
    def ordered(self) -> CashEvent:
        if self.latest_date < self.earliest_date:
            raise ValueError("invalid_date_order")
        return self


class CashLadderInput(CurrencyInput):
    opening_cash_minor: Money
    reserve_floor_minor: Money
    opening_reconciled: Confirmed
    mandatory_outflows_complete: Confirmed
    start_date: IsoDate
    end_date: IsoDate
    timezone: Annotated[str, Field(min_length=1, max_length=64)]
    timing_basis: Literal["explicit_settlement_dates"]
    events: Annotated[list[CashEvent], Field(max_length=100)]

    @model_validator(mode="after")
    def valid_horizon(self) -> CashLadderInput:
        if not 0 <= (self.end_date - self.start_date).days <= 366:
            raise ValueError("cash_horizon_outside_supported_range")
        if len({e.event_id for e in self.events}) != len(self.events):
            raise ValueError("duplicate_cash_event")
        if any(
            e.earliest_date < self.start_date or e.latest_date > self.end_date
            for e in self.events
        ):
            raise ValueError("cash_event_outside_horizon")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("unknown_timezone") from exc
        return self


class CashLadderOutput(Contract):
    minimum_cash_worst_timing_minor: int
    minimum_cash_best_timing_minor: int
    closing_cash_minor: int
    first_reserve_breach_worst_timing: date | None
    first_reserve_breach_best_timing: date | None
    negative_cash_possible: bool
    timing_uncertain: bool
    events_evaluated: int


def _cash_path(body: CashLadderInput, *, worst: bool) -> tuple[int, int, date | None]:
    days: dict[date, int] = defaultdict(int)
    for event in body.events:
        earliest = (event.direction == "outflow") == worst
        day = event.earliest_date if earliest else event.latest_date
        days[day] += event.amount_minor * (1 if event.direction == "inflow" else -1)
    cash = body.opening_cash_minor
    minimum = cash
    breach = body.start_date if cash < body.reserve_floor_minor else None
    for day in sorted(days):
        cash = _minor(cash + days[day])
        minimum = min(minimum, cash)
        if breach is None and cash < body.reserve_floor_minor:
            breach = day
    return minimum, cash, breach


def _cash_ladder(body: CashLadderInput) -> CashLadderOutput:
    worst, closing, first_worst = _cash_path(body, worst=True)
    best, _, first_best = _cash_path(body, worst=False)
    return CashLadderOutput(
        minimum_cash_worst_timing_minor=worst,
        minimum_cash_best_timing_minor=best,
        closing_cash_minor=closing,
        first_reserve_breach_worst_timing=first_worst,
        first_reserve_breach_best_timing=first_best,
        negative_cash_possible=worst < 0,
        timing_uncertain=any(e.earliest_date != e.latest_date for e in body.events),
        events_evaluated=len(body.events),
    )


class ReserveInput(CurrencyInput):
    liquid_cash_minor: Money
    monthly_reference_spending_minor: PositiveMoney
    chosen_reserve_floor_minor: Money
    liquidity_confirmed: Confirmed
    reference_basis: Literal["chosen_essential_spending", "accepted_ongoing_cost"]


class ReserveOutput(Contract):
    reference_months_covered: str
    above_chosen_floor_minor: int
    replenishment_needed_minor: int
    below_chosen_floor: bool


def _reserve(body: ReserveInput) -> ReserveOutput:
    delta = body.liquid_cash_minor - body.chosen_reserve_floor_minor
    return ReserveOutput(
        reference_months_covered=_text(
            Decimal(body.liquid_cash_minor) / body.monthly_reference_spending_minor
        ),
        above_chosen_floor_minor=max(0, delta),
        replenishment_needed_minor=max(0, -delta),
        below_chosen_floor=delta < 0,
    )


class LoanTerms(Contract):
    principal_minor: PositiveMoney
    annual_rate: DecimalText
    monthly_payment_minor: PositiveMoney
    extra_monthly_principal_minor: Money = 0
    term_features: Literal["fixed_monthly_no_other_fees"]

    @model_validator(mode="after")
    def valid_rate(self) -> LoanTerms:
        _rate(self.annual_rate)
        return self


class DebtInput(CurrencyInput):
    terms: LoanTerms
    horizon_months: Months
    cash_upfront_fee_minor: Money
    payoff_penalty_minor: Money


class DebtOutput(Contract):
    payoff_complete: bool
    payoff_month: int | None
    ending_principal_minor: int
    principal_paid_minor: int
    interest_paid_minor: int
    cash_fees_paid_minor: int
    total_cash_paid_minor: int
    negative_amortization: bool


@dataclass(frozen=True)
class _DebtPath:
    balance: int
    interest: int
    principal_paid: int
    payoff_month: int | None
    cumulative_interest: tuple[int, ...]


def _debt_path(terms: LoanTerms, months: int) -> _DebtPath:
    balance = terms.principal_minor
    interest_total = principal_total = 0
    monthly_rate = Decimal(terms.annual_rate) / 12
    payment = terms.monthly_payment_minor + terms.extra_monthly_principal_minor
    payoff: int | None = None
    cumulative: list[int] = []
    for month in range(1, months + 1):
        if balance > 0:
            interest = _minor(Decimal(balance) * monthly_rate)
            if payment <= interest:
                raise CalculatorError("payment_does_not_amortize_debt")
            principal = min(balance, payment - interest)
            interest_total = _minor(interest_total + interest)
            principal_total += principal
            balance -= principal
            if balance == 0:
                payoff = month
        cumulative.append(interest_total)
    return _DebtPath(
        balance, interest_total, principal_total, payoff, tuple(cumulative)
    )


def _debt(body: DebtInput) -> DebtOutput:
    path = _debt_path(body.terms, body.horizon_months)
    fees = body.cash_upfront_fee_minor
    if path.payoff_month is not None:
        fees += body.payoff_penalty_minor
    return DebtOutput(
        payoff_complete=path.payoff_month is not None,
        payoff_month=path.payoff_month,
        ending_principal_minor=path.balance,
        principal_paid_minor=path.principal_paid,
        interest_paid_minor=path.interest,
        cash_fees_paid_minor=fees,
        total_cash_paid_minor=_minor(path.principal_paid + path.interest + fees),
        negative_amortization=False,
    )


class RefinanceInput(CurrencyInput):
    existing: LoanTerms
    replacement: LoanTerms
    horizon_months: Months
    cash_closing_cost_minor: Money
    financed_fee_minor: Money
    existing_payoff_penalty_minor: Money

    @model_validator(mode="after")
    def no_unmodelled_cashout(self) -> RefinanceInput:
        if self.replacement.principal_minor != (
            self.existing.principal_minor + self.financed_fee_minor
        ):
            raise ValueError("replacement_balance_must_equal_payoff_plus_financed_fees")
        return self


class RefinanceOutput(Contract):
    transition_cash_required_minor: int
    existing_interest_in_horizon_minor: int
    replacement_interest_in_horizon_minor: int
    interest_and_fee_savings_in_horizon_minor: int
    first_interest_fee_break_even_month: int | None
    existing_ending_principal_minor: int
    replacement_ending_principal_minor: int
    both_loans_paid_off_in_horizon: bool
    monthly_scheduled_cash_reduction_minor: int


def _refinance(body: RefinanceInput) -> RefinanceOutput:
    old = _debt_path(body.existing, body.horizon_months)
    new = _debt_path(body.replacement, body.horizon_months)
    fees = (
        body.cash_closing_cost_minor
        + body.existing_payoff_penalty_minor
        + body.financed_fee_minor
    )
    break_even = next(
        (
            month
            for month, (old_interest, new_interest) in enumerate(
                zip(old.cumulative_interest, new.cumulative_interest, strict=True), 1
            )
            if old_interest - new_interest >= fees
        ),
        None,
    )
    return RefinanceOutput(
        transition_cash_required_minor=(
            body.cash_closing_cost_minor + body.existing_payoff_penalty_minor
        ),
        existing_interest_in_horizon_minor=old.interest,
        replacement_interest_in_horizon_minor=new.interest,
        interest_and_fee_savings_in_horizon_minor=old.interest - new.interest - fees,
        first_interest_fee_break_even_month=break_even,
        existing_ending_principal_minor=old.balance,
        replacement_ending_principal_minor=new.balance,
        both_loans_paid_off_in_horizon=(
            old.payoff_month is not None and new.payoff_month is not None
        ),
        monthly_scheduled_cash_reduction_minor=(
            body.existing.monthly_payment_minor
            + body.existing.extra_monthly_principal_minor
            - body.replacement.monthly_payment_minor
            - body.replacement.extra_monthly_principal_minor
        ),
    )


class UnitCostInput(CurrencyInput):
    purchase_minor: Money
    allocated_tax_minor: Money
    allocated_shipping_minor: Money
    allocated_discount_minor: Money
    matched_refund_minor: Money
    retained_quantity: DecimalText
    unit: Literal["g", "kg", "ml", "l", "count"]

    @model_validator(mode="after")
    def valid_quantity_and_cost(self) -> UnitCostInput:
        if not 0 < Decimal(self.retained_quantity) <= 1_000_000:
            raise ValueError("retained_quantity_outside_supported_range")
        if (
            self.purchase_minor
            + self.allocated_tax_minor
            + self.allocated_shipping_minor
            < (self.allocated_discount_minor + self.matched_refund_minor)
        ):
            raise ValueError("refund_and_discount_exceed_allocated_cost")
        return self


class UnitCostOutput(Contract):
    retained_net_cost_minor: int
    minor_units_per_selected_unit: str
    selected_unit: str


def _unit_cost(body: UnitCostInput) -> UnitCostOutput:
    cost = (
        body.purchase_minor
        + body.allocated_tax_minor
        + body.allocated_shipping_minor
        - body.allocated_discount_minor
        - body.matched_refund_minor
    )
    return UnitCostOutput(
        retained_net_cost_minor=cost,
        minor_units_per_selected_unit=_text(
            Decimal(cost) / Decimal(body.retained_quantity)
        ),
        selected_unit=body.unit,
    )


class OwnershipInput(CurrencyInput):
    option_kind: Literal["buy", "lease", "keep", "repair"]
    upfront_minor: Money
    monthly_cost_minor: Money
    months: Months
    terminal_receipt_minor: Money
    terminal_cost_minor: Money
    annual_discount_rate: DecimalText
    money_basis: Literal["nominal"]
    discount_convention: Literal["nominal_annual_monthly"]

    @model_validator(mode="after")
    def valid_terms(self) -> OwnershipInput:
        _rate(self.annual_discount_rate)
        if self.option_kind == "lease" and self.terminal_receipt_minor != 0:
            raise ValueError("lease_does_not_confer_resale_asset")
        return self


class OwnershipOutput(Contract):
    nominal_net_cost_minor: int
    discounted_net_cost_minor: int
    initial_cash_required_minor: int
    option_kind: str


def _ownership(body: OwnershipInput) -> OwnershipOutput:
    rate = Decimal(body.annual_discount_rate) / 12
    terminal = body.terminal_cost_minor - body.terminal_receipt_minor
    present = Decimal(body.upfront_minor)
    for month in range(1, body.months + 1):
        present += Decimal(body.monthly_cost_minor) / (1 + rate) ** month
    present += Decimal(terminal) / (1 + rate) ** body.months
    return OwnershipOutput(
        nominal_net_cost_minor=_minor(
            body.upfront_minor + body.monthly_cost_minor * body.months + terminal
        ),
        discounted_net_cost_minor=_minor(present),
        initial_cash_required_minor=body.upfront_minor,
        option_kind=body.option_kind,
    )


class PayInput(CurrencyInput):
    person_ref: Identifier
    pay_frequency: Literal["weekly_52", "biweekly_26", "semimonthly_24", "monthly_12"]
    gross_per_period_minor: Money
    pretax_deductions_per_period_minor: Money
    tax_withheld_per_period_minor: Money
    posttax_deductions_per_period_minor: Money
    employer_contributions_per_period_minor: Money

    @model_validator(mode="after")
    def nonnegative_take_home(self) -> PayInput:
        if self.gross_per_period_minor < (
            self.pretax_deductions_per_period_minor
            + self.tax_withheld_per_period_minor
            + self.posttax_deductions_per_period_minor
        ):
            raise ValueError("deductions_exceed_gross_pay")
        return self


class PayOutput(Contract):
    take_home_per_period_minor: int
    annualized_gross_minor: int
    annualized_take_home_minor: int
    average_monthly_take_home_minor: int
    annualized_employer_contributions_minor: int
    assumed_periods_per_year: int


def _pay(body: PayInput) -> PayOutput:
    periods = int(body.pay_frequency.rsplit("_", 1)[1])
    take_home = (
        body.gross_per_period_minor
        - body.pretax_deductions_per_period_minor
        - body.tax_withheld_per_period_minor
        - body.posttax_deductions_per_period_minor
    )
    return PayOutput(
        take_home_per_period_minor=take_home,
        annualized_gross_minor=_minor(body.gross_per_period_minor * periods),
        annualized_take_home_minor=_minor(take_home * periods),
        average_monthly_take_home_minor=_minor(Decimal(take_home * periods) / 12),
        annualized_employer_contributions_minor=_minor(
            body.employer_contributions_per_period_minor * periods
        ),
        assumed_periods_per_year=periods,
    )


class LeaveInput(CurrencyInput):
    person_ref: Identifier
    selected_work_days: Annotated[int, Field(strict=True, ge=1, le=366)]
    paid_leave_days: Annotated[int, Field(strict=True, ge=0, le=366)]
    normal_take_home_per_day_minor: Money
    paid_leave_take_home_per_day_minor: Money
    additional_premiums_minor: Money
    additional_care_cost_minor: Money
    calendar_basis: Literal["selected_paid_work_days"]

    @model_validator(mode="after")
    def bounded_paid_days(self) -> LeaveInput:
        if self.paid_leave_days > self.selected_work_days:
            raise ValueError("paid_days_exceed_selected_days")
        return self


class LeaveOutput(Contract):
    baseline_take_home_minor: int
    leave_take_home_minor: int
    incremental_cost_minor: int
    cash_gap_minor: int


def _leave(body: LeaveInput) -> LeaveOutput:
    baseline = body.selected_work_days * body.normal_take_home_per_day_minor
    leave = body.paid_leave_days * body.paid_leave_take_home_per_day_minor
    cost = body.additional_premiums_minor + body.additional_care_cost_minor
    return LeaveOutput(
        baseline_take_home_minor=baseline,
        leave_take_home_minor=leave,
        incremental_cost_minor=cost,
        cash_gap_minor=_minor(baseline - leave + cost),
    )


class Position(Contract):
    position_ref: Identifier
    issuer_ref: Identifier
    asset_class: Identifier
    tax_wrapper: Literal["taxable", "deferred", "exempt", "unknown"]
    value_minor: Money
    annual_fee_rate: DecimalText | None

    @model_validator(mode="after")
    def valid_fee(self) -> Position:
        if self.annual_fee_rate is not None:
            _rate(self.annual_fee_rate)
        return self


class PortfolioInput(CurrencyInput):
    valuation_date: IsoDate
    coverage: Literal["listed_partial", "complete_confirmed"]
    positions: Annotated[list[Position], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def bounded_groups(self) -> PortfolioInput:
        if len({p.position_ref for p in self.positions}) != len(self.positions):
            raise ValueError("duplicate_position")
        if len({p.asset_class for p in self.positions}) > 20:
            raise ValueError("too_many_asset_classes_for_interactive_result")
        if sum(p.value_minor for p in self.positions) == 0:
            raise ValueError("portfolio_weights_require_positive_listed_value")
        return self


class Allocation(Contract):
    asset_class: str
    value_minor: int
    listed_weight: str


class PortfolioOutput(Contract):
    listed_value_minor: int
    largest_position_weight: str
    largest_issuer_weight: str
    known_fee_value_minor: int
    known_fee_value_fraction: str
    weighted_fee_rate_on_known_values: str | None
    weighted_fee_rate_all_listed: str | None
    estimated_annual_fees_on_known_values_minor: int
    coverage: str
    allocation: list[Allocation]


def _portfolio(body: PortfolioInput) -> PortfolioOutput:
    classes: dict[str, int] = defaultdict(int)
    issuers: dict[str, int] = defaultdict(int)
    total = sum(p.value_minor for p in body.positions)
    known = 0
    fees = Decimal(0)
    for position in body.positions:
        classes[position.asset_class] += position.value_minor
        issuers[position.issuer_ref] += position.value_minor
        if position.annual_fee_rate is not None:
            known += position.value_minor
            fees += position.value_minor * Decimal(position.annual_fee_rate)
    return PortfolioOutput(
        listed_value_minor=total,
        largest_position_weight=_text(
            Decimal(max(p.value_minor for p in body.positions)) / total
        ),
        largest_issuer_weight=_text(Decimal(max(issuers.values())) / total),
        known_fee_value_minor=known,
        known_fee_value_fraction=_text(Decimal(known) / total),
        weighted_fee_rate_on_known_values=_text(fees / known) if known else None,
        weighted_fee_rate_all_listed=_text(fees / total) if known == total else None,
        estimated_annual_fees_on_known_values_minor=_minor(fees),
        coverage=body.coverage,
        allocation=[
            Allocation(
                asset_class=key,
                value_minor=value,
                listed_weight=_text(Decimal(value) / total),
            )
            for key, value in sorted(classes.items())
        ],
    )


class GoalPathInput(CurrencyInput):
    opening_value_minor: Money
    monthly_contribution_minor: Money
    monthly_withdrawal_minor: Money
    monthly_returns: Annotated[list[DecimalText], Field(min_length=1, max_length=720)]
    monthly_inflation_rate: DecimalText
    target_nominal_minor: Money
    returns_basis: Literal["monthly_nominal_net_of_fees_before_tax"]
    cashflow_timing: Literal["beginning_of_month"]

    @model_validator(mode="after")
    def valid_rates(self) -> GoalPathInput:
        _rate(self.monthly_inflation_rate)
        for value in self.monthly_returns:
            _rate(value, minimum=Decimal(-1))
        return self


class GoalPathOutput(Contract):
    months_evaluated: int
    ending_nominal_value_minor: int
    ending_real_value_minor: int
    contribution_total_minor: int
    withdrawal_delivered_minor: int
    unmet_withdrawals_minor: int
    first_unfunded_withdrawal_month: int | None
    target_gap_nominal_minor: int


def _goal_path(body: GoalPathInput) -> GoalPathOutput:
    balance = body.opening_value_minor
    delivered = missing = 0
    first_missing: int | None = None
    for month, rate in enumerate(body.monthly_returns, 1):
        available = balance + body.monthly_contribution_minor
        withdrawal = min(available, body.monthly_withdrawal_minor)
        shortfall = body.monthly_withdrawal_minor - withdrawal
        if shortfall and first_missing is None:
            first_missing = month
        missing = _minor(missing + shortfall)
        delivered = _minor(delivered + withdrawal)
        balance = _minor(Decimal(available - withdrawal) * (1 + Decimal(rate)))
    months = len(body.monthly_returns)
    return GoalPathOutput(
        months_evaluated=months,
        ending_nominal_value_minor=balance,
        ending_real_value_minor=_minor(
            Decimal(balance) / (1 + Decimal(body.monthly_inflation_rate)) ** months
        ),
        contribution_total_minor=_minor(body.monthly_contribution_minor * months),
        withdrawal_delivered_minor=delivered,
        unmet_withdrawals_minor=missing,
        first_unfunded_withdrawal_month=first_missing,
        target_gap_nominal_minor=max(0, body.target_nominal_minor - balance),
    )


class SupportInput(CurrencyInput):
    liquid_cash_minor: Money
    chosen_reserve_floor_minor: Money
    other_committed_cash_minor: Money
    proposed_support_minor: Money
    support_intent: Literal[
        "gift", "optional_reserve", "documented_loan", "reimbursement"
    ]
    period_start: IsoDate
    period_end: IsoDate
    mandatory_cash_requirements_complete: Confirmed

    @model_validator(mode="after")
    def ordered_period(self) -> SupportInput:
        if self.period_end < self.period_start:
            raise ValueError("invalid_date_order")
        return self


class SupportOutput(Contract):
    capacity_after_floor_and_commitments_minor: int
    support_shortfall_minor: int
    cash_after_support_and_commitments_minor: int
    optional_reserve_is_legal_debt: Literal[False] = False
    expected_repayment_counted_as_cash_minor: Literal[0] = 0


def _support(body: SupportInput) -> SupportOutput:
    capacity = max(
        0,
        body.liquid_cash_minor
        - body.chosen_reserve_floor_minor
        - body.other_committed_cash_minor,
    )
    return SupportOutput(
        capacity_after_floor_and_commitments_minor=capacity,
        support_shortfall_minor=max(0, body.proposed_support_minor - capacity),
        cash_after_support_and_commitments_minor=(
            body.liquid_cash_minor
            - body.other_committed_cash_minor
            - body.proposed_support_minor
        ),
    )


class ComparableMeasure(Contract):
    publication_ref: Identifier
    comparison_basis_ref: Identifier
    coverage_ref: Identifier
    basis: Literal[
        "accounting_actual", "cash_movement", "observed_value", "ongoing_cost_reference"
    ]
    period_unit: Literal["day", "month", "year", "point_in_time"]
    period_count: Annotated[int, Field(strict=True, ge=1, le=720)]
    value_minor: SignedMoney


class DecisionInput(CurrencyInput):
    baseline: ComparableMeasure
    subsequent: ComparableMeasure
    expected_change_minor: SignedMoney | None
    implementation_status: Literal["proposed", "implemented", "unknown"]

    @model_validator(mode="after")
    def comparable(self) -> DecisionInput:
        for key in (
            "comparison_basis_ref",
            "coverage_ref",
            "basis",
            "period_unit",
            "period_count",
        ):
            if getattr(self.baseline, key) != getattr(self.subsequent, key):
                raise ValueError("measures_are_not_like_for_like")
        return self


class DecisionOutput(Contract):
    observed_change_minor: int
    difference_from_expected_change_minor: int | None
    implementation_status: str
    causal_savings_minor: None = None
    attribution: Literal["not_established"] = "not_established"


def _decision(body: DecisionInput) -> DecisionOutput:
    change = body.subsequent.value_minor - body.baseline.value_minor
    return DecisionOutput(
        observed_change_minor=change,
        difference_from_expected_change_minor=(
            change - body.expected_change_minor
            if body.expected_change_minor is not None
            else None
        ),
        implementation_status=body.implementation_status,
    )


class CreditLine(Contract):
    line_ref: Identifier
    reported_balance_minor: Money
    reported_limit_minor: PositiveMoney


class CreditInput(CurrencyInput):
    report_date: IsoDate
    lines: Annotated[list[CreditLine], Field(min_length=1, max_length=50)]
    coverage: Literal["listed_partial", "complete_confirmed"]

    @model_validator(mode="after")
    def unique_lines(self) -> CreditInput:
        if len({line.line_ref for line in self.lines}) != len(self.lines):
            raise ValueError("duplicate_credit_line")
        return self


class CreditOutput(Contract):
    listed_reported_balance_minor: int
    listed_reported_limit_minor: int
    listed_utilization_ratio: str
    highest_individual_utilization_ratio: str
    coverage: str
    predicted_score_change: None = None


def _credit(body: CreditInput) -> CreditOutput:
    balance = sum(line.reported_balance_minor for line in body.lines)
    limit = sum(line.reported_limit_minor for line in body.lines)
    return CreditOutput(
        listed_reported_balance_minor=balance,
        listed_reported_limit_minor=limit,
        listed_utilization_ratio=_text(Decimal(balance) / limit),
        highest_individual_utilization_ratio=_text(
            max(
                Decimal(line.reported_balance_minor) / line.reported_limit_minor
                for line in body.lines
            )
        ),
        coverage=body.coverage,
    )


class RegisterItem(Contract):
    item_ref: Identifier
    kind: Literal["asset", "liability", "lease", "optional_reserve"]
    listed_value_minor: Money | None
    ownership_share: DecimalText
    liquid: StrictBool
    valuation_date: IsoDate | None

    @model_validator(mode="after")
    def valid_share(self) -> RegisterItem:
        _rate(self.ownership_share)
        if self.liquid and self.kind != "asset":
            raise ValueError("only_assets_can_be_liquid")
        if self.listed_value_minor is not None and self.valuation_date is None:
            raise ValueError("known_values_require_valuation_date")
        return self


class NetWorthInput(CurrencyInput):
    as_of_date: IsoDate
    items: Annotated[list[RegisterItem], Field(min_length=1, max_length=100)]
    coverage: Literal["listed_partial", "complete_confirmed"]

    @model_validator(mode="after")
    def unique_items(self) -> NetWorthInput:
        if len({item.item_ref for item in self.items}) != len(self.items):
            raise ValueError("duplicate_register_item")
        if any(
            item.valuation_date is not None and item.valuation_date > self.as_of_date
            for item in self.items
        ):
            raise ValueError("valuation_after_as_of_date")
        return self


class NetWorthOutput(Contract):
    known_listed_assets_minor: int | None
    known_listed_liabilities_minor: int | None
    known_listed_net_minor: int | None
    known_listed_liquid_assets_minor: int | None
    missing_asset_or_liability_values: bool
    coverage: str
    formal_balance_sheet_released: Literal[False] = False


def _net_worth(body: NetWorthInput) -> NetWorthOutput:
    assets = liabilities = liquid = 0
    missing = False
    known_assets = known_liabilities = known_liquid = False
    for item in body.items:
        if item.kind not in {"asset", "liability"}:
            continue
        if item.listed_value_minor is None:
            missing = True
            continue
        owned = _minor(Decimal(item.listed_value_minor) * Decimal(item.ownership_share))
        if item.kind == "asset":
            known_assets = True
            assets += owned
            if item.liquid:
                known_liquid = True
                liquid += owned
        else:
            known_liabilities = True
            liabilities += owned
    return NetWorthOutput(
        known_listed_assets_minor=assets if known_assets else None,
        known_listed_liabilities_minor=liabilities if known_liabilities else None,
        known_listed_net_minor=(
            assets - liabilities
            if not missing and (known_assets or known_liabilities)
            else None
        ),
        known_listed_liquid_assets_minor=liquid if known_liquid else None,
        missing_asset_or_liability_values=missing,
        coverage="listed_partial" if missing else body.coverage,
    )


class RollForwardInput(CurrencyInput):
    opening_minor: SignedMoney
    accepted_inflows_minor: Money
    accepted_outflows_minor: Money
    stated_closing_minor: SignedMoney
    opening_reconciled: Confirmed
    flows_complete: Confirmed
    period_start: IsoDate
    period_end: IsoDate
    basis: Literal["cash_movement"]

    @model_validator(mode="after")
    def ordered_period(self) -> RollForwardInput:
        if self.period_end < self.period_start:
            raise ValueError("invalid_date_order")
        return self


class RollForwardOutput(Contract):
    calculated_closing_minor: int
    unresolved_difference_minor: int
    arithmetically_reconciles: bool
    balancing_plug_minor: None = None


def _roll_forward(body: RollForwardInput) -> RollForwardOutput:
    calculated = (
        body.opening_minor + body.accepted_inflows_minor - body.accepted_outflows_minor
    )
    difference = body.stated_closing_minor - calculated
    return RollForwardOutput(
        calculated_closing_minor=calculated,
        unresolved_difference_minor=difference,
        arithmetically_reconciles=difference == 0,
    )


class ShockInput(CurrencyInput):
    explicit_shock_cost_minor: Money
    reviewed_covered_amount_minor: Money
    deductible_minor: Money
    liquid_cash_minor: Money
    chosen_reserve_floor_minor: Money
    coverage_basis: Literal["reviewed_scenario_amount_not_policy_interpretation"]

    @model_validator(mode="after")
    def coverage_not_excessive(self) -> ShockInput:
        if self.reviewed_covered_amount_minor > self.explicit_shock_cost_minor:
            raise ValueError("covered_amount_exceeds_shock_cost")
        return self


class ShockOutput(Contract):
    assumed_insurer_payment_minor: int
    household_cash_exposure_minor: int
    remaining_liquid_cash_minor: int
    shortfall_to_chosen_floor_minor: int


def _shock(body: ShockInput) -> ShockOutput:
    insurer = max(0, body.reviewed_covered_amount_minor - body.deductible_minor)
    exposure = body.explicit_shock_cost_minor - insurer
    remaining = body.liquid_cash_minor - exposure
    return ShockOutput(
        assumed_insurer_payment_minor=insurer,
        household_cash_exposure_minor=exposure,
        remaining_liquid_cash_minor=remaining,
        shortfall_to_chosen_floor_minor=max(
            0, body.chosen_reserve_floor_minor - remaining
        ),
    )


@dataclass(frozen=True)
class _Spec:
    family: str
    input_model: type[CurrencyInput]
    output_model: type[Contract]
    evaluate: Callable[..., Contract]
    basis: str
    assumptions: tuple[str, ...]
    exclusions: tuple[str, ...]
    professional_review: bool = False


_SPECS: dict[str, _Spec] = {
    "cash_ladder": _Spec(
        "C03",
        CashLadderInput,
        CashLadderOutput,
        _cash_ladder,
        "cash_movement",
        (
            "Confirmed opening and complete mandatory outflows are required.",
            "End-of-day settlement dates in the selected timezone; no intraday safety conclusion.",
            "Worst timing pays outflows earliest and receives inflows latest; best timing reverses this.",
        ),
        (
            "No inferred payday, holiday, business-day adjustment, overdraft facility or unknown outflow.",
        ),
    ),
    "reserve_coverage": _Spec(
        "C03",
        ReserveInput,
        ReserveOutput,
        _reserve,
        "ongoing_cost_reference",
        (
            "Cash divided by the explicitly selected monthly spending reference, without future income.",
            "The reserve floor is chosen by the caller; this model does not prescribe it.",
        ),
        (
            "Home equity, retirement assets, credit limits and expected reimbursement are not liquid cash.",
            "Reference coverage is not a dated cash safety forecast.",
        ),
    ),
    "fixed_debt": _Spec(
        "C04",
        DebtInput,
        DebtOutput,
        _debt,
        "forecast",
        (
            "Annual nominal fixed rate divided by 12; interest rounded half-up each month.",
            "End-of-month payment and extra principal; final payment is capped at balance plus interest.",
            "Cash fees are separate from principal and interest; payoff penalty applies only on modeled payoff.",
        ),
        (
            "No variable rates, daily accrual, promotional grace rules, escrow, balloon, unknown fees or lender quote.",
            "Unpaid ending principal means reported costs cover only the selected horizon.",
        ),
        True,
    ),
    "refinance": _Spec(
        "C04",
        RefinanceInput,
        RefinanceOutput,
        _refinance,
        "forecast",
        (
            "Replacement principal equals existing principal plus explicitly financed fees; no cash-out.",
            "Break-even is the first undiscounted interest saving that covers all stated fees.",
            "A later period can reverse first break-even; compare end-of-horizon savings and remaining debt.",
        ),
        (
            "No approval, lender quote, future rate reset, tax effect, discounting, escrow or unsupported fee.",
            "A lower scheduled payment does not by itself establish a saving.",
        ),
        True,
    ),
    "unit_cost": _Spec(
        "C05",
        UnitCostInput,
        UnitCostOutput,
        _unit_cost,
        "accounting_actual",
        (
            "Selected costs, discounts and matched refunds are allocated to the retained quantity explicitly.",
            "Compare results only after matching unit and currency.",
        ),
        (
            "No automatic receipt match, quantity inference, necessity judgment or purchase/return action.",
        ),
    ),
    "ownership_cost": _Spec(
        "C05",
        OwnershipInput,
        OwnershipOutput,
        _ownership,
        "forecast",
        (
            "Nominal amounts; upfront cash at time zero, recurring costs at each month end.",
            "Terminal costs/receipts occur at horizon end; nominal annual discount rate divided by 12.",
            "A lease cannot include an owned-asset resale receipt.",
        ),
        (
            "Unlisted fees, taxes, mileage, repairs and financing are not inferred or included.",
            "Residual receipts are assumptions, not guaranteed valuations; liquidity timing needs cash_ladder.",
        ),
    ),
    "pay_bridge": _Spec(
        "C06",
        PayInput,
        PayOutput,
        _pay,
        "ongoing_cost_reference",
        (
            "Withholding and deductions are supplied person-specific amounts, not tax estimates.",
            "52/26/24/12 pay periods are annualization conventions; monthly result is an average.",
            "Employer contributions are separate from spendable take-home pay.",
        ),
        (
            "No tax liability, benefit eligibility, 53/27-paycheck year adjustment or dated payroll forecast.",
        ),
    ),
    "leave_gap": _Spec(
        "C06",
        LeaveInput,
        LeaveOutput,
        _leave,
        "forecast",
        (
            "Selected paid work days and explicit take-home amounts determine the gap.",
            "Additional premium and care costs are total costs for the selected interval.",
        ),
        (
            "No leave entitlement, eligibility, clinical judgment, tax model or benefit election.",
        ),
        True,
    ),
    "portfolio_summary": _Spec(
        "C08",
        PortfolioInput,
        PortfolioOutput,
        _portfolio,
        "observed_value",
        (
            "Weights use listed positions at the supplied valuation date, without inferred holdings.",
            "Issuer and asset classifications must already be reviewed.",
            "Missing fees yield an unknown all-listed weighted fee, not zero.",
        ),
        (
            "No performance/return calculation, fund look-through, pricing, tax advice or trade recommendation.",
            "Listed coverage does not establish complete household coverage.",
        ),
        True,
    ),
    "goal_path": _Spec(
        "C09",
        GoalPathInput,
        GoalPathOutput,
        _goal_path,
        "forecast",
        (
            "At most 720 monthly returns, nominal and already net of fees, before tax.",
            "Contributions and withdrawals happen before each month's return; monthly amounts round half-up.",
            "Unfunded withdrawals are recorded as shortfalls and do not become negative invested assets.",
            "Real ending value is deflated using the explicit monthly inflation rate.",
        ),
        (
            "No success probability, simulation, tax, pension, contribution limit, longevity or withdrawal rule.",
            "This conditional path is not a complete retirement plan or investment recommendation.",
        ),
        True,
    ),
    "support_capacity": _Spec(
        "C10",
        SupportInput,
        SupportOutput,
        _support,
        "intended_obligation",
        (
            "Support capacity deducts the chosen floor and other commitments from liquid cash.",
            "Expected repayment is never counted as received cash; optional reserves are not legal debt.",
        ),
        (
            "No enforceability, gift/loan tax treatment, consent inference or recipient disclosure.",
            "Aggregate capacity does not establish payment-date safety; use cash_ladder for timing.",
        ),
        True,
    ),
    "decision_bridge": _Spec(
        "C11",
        DecisionInput,
        DecisionOutput,
        _decision,
        "comparison",
        (
            "Baseline and subsequent measures require identical semantic, coverage and period bases.",
            "The observed difference and its difference from expectation do not establish causation.",
        ),
        ("No inferred implementation or causal savings claim from a trend.",),
    ),
    "credit_utilization": _Spec(
        "C11",
        CreditInput,
        CreditOutput,
        _credit,
        "observed_value",
        (
            "Ratios use dated reported balances and positive limits for listed lines only.",
        ),
        (
            "No predicted score movement, bureau access, hard inquiry or dispute filing.",
        ),
    ),
    "listed_net_worth": _Spec(
        "C08",
        NetWorthInput,
        NetWorthOutput,
        _net_worth,
        "observed_value",
        (
            "Known listed assets less known listed liabilities, allocated by explicit ownership share.",
            "Missing values remain missing; lease and optional-reserve rows are excluded from assets/debt.",
            "Valuations retain their supplied dates; the model does not attest freshness.",
        ),
        (
            "Not a formal released balance sheet or a complete net-worth conclusion.",
            "Unknown values and excluded items are not measured zero; liquidity classification must be reviewed.",
        ),
    ),
    "roll_forward": _Spec(
        "C01",
        RollForwardInput,
        RollForwardOutput,
        _roll_forward,
        "cash_movement",
        (
            "Opening plus accepted inflows less accepted outflows is compared with stated closing.",
            "A difference is unresolved; no balancing plug is generated.",
        ),
        (
            "No source acceptance, transaction classification, transfer matching or close approval.",
        ),
    ),
    "shock_coverage": _Spec(
        "C10",
        ShockInput,
        ShockOutput,
        _shock,
        "forecast",
        (
            "Reviewed covered amount is before deductible; insurer payment cannot be negative.",
            "The input is a selected scenario amount, not a policy interpretation or claim determination.",
        ),
        (
            "No assessment of exclusions, coinsurance, eligibility, coverage limits or legal entitlement.",
            "Payment timing is not modeled; no insurance action or claim assertion is authorized.",
        ),
        True,
    ),
}


def calculator_metadata() -> dict[str, dict[str, Any]]:
    """Small descriptors for admission/routing, without constructing schemas."""
    return {
        identifier: {
            "calculator_id": identifier,
            "version": "1",
            "family": spec.family,
            "implementation_state": "implemented",
            "performance_class": "light",
            "maximum_monthly_periods": 720,
            "rounding_policy": "integer minor units; Decimal precision 40; ROUND_HALF_UP",
            "assumptions": list(spec.assumptions),
            "exclusions": list(spec.exclusions),
            "professional_review": spec.professional_review,
        }
        for identifier, spec in _SPECS.items()
    }


def calculator_catalog() -> dict[str, dict[str, Any]]:
    """Explicit server-side schema lookup; never add this set to global context."""
    metadata = calculator_metadata()
    for identifier, spec in _SPECS.items():
        metadata[identifier].update(
            input_schema=spec.input_model.model_json_schema(),
            output_schema=spec.output_model.model_json_schema(),
            output_envelope_schema=CalculationResult.model_json_schema(),
        )
    return metadata


def _period(body: CurrencyInput) -> str:
    if isinstance(body, CashLadderInput):
        return f"{body.start_date.isoformat()}/{body.end_date.isoformat()}"
    if isinstance(body, (SupportInput, RollForwardInput)):
        return f"{body.period_start.isoformat()}/{body.period_end.isoformat()}"
    if isinstance(body, (DebtInput, RefinanceInput)):
        return f"{body.horizon_months} months"
    if isinstance(body, OwnershipInput):
        return f"{body.months} months"
    if isinstance(body, GoalPathInput):
        return f"{len(body.monthly_returns)} months"
    if isinstance(body, PortfolioInput):
        return body.valuation_date.isoformat()
    if isinstance(body, CreditInput):
        return body.report_date.isoformat()
    if isinstance(body, NetWorthInput):
        return body.as_of_date.isoformat()
    if isinstance(body, LeaveInput):
        return f"{body.selected_work_days} selected work days"
    if isinstance(body, PayInput):
        return "annualized reference"
    if isinstance(body, DecisionInput):
        return f"{body.baseline.period_count} {body.baseline.period_unit}"
    return "selected snapshot"


def _metric_unit(key: str, currency: str) -> str:
    if key.endswith("_minor"):
        return f"{currency} minor units"
    if key.endswith(("_month", "_months")) or key == "reference_months_covered":
        return "months"
    if key == "minor_units_per_selected_unit":
        return f"{currency} minor units per selected unit"
    if any(part in key for part in ("ratio", "weight", "rate", "fraction")):
        return "ratio"
    return "count"


_DECIMAL_FIELDS = frozenset(
    {
        "annual_rate",
        "retained_quantity",
        "annual_discount_rate",
        "annual_fee_rate",
        "monthly_inflation_rate",
        "ownership_share",
    }
)


def _normalized_inputs(value: Any, field: str = "") -> Any:
    """Normalize only declared decimal fields; canonical identifiers stay intact."""
    if isinstance(value, dict):
        return {key: _normalized_inputs(item, key) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalized_inputs(item, field) for item in value]
    if value is not None and field in _DECIMAL_FIELDS | {"monthly_returns"}:
        number = Decimal(value)
        return "0" if number == 0 else format(number.normalize(), "f")
    return value


def calculate(
    calculator_id: str, version: str, inputs: dict[str, Any]
) -> dict[str, Any]:
    """Evaluate one allowlisted model; arbitrary expressions and SQL are absent.

    A successful calculation remains qualified: arithmetic does not establish
    accepted evidence, current authorization, suitability or professional review.
    """
    spec = _SPECS.get(calculator_id)
    if spec is None:
        raise CalculatorError("model_unavailable")
    if version != "1":
        raise CalculatorError("model_version_unavailable")
    try:
        body = spec.input_model.model_validate(inputs)
    except ValidationError as exc:
        missing = tuple(
            ".".join(str(part) for part in item["loc"])
            for item in exc.errors(include_input=False, include_context=False)
            if item["type"] == "missing"
        )
        raise CalculatorError(
            "missing_evidence" if missing else "invalid_inputs", missing_fields=missing
        ) from None
    try:
        with localcontext() as context:
            context.prec = 40
            normalized = _normalized_inputs(body.model_dump(mode="json"))
            result_id = hashlib.sha256(
                json.dumps(
                    {
                        "calculator_id": calculator_id,
                        "version": version,
                        "inputs": normalized,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ).encode("ascii")
            ).hexdigest()
            output = spec.evaluate(body)
            values = output.model_dump(mode="json")
    except DecimalException:
        raise CalculatorError("unsupported_numeric_range") from None
    metrics: list[Metric] = []
    for key, value in values.items():
        if (
            isinstance(value, bool)
            or isinstance(value, (list, dict))
            or "breach" in key
        ):
            continue
        if (
            isinstance(value, int)
            or value is None
            or (
                isinstance(value, str)
                and key
                not in {
                    "option_kind",
                    "selected_unit",
                    "coverage",
                    "implementation_status",
                    "attribution",
                }
                and "breach" not in key
            )
        ):
            metrics.append(
                Metric(
                    metric_id=f"{result_id}:{key}",
                    value=value,
                    unit=_metric_unit(key, body.currency),
                )
            )
    if isinstance(output, PortfolioOutput):
        for allocation in output.allocation:
            for suffix, value, unit in (
                ("value", allocation.value_minor, f"{body.currency} minor units"),
                ("weight", allocation.listed_weight, "ratio"),
            ):
                metrics.append(
                    Metric(
                        metric_id=f"{result_id}:allocation:{allocation.asset_class}:{suffix}",
                        value=value,
                        unit=unit,
                    )
                )
    return CalculationResult(
        result_id=result_id,
        calculator_id=calculator_id,
        currency=body.currency,
        basis=spec.basis,
        period=_period(body),
        values=values,
        metrics=metrics,
        assumptions=list(spec.assumptions),
        exclusions=list(spec.exclusions),
        professional_review=spec.professional_review,
    ).model_dump(mode="json")
