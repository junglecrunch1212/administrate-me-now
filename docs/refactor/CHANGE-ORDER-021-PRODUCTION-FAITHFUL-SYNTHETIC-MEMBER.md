# CO-021 — Production-Faithful Synthetic Member

Status: **PHASE A IMPLEMENTED; NO DEPLOYMENT OR LIVE PROVIDER CHANGE AUTHORIZED**

Date: 2026-08-30

Governing sources: ADR-0000, ADR-0001, ADR-0013, D6, D15, D26, CO-007,
CO-011, CO-014, CO-019, CO-020, the UX behavior contract, and the acceptance
contract.

## Objective

Provide a persistent member whose fictional life has the same useful product
shape as a busy adult household member: multiple communications accounts,
mixed Gmail categories, real-contact obligations, repeated sign-in notices,
calendar events, provider tasks, finance observations, Triage candidates,
prior feedback, and a reversible learned shadow rule. The member exists so a
human can continuously inspect and operate the portal from the central host
without weakening the rule that the central infrastructure identity is never
a real household member.

The scenario must exercise the production Python composition. A source fact is
appended to the encrypted log, consumed by the durable bus and normal
pipelines, interpreted through the pinned OpenClaw `llm-task` seam, projected
into member surfaces, and changed only through the normal authenticated API
and Triage human gate. A dashboard decision must persist and replay after
restart. No real household content, provider credential, or live provider call
is part of the default scenario.

## Runtime contract

- The scenario is versioned as `member-life-v1` and uses an explicitly marked
  `tenant-synthetic-*` instance. An existing directory without the exact
  synthetic marker is rejected.
- Synthetic mode is constructed only by the developer CLI. Production
  environment parsing has no switch that can enable it.
- Browser identity fallback is accepted only from loopback and only for the
  exact synthetic principal. A supplied unknown identity still fails closed.
- Every configured connection is explicitly marked synthetic. The central
  supervisor does not instantiate any provider adapter. Settings presents
  simulated connection health and capability shape without claiming a live
  provider handshake.
- The portal displays an unavoidable synthetic banner and names the active
  model transport. The default is the deterministic OpenClaw fixture. An
  explicitly requested Gateway mode uses the pinned loopback Gateway and
  requires injected OpenClaw credentials; no provider SDK or second router is
  introduced.
- Observation mode remains active. Synthetic Triage approvals can create
  canonical household truth inside the isolated database; provider-facing
  actions still require the normal independent approval path and cannot reach
  real adapters.
- Seed writes are idempotent. Restart reuses the same encrypted log, replays
  projections, preserves member decisions/feedback/rules, and does not clone
  source facts.

## Scenario coverage

The initial scenario includes:

1. personal and work Gmail accounts plus a Messages-shaped source;
2. ordinary requests from fictional contacts and organizations;
3. two same-context sign-in notices that exercise deterministic matter
   grouping without semantic cross-provider merging;
4. Updates, Promotions, Social, Spam, Important, and Inbox label observations;
5. Google Calendar, Google Tasks, Apple Reminders, Apple Notes, Plaid, and
   contact connection shapes;
6. three provider task observations, three calendar events, a privileged
   synthetic account and money-flow observation;
7. two historical recurring-normal feedback facts and one active,
   reversible, shadow-only recurrence rule; and
8. member chat plus message classification through the same OpenClaw-owned
   model boundary used by production.

The fixture is behaviorally representative, not a copy or statistical claim
about any named member. Adding real names, identifiers, addresses, account
values, message bodies, or provider tokens to this scenario is prohibited.

## Operator use

From a locked development checkout:

```bash
PYTHONPATH=bridge poetry run adminme demo synthetic-member \
  --instance-dir /absolute/path/to/dedicated-synthetic-instance
```

The server binds only `127.0.0.1` and prints the local Triage URL. The chosen
directory is persistent; use a new dedicated directory when a clean scenario
is needed. The command will not repurpose an ordinary or live instance.

Real OpenClaw model routing is an explicit developer choice:

```bash
ADMINME_OPENCLAW_GATEWAY_TOKEN=... \
ADMINME_OPENCLAW_SHARED_SECRET=... \
PYTHONPATH=bridge poetry run adminme demo synthetic-member \
  --instance-dir /absolute/path/to/dedicated-synthetic-instance \
  --model-transport gateway
```

This still does not authorize deployment, provider enrollment, write
enablement, Keychain changes, or changes to a live family instance.

## Acceptance

`tests/acceptance/test_synthetic_member_sandbox.py` proves through
`create_application` that:

- loopback synthetic ingress succeeds while an unknown explicit identity and
  an ordinary no-header runtime fail closed;
- nine source connections are visible as synthetic connected/running sources;
- source facts produce six candidates in five grouped matters, with reviewed
  Gmail-category context and the seeded learned shadow rule;
- confirming one candidate materializes a real canonical task, while
  dismissing another emits reversible feedback;
- pinned-seam `skill.call.recorded` evidence identifies the fixture model and
  member chat streams through the OpenClaw transport contract;
- no external write request is produced by those decisions; and
- restart retains decisions and feedback while preserving exactly nine seeded
  message facts.

## Constitutional disposition

- **PRESERVE:** production Tailscale identity for real members, the non-member
  central host, encrypted append-only truth, deterministic projections,
  human-gated Triage, scoped learning, observation mode, provider governance,
  and the pinned OpenClaw seam.
- **MODERNIZE:** the one-message credential-free demo into a persistent,
  visually operable, multi-source member lifecycle without changing the live
  runtime topology.
- **SUPERSEDE:** using copied member data, browser header extensions, central
  host impersonation, or an unmarked local database as the primary frontend
  audit method.
- **DEFER:** statistically generated long-horizon history and synthetic
  terminal provider mutations across every adapter. Neither is required for
  safe continuous frontend review, real Triage decisions, learning, replay,
  or OpenClaw-bound interpretation.
