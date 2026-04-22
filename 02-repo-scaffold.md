# Prompt 02: Repo scaffolding

**Phase:** BUILD.md PHASE 0.
**Depends on:** Prompt 01 passed. `docs/architecture-summary.md` exists.
**Estimated duration:** 1-2 hours.
**Stop condition:** Directory tree matches spec; `poetry install` / `npm install` succeed; all stub modules import without error; `pytest --collect-only` runs (even if no tests yet).

---

## Read first (required)

1. `docs/architecture-summary.md` (your own work from prompt 01). Section 1 (five-layer model) is your directory organization principle.
2. `ADMINISTRATEME_BUILD.md` — section labeled **PHASE 0** in the "PHASE PLAN" part of the doc (use grep or search to locate). This is the canonical scope for this prompt.
3. `ADMINISTRATEME_BUILD.md` — section "SAMPLE INSTANCE — WHAT `~/.adminme/` LOOKS LIKE AFTER BOOTSTRAP". You're not creating the instance directory in this prompt, but understanding its shape informs how the code directory mirrors it.
4. `ADMINISTRATEME_BUILD.md` — section "STACK" and the language-split paragraphs. This tells you what goes in Python vs. Node.

## Operating context

You are creating the scaffolding — the empty room before any furniture. Directory structure, dependency manifests (`pyproject.toml`, `package.json`), empty stub modules with clear module-level docstrings explaining what will live there, and pre-wired test harness that runs but finds no tests yet. Layer hygiene matters from day one: later prompts will fail if the directory structure is wrong, because files will end up in layers they shouldn't.

This prompt is **additive only** — you are not implementing logic, not writing tests, not running code. You are creating the shape of the codebase so that later prompts can fill it in.

## Objective

Produce the directory tree and dependency manifests exactly as specified by BUILD.md PHASE 0, with empty stub modules that document what each file's job will be.

## Out of scope

- Do NOT write any implementation code. Stubs only.
- Do NOT write any tests. Just the test directories + harness + import of stubs.
- Do NOT set up CI/CD (GitHub Actions, etc.). That's out of scope for v1; manual testing on operator's lab.
- Do NOT run migrations, create databases, or touch `~/.adminme/`.
- Do NOT install OpenClaw skills, plugins, slash commands, or standing orders. That's prompt 15.

## Deliverables

### Directory structure

Create this tree in the repo root (the repo already has `ADMINISTRATEME_*.md` files + `docs/` from prompt 01 — leave those alone):

