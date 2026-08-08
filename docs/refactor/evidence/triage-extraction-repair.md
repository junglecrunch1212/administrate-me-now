# Live Triage extraction repair evidence

Date: 2026-08-08

Branches: `codex/repair-triage-extraction`, `codex/repair-openclaw-skill-auth`

Disposition: **first repair live; authenticated NLP/tool-registration follow-up candidate**

## Incident evidence

The live member Comms projection contained 82 Gmail interaction rows while the
Triage projection contained zero candidates. The required subscriber
`pipeline:pipeline:noise_filtering` had:

- no checkpoint or successful delivery;
- five consecutive failures;
- a durable poison event;
- 347 relevant events of lag; and
- a runtime exception stating that `ADMINME_INSTANCE_DIR` was unavailable
  while the skill runner tried to resolve its raw-event path.

The checkpoint survived later healthy process starts exactly as the durable
bus contract required. Doctor still returned healthy because it counted only
the fourteen projection subscribers. The identity subscriber was separately
poisoned by reusing one endpoint-wide idempotency key with a changed
source-interaction payload.

No provider write occurred during diagnosis. Observation mode remained active.
No private message text, household identifier, credential, or token was copied
into the repository or this evidence.

The first reviewed repair merged as PR #289 at commit
`fc258ec84d20706a249a0f9fdc1b7cdac4c8db6c` and upgraded through the guarded
updater. Its versioned replay completed with zero lag, failures, poison events,
confirmations, canonical task creations, or external-write requests. It
produced 51 exact-source pending proposals from 31 messages: 47 tasks, two
recurrences, one reminder, and one relationship fact.

That live evidence exposed two further production-only gaps hidden by fixture
tests: all 105 language calls lacked the injected Gateway bearer token and,
after supplying it in an isolated synthetic probe, the Gateway reported
`llm-task` unavailable. The pinned OpenClaw build includes `llm-task` as a
bundled optional plugin, disabled by default; it also requires an explicit
`tools.alsoAllow` entry. Plugin discovery of `administrateme` alone was not
proof of the constitutional model seam.

## Reviewed repair behavior

1. The production composition explicitly injects the event sink, encrypted
   event log, and raw-event directory into the skill runner. Retried delivery
   reuses the recorded model result.
2. Durable consumer rows store their reviewed version. A degraded consumer is
   reset only when that version changes, and replay resumes after the last good
   checkpoint. The same version remains poisoned and fail-closed.
3. `noise_filtering` v3 catches non-authoritative language-seam failures,
   retains a defensive classification, and continues deterministic extraction.
4. `classify_message_nature` v4 can extract due actions, requested decisions or
   replies, bills, recurrences, and outbound member promises. Fixed code
   requires a verbatim contiguous subject/body quote, a closed target kind,
   allowlisted JSON-scalar fields, exact private member scope, and deterministic
   deduplication. Confidence never controls admission.
5. Gmail history preserves `SENT` as `messaging.sent@v2`. Historical
   `messaging.received@v3` facts retain the same provider label, so replay can
   recover their outbound meaning without mutating the source event. Inbound
   promises cannot be assigned to the member.
6. Identity observation idempotency is per source event while endpoint identity
   remains stable, so repeat mail from one sender cannot poison the subscriber.
7. Doctor fails when a required reactive subscriber is absent, failed,
   degraded, or poisoned. An empty Triage queue is therefore never accepted as
   proof that extraction succeeded.
8. The skill-runtime dependency includes the Keychain-resolved Gateway token
   with secret-safe representation; explicitly constructed production runtimes
   never consult ambient token state.
9. Bootstrap and upgrade reconcile the pinned bundled `llm-task` plugin plus
   its additive tool-policy allow entry through OpenClaw's validated config
   command. Doctor requires configuration and runtime tool registration, not
   only AdministrateMe plugin discovery.
10. `noise_filtering` v4 replays language recovery without rewriting fallback
    classifications or existing proposals. Changed semantic facts use a
    reviewed idempotency generation and only newly discovered candidate
    kind/key pairs append.

## Executable proof

The production-composition Triage acceptance test runs with
`ADMINME_INSTANCE_DIR` absent, appends a private Gmail recurring-bill fact,
waits for the real pipeline subscriber and Triage projection, and proves:

- one Task proposal and one Recurrence proposal exist;
- each title is exact source evidence;
- each remains human-gated;
- neither the Tasks nor Recurrences source-of-truth projection contains a new
  canonical row; and
- the provider fixture delivered no external effect.

Additional tests prove outbound promise direction, Gmail `SENT` emission,
ungrounded model rejection, field filtering, overlapping-evidence deduplication,
generic language-seam failure containment, consumer-version replay, repeat
sender idempotency, and Doctor poison failure.

Final candidate command, using the reviewed installed Node runtime:

```bash
PATH=/opt/homebrew/opt/node@24/bin:$PATH \
  bash scripts/acceptance/release-candidate.sh
```

Result:

- strict acceptance: 36 passed;
- root and pack Python: 1,631 passed;
- member bridge: 60 passed;
- Ruff: 502 files formatted and 225 production files clean;
- mypy: 225-source release target clean;
- Pyright: zero errors/warnings;
- dependency audits: Python clean; production Node clean;
- performance budgets: pass;
- architecture negative, clean-install, tenant-isolation, backup/restore: pass;
- pinned OpenClaw plugin contract: 15 passed, build and package pass; and
- complete release command: `ADMINISTRATEME RELEASE CANDIDATE SUITE: PASS`.

The only warnings were the already-known Starlette `httpx` deprecation,
Poetry metadata deprecations, a newer optional Pyright release, and a
development-only Node audit advisory; the production Node audit reported zero
vulnerabilities.

## Phase-B requirement

After independent review and merge, use only the guarded updater. Verify the
pre-switch encrypted backup and observation mode, allow the versioned
subscribers to replay, then require:

- both required subscribers non-poisoned with zero relevant lag;
- Doctor healthy including `required_reactive_pipeline_delivery`;
- Triage processing health healthy with a non-null extraction checkpoint;
- aggregate candidate counts by type and review lane;
- a privacy-safe sample confirming source grounding; and
- zero provider writes during replay.

Live candidate contents and household decisions remain private runtime data and
must not be committed as release evidence. The follow-up must additionally
prove an authenticated synthetic `llm-task` call, successful language records
for the source backlog, grounded aggregate candidate counts, zero lag, healthy
Doctor tool-registration evidence, and zero provider/canonical writes.
