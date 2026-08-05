# adminme-bridge

`adminme-bridge` is the per-member edge daemon. It is a separate Poetry
package and process from the one central AdministrateMe runtime. It sends
canonical, typed events to the central `/api/bridge/ingest` namespace over the
member's authenticated Tailscale route.

Gate 6 ships three opt-in Cat-D sources:

- `knowledge-apple-notes`
- `knowledge-apple-voice-memos`
- `knowledge-obsidian` (adult members only; central authorization denies child
  use even if a local config is tampered with)

Set a source name in `enabled_packs` (or provide its `pack_config`) and provide
its export/transcript/vault `root`. Unconfigured sources remain disabled. Each
watcher uses the durable metadata-only cursor database for lease renewal,
provider watermark, external-id/content-hash deduplication, error count, and
circuit recovery. Content and credentials are never stored in that database.

Every accepted knowledge event is private to the enrolled member, keeps source
and correction provenance, and enters the `member_knowledge` read model. Only
non-privileged content may enter the scope-filtered domain `vector_search`
projection. The bridge does not copy OpenClaw conversations or generic memory.

The supported first enrollment is driven from the central Mac with
`bootstrap/bridge-export.sh`, accepted on the exact reviewed member-Mac checkout
with `bootstrap/install-member-bridge.sh`, and closed centrally with
`bootstrap/bridge-finalize.sh` or `bootstrap/bridge-revoke.sh`. The encrypted
transfer and separately conveyed one-time code expire after 30 minutes. The
installer verifies the Git SHA and Tailscale self identity, writes a per-user
LaunchAgent, emits the owner-scoped enrollment proof, and consumes the transfer
file. Enrollment deliberately enables no data pack; pack roots and macOS
privacy grants remain explicit later opt-ins.
