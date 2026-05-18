# adminme-bridge

L1-bridge daemon — sibling Poetry package to the root `adminme` package.

Per `docs/04-architecture-amendment-knowledge-vaults-and-member-bridges.md`
§2 / `docs/DECISIONS.md` D17 / [PM-29], the bridge runs on per-member Mac
Minis and emits canonical events via HTTP to the central `:3337` ingest
service. It is NOT installed alongside `adminme` on the central host — it
is a separate distributable with its own process lifecycle.

11c-i ships the daemon shell only (this package + `bridge.enrolled@v1`
schema + the central `:3337` ingest service). Concrete knowledge-source
adapters (Apple Notes, Voice Notes, Obsidian) land in 11c-ii.

See `prompts/_active/11c-i-bridge-daemon-and-ingest.md` for the build
contract; `docs/build_log.md` Prompt 11c-i for the as-shipped record.
