# Prompt 16: Bootstrap wizard

**Phase:** BUILD.md "BOOTSTRAP WIZARD" — nine sections, Textual TUI, resumability.
**Depends on:** Prompt 15. All pieces ready for a household to actually set up.
**Estimated duration:** 6-8 hours. This is one of the longest prompts; budget accordingly.
**Stop condition:** `./bootstrap/install.sh` in lab mode runs end-to-end, producing a populated fake instance with events flowing through all layers. `./bootstrap/install.sh` in real mode can also run but halts at any section needing tenant-specific input.

---

## Read first (required)

1. `ADMINISTRATEME_BUILD.md` **"BOOTSTRAP WIZARD"** section — the nine-section structure in full. This is a long section; read every subsection.
2. `ADMINISTRATEME_BUILD.md` **"SAMPLE INSTANCE — WHAT `~/.adminme/` LOOKS LIKE AFTER BOOTSTRAP"** — the target state.
3. `ADMINISTRATEME_FIELD_MANUAL.md` chapter 4 — the operator-facing version of what this wizard does.
4. `docs/reference/textual/` — TUI framework patterns (App structure, Screens, reactive attributes, testing harness). Mirror only; no WebFetch.
5. `docs/reference/sqlcipher/` — key derivation, encryption-at-rest. Mirror only.
6. `docs/reference/openclaw/` — persona-activation, pack-install, plugin-install, standing-order-registration APIs. The wizard calls every one of these. Mirror only.

## Operating context

**This prompt BUILDS the bootstrap wizard code. The wizard itself RUNS during Phase B on the Mac Mini.** Your job is to produce working wizard code and verify it in your Phase A sandbox using mocks and lab-mode. The operator runs the real wizard later.

The bootstrap wizard is the only interactive piece in the system. It runs once per instance setup. The operator sits in front of a Terminal, the wizard walks them through nine sections with Textual TUI, and at the end they have a working AdministrateMe instance.

Two modes:
- **Real mode** (`./bootstrap/install.sh`): against real tenant accounts. Asks for real credentials. Installs and configures everything. This is what James runs on the Mac Mini. **You cannot test this mode in Phase A** — it requires live OpenClaw, real Google/Apple/Plaid credentials, etc. Your verification for real mode is: the wizard starts, reaches the first credential prompt, and stops gracefully (no crashes) when you supply a fixture-invalid credential.
- **Lab mode** (`./bootstrap/install.sh --lab-mode`): against fake data. Skips all credential prompts; uses fixture tenant directory; wires sandbox Plaid / test Gmail / mock OpenClaw. **This IS testable in Phase A** — the whole point of lab mode is that it runs end-to-end with no external dependencies. Your Phase A integration test drives this mode non-interactively.

The wizard must be **resumable** — if the operator quits partway, they can re-run `./bootstrap/install.sh` and it picks up where they left off. This is achieved via:
- `$ADMINME_INSTANCE_DIR/bootstrap-answers.yaml.enc` — encrypted file storing every answer given so far.
- Event log — every section that completes emits a `bootstrap.section.completed` event.
- On restart: re-read answers, replay events, skip completed sections, resume at the first incomplete one.

## Objective

Build the full bootstrap wizard as a Textual TUI, with all nine sections, resumability, idempotency, and lab-mode support.

## Out of scope

- Do NOT write any production code beyond the bootstrap itself. All dependencies are complete at this point; this prompt assembles them.
- Do NOT implement CLI subcommands beyond `bootstrap` itself (that's prompt 17).

## Deliverables

### `bootstrap/install.sh`

Thin shell wrapper that:
1. Verifies Python 3.12+, Poetry, OpenClaw reachability.
2. Activates the virtualenv.
3. Exports `ADMINME_INSTANCE_DIR` (default `~/.adminme`; `~/adminme-lab-data` in lab mode).
4. Invokes `poetry run python -m bootstrap.wizard $@` with any flags passed through.

### `bootstrap/wizard.py`

Textual TUI app. Nine sections per BUILD.md:

1. **Environment preflight** — runs same checks as prompt 00, surfaces any issues, offers remediation where possible.
2. **Tenant identity + member directory** — household name, members (name, role, age, color, kid stars config, emergency contacts).
3. **Storage + encryption** — SQLCipher key (store in 1Password; ref in config), backup destinations.
4. **Channels (OpenClaw side)** — pair iMessage via BlueBubbles; add Telegram/Discord/web if desired.
5. **External data sources** — Apple ID, Google Workspace OAuth, iCloud CalDAV, Plaid Link for each institution.
6. **Packs to install** — list built-in packs + any third-party; operator picks which to install. Adapters / pipelines / personas / profiles / skills.
7. **Seed data** — contacts import, calendar backfill, lists seeding. Sections skipped produce inbox tasks for the operator to complete later.
8. **Governance config** — review action_gates, rate limits, coach_can_see columns; operator can edit before continuing.
9. **Final readiness check** — verifies each layer produces expected events; event log has bootstrap.* events; projections rebuilt from log match live state; OpenClaw registrations match BUILD.md's expected list.

### Sections as pluggable modules

Each section in `bootstrap/sections/<n>.py`. Each section exports:
- `id: str`
- `title: str`
- `should_run(answers: dict) -> bool` — returns False if already complete (from prior answers).
- `async def run(app, answers: dict) -> dict` — runs the interactive flow; returns updated answers. May emit events along the way.
- `async def verify(answers: dict, event_log) -> bool` — called during resumability; checks the section's completion events.

### Resumability

`bootstrap/resume.py`:
- Loads `~/.adminme/bootstrap-answers.yaml.enc`.
- Replays `bootstrap.section.completed` events from event log.
- Determines first incomplete section.
- Starts wizard at that section.

### Lab mode

`--lab-mode` flag:
- `ADMINME_INSTANCE_DIR` defaults to `~/adminme-lab-data`.
- Section 2: loads fixture members (Stice family as canonical test tenant).
- Section 4: skips real BlueBubbles pairing; installs mock channels.
- Section 5: skips real credential prompts; uses Plaid sandbox, test Gmail, scratch iCloud list.
- Section 7: seeds fixture data.

### Tests

- `tests/integration/test_bootstrap_lab.py` — runs `./bootstrap/install.sh --lab-mode` end-to-end non-interactively (feeds scripted answers via Textual's test harness). Asserts final instance state matches BUILD.md's SAMPLE INSTANCE expected shape.
- `tests/unit/test_bootstrap_resume.py` — simulates interruption at each section, verifies resume picks up correctly.
- `tests/unit/test_bootstrap_sections/test_<n>.py` — one per section, isolated.

## Verification

```bash
poetry run pytest tests/integration/test_bootstrap_lab.py tests/unit/test_bootstrap_* -v

# Manual lab run (interactive)
./bootstrap/install.sh --lab-mode

# Verify final state
ls -la ~/adminme-lab-data/
poetry run python -c "
from adminme.events.log import EventLog
from pathlib import Path
# count events by type, assert expected bootstrap.* events present
"

git add bootstrap/ tests/
git commit -m "phase 16: bootstrap wizard"
```

## Stop

**Explicit stop message:**

> Bootstrap wizard works. Lab mode produces a fully populated fake instance. Real mode can run too (but the operator should not yet — we need the CLI in prompt 17 first for ongoing operations). Ready for prompt 17 (CLI + deploy + migrations).