```
platform/
├── __init__.py
├── adapters/
│   ├── __init__.py
│   ├── base.py                  # Adapter Protocol (stub with docstring)
│   ├── messaging/
│   │   ├── __init__.py
│   │   └── README.md            # "messaging-family adapters go here"
│   ├── calendaring/
│   │   ├── __init__.py
│   │   └── README.md
│   ├── contacts/
│   │   ├── __init__.py
│   │   └── README.md
│   ├── documents/
│   │   ├── __init__.py
│   │   └── README.md
│   ├── telephony/
│   │   ├── __init__.py
│   │   └── README.md
│   ├── financial/
│   │   ├── __init__.py
│   │   └── README.md
│   ├── reminders/
│   │   ├── __init__.py
│   │   └── README.md
│   ├── webhook/
│   │   ├── __init__.py
│   │   └── README.md
│   └── iot/
│       ├── __init__.py
│       └── README.md
├── events/
│   ├── __init__.py
│   ├── log.py                   # event log (L2; prompt 03 fills in)
│   ├── bus.py                   # event bus (L2; prompt 03)
│   ├── schemas/                 # pydantic schemas (prompt 04)
│   │   ├── __init__.py
│   │   └── README.md            # "one file per event family; registered in registry.py"
│   └── registry.py              # schema registry (prompt 04)
├── projections/
│   ├── __init__.py
│   ├── base.py                  # Projection protocol stub
│   ├── parties/                 # prompt 05
│   │   ├── __init__.py
│   │   ├── schema.sql
│   │   ├── handlers.py
│   │   └── queries.py
│   ├── interactions/            # prompt 05
│   ├── artifacts/               # prompt 05
│   ├── commitments/             # prompt 06
│   ├── tasks/                   # prompt 06
│   ├── recurrences/             # prompt 06
│   ├── calendars/               # prompt 06
│   ├── places_assets_accounts/  # prompt 07
│   ├── money/                   # prompt 07
│   ├── vector_search/           # prompt 07
│   └── xlsx_workbooks/          # prompt 07 (bidirectional)
│       ├── __init__.py
│       ├── forward.py           # event-driven xlsx regeneration
│       ├── reverse.py           # xlsx-change-driven event emission
│       ├── schemas.py           # per-sheet schemas
│       └── tests/
├── pipelines/
│   ├── __init__.py
│   ├── runner.py                # prompt 10a
│   └── <pipeline-packs installed to ~/.adminme/packs/pipelines/ at runtime>
├── lib/
│   ├── __init__.py
│   ├── session.py               # Session + authMember/viewMember (prompt 08)
│   ├── scope.py                 # scope enforcement (prompt 08)
│   ├── governance.py            # authority gate + action_gates (prompt 08)
│   ├── observation.py           # observation mode wrapper (prompt 08)
│   ├── skill_runner/            # thin wrapper around OpenClaw (prompt 09a)
│   │   ├── __init__.py
│   │   └── wrapper.py
│   ├── correlation.py           # correlation_id helpers
│   ├── identifiers.py           # normalization helpers (emails, phones)
│   └── crypto.py                # SQLCipher key derivation, secret handling
├── products/
│   ├── __init__.py
│   ├── core/                    # prompt 13a; FastAPI service at :3333
│   │   ├── __init__.py
│   │   ├── main.py
│   │   ├── routers/
│   │   └── scheduled/           # internal schedules only; proactive → standing orders
│   ├── comms/                   # prompt 13a; :3334
│   ├── capture/                 # prompt 13b; :3335
│   └── automation/              # prompt 13b; :3336
├── openclaw_plugins/
│   ├── __init__.py
│   ├── memory_bridge/           # prompt 12
│   ├── channel_bridge_bluebubbles/  # prompt 12
│   └── README.md                # "OpenClaw plugins written in Python; installed via `openclaw plugin install`"
├── daemons/
│   ├── __init__.py
│   ├── adapter_supervisor.py    # prompt 11
│   ├── pipeline_supervisor.py   # prompt 10a
│   ├── xlsx_forward.py          # prompt 07
│   └── xlsx_reverse.py          # prompt 07
└── cli/
    ├── __init__.py
    └── main.py                  # prompt 17; `adminme` entrypoint

console/
├── package.json
├── server.js                    # Express at :3330 (prompt 14a)
├── routes/                      # prompt 14
├── lib/                         # session.js, bridge.js, guardedWrite.js (prompt 14a)
├── views/                       # compiled JSX served as static (prompt 14b/c)
├── profiles/                    # README: "profile packs compiled to ~/.adminme/..."
├── static/
└── tests/

bootstrap/
├── __init__.py
├── install.sh                   # prompt 16 entry script
├── wizard.py                    # prompt 16 Textual TUI
├── sections/                    # prompt 16; one file per wizard section
│   ├── __init__.py
│   └── README.md

packs/
├── README.md                    # "built-in packs live here; installed to ~/.adminme/packs/ during bootstrap"
├── adapters/
├── pipelines/
├── skills/
├── profiles/
└── personas/

tests/
├── __init__.py
├── unit/
│   └── README.md
├── integration/
│   └── README.md
├── e2e/
│   └── README.md
├── fixtures/                    # shared fixtures
│   └── README.md
└── conftest.py                  # shared pytest fixtures

docs/
├── preflight-report.md          # from prompt 00
├── openclaw-cheatsheet.md       # from prompt 01
├── architecture-summary.md      # from prompt 01
├── adrs/                        # architecture decision records
│   └── 0001-use-openclaw-as-substrate.md
│       (document the decision made in BUILD.md's OPENCLAW IS THE ASSISTANT SUBSTRATE section)
└── README.md

.gitignore
pyproject.toml                   # Poetry config; Python 3.12+
poetry.lock                      # generated by `poetry install`
README.md                        # top-level; links to the artifact set
```

### `pyproject.toml`

Use Poetry. Include these dependency groups:

