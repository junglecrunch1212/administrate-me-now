"""Bridge-side connector-pack scaffolds.

Per memo 04 §3.1 + docs/10-split-memo-11b-ii-alpha.md §2: connector-packs
that run Cat-B state-mirror adapters on the bridge runtime live here. Each
subdirectory is one connector-pack with a `pack.yaml` (declaring
`kind: adapter, subkind: state-mirror` so α-1's `discover_packs()`
allow-list picks it up) and an entrypoint class satisfying α-1's
`StateMirrorBridgeAdapter` Protocol.

First connector-pack: `reminders_apple_eventkit_bridge/` (11b-ii-α-2).
"""
