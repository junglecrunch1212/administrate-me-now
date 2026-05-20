"""Bridge-side connector-pack scaffolds.

Per memo 04 §3.1 + ``docs/10-split-memo-11b-ii-alpha.md`` §2: connector-packs
that run Cat-B state-mirror adapters on the bridge runtime live here. Each
subdirectory carries a ``pack.yaml`` (declaring ``kind: adapter, subkind:
state-mirror`` so 11b-ii-α-1's :func:`adminme_bridge.packs.discover_packs`
allow-list picks it up) plus an entrypoint class satisfying α-1's
:class:`adminme_bridge.adapters.base.StateMirrorBridgeAdapter` Protocol.

First connector-pack: ``reminders_apple_eventkit_bridge/`` (11b-ii-α-2).
"""
