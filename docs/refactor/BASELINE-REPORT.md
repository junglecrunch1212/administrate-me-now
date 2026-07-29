# Gate 1 baseline report

- Audit date: 2026-07-29 (America/New_York)
- Repository: `junglecrunch1212/administrate-me-now` (public)
- Audited commit: `64192f465606d0bcf92f5137cb0c1f0e4c729297`
- `origin/main` at audit: same SHA; main had not advanced
- Working branch: `codex/administrateme-v2-runtime`
- Baseline worktree: clean before Gate 1 artifacts
- Production behavior changed in Gate 1: no

## Repository resolution

The requested branch did not exist locally or remotely and no matching PR existed. It was created from the exact audited main commit. The ordinary Windows checkout could not materialize five tracked Jinja template filenames containing the illegal Windows characters `<` and `>` (`test_<NAME>_adapter_pack_internal.py.j2` under each Cat-A–E scaffold template). The audit checkout was therefore populated with those five paths marked skip-worktree; the remote objects were not modified. This causes six scaffold test failures in the Windows run and is itself a portability defect.

The parent workspace already contained three unrelated untracked user documents. They were outside this checkout and were not edited or staged. No baseline root `AGENTS.md` existed. `cowork-partner/PROJECT-INSTRUCTIONS.md` is valuable historical workflow evidence but describes the earlier Claude/Cowork prompt process; v2 agents follow root `AGENTS.md` and `docs/refactor/`.

First-party inventory: 1,631 tracked paths, 388 Python files, 242 test/fixture paths by the audit count, six GitHub workflow files, root and bridge Poetry packages, and a separate npm console package. There is no Makefile. CI is primarily prompt executor orchestration, constitutional synchronization, and an OAuth smoke workflow; there is no conventional untrusted-PR lint/type/full-test/release gate.

## Constitutional review

All eleven required sources were read completely. Exact hashes, byte/line counts, precedence, and reconciliations are in `ADR-0000-CONSTITUTIONAL-RECONCILIATION.md`; all material promises are indexed in `CONSTITUTIONAL-CROSSWALK.md`.

Genuine conflicts found:

- twelve projections in the older mental model versus thirteen after `member_knowledge` and `lists` amendments;
- nine bootstrap sections in introductions versus later required §10 bridge enrollment;
- old quick-capture/voice/triage flows versus Capture-as-Knowledge-read-surface after D17;
- separate Python services/ports and Node server versus permitted one-Python-process v2 topology;
- Python OpenClaw plugin scaffolding versus current native TypeScript/manifest format;
- old `/skills/invoke`/direct-provider/fallback examples versus the one `llm-task` seam and OpenClaw-owned routing;
- product scheduling/APScheduler language versus OpenClaw-owned cron/standing orders;
- broad observation suppression versus later user-initiated Cat-B mirror-write language;
- mock Tailscale-header trust versus the required trusted local proxy boundary;
- example household data/runtime snippets versus tenant-neutral platform and current APIs.

### Reference console evidence

The HTML and its inline state/event code were audited completely. Source-demonstrated interactions include all eight tabs; carousel/compressed/child modes; view-as; inbox approval drawer; reward toast; calendar busy redaction; observation and degraded banners; settings panes; and chat.

Rendered interaction was attempted through the required in-app browser, but navigation to the local `file:` URL was blocked by the browser URL-security policy. The reference has no deployed URL. No local-server/data-URL/raw-host workaround was used. Therefore Gate 1 has source/DOM evidence but **no rendered visual or live interaction proof**. `UX-BEHAVIOR-CONTRACT.md` records the missing evidence and required capture. This is the only Gate 1 activity that could not be completed in the available environment.

## Toolchain observed

| Tool | Observed | Manifest target/impact |
|---|---|---|
| OS | Windows_NT 10.0.26200 | Release rehearsal requires clean macOS lab. |
| Python | 3.12.10 | Root/bridge require Python `^3.11`; close enough for audit, not release proof. |
| Poetry | missing | Documented install/package commands unavailable. |
| pytest | 8.3.3 | Used directly with installed environment. |
| ruff | 0.7.2 | Used directly. |
| mypy | 2.1.0 | Used directly. |
| pyright | missing | No pyright check performed. |
| Node | 24.14.1 | Console requires `>=22 <23`; unsupported. |
| npm | 11.11.0 | `npm.cmd` used because the PowerShell shim is blocked by local execution policy. |
| git | 2.53.0.windows.1 | Repository operations available. |
| GitHub CLI | 2.96.0 | Authenticated without printing credentials. |

## Commands and honest results