- **core:** `pydantic>=2`, `sqlalchemy`, `pysqlcipher3`, `sqlite-vec`, `openpyxl`, `pandas`, `httpx`, `typer`, `rich`, `textual`, `apscheduler`, `watchdog`, `authlib`, `cryptography`, `python-dotenv`, `structlog`.
- **llm:** (empty for now — OpenClaw handles LLM calls; we don't take a direct dep on anthropic/openai)
- **dev:** `pytest`, `pytest-asyncio`, `pytest-cov`, `ruff`, `mypy`, `hypothesis`.

Python version: `^3.12`.

Entry points:
- `adminme = "platform.cli.main:app"` (Typer app; prompt 17 fills in).

### `console/package.json`

Node 24. Dependencies:
- `express`, `express-rate-limit` (or custom per CONSOLE_PATTERNS.md §4), `better-sqlite3`, `axios` or `node-fetch`, `jsdom` (SSR for compiled JSX if needed).
- Dev: `vitest` or `jest`, `esbuild` (for profile JSX compilation).

Scripts:
- `start`: `node server.js`
- `test`: placeholder
- `build:profiles`: `node scripts/compile-profiles.js` (stub; prompt 14b/c implements)

### `.gitignore`

Include everything that must never reach GitHub:
- Instance data paths (`~/.adminme/`, `~/adminme-lab-data/`) — neither should ever be committed; they live outside the repo, but if someone accidentally creates those paths inside the repo checkout, the `.gitignore` catches them. Also catch: `*.db`, `*.db-wal`, `*.db-shm`, `*.env`, `.env*` (except `.env.example`)
- `node_modules/`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `dist/`, `build/`
- IDE files
- macOS: `.DS_Store`
- Secrets: `*.pem`, `*.key`, `credentials.*`, `secrets/`

### `README.md` (top-level)

Short. Links to `docs/architecture-summary.md` and the five artifact files. Instructions to read them first. No marketing copy. No installation instructions for end users — this is not an open-source project yet; it's a household deployment.

### Every stub `__init__.py` is empty EXCEPT where it exposes module-level symbols that later prompts depend on. Every stub `.py` file that contains logic later (e.g., `platform/events/log.py`) has a module docstring of the form:

```python
"""
L2 Event Log — append-only SQLCipher-backed event storage.

Implemented in prompt 03 per ADMINISTRATEME_BUILD.md §L2.

This module will expose:
- `EventLog`: async class with .append(event), .read_since(cursor), .replay()
- Partitioning by owner_scope (see BUILD.md §2)
- SQLCipher encryption with key derived from 1Password secret

Do not implement in this scaffolding prompt. Later prompts will fill in.
"""
```

Every directory that contains packs later (`packs/*/`) gets a README.md explaining what pack kind goes there.

### Architecture Decision Record

`docs/adrs/0001-use-openclaw-as-substrate.md` — write a brief ADR (~100 lines) documenting:
- **Context:** The system needs an assistant runtime that handles channels (iMessage/Telegram/Discord), session management, skill execution, slash commands, and standing orders.
- **Decision:** Use OpenClaw as the substrate; AdministrateMe layers on top via skills/plugins/slash commands/standing orders.
- **Alternatives considered:** Custom runtime (rejected: duplicates OpenClaw's work); use only OpenClaw's memory as data layer (rejected: no event sourcing, no projections).
- **Consequences:** +leverages mature substrate; +channels work out of the box; -introduces an integration seam that must stay in sync; -two session/memory models that must be bridged.
- Cite the BUILD.md section that formalizes this.

## Verification

Run these commands and paste the full output:

```bash
# Directory structure
tree -L 3 --dirsfirst -I 'node_modules|__pycache__|.git' . | head -120

# Python deps install
poetry install

# Python imports
poetry run python -c "
import platform.events.log
import platform.events.bus
import platform.projections.parties
import platform.projections.xlsx_workbooks.forward
import platform.lib.session
import platform.lib.skill_runner.wrapper
import platform.products.core.main
import platform.openclaw_plugins.memory_bridge
print('All imports OK')
"

# Pytest harness
poetry run pytest --collect-only

# Node install
cd console && npm install && cd ..

# git status
git status
```

Expected:
1. Tree output shows the full structure above.
2. `poetry install` succeeds with no errors.
3. All Python imports succeed (empty modules with docstrings are importable).
4. `pytest --collect-only` reports "collected 0 items" or similar — no errors.
5. `npm install` in `console/` succeeds.
6. `git status` shows all new files, ready to commit.

Then:

```bash
git add -A
git commit -m "phase 02: repo scaffolding + dependencies"
```

## Stop

**Explicit stop message:**

> Scaffolding complete. The shape of the codebase is in place. All stubs import cleanly, dependencies resolve, tests run (find 0). Ready for prompt 03 (event log + bus). Please review the directory tree, `pyproject.toml`, `.gitignore`, and `docs/adrs/0001-use-openclaw-as-substrate.md` before proceeding.

Do not implement the event log in this session. That is prompt 03.
