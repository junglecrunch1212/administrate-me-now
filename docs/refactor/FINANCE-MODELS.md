# Bounded finance models

Classification: **MODERNIZE** the deterministic calculation seam in
[ADR-0007](ADR-0007-FINANCE-WORKBOOK-AUTHORITY.md), preserving accepted Books
scenario outputs rather than reimplementing their formulas.

`adminme/finance/calculators.py` implements named, version `1`, explicitly
hypothetical models. This is code implementation, not live financial acceptance
or professional validation. The service resolves accepted references and enforces
current actor/audience authority independently. No calculator changes a provider,
publication, decision or permission. No model calls occur here.

`calculator_metadata()` supplies lightweight routing/admission descriptors;
`calculator_catalog()` supplies the strict Pydantic input and output schemas on
explicit request. Do not put the whole schema registry into core CoS context.
`calculate(id, version, inputs)` returns basis, period, currency, named metric
receipts, assumptions and exclusions. Every successful result is `qualified` and
`explicit_hypothetical`; arithmetic cannot establish data acceptance, suitable
advice, or permission to act. Unknown model/version and invalid or missing inputs
produce safe error codes without echoing financial values.

| Model | Supported calculation | Boundary and independent oracle |
|---|---|---|
| `cash_ladder` | At most 100 dated flows over 366 days; independent early/late settlement bounds, minimum and closing cash, reserve breach date | Reconciled opening and complete mandatory flows required. No intraday/holiday inference. Hand-worked early bill/late income ledger tests. |
| `reserve_coverage` | Liquid cash divided by positive selected monthly spending; gap to chosen floor | No universal reserve prescription or dated safety verdict. Exact ratio and zero-denominator tests. |
| `fixed_debt` | Fixed nominal APR/12, monthly rounded interest, principal, extra principal, fees, remaining liability and payoff | At most 720 months; no daily accrual, promotional or variable terms, escrow or unknown fees. Hand-worked monthly interest and principal ledgers. |
| `refinance` | Compare fixed-debt paths, fees, transition cash, remaining debt and first undiscounted interest/fee break-even | Replacement balance equals payoff plus financed fees. First break-even need not persist. No lender quote/tax/discount conclusion. Independent fee and monthly-interest cases. |
| `unit_cost` | Allocated tax/shipping/discount/matched refund over retained quantity | No receipt matching or item-necessity inference. Hand-worked item allocation identity. |
| `ownership_cost` | Nominal and discounted supplied upfront, monthly and terminal cash costs | No implicit lease resale asset. Costs/fees must be supplied; residual is assumed. Zero-rate identity and one-period present-value oracle. |
| `pay_bridge` | Person-specific supplied gross, withholding, deductions and employer contributions; 52/26/24/12 annualization | No tax liability or dated payroll/extra-paycheck-year model. Independent gross/net and annual/monthly identities. |
| `leave_gap` | Selected paid work days, supplied take-home and incremental premium/care costs | No leave or insurance eligibility inference. Independent income-gap ledger. |
| `portfolio_summary` | Listed allocation, position/issuer concentration and known-value weighted fees, at most 100 positions/20 classes | No inferred holdings, fund look-through, return measure or trade advice. Unknown fees retain null all-listed metric. Weighted-average oracle. |
| `goal_path` | At most 720 supplied monthly net-of-fee returns, beginning-month contributions/withdrawals, shortfall and real ending value | Deterministic conditional path only; no probability, seeded simulation, tax, pension or retirement sufficiency claim. Independent reversed-return sequence and inflation cases. |
| `support_capacity` | Liquid cash less chosen reserve and complete other cash commitments | Expected repayment is not cash; optional reserve is not debt. No legal/tax classification, consent or timing guarantee. Hand-worked capacity identity. |
| `decision_bridge` | Like-for-like observed change and deviation from selected expectation | Semantic, coverage and period bases must match. Attribution always unestablished. Independent signed-difference case. |
| `credit_utilization` | Weighted listed balance/limit ratio and highest individual ratio | No score movement prediction or bureau action. Independent weighted-ratio case. |
| `listed_net_worth` | Known listed assets/liabilities with explicit ownership allocation and valuation dates; separate liquid subset | No formal statement. Missing values prevent a net conclusion; leases/reserves excluded. Independent ownership/elimination/missing-data cases. |
| `roll_forward` | Explicit opening + cash inflows − cash outflows against stated closing | Only arithmetic reconciliation; no source acceptance or balancing plug. Independent closing difference. |
| `shock_coverage` | Reviewed pre-deductible scenario amount, deductible, exposure and reserve gap | No policy interpretation, coinsurance/eligibility determination or claim assertion. Independent shock/deductible case. |

Inputs reject additional fields, formula strings, float monetary values, invalid
units, invalid rates and unsupported dimensions. Arithmetic uses integer minor
units and `Decimal` with a local precision of 40 and half-up rounding. Rates are
finite decimal strings. Inputs are capped at 10^12 minor units; results above
9×10^15 minor units fail instead of overflowing consumer numeric precision.
The general result remains subject to the service's stricter turn/evidence budget.

Tests are in `tests/unit/test_finance_calculators.py`. They include independent
numeric examples, all-model missing/invalid input cases, strict extra-field
rejection, zero/unknown distinctions, declared exclusions, bounded dimensions,
immutable metric identifiers and independence from caller decimal context. They
must pass in hosted CI or a disposable lab before any verified-model claim.
The parent capability acceptance ledger records composition-root and live status.

Tax-year/jurisdiction estimates, MFS alternatives, variable-rate refinancing,
seeded retirement simulations, pension/annuity rules, complex securities, policy
interpretation, estate/foreign/business conclusions and source matching are not
implemented by this module. Route these to precise evidence requests and
professional preparation; do not substitute the presence of a related model for
support of an excluded variant. No numerical tax model is advertised.
