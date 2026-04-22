# Prompt 10b: Reactive pipelines

**Phase:** BUILD.md L4 pipelines (reactive half).
**Depends on:** Prompt 10a. Runner functional.
**Estimated duration:** 4-5 hours.
**Stop condition:** Four pipelines implemented, each with fixtures; each produces the expected events when fed test inputs.

## Read first

1. `ADMINISTRATEME_BUILD.md` subsections (grep):
   - `identity_resolution`
   - `noise_filtering`
   - `commitment_extraction`
   - `thank_you`
2. `ADMINISTRATEME_REFERENCE_EXAMPLES.md` §2 (commitment_extraction worked in full).
3. `docs/architecture-summary.md` §5.

## Objective

Implement four reactive pipelines as pack directories under `packs/pipelines/`. Each subscribes to the bus; each has fixtures; each is tested.

## Out of scope

- Proactive pipelines — prompt 10c.
- Skill packs beyond the two canonical ones — inline them only if needed; the four pipelines here need `classify_commitment_candidate`, `extract_commitment_fields`, `classify_thank_you_candidate` (exists), and `extract_thank_you_fields`. Create the commitment skills in this prompt following the §3 pattern; `extract_thank_you_fields` is trivial and lives in the thank_you pipeline.

## Deliverables

### Pipelines (each in `packs/pipelines/<name>/`)

- **`identity_resolution`** — subscribes to `messaging.received`, `messaging.sent`, `telephony.sms_received`. Resolves sender/recipient identifiers to parties. Emits `identity.merge_suggested` for fuzzy matches above threshold; `party.created` for no match. Never auto-merges. Full provenance.
- **`noise_filtering`** — subscribes to `messaging.received`, `telephony.sms_received`. Classifies spam / bulk / one-time-code / transactional. Emits `noise.filtered` event when sensitive to filter. Does NOT delete anything; just tags.
- **`commitment_extraction`** — see REFERENCE_EXAMPLES.md §2 in full. Three skill calls (classify, extract, suggest_due). Emits `commitment.proposed`. Includes the kate_kitchen_walkthrough and coach_practice_reschedule fixtures.
- **`thank_you`** — subscribes to `messaging.received` where sender is a non-household party. Runs classify_thank_you_candidate (from 09b). On positive, runs extract_thank_you_fields (new skill in this prompt; simple). Emits `commitment.proposed` with `kind: thank_you`.

### New skill packs

- `packs/skills/classify_commitment_candidate/` — same §3 shape; prompt body per REFERENCE_EXAMPLES.md §2 text.
- `packs/skills/extract_commitment_fields/` — same shape.
- `packs/skills/extract_thank_you_fields/` — small; inline per REFERENCE_EXAMPLES conventions.

Install all into OpenClaw.

### Schemas

Add event schemas for any new events: `identity.merge_suggested`, `noise.filtered`, `commitment.proposed` already exists, expand if payload fields are needed.

### Tests

Per pipeline:
- Fixture inputs → expected output events.
- Replay test: feed 50 fixture events through the runner; assert final event log state matches snapshot.

## Verification

```bash
poetry run pytest tests/unit/test_pipeline_* packs/pipelines/*/tests/ -v
# All 09b tests still pass; all 08 security tests still pass
poetry run pytest -v
git commit -m "phase 10b: reactive pipelines"
```

## Stop

> Four reactive pipelines live. Ready for 10c (proactive pipelines as standing orders).

