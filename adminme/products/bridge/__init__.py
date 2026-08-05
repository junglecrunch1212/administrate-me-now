"""
Central :3330 `/api/bridge` ingest product.

Per memo 04 §2.2 + [§6.19] + [D17] — the loopback (but tailscale-served)
FastAPI service that bridges send canonical events to. Identity is bound
to ``owner_scope`` here via the Tailscale-User-Login header; the bridge
client NEVER chooses its own ``owner_scope`` (F-3 hard rule, 11c-i §3).
"""
