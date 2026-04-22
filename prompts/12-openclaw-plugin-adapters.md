# Prompt 12: OpenClaw plugin adapters (memory bridge, BlueBubbles, Telegram, Discord)

**Phase:** BUILD.md L1 (OpenClaw plugin half) + integration guidance.
**Depends on:** Prompt 11.
**Estimated duration:** 3-4 hours.
**Stop condition:** Plugins install into OpenClaw; inbound messages from OpenClaw's channels land in AdministrateMe's event log; outbound via `outbound()` routes through OpenClaw's channel API.

## Read first

1. `ADMINISTRATEME_BUILD.md` L1 adapters section — OpenClaw plugin runtime paragraphs.
2. `docs/openclaw-cheatsheet.md` question 4 (plugin registration).
3. `docs/reference/openclaw/` — specifically files covering plugin manifests, plugin lifecycle hooks, and the channel-send API contract. Mirror only.
4. `docs/reference/bluebubbles/` — BlueBubbles API reference and WebSocket event shapes for the BlueBubbles bridge. Mirror only; no WebFetch.
5. `ADMINISTRATEME_REFERENCE_EXAMPLES.md` §1 — the BlueBubbles adapter, now in its correct framing as an OpenClaw plugin. The §1 example shows WebSocket ingest + outbound loop shape.

## Operating context

OpenClaw already handles channels (iMessage via BlueBubbles, Telegram, Discord, web). Our job is to bridge those into AdministrateMe's event log without reinventing channel transport. We do this via OpenClaw plugins:

- **memory_bridge** — ingests OpenClaw's conversation state (session history, summaries) into AdministrateMe as `conversation.turn.recorded` events. Read-only from OpenClaw's perspective; emit-only to AdministrateMe.
- **channel bridges** — one per channel. When OpenClaw receives an inbound on the channel, the plugin emits `messaging.received` to AdministrateMe. When AdministrateMe wants to send (via `outbound()`), it calls into OpenClaw's channel send API.

## Objective

Build `memory_bridge` plugin + `channel_bridge_bluebubbles` plugin + templates/shared code for Telegram/Discord bridges (which the operator can enable later).

## Deliverables

### `adminme/openclaw_plugins/memory_bridge/`

- `plugin.yaml` or OpenClaw's plugin manifest format (per cheatsheet).
- `handler.py` — subscribes to OpenClaw session events; maps to AdministrateMe event shapes; appends to event log.

### `adminme/openclaw_plugins/channel_bridge_bluebubbles/`

- Inbound: receives OpenClaw's channel-received hook → emits `messaging.received` via bus.
- Outbound: exposes a handler OpenClaw calls when AdministrateMe wants to send; translates to OpenClaw's send-to-channel API call.

### Shared library

`adminme/openclaw_plugins/_shared/bridge_base.py` — common code for all channel bridges (message normalization, sensitivity defaulting, dedup).

### Validate (Phase A)

**No live plugin install in Phase A** (no OpenClaw gateway in the sandbox). Instead:
1. Validate each plugin's manifest file against the shape `docs/reference/openclaw/` documents.
2. Add both plugins to `bootstrap/plugin_install_order.yaml` — consumed by the bootstrap wizard during Phase B.

For Phase B verification (by the operator after bootstrap):

```bash
openclaw plugin install adminme/openclaw_plugins/memory_bridge
openclaw plugin install adminme/openclaw_plugins/channel_bridge_bluebubbles
openclaw plugin list | grep adminme
```

### Tests

- `tests/unit/test_memory_bridge.py` — mock OpenClaw session event → correct AdministrateMe event emitted.
- `tests/integration/test_channel_bridge_bluebubbles.py` — marked `@pytest.mark.requires_live_services`; skipped in Phase A. Exercises real BlueBubbles + OpenClaw during Phase B only.

## Verification

```bash
# Phase A
poetry run pytest tests/unit/test_memory_bridge.py -v
poetry run pytest tests/ -m "not requires_live_services" -k "channel_bridge or memory_bridge" -v

# Manifest validation
poetry run python -c "
from adminme.openclaw_plugins._shared.bridge_base import validate_manifest
validate_manifest('adminme/openclaw_plugins/memory_bridge')
validate_manifest('adminme/openclaw_plugins/channel_bridge_bluebubbles')
print('Plugin manifests valid')
"

grep -E 'memory_bridge|channel_bridge' bootstrap/plugin_install_order.yaml

git commit -m "phase 12: OpenClaw plugin adapters"
git push
```

## Stop

> Channel bridges in. OpenClaw's conversation surface now feeds AdministrateMe's event log. Outbound routing goes through OpenClaw. Ready for prompt 13a (product APIs core + comms).