| Command | Exit | Result |
|---|---:|---|
| `python -m pytest --collect-only -q --tb=short` | 1 | 1,170 tests collected; 10 collection errors: four modules cannot import the separately packaged `adminme_bridge`, and six xlsx modules cannot import POSIX `fcntl`. |
| `PYTHONPATH=bridge python -m pytest -q --tb=short` with six directly `fcntl`-dependent modules ignored to obtain a portable execution baseline | 1 | 1,160 passed, 20 failed, 3 skipped, 3 warnings in 70.53s. Failures: 4 bridge/dual wrapper emits dropped; 6 scaffold tests lack Windows-illegal templates; 1 event-bus persisted-checkpoint mismatch; 9 additional xlsx regeneration tests import `fcntl`. |
| `PYTHONPATH=bridge python -m pytest bridge/tests -q --tb=short` | 0 | 55 passed in 16.48s; one pytest-asyncio config warning. |
| `ruff check . --output-format concise` | 1 | 13 errors: 4 bridge-test unused variables/imports, 2 vendored Plaid-reference unused imports, 7 integration-test unused imports. |
| `ruff check adminme bridge/adminme_bridge --output-format concise` | 0 | All production-package checks passed. |
| `mypy adminme bridge/adminme_bridge --no-error-summary` | 1 | 5 Windows errors in xlsx `lockfile.py`: `fcntl.flock`, `LOCK_EX`, `LOCK_NB`, `LOCK_UN` unavailable to the platform/types. |
| `bash scripts/verify_invariants.sh` | 0 | Passed with no output. This is a static subset, not a release proof. |
| `python scripts/demo_event_log.py` | 0 | 20 valid events appended/read; invalid confidence rejected as expected. |
| `python scripts/demo_projections.py` | 1 | Derived rows were built for ten manually registered projections, then Windows cp1252 could not print `→`; exception cleanup also left the bus checkpoint DB open. |
| `python scripts/demo_xlsx_forward.py` | 0 | Both temporary workbooks produced; expected sheet row counts; two `xlsx.regenerated` events. |
| `python scripts/demo_xlsx_roundtrip.py` | 0 | Temporary round trip produced reverse events and final total 17 events. This does not erase the pytest/mypy `fcntl` failures. |
| `cd console && npm.cmd ci` | 1 | `EBADENGINE` on Node 24; `better-sqlite3` had no Node-24 Windows prebuild and native build could not find Visual Studio C++ tools. |
| `cd console && npm.cmd test` | not run | Install failed first. The declared script is only `echo "tests added by prompt 14a" && exit 0`, so it would be a false placeholder, not test evidence. |
| `poetry install/check` | not run | Poetry is not installed. Lockfile/manifests were inspected statically. |
| `pyright` | not run | Tool is not installed and no supported repository command was present. |

No failing/skipped test was changed, removed, or reclassified to manufacture a pass.

## Independent seam audit

| Seam | Evidence | Baseline verdict |
|---|---|---|
| Adapter -> real event log | Central adapters generally receive an `event_log`; bridge has real ingest router/log tests. Four process-like bridge emit tests fail because no event arrives. | PARTIAL/BLOCKING |
| Append -> bus publish | `EventBus.notify(event_id)` exists and demos call it manually. `EventLog.append/append_batch`, bridge ingest, pipelines, governance, observation, and skill wrapper do not compose notification. | MISSING/BLOCKING |
| Durable bus semantics | SQLite offsets, retry, degraded state, and checkpoint-after-callback exist. One persisted-checkpoint test fails. | PARTIAL/BLOCKING |
| Projection registration | Runner mechanism exists. `DEFAULT_PROJECTION_CLASSES` contains only `ListsProjection`; `member_knowledge` is absent. | MISSING 12/13 DEFAULT CAPABILITIES |
| Projection replay | Per-projection rebuild code and isolated tests exist; old integration explicitly says ten projections. | PARTIAL/BLOCKING |
| Pipeline registration/replay | Loader/runner and ten packs exist; runner is not assembled with the production log/bus; supervisor is prose only. | PARTIAL/BLOCKING |
| Process composition | No central entrypoint constructs the event log, one bus, projections, pipelines, adapters, xlsx and APIs. | MISSING/BLOCKING |
| Shutdown | Bridge handles SIGTERM/SIGINT; isolated runners have `stop`; no ordered central drain/close contract is implemented. | PARTIAL/BLOCKING |
| Product APIs | Only `/api/bridge/{ingest,ingest/batch,health}` is implemented. Core, Comms, Capture, Automation files are docstring stubs. | MISSING/BLOCKING |
| Console | `console/server.js` is comments plus a stub; test script echoes success. Reference HTML is not the application. | MISSING/BLOCKING |
| Bootstrap | Section readmes, six standing-order files, cron YAML, and pack order exist; no ten-section wizard/state machine. | MISSING/BLOCKING |
| OpenClaw plugin | README claims Python plugins; packages are placeholders; no native manifest/TS/package pin. | INCOMPATIBLE/MISSING |
| Four OpenClaw seams | Skill wrapper/packs are substantial; slash/standing/channel seams are prose or partial config; no exact host pin. | PARTIAL/BLOCKING |
| LLM provenance | Wrapper validates schemas and emits records, but assumes response shapes, has no host version gate, and iterates provider preferences itself. | PARTIAL/REWRITE |
| Governance/observation | Useful isolated implementation/tests; not composed with independent approvals and one final outbound content/privacy gate. | PARTIAL/BLOCKING |
| Workbooks | Substantial forward/reverse behavior and demos; portability/type failures and no runtime composition. | PARTIAL/BLOCKING |
| Profiles/personas | Five/four specified in docs; no built-in directories or compiler. | MISSING/BLOCKING |
| Backup/restore/Doctor | No release-grade composed implementation/evidence. | MISSING/BLOCKING |

## Baseline conclusion

The repository contains valuable domain schemas, projection logic, adapter/pipeline/skill assets, scope/governance primitives, and extensive isolated tests. It is not a truthful runnable product at the baseline: the composition root, most APIs, family console, native OpenClaw plugin, `member_knowledge`, profiles/personas, and bootstrap are absent, and the durable seams are not connected. Phase 2 must start with composition and the two golden vertical slices, not cosmetic UI work.

Current primary-source verification selected stable OpenClaw `v2026.7.1` (release commit `2d2ddc43d0dcf71f31283d780f9fe9ff4cc04fe4`) for the compatibility contract; exact npm integrity and upgrade rules are recorded in ADR-0001. This external check did not install or execute OpenClaw on the audit host.
