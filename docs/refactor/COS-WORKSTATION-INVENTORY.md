# Current CoS workstation inventory

MODERNIZE the working-environment diagnostic contract: the CoS consults
`administrateme_workstation_inventory` before explaining an unavailable account
or selecting a route. The tool is read-only, uses the native member/service
provenance and audits the read. It exposes the CoS's own coordinates plus member
coordinates permitted by the current read-context delegation. It never converts
an identity map or configured tool into permission, a provider call or success.

The authoritative account, grant and runtime roots remain provider bindings,
service identities, member bridge enrollment, current Apple service permissions
and OpenClaw's configuration. The supplemental private file
`state/config/workstation-identities.json` is the single owner-maintained record
for browser and native-service identity coordinates. Reconcile any older private
identity notes into that file; leave pointers and usage guidance in the old notes,
not a second independently maintained map. Its schema forbids extra fields,
accepts secure credential-store references only, and carries evidence, binding
revision and validity. No secrets or household coordinates belong in Git.

The deployed `python -m bootstrap.workstation_inventory --schema` prints the
schema. Provision a reviewed private input with `--instance-root`, `--input` and
`--expected-revision` (zero for first creation). The command checks private file
ownership, avoids symlinks, compares revisions under a lock and writes only this
map. Changed identity coordinates require a new binding revision. It neither
signs in nor changes any provider, OpenClaw, member or OS grant. The runtime
rereads the map and current authorities for each tool call; malformed input is
reported without exposing its content.

Each binding separates configured, connected, authenticated, authorized,
tool-accessible, executable and outcome-verified state. Null means unknown. Past
source acquisition has a timestamp and is explicitly distinguished from current
transport or provider-write proof. A matching saved browser observation is a
verified record, not proof of the present tab's account. Wrong account, stale
binding revision or expired evidence cannot establish execution readiness.
OpenClaw owns actual tool policy and registration: a literal deny or closed
allowlist can explain a missing tool, while allowed configuration alone never
claims a loaded or functioning tool. Native `tools.catalog` is declaration
metadata and cannot prove the effective agent tool set or an invocation. A
current effective-tool-set check or bounded actual invocation is required.
General workstation enablement remains a
separate supported OpenClaw operation; this slice does not open a shell, browser,
native app, file tree or scheduler.

Recovery: revert the private map with a new reviewed revision, or roll back the
release through the existing supported updater. The additive map is ignored by
the preceding release and introduces no event schema, provider effect or history
migration. Acceptance covers real application composition, current owner/grant
filtering, wrong account and stale evidence, native tool routing and private-file
validation. A live inventory read proves diagnosis availability; an actual action
still needs its own terminal result and later observation where supported.

The native tool returns a bounded overview by default. Counts are computed after
authority filtering and remain separate for identity bindings, integrations,
bridges and native tools. Compact rows carry seven explicit true/false/unknown
states; detail pages retain every evidence reference, scope, account coordinate
and recovery field. Each domain page is at most 3,800 UTF-8 bytes and its complete
native ToolResult must stay below 9,500 bytes, covering both model and transcript
budgets. Continue with `next_offset` and the returned snapshot; concurrent state
changes produce 409 instead of silently mixing pages. Long detail text is split
into lossless offset fragments, never character-truncated JSON.
