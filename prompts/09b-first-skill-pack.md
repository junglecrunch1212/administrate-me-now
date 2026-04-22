# Prompt 09b: First skill pack — classify_thank_you_candidate

**Phase:** BUILD.md L4 (skills).
**Depends on:** Prompt 09a. Wrapper functional.
**Estimated duration:** 2 hours.
**Stop condition:** The canonical skill pack from REFERENCE_EXAMPLES.md §3 is installed and running; fixture tests pass; running it against the three provided fixtures produces the expected classification.

---

## Read first (required)

1. `ADMINISTRATEME_REFERENCE_EXAMPLES.md` §3 **in full**. This is the complete pack. Reproduce it faithfully.
2. `docs/openclaw-cheatsheet.md` question 1 (install) and question 5 (SKILL.md shape).
3. `adminme/lib/skill_runner/wrapper.py` from prompt 09a — confirm the pack shape matches what the wrapper expects.

## Operating context

This is the first real skill pack. It establishes the pattern every other skill pack follows. Don't cut corners — the test fixtures, prompt, and schemas from REFERENCE_EXAMPLES.md §3 are what later pipelines will rely on.

## Objective

Implement `packs/skills/classify_thank_you_candidate/` per REFERENCE_EXAMPLES.md §3. Install it into OpenClaw via the `openclaw` CLI. Run it against the three provided fixtures and verify outputs.

## Out of scope

- Do NOT implement the `thank_you` pipeline that uses this skill. That's prompt 10b.
- Do NOT build other skill packs (classify_commitment_candidate, extract_commitment_fields, etc.). They come with their owning pipelines.

## Deliverables

### The pack

`packs/skills/classify_thank_you_candidate/`:
- `pack.yaml` — exactly as §3 specifies.
- `SKILL.md` — prompt body per §3.
- `input.schema.json` — per §3.
- `output.schema.json` — per §3.
- `handler.py` — post-processing as specified.
- `examples/` — the input/output pairs.
- `tests/fixtures/` — the three named fixtures: `kleins_hosted_us`, `reciprocal_coffee`, `coparent_pickup`.
- `tests/test_skill.py` — unit tests per §3's test pattern.

### Validate (Phase A)

**No live OpenClaw install in Phase A.** Instead:
1. Validate the pack's structure using the pack_loader (from prompt 09a's `adminme/lib/skill_runner/pack_loader.py`). All manifest fields present; schemas parse; handler.py importable.
2. Add the pack ID to `bootstrap/pack_install_order.yaml` — an ordered list of built-in packs the bootstrap wizard installs into OpenClaw during Phase B. This file is consumed in prompt 15 and by the bootstrap wizard in prompt 16.
3. Reference the pack in any pipeline that will depend on it (in later prompts).

### Test (Phase A)

Use mocks for OpenClaw. The `tests/unit/test_skill_wrapper.py` infrastructure from prompt 09a provides fixture responses that exercise the classifier's handler logic without calling a live gateway. For each of the three fixtures (`kleins_hosted_us`, `reciprocal_coffee`, `coparent_pickup`), the test mocks OpenClaw's response to what a well-behaved gateway would return, then asserts the handler's post-processing produces the expected `is_candidate` / `confidence` / fallback behavior.

A separate `tests/integration/test_classify_thank_you_live.py`, marked `@pytest.mark.requires_live_services`, exercises the real classifier during Phase B.

## Verification

```bash
# Phase A: no live OpenClaw
poetry run python -c "
from adminme.lib.skill_runner.pack_loader import PackLoader
p = PackLoader().load('classify_thank_you_candidate')
print(f'Pack: {p.id} v{p.version}, schemas OK, handler OK')
"
poetry run pytest packs/skills/classify_thank_you_candidate/tests/ -v
poetry run python scripts/demo_classify_thank_you.py  # uses mocked OpenClaw

# Verify pack is queued for Phase B install
grep classify_thank_you_candidate bootstrap/pack_install_order.yaml

git add packs/skills/classify_thank_you_candidate/ bootstrap/pack_install_order.yaml
git commit -m "phase 09b: classify_thank_you_candidate skill pack"
git push
```

Expected:
- Pack validates structurally.
- Unit tests pass using mocked OpenClaw responses.
- Demo script prints classification for each fixture (via mock).
- Pack is listed in `bootstrap/pack_install_order.yaml` for Phase B install.

## Stop

**Explicit stop message:**

> First skill pack live. The pattern is established. Future prompts that add skill packs follow the same shape. Ready for prompt 10a (pipeline runner).
