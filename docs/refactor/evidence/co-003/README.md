# CO-003 People release evidence

Date: 2026-08-06

Boundary: credential-free production composition and reviewed local code.
Provider latency, member consent, and live contact writes are excluded.

## Constitutional result

- ADR-0004 is accepted and CO-003 is approved.
- Endpoint, provider Contact Record, Party, Identifier Link, Membership,
  Relationship, member profile, fact, and Interaction remain distinct.
- Private fields filter before resolution, search, counts, detail, or cue
  computation. FTS rows are audience-specific; granted fact/identifier search
  revalidates the live grant.
- Unknown senders remain Endpoints. Curated provider records or human Triage
  decisions can qualify a Party. Ambiguous exact matches fail closed.
- Relationship cues require explicit member cadence and use deterministic
  templates. A model cannot infer identity, access, closeness, priority,
  outreach, merge, or provider action.

## Executable evidence

Command:

```bash
bash scripts/acceptance/release-candidate.sh
```

Result: **PASS**

- strict acceptance: 30 passed, including AC-30;
- root Python and pack suites: 1,551 passed;
- standalone bridge: 60 passed;
- Ruff lint and format: pass, 489 files formatted;
- mypy: pass, 216 source files;
- Pyright: 0 errors, 0 warnings;
- pip/npm dependency audits: no known third-party vulnerabilities; local
  unpublished packages are correctly reported as unauditable by PyPI;
- performance smoke: all budgets pass;
- root and bridge sdist/wheel builds: pass;
- architecture-negative, clean-install, tenant-isolation, authenticated
  backup/restore, shellcheck, Bash syntax, and diff hygiene: pass;
- exact OpenClaw `2026.7.2` contract at
  `944cf08f94c334cff2b0fdf7e01187a9efa42715`: pass;
- pinned OpenClaw plugin: 14 tests passed; TypeScript check/build/pack and npm
  audit passed.

The only repeated test warning is the already-known FastAPI TestClient
Starlette/httpx deprecation. It is non-failing and unrelated to CO-003.

The first observation switch of implementation commit `5f5f58d` failed closed
before OpenClaw startup because the existing v1 identifiers table did not yet
contain `owner_scope` when `schema.sql` tried to create its v2 scope index. The
transactional updater restored the prior release, and status/Doctor remained
healthy. The fix creates the index only after additive columns and retains a
legacy-table regression matching the production startup ordering. The full
release suite result above is from the corrected candidate.

## AC-30 behaviors

The production-composed People slice proves qualified curated contacts,
unknown-endpoint exclusion, authorized search/detail, exact provider revision,
member profile save, explicit grant/revoke without provider-record or profile
leakage, granted-fact search, child and cross-adult denial, reversible archive/
restore, Party promotion only after Triage confirmation, and replay. Projection
tests additionally prove ambiguous exact resolution fails closed, private
search terms cannot influence another adult's results, and an allocated Party
merge can be split without losing identifier evidence. Contact adapter tests
prove private loss-aware record/group/deletion events, cursor state, normalized
endpoints, capability gating, provider revision conflict, and observation-safe
write behavior.

## Live activation boundary

The live family instance remains in observation. Apple Contacts requires a
member bridge plus that member's macOS consent. Google People, Microsoft Graph,
and CardDAV require exact per-member account enrollment. Microsoft/CardDAV and
any unselected source remain Not Configured. No live contact provider write is
authorized by this evidence.

Immutable commit, pre-switch backup, Doctor, Tailscale route, and desktop/
mobile browser findings are appended after deployment.
