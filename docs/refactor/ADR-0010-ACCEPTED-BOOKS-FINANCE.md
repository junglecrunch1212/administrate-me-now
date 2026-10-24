# ADR-0010 — Accepted Books publications and bounded finance conversations

Status: implemented candidate; synthetic validation and activation gates apply.
Date: 2026-10-23

**SUPERSEDE** ADR-0007's XLSX planning authority for a Books-owned book. Books
owns accepted accounting/planning publications and its scenario math. The legacy
XLSX path is not reused for this namespace. No automatic migration or dual write
is authorized. Activation must disable the legacy planning reverse path for the
adopted book; until that exclusion is proven, activation remains blocked.

**PRESERVE** ADR-0001's dependency direction, one Python runtime, OpenClaw model
and scheduling boundary, domain approval, privileged owner access, observation,
and encrypted append-only history. No provider token reuse or provider feed is
introduced. Private publication ingress and finance model access default off;
production configuration does not enable these settings automatically.

**SUPERSEDE** the mixed dashboard for an owner with an accepted Books publication.
The Finance page leads with that publication, its dated projections and owner
choices. Legacy workbook forecasts and provider-derived plan cards are not
rendered alongside it, including when reopening an older Finance view URL.
This presentation change does not itself enable import or model processing.

**MODERNIZE** Finance's empty state: unknown is null, partial account observations
are qualified, forecasts require authored inputs, and unreleased statements are
unavailable. A catalog route is not evidence of a supported calculation model.

The canonical interchange is
`adminme/finance/contracts/household-books.accepted-publication.v1.schema.json`.
Its adjacent `.sha256` is the exact UTF-8 file digest. The synthetic publication
is `tests/fixtures/finance/accepted-publication.synthetic.json`. The application
owns this schema and tests. A Books exporter must adopt the same version/hash;
no source adapter may invent a parallel shape. No Sheet edits are part of this
change. Initial exports contain bounded accepted aggregate metrics and scenario
results, with exact revision references, separate source/acceptance clocks,
quality dimensions, report release states and opaque owner-only evidence IDs.
Raw transaction/receipt acquisition is outside this interface.

Canonical content hashing excludes only `content_sha256`, sorts object keys,
uses compact ASCII JSON and disallows nonfinite values. Schema hashing uses the
unmodified file bytes. A whole publication validates before a single private
`finance.publication_accepted` event. The prior-publication successor slot has an
event-log idempotency key; conflicting content or forks reject. The money
projection writes immutable history, indexed metrics and the current pointer in
one transaction. Partial validation never advances the pointer. A crash after
commit is resolved from the same receipt and replay; it is not permission to
read pending Books recovery revisions or clear a recovery hold.

Numbers preserve basis, currency/unit, period, owner/allocation, inclusion policy,
quality and evidence. Accounting actuals, cash movement, observed values, chosen
budget, cost references, forecasts and intended obligations cannot mix silently.
The four synthetic scenario results demonstrate unchanged export, not household
scenario validation. Missing openings/coverage block the associated reports.

Owner-only results use the private book namespace and authenticated actor.
Another adult, a joint session, child, coach, family audience or professional
gets no account existence/counts or source link. Joint processing and authorized
source proxies require additional reviewed authority support. Existing generic
read-context grants do not imply privileged model-processing consent. Source
instructions are data, never policy. The runtime may return a preparation route
without returning private evidence.

The small native facade has status/query/calculate/job operations. Full F01–F31
metadata stays server-side; persistent context is a bounded status pointer.
Calculators accept named typed models and exact arithmetic, not expressions or
SQL. Accepted scenario outputs are returned unchanged. Complex tax, legal,
benefit eligibility and unvalidated strategic variants have explicit model and
professional boundaries, with useful evidence/preparation workflows.

Heavy work remains unavailable until reserved general-CoS capacity, process
termination/memory enforcement, durable terminal receipts and mixed-load
acceptance are proven. A disabled job route is not an implemented worker.
Protected caches are optional; private data is not persisted in a generic cache
or vector corpus. Resource limits and remaining gates are recorded in
`FINANCE-RESOURCE-GATES.md` and the capability acceptance ledger.

Activation requires separate owner authorization, exporter schema adoption,
private accepted-data comparisons, model/policy/tool verification, legacy XLSX
exclusion, guarded update/backup/rollback, authenticated member acceptance and
measured general-CoS performance. This candidate authorizes none of those live
operations.

## Dated projection export

The optional `cash_projection` object preserves Books-produced current,
expected and chosen scenarios, each with 7/30/90/180/365-day horizons. Horizon
end dates are `as_of_date + days` in the declared timezone/calendar-date model.
The opening is a reconciled liquid-cash value, not net worth. Mandatory-flow
coverage is explicit; missing opening/coverage forbids numerical safety results.
Shortage/surplus dates are accepted engine signals, not recomputed here.

`cash_projection` and `cash_events` reports index these accepted values and
planned events unchanged. Events retain direction, kind, certainty, earliest/
latest dates and protected evidence references. They are forecasts, not actual
posted transactions. Event detail defaults to the current scenario and 90 days;
callers can explicitly select a supported scenario/horizon. Economic-cost
measures are not separately defined in v1; a later reviewed schema version must
specify them rather than relabel cash or accounting numbers. The dated synthetic
fixture is `accepted-publication.dated.synthetic.json`. Schema adoption remains
a coordinating review gate, independent from this producer-neutral reader.
