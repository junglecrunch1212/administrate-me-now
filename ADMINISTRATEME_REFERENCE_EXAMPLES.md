# REFERENCE_EXAMPLES.md

**Worked examples of the seven extension-point types in AdministrateMe.**

One of each: adapter, pipeline, skill, projection, event type, profile pack, persona pack.

---

## How to read this document

This document exists to answer a single question Claude Code will ask repeatedly while building AdministrateMe: *"what does a real instance of this extension point look like?"* BUILD.md specifies the contracts abstractly; this file shows what pack authors actually write.

Each section is a complete example as it would ship, including:
- The manifest / metadata
- All source files (paths relative to the pack root)
- Schemas (JSON Schema for data contracts)
- README notes where there's a human audience
- Example inputs and expected outputs, or example events emitted
- Notes on failure modes and testing

**These examples use the Stice household as the subject matter.** That is deliberate — the Stice instance is the development instance, and pack authors working on the reference deployment will encounter these exact examples in code review. But a few rules apply to Claude Code's work:

1. **These files represent pack authors' work, not platform code.** They demonstrate what packs look like when written. Claude Code's job is to write the *platform* — the event log, the projection engine, the skill runner, the pipeline orchestrator, the pack loader — that *runs* packs like these. That platform code must be tenant-agnostic.

2. **When Claude Code bootstraps the Stice instance, these packs are the seed content.** They go into `~/.adminme/packs/profiles/`, `~/.adminme/packs/personas/`, `~/.adminme/packs/adapters/`, etc. Other households will have different profile/persona configurations (different names, different reward tier mixes, different calendar privacy policies, different supplement lists).

3. **Anything hardcoded to Stice-specific identifiers below is acceptable in the pack content.** Pack content is per-instance by design. What is *not* acceptable is hardcoded tenant-specific data in `adminme/`, `bootstrap/`, `profiles/` (the *built-in* profiles), `personas/` (the *built-in* personas), `integrations/` (the *built-in* adapters), or `tests/`. The identity scan (`tests/unit/test_no_hardcoded_identity.py`) enforces this.

4. **Pack paths follow the convention in BUILD.md.** Each pack is a directory with a `pack.yaml` at the root, language-appropriate source files (`adapter.py`, `pipeline.py`, `skill.md` + `handler.py`, etc.), and a `tests/` subdirectory.

5. **All code below runs on Python 3.12+** (the platform's runtime) except profile-pack view code, which is JSX compiled at install time to a bundle that the Node console serves.

---

## Example index

1. [Adapter: `messaging:bluebubbles_adminme`](#1-adapter-messagingbluebubbles_adminme)
2. [Pipeline: `commitment_extraction`](#2-pipeline-commitment_extraction)
3. [Skill: `classify_thank_you_candidate`](#3-skill-classify_thank_you_candidate)
4. [Projection: `parties`](#4-projection-parties)
5. [Event type: `commitment.proposed`](#5-event-type-commitmentproposed)
6. [Profile pack: `adhd_executive` (James's profile)](#6-profile-pack-adhd_executive-jamess-profile)
7. [Persona pack: `poopsy`](#7-persona-pack-poopsy)

---

## 1. Adapter: `messaging:bluebubbles_adminme`

**What it is.** An adapter that connects AdministrateMe to a BlueBubbles server running on the household Mac Mini. BlueBubbles is the open-source project that bridges iMessage to non-Apple environments via AppleScript on a signed-in Mac. This adapter subscribes to the BlueBubbles WebSocket for incoming messages, and posts outgoing messages back through BlueBubbles' REST API.

**Why this adapter is the reference example.** It's the most architecturally complete messaging adapter in the Stice deployment: it handles both directions (ingest + outbound), it deals with identity resolution (who is this handle?), it fires into guardedWrite for outbound, it integrates with the observation-mode filter, and it has the subtlest failure mode (the Mac Mini might reboot or lose its Apple ID session, which looks identical to "no messages" from outside).

**Pack root.** `~/.adminme/packs/adapters/messaging-bluebubbles-adminme/`

### `pack.yaml`

```yaml
pack:
  id: messaging:bluebubbles_adminme
  name: BlueBubbles iMessage bridge (AdministrateMe)
  version: 2.1.3
  kind: adapter
  category: messaging
  author: built-in
  license: Apache-2.0
  min_platform: 0.4.0

runtime:
  language: python
  python_version: ">=3.12"
  entrypoint: adapter.py
  class: BlueBubblesAdapter

capabilities:
  - messaging.ingest       # subscribes to incoming messages
  - messaging.outbound     # sends messages
  - contacts.resolve       # uses Apple handle → party_id resolution

dependencies:
  pip:
    - websockets>=12.0
    - httpx>=0.27
    - pydantic>=2.6

config:
  schema: config.schema.json
  example: config.example.yaml

health_check:
  interval_s: 60
  endpoint: http_get_status   # called by the adapter supervisor

events_emitted:
  - messaging.received
  - messaging.outbound.sent
  - messaging.outbound.failed
  - adapter.health.degraded
  - adapter.health.recovered

events_consumed:
  - messaging.outbound.requested   # guardedWrite-approved outbound
```

### `config.schema.json`

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "BlueBubbles adapter config",
  "type": "object",
  "required": ["bluebubbles_url", "api_password_ref", "assistant_apple_id", "tenant_id"],
  "properties": {
    "bluebubbles_url": {
      "type": "string",
      "format": "uri",
      "description": "Base URL of the BlueBubbles server (e.g. http://127.0.0.1:1234)"
    },
    "api_password_ref": {
      "type": "string",
      "pattern": "^op://.+",
      "description": "1Password secret reference for the BlueBubbles API password"
    },
    "assistant_apple_id": {
      "type": "string",
      "format": "email",
      "description": "Apple ID signed in on the Mac Mini (the assistant's identity)"
    },
    "tenant_id": { "type": "string" },
    "rate_limit": {
      "type": "object",
      "properties": {
        "outbound_per_minute": { "type": "integer", "default": 20 },
        "outbound_per_hour": { "type": "integer", "default": 200 }
      }
    },
    "group_chat_policy": {
      "type": "string",
      "enum": ["ingest_only", "ingest_and_outbound", "ignore"],
      "default": "ingest_only",
      "description": "Whether the adapter can send to group chats. Default is ingest_only — read them, but don't post to them, because group-chat outbound is high-surprise and high-regret."
    },
    "read_receipts": {
      "type": "string",
      "enum": ["on", "off"],
      "default": "off",
      "description": "Whether outbound messages send read receipts for incoming messages in the same thread."
    }
  }
}
```

### `config.example.yaml`

```yaml
# The Stice instance ships this file as a template; bootstrap fills it in.
bluebubbles_url: "http://127.0.0.1:1234"
api_password_ref: "op://Private/BlueBubbles adminme/password"
assistant_apple_id: "poopsy.stice@icloud.com"
tenant_id: "stice-household"
rate_limit:
  outbound_per_minute: 20
  outbound_per_hour: 200
group_chat_policy: "ingest_only"
read_receipts: "off"
```

### `adapter.py`

```python
"""
BlueBubbles iMessage adapter for AdministrateMe.

Architecture:
  - On start, opens a WebSocket to BlueBubbles' /socket endpoint.
  - Subscribes to 'new-message' and 'updated-message' events.
  - Normalizes each into a messaging.received event and emits to the event log.
  - For outbound, listens on the platform's messaging.outbound.requested
    event stream and POSTs to BlueBubbles' /api/v1/message/text endpoint.
  - Exposes an HTTP health endpoint that supervisors poll.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Optional

import httpx
import websockets
from pydantic import BaseModel

from adminme_platform.adapters import AdapterBase, AdapterHealth
from adminme_platform.events import emit_event, subscribe_events
from adminme_platform.secrets import resolve_secret


log = logging.getLogger(__name__)


class BlueBubblesConfig(BaseModel):
    bluebubbles_url: str
    api_password_ref: str
    assistant_apple_id: str
    tenant_id: str
    rate_limit: dict = {"outbound_per_minute": 20, "outbound_per_hour": 200}
    group_chat_policy: str = "ingest_only"
    read_receipts: str = "off"


@dataclass
class _BBMessage:
    """Normalized BlueBubbles message before conversion to platform event."""
    guid: str
    text: str
    is_from_me: bool
    handle: Optional[str]                  # e.g. "+14045550184" or "kate@icloud.com"
    chat_guid: str
    chat_is_group: bool
    chat_participants: list[str]           # handles
    date_created_ms: int
    subject: Optional[str] = None
    attachments: list[dict] = None
    reply_to_guid: Optional[str] = None


class BlueBubblesAdapter(AdapterBase):

    def __init__(self, config: dict, pack_id: str):
        super().__init__(pack_id=pack_id)
        self.config = BlueBubblesConfig(**config)
        self._api_password: Optional[str] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._outbound_task: Optional[asyncio.Task] = None
        self._health = AdapterHealth(pack_id=pack_id)
        self._http = httpx.AsyncClient(timeout=10.0)

    # ---- AdapterBase interface ----

    async def start(self) -> None:
        self._api_password = await resolve_secret(self.config.api_password_ref)
        self._ws_task = asyncio.create_task(self._ws_loop(), name="bb-ws")
        self._outbound_task = asyncio.create_task(self._outbound_loop(), name="bb-out")
        log.info("bluebubbles adapter started")

    async def stop(self) -> None:
        if self._ws_task:
            self._ws_task.cancel()
        if self._outbound_task:
            self._outbound_task.cancel()
        await self._http.aclose()

    async def health_check(self) -> dict:
        """Called by supervisor every 60s."""
        try:
            r = await self._http.get(
                f"{self.config.bluebubbles_url}/api/v1/server/info",
                params={"password": self._api_password},
            )
            r.raise_for_status()
            data = r.json()
            return {
                "healthy": data.get("data", {}).get("imessage_signed_in", False),
                "detail": {
                    "server_version": data.get("data", {}).get("server_version"),
                    "imessage_signed_in": data.get("data", {}).get("imessage_signed_in"),
                    "apple_id": data.get("data", {}).get("apple_id"),
                },
            }
        except Exception as e:
            return {"healthy": False, "detail": {"error": str(e)}}

    # ---- WebSocket ingest loop ----

    async def _ws_loop(self) -> None:
        url = self.config.bluebubbles_url.replace("http", "ws") + "/socket.io/?password=" + self._api_password
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    log.info("ws connected")
                    self._health.mark_healthy()
                    backoff = 1.0
                    # Subscribe to message events (BlueBubbles socket.io v2 wire format)
                    await ws.send('42["subscribe","new-message"]')
                    await ws.send('42["subscribe","updated-message"]')

                    async for raw in ws:
                        await self._on_ws_frame(raw)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._health.mark_degraded(reason=f"ws_error: {e}")
                await emit_event({
                    "type": "adapter.health.degraded",
                    "tenant_id": self.config.tenant_id,
                    "pack_id": self.pack_id,
                    "payload": {"reason": str(e)},
                })
                log.warning("ws disconnected, backing off %.1fs: %s", backoff, e)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    async def _on_ws_frame(self, raw: str) -> None:
        # Socket.io frames look like `42["new-message",{...}]`. Be tolerant of
        # heartbeats and other event types.
        if not raw.startswith("42"):
            return
        try:
            payload = json.loads(raw[2:])
        except json.JSONDecodeError:
            return

        event_name, *data = payload
        if event_name not in ("new-message", "updated-message"):
            return

        bb_msg = self._normalize(data[0])
        if bb_msg is None:
            return

        if bb_msg.is_from_me:
            # Outgoing message that originated from the Mac Mini (either via
            # us or via a human using Messages on that Mac). Record it but
            # don't double-count.
            await self._emit_outbound_mirror(bb_msg)
            return

        await self._emit_received(bb_msg)

    def _normalize(self, raw: dict) -> Optional[_BBMessage]:
        try:
            chats = raw.get("chats") or []
            chat = chats[0] if chats else {}
            participants = [p.get("address") for p in chat.get("participants", []) if p.get("address")]
            return _BBMessage(
                guid=raw.get("guid"),
                text=raw.get("text") or "",
                is_from_me=bool(raw.get("isFromMe")),
                handle=(raw.get("handle") or {}).get("address"),
                chat_guid=chat.get("guid", "unknown"),
                chat_is_group=bool(chat.get("style") == 43),   # BB chat style 43 = group
                chat_participants=participants,
                date_created_ms=raw.get("dateCreated") or 0,
                subject=raw.get("subject"),
                attachments=raw.get("attachments") or [],
                reply_to_guid=(raw.get("associatedMessageGuid") or None),
            )
        except Exception as e:
            log.warning("normalize failed: %s", e)
            return None

    async def _emit_received(self, m: _BBMessage) -> None:
        # Group-chat policy short-circuit: we can still ingest (ingest_only),
        # but we tag the event so outbound pipelines know not to auto-reply.
        group_policy = self.config.group_chat_policy
        if m.chat_is_group and group_policy == "ignore":
            return

        await emit_event({
            "type": "messaging.received",
            "tenant_id": self.config.tenant_id,
            "source": {
                "adapter": self.pack_id,
                "native_guid": m.guid,
            },
            "payload": {
                "channel": "imessage",
                "text": m.text,
                "subject": m.subject,
                "from_handle": m.handle,
                "from_handle_kind": "email" if "@" in (m.handle or "") else "phone",
                "chat_guid": m.chat_guid,
                "chat_is_group": m.chat_is_group,
                "chat_participants": m.chat_participants,
                "received_at_ms": m.date_created_ms,
                "attachments": [
                    {"mime": a.get("mimeType"), "name": a.get("transferName")}
                    for a in (m.attachments or [])
                ],
                "reply_to_native_guid": m.reply_to_guid,
                "outbound_allowed": (
                    not m.chat_is_group or group_policy == "ingest_and_outbound"
                ),
            },
        })

    async def _emit_outbound_mirror(self, m: _BBMessage) -> None:
        """Mirror of outbound we sent (or the human sent from the Mac)."""
        await emit_event({
            "type": "messaging.outbound.mirror",
            "tenant_id": self.config.tenant_id,
            "source": {"adapter": self.pack_id, "native_guid": m.guid},
            "payload": {
                "channel": "imessage",
                "text": m.text,
                "chat_guid": m.chat_guid,
                "sent_at_ms": m.date_created_ms,
                "origin": "mac_native",  # distinguished from our own sends via correlation ID tracking
            },
        })

    # ---- Outbound loop ----

    async def _outbound_loop(self) -> None:
        """
        Subscribe to messaging.outbound.requested events that target this
        adapter, perform the POST, emit messaging.outbound.sent or .failed.

        These events are emitted by the platform ONLY after guardedWrite
        has approved and observation-mode has not suppressed. So we trust
        them and just execute.
        """
        async for event in subscribe_events(
            types=["messaging.outbound.requested"],
            tenant_id=self.config.tenant_id,
            adapter_filter=self.pack_id,
        ):
            await self._do_outbound(event)

    async def _do_outbound(self, event: dict) -> None:
        p = event["payload"]
        try:
            r = await self._http.post(
                f"{self.config.bluebubbles_url}/api/v1/message/text",
                params={"password": self._api_password},
                json={
                    "chatGuid": p["chat_guid"],
                    "message": p["text"],
                    "method": "apple-script",     # signed-in Mac, not private API
                    "tempGuid": event.get("correlation_id", "unknown"),
                },
            )
            r.raise_for_status()
            data = r.json().get("data", {})
            await emit_event({
                "type": "messaging.outbound.sent",
                "tenant_id": self.config.tenant_id,
                "correlation_id": event.get("correlation_id"),
                "source": {"adapter": self.pack_id, "native_guid": data.get("guid")},
                "payload": {
                    "chat_guid": p["chat_guid"],
                    "text": p["text"],
                    "sent_at_ms": data.get("dateCreated") or 0,
                },
            })
        except Exception as e:
            log.warning("outbound failed: %s", e)
            await emit_event({
                "type": "messaging.outbound.failed",
                "tenant_id": self.config.tenant_id,
                "correlation_id": event.get("correlation_id"),
                "source": {"adapter": self.pack_id},
                "payload": {
                    "chat_guid": p["chat_guid"],
                    "text_preview": p["text"][:80],
                    "error": str(e),
                },
            })
```

### `README.md` (excerpt)

```markdown
# BlueBubbles adapter — AdministrateMe

Bridges iMessage to AdministrateMe via the BlueBubbles open-source server
running on the household Mac Mini.

## Why BlueBubbles and not sms:twilio?

For the Stice household, iMessage is the dominant family/friend channel —
blue bubbles with Kate, with Laura when she's out with Charlie, with
coparent Mike for kid handoffs. Twilio SMS is a fallback for non-Apple
contacts (Coach Mark, some vendors). Both adapters can be active; the
comms router chooses per-thread based on the target handle's history.

## Assistant identity

The Mac Mini is signed into the household's *assistant* Apple ID
(poopsy.stice@icloud.com, not James's or Laura's personal iCloud).
This matters because:

- Messages sent through BlueBubbles appear to come from the assistant's
  handle, not from a household member. This prevents accidental
  impersonation.
- The Mac Mini stays signed into this one ID across reboots. Don't
  sign in as James or Laura on this machine.
- If the assistant Apple ID ever gets signed out (rare but happens after
  major macOS updates), the adapter's health check detects it
  (`imessage_signed_in: false`) and the console banner flips to degraded
  within 60s.

## Outbound gotchas

- **Group chats** default to ingest-only. Auto-posting to a group chat
  is high-surprise. Override in config if a specific household wants it.
- **Attachments outbound** are not yet supported. BlueBubbles supports
  it, but the adapter doesn't expose it pending a decision on media
  storage (see ADR-014 in BUILD.md).
- **Read receipts** default off. Enabling them means the assistant's
  reads show up in the sender's chat, which can be socially weird
  ("why did Poopsy read that and not reply?").

## Failure modes

1. **Mac Mini offline.** WebSocket fails; adapter backs off 1s → 60s
   and retries. Health degraded. Events pile up on the BlueBubbles side
   until the Mac is back; BlueBubbles replays them on reconnect.

2. **Apple ID signed out.** Health check returns
   `imessage_signed_in: false`. Adapter keeps running but outbound
   requests fail. Supervisor surfaces this to the principal with a
   specific message ("re-sign in on the Mac Mini").

3. **BB API password rotated but not updated in 1Password.** All REST
   calls 401. Adapter marks degraded; no outbound. Ingest still works
   (WebSocket auth is cached per-connection).

4. **Native message id collisions on reply threading.** BlueBubbles'
   `associatedMessageGuid` is sometimes null when it shouldn't be, for
   Tapbacks specifically. The adapter passes the field through; the
   comms projection handles the null case (treats the message as
   top-level and logs a warning).
```

### `tests/test_adapter.py` (skeleton)

```python
"""
Representative tests. Full coverage lives alongside the adapter.
Uses respx for httpx mocking and a fake WebSocket server.
"""
import pytest
import respx

from adminme_packs.messaging_bluebubbles_adminme.adapter import BlueBubblesAdapter


@pytest.mark.asyncio
async def test_normalize_individual_message():
    adapter = _make_adapter()
    bb = adapter._normalize({
        "guid": "g1",
        "text": "hey! lunch tomorrow?",
        "isFromMe": False,
        "handle": {"address": "kate@icloud.com"},
        "chats": [{"guid": "c1", "style": 45, "participants": [{"address": "kate@icloud.com"}]}],
        "dateCreated": 1745231400000,
    })
    assert bb.is_from_me is False
    assert bb.handle == "kate@icloud.com"
    assert bb.chat_is_group is False


@pytest.mark.asyncio
async def test_group_chat_ingest_only_policy():
    # Outbound_allowed should be False in default config
    ...

@pytest.mark.asyncio
async def test_outbound_success_emits_sent_event():
    ...

@pytest.mark.asyncio
async def test_outbound_http_error_emits_failed_event():
    ...

@pytest.mark.asyncio
async def test_health_check_detects_signed_out():
    ...
```

**Key takeaways from this example for Claude Code:**

- Adapter config is a Pydantic model loaded from YAML; the schema JSON is for the bootstrap wizard's UI only.
- `emit_event` is the sole way adapters put data into the event log. No direct DB writes.
- Outbound adapters *subscribe* to `*.outbound.requested` events the platform emits after guardedWrite — they never receive outbound requests directly via function call. This decouples policy from transport.
- Health checks are async and return a `{healthy, detail}` shape; the supervisor polls.
- Adapters should be tolerant: bad websocket frames get logged and skipped, not crashed on.
- Secrets resolve through `resolve_secret("op://...")` so the adapter never sees raw credentials in config.

---

## 2. Pipeline: `commitment_extraction`

**What it is.** A pipeline that consumes every `messaging.received` event, runs a skill to detect whether the message contains a commitment (a promise-bearing statement — "I'll call you Tuesday," "can you send the form by Friday?"), and if so, proposes one by emitting a `commitment.proposed` event that the inbox surfaces for principal approval.

**Why this pipeline is the reference example.** It's the most-used and architecturally typical pipeline: event-triggered, skill-backed, emits a proposed-state event that a human confirms, with observation-mode awareness and explicit confidence thresholds. It's also the pipeline with the highest stakes for false positives (too many bad proposals → inbox fatigue → principal ignores the real ones).

**Pack root.** `~/.adminme/packs/pipelines/commitment-extraction/`

### `pack.yaml`

```yaml
pack:
  id: pipeline:commitment_extraction
  name: Commitment extraction
  version: 4.2.0
  kind: pipeline
  author: built-in
  license: Apache-2.0
  min_platform: 0.4.0

runtime:
  language: python
  python_version: ">=3.12"
  entrypoint: pipeline.py
  class: CommitmentExtractionPipeline

triggers:
  events:
    - messaging.received      # every inbound message
    # Not triggered by outbound; we don't re-extract from what we sent.

depends_on:
  skills:
    - classify_commitment_candidate@^3.0.0
    - extract_commitment_fields@^2.1.0
  projections:
    - parties               # for identity resolution
    - commitments           # to dedupe against existing

events_emitted:
  - commitment.proposed
  - commitment.suppressed   # when skills decide it's not worth proposing

config:
  schema: config.schema.json
  example: config.example.yaml

tests:
  fixtures: tests/fixtures/   # fixture messages + expected emissions
```

### `config.example.yaml`

```yaml
# The skills return confidence values 0.0 – 1.0. Messages below `min_confidence`
# are dropped silently; messages above `review_threshold` propose directly;
# messages between go to a "weak proposal" state that surfaces with a visible
# confidence score so the principal can calibrate.

min_confidence: 0.55          # below this → commitment.suppressed
review_threshold: 0.75        # at/above this → commitment.proposed (confident)
                              # below but above min_confidence → commitment.proposed (weak, flagged)

# De-duplication: if a message quotes/references an earlier message that
# already produced a commitment, don't re-propose.
dedupe_window_hours: 72

# Per-member tuning — values override the global thresholds.
# Use for children (never propose commitments from child messages), or for
# members who prefer fewer/more proposals.
per_member_overrides:
  # James prefers aggressive extraction; ADHD-PI tooling favors more
  # approval prompts over missing them.
  "stice-james":
    min_confidence: 0.50
    review_threshold: 0.70
  # Laura prefers minimalist flow; higher threshold means fewer proposals.
  "stice-laura":
    min_confidence: 0.65
    review_threshold: 0.82
  # Children never generate commitments.
  "stice-charlie":
    min_confidence: 1.1       # impossibly high = disabled

# Never extract commitments from interactions with these party tags.
# Privileged attorney-client threads, medical threads with specific providers,
# etc. Pipeline doesn't read content; skips on tag match.
skip_party_tags:
  - privileged
  - opposing_counsel
  - provider:attorney
```

### `pipeline.py`

```python
"""
Commitment extraction pipeline.

Architecture:
  1. Subscribes to messaging.received events via the platform's event bus.
  2. For each event:
     a. Resolve the sender's party_id via the parties projection.
     b. Check skip rules: privileged tag, child member, party tag blocklist.
     c. Call classify_commitment_candidate skill → {is_candidate, confidence, reasons}
     d. If confidence < min: emit commitment.suppressed for audit, done.
     e. If ≥ min: call extract_commitment_fields skill → {kind, owed_by, owed_to, ...}
     f. Dedupe against open commitments referencing this thread in the last N hrs.
     g. Emit commitment.proposed.
"""

import logging
from typing import Optional

from adminme_platform.pipelines import PipelineBase, pipeline_main
from adminme_platform.events import emit_event
from adminme_platform.skills import run_skill
from adminme_platform.projections import parties, commitments

log = logging.getLogger(__name__)


class CommitmentExtractionPipeline(PipelineBase):

    pipeline_id = "commitment_extraction"
    pipeline_version = "4.2.0"

    async def on_event(self, event: dict) -> None:
        if event["type"] != "messaging.received":
            return

        p = event["payload"]
        tenant_id = event["tenant_id"]

        # 1. Resolve sender party.
        sender_party = await parties.resolve_by_identifier(
            tenant_id=tenant_id,
            kind=p["from_handle_kind"],
            value=p["from_handle"],
        )
        if sender_party is None:
            log.debug("unknown sender, skipping: %s", p.get("from_handle"))
            return

        # 2. Skip rules.
        if self._should_skip(sender_party, p):
            return

        # 3. Resolve the receiving member — the party this message was sent
        # TO inside the household. For a 1:1 message this is the member who
        # owns the assistant's routed channel; for group chats it's the
        # group's party.
        receiving_member = await self._resolve_receiving_member(tenant_id, p)
        if receiving_member is None:
            return
        if self._is_child(receiving_member):
            return

        thresholds = self._thresholds_for_member(receiving_member["member_id"])

        # 4. Candidate classification.
        classify = await run_skill(
            skill_id="classify_commitment_candidate@^3.0.0",
            inputs={
                "message_text": p["text"],
                "sender_party_id": sender_party["party_id"],
                "receiving_member_id": receiving_member["member_id"],
                "thread_context": await self._thread_context(tenant_id, p),
            },
            correlation_id=event.get("correlation_id"),
        )

        conf = classify["confidence"]

        if conf < thresholds["min_confidence"]:
            await emit_event({
                "type": "commitment.suppressed",
                "tenant_id": tenant_id,
                "correlation_id": event.get("correlation_id"),
                "source": {
                    "pipeline": self.pipeline_id,
                    "pipeline_version": self.pipeline_version,
                },
                "payload": {
                    "reason": "below_confidence_threshold",
                    "confidence": conf,
                    "threshold": thresholds["min_confidence"],
                    "source_event_id": event["event_id"],
                },
            })
            return

        # 5. Field extraction.
        fields = await run_skill(
            skill_id="extract_commitment_fields@^2.1.0",
            inputs={
                "message_text": p["text"],
                "sender_party_id": sender_party["party_id"],
                "receiving_member_id": receiving_member["member_id"],
                "classify_reasons": classify["reasons"],
            },
            correlation_id=event.get("correlation_id"),
        )

        # 6. Dedupe.
        duplicate = await commitments.find_open_commitment_for_thread(
            tenant_id=tenant_id,
            thread_key=p.get("chat_guid"),
            within_hours=self.config.get("dedupe_window_hours", 72),
            kind=fields["kind"],
        )
        if duplicate is not None:
            log.info("dedupe hit: open commitment %s", duplicate["commitment_id"])
            return

        # 7. Propose.
        await emit_event({
            "type": "commitment.proposed",
            "tenant_id": tenant_id,
            "correlation_id": event.get("correlation_id"),
            "source": {
                "pipeline": self.pipeline_id,
                "pipeline_version": self.pipeline_version,
                "skills": [
                    f"classify_commitment_candidate@{classify['skill_version']}",
                    f"extract_commitment_fields@{fields['skill_version']}",
                ],
                "source_event_id": event["event_id"],
            },
            "payload": {
                "kind": fields["kind"],                     # 'reply' | 'task' | 'appointment' | 'payment' | ...
                "owed_by_member_id": receiving_member["member_id"],
                "owed_to_party_id": sender_party["party_id"],
                "text_summary": fields["text_summary"],
                "suggested_due": fields.get("suggested_due"),
                "urgency": fields.get("urgency", "this_week"),
                "confidence": conf,
                "classify_reasons": classify["reasons"],
                "strength": "confident" if conf >= thresholds["review_threshold"] else "weak",
                "source_interaction_id": p.get("interaction_id"),
                "source_message_preview": p["text"][:240],
            },
        })

    # ---- helpers ----

    def _should_skip(self, sender_party: dict, p: dict) -> bool:
        skip_tags = set(self.config.get("skip_party_tags", []))
        party_tags = set(sender_party.get("tags", []))
        return bool(skip_tags & party_tags)

    async def _resolve_receiving_member(self, tenant_id: str, p: dict) -> Optional[dict]:
        # For a 1:1 chat, the assistant's channel is routed to exactly one
        # member per the comms router config. For a group chat, the receiving
        # "member" is the household-household membership (aggregate) and
        # commitments don't extract from groups without policy override.
        if p.get("chat_is_group"):
            return None  # v4 policy: no commitments from group chats
        return await parties.resolve_routing_owner(
            tenant_id=tenant_id,
            channel="imessage",
            chat_guid=p["chat_guid"],
        )

    def _is_child(self, member: dict) -> bool:
        return member.get("role") == "child"

    def _thresholds_for_member(self, member_id: str) -> dict:
        overrides = self.config.get("per_member_overrides", {})
        if member_id in overrides:
            return {
                "min_confidence": overrides[member_id].get(
                    "min_confidence", self.config["min_confidence"]
                ),
                "review_threshold": overrides[member_id].get(
                    "review_threshold", self.config["review_threshold"]
                ),
            }
        return {
            "min_confidence": self.config["min_confidence"],
            "review_threshold": self.config["review_threshold"],
        }

    async def _thread_context(self, tenant_id: str, p: dict) -> list[dict]:
        """Last 5 messages in the thread for skill context."""
        if not p.get("chat_guid"):
            return []
        from adminme_platform.projections import interactions
        return await interactions.recent_in_thread(
            tenant_id=tenant_id,
            thread_key=p["chat_guid"],
            limit=5,
        )


if __name__ == "__main__":
    pipeline_main(CommitmentExtractionPipeline)
```

### `tests/fixtures/kate_kitchen_walkthrough.yaml`

```yaml
# Stice household — Kate asking about the Saturday walk-through.
# Expected: a commitment.proposed event with kind=reply, urgency=this_week.

name: kate_kitchen_walkthrough_saturday
description: |
  Kate asks if James can come Saturday at 2pm. Extraction should
  propose a "reply" commitment owed by James to Kate, urgency this_week.

input_event:
  type: messaging.received
  event_id: ev-test-001
  tenant_id: stice-household
  source:
    adapter: messaging:bluebubbles_adminme
    native_guid: bb-g-12345
  payload:
    channel: imessage
    text: "Hey! Any interest in swinging by Sat around 2? Want to pick your brain on the island layout before we commit. Wine on me."
    from_handle: "kate@icloud.com"
    from_handle_kind: email
    chat_guid: c-kate-1to1
    chat_is_group: false
    chat_participants: ["kate@icloud.com"]
    received_at_ms: 1745231400000

skill_stubs:
  classify_commitment_candidate:
    is_candidate: true
    confidence: 0.87
    reasons:
      - "contains scheduling proposal ('swinging by Sat around 2')"
      - "awaits sender-relevant response ('want to pick your brain')"
      - "sender is in principal's close tier (t2)"
    skill_version: "3.2.1"

  extract_commitment_fields:
    kind: reply
    text_summary: "Confirm or propose alternative for Saturday 2pm kitchen walk-through with Kate"
    suggested_due: "2026-04-26"
    urgency: this_week
    skill_version: "2.1.0"

expected_emissions:
  - type: commitment.proposed
    tenant_id: stice-household
    payload:
      kind: reply
      owed_by_member_id: stice-james
      owed_to_party_id: p-kate
      text_summary: "Confirm or propose alternative for Saturday 2pm kitchen walk-through with Kate"
      urgency: this_week
      confidence: 0.87
      strength: confident
      source_event_id: ev-test-001
```

### `tests/fixtures/coach_practice_reschedule.yaml`

```yaml
# Coach Mark announces a practice change. Not a commitment — informational.
# Expected: commitment.suppressed (below threshold).

name: coach_practice_informational
description: |
  Coach Mark texts a practice time change. No promise required from James.
  Classifier should return low confidence; pipeline emits suppressed event.

input_event:
  type: messaging.received
  event_id: ev-test-002
  tenant_id: stice-household
  source: { adapter: "messaging:bluebubbles_adminme", native_guid: "bb-g-12346" }
  payload:
    channel: imessage
    text: "Practice Thurs moved to the upper field — north entrance. 5:30 sharp."
    from_handle: "+14045550199"
    from_handle_kind: phone
    chat_guid: c-coach-1to1
    chat_is_group: false
    received_at_ms: 1745145000000

skill_stubs:
  classify_commitment_candidate:
    is_candidate: false
    confidence: 0.22
    reasons:
      - "informational announcement, no response solicited"
      - "logistical detail only"
    skill_version: "3.2.1"

expected_emissions:
  - type: commitment.suppressed
    tenant_id: stice-household
    payload:
      reason: below_confidence_threshold
      confidence: 0.22
      threshold: 0.50      # James's override
      source_event_id: ev-test-002
```

### `tests/fixtures/privileged_skip.yaml`

```yaml
# Laura receives an email from opposing counsel on a case. The sender's
# party is tagged opposing_counsel; pipeline MUST NOT run classification.

name: opposing_counsel_privileged_skip
description: |
  Even with content that would classify as a commitment, party-tag
  filtering should skip extraction entirely. No skills called.

input_event:
  type: messaging.received
  event_id: ev-test-003
  tenant_id: stice-household
  source: { adapter: "messaging:gmail_api" }
  payload:
    channel: gmail
    text: "Please send the production documents by Friday per the schedule."
    from_handle: "opposing@otherfirm.example"
    from_handle_kind: email
    chat_guid: null
    received_at_ms: 1745145000000

party_fixture:
  p-opposing-counsel-xyz:
    tags: ["privileged", "opposing_counsel", "provider:attorney"]

skill_stubs: {}   # must NOT be called

expected_emissions: []   # no events emitted (skip is silent by design)
```

**Key takeaways for Claude Code:**

- Pipelines subscribe to event types via `triggers.events` in pack.yaml; the platform's pipeline runner handles dispatch.
- Every skill call must include `correlation_id`. This is what lets audit tools trace one incoming message through skills, projections, and emitted events.
- Thresholds are configurable both globally and per-member. Per-member overrides go in the config file, not in code.
- When a pipeline decides *not* to act, emit a `.suppressed` event with the reason. Silent drops make debugging impossible. (Exception: party-tag skip is silent by design because the pipeline should never have read the content.)
- Pipelines emit *proposed-state* events that humans or other pipelines confirm. They don't emit *confirmed* events directly.
- Fixtures in `tests/fixtures/` are YAML with input event + skill stubs + expected emissions. The pipeline test harness loads them and runs the pipeline with stubbed skills.

---

## 3. Skill: `classify_thank_you_candidate`

**What it is.** A skill that takes a messaging exchange and decides whether the household member should send a thank-you note. Returns `{is_candidate, urgency, suggested_medium, reasons, confidence}`. Called by the `thank_you` pipeline, which proposes a drafted note to the principal for approval.

**How it runs.** This skill is installed as an **OpenClaw skill**. When the pipeline invokes it, the call goes through AdministrateMe's thin skill-runner wrapper, which in turn invokes OpenClaw's skill execution API (`POST http://127.0.0.1:18789/skills/invoke`). OpenClaw handles prompt rendering, provider selection, retries, and token accounting. AdministrateMe's wrapper validates inputs/outputs against the schemas below and records a `skill.call.recorded` event with the `openclaw_invocation_id` for traceability. See BUILD.md "L4 CONTINUED: THE SKILL RUNNER" for the full contract.

**Why this skill is the reference example.** It's small enough to show completely (manifest, prompt, handler, schemas, tests) while demonstrating the full skill-runner contract end-to-end: versioning, input/output schemas, prompt templating, OpenClaw-compatible frontmatter, post-processing via handler.py, test fixtures, replay semantics.

**Pack root.** `~/.adminme/packs/skills/classify-thank-you-candidate/`

### `pack.yaml`

```yaml
pack:
  id: skill:classify_thank_you_candidate
  name: Classify thank-you candidate
  version: 1.3.0
  kind: skill
  author: built-in
  license: Apache-2.0
  min_platform: 0.4.0

runtime:
  language: python
  python_version: ">=3.12"
  entrypoint: handler.py
  class: ClassifyThankYouCandidate

model:
  # This skill calls an LLM. The platform's model router handles which one.
  # The pack declares preferences; the instance config can override.
  preferred: claude-haiku-4-5
  fallback: claude-opus-4-7
  deterministic_mode: false     # we expect slight variance across runs
  # Replay: recorded model call transcripts go in tests/fixtures/replays/
  # so changes to prompt can be diffed against old outputs.

inputs:
  schema: schemas/input.schema.json
outputs:
  schema: schemas/output.schema.json

documentation:
  # The SKILL.md is what the skill author writes for humans (and for LLMs
  # that assemble context for the skill caller). It's NOT the prompt.
  readme: SKILL.md
  # The prompt template is separate — rendered via the skill runner's
  # standard template function.
  prompt: prompt.jinja2
```

### `SKILL.md`

```markdown
# classify_thank_you_candidate

Decide whether a principal should send a thank-you note to a party after a
recent interaction or favor.

## What counts as a candidate

- **Hosted hospitality.** The party hosted the household — hosted for
  dinner, let the kids sleep over, had the household at their beach
  house, etc. Formal ("fancy dinner") and casual ("they had us for
  pizza Friday") both qualify.
- **Substantial favor.** Watching the kids for an evening. Driving
  someone to the airport. Picking up groceries during a flu week.
  Returning a lost phone. Fixing a plumbing emergency at cost.
- **Significant gift.** Birthday, housewarming, baby, graduation.
  Handwritten-note-warranting, not "they brought a $12 bottle of wine
  to our open house" level.
- **Professional kindness.** A doctor who stayed late to fit you in.
  A contractor who did something above-scope without billing. A
  neighbor who shoveled your sidewalk.

## What does NOT count

- **Transactional exchanges.** Paying someone for a service. Reciprocal
  meet-ups between close friends ("we had coffee"). Standard helpful
  replies to asked questions.
- **Already-reciprocated interactions.** If the household has already
  said thanks in the thread, don't propose a separate note.
- **Interactions where the member has negative affect.** If the principal
  is visibly frustrated in the thread (detected via sentiment signals
  in the message history), no thank-you proposal — the relationship
  dynamic needs a human to decide.
- **Coparent/coparenting exchanges.** Coparenting logistics — kid
  handoffs, school pickups, medical-appointment coordination — do not
  generate thank-you proposals regardless of cordiality. Emotional
  texture is too load-bearing.
- **Professional-transaction providers.** Standard service provider
  interactions (the dentist's scheduler, the pool guy, the accountant)
  don't generate thank-you proposals unless there's a specific
  above-scope favor.

## Output

Returns a JSON object with:

- `is_candidate`: boolean
- `urgency`: `'within_24h' | 'this_week' | 'within_month' | 'no_rush'`
- `suggested_medium`: `'text' | 'email' | 'handwritten_card' | 'small_gift'`
  (handwritten implied for significant gifts and hosting; text for
  small favors; email for professional kindness.)
- `reasons`: array of short strings, each a concrete signal
- `confidence`: 0.0-1.0

## Calibration notes

The skill is tuned for the Stice instance's tier-2-and-above parties
(close friends and above). Acquaintances may trigger false positives
in the "small gift" branch. The `commitment_extraction` pipeline
upstream already filters by party tier before calling this skill, so
the skill itself doesn't need to re-check tier.

## Change log

- **1.3.0** — Added `suggested_medium` and handwritten-card preference
  for hosting-hospitality cases (per Stice household preference).
- **1.2.0** — Added coparent skip rule.
- **1.1.0** — Initial tier-2 tuning.
```

### `schemas/input.schema.json`

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "classify_thank_you_candidate input",
  "type": "object",
  "required": ["interaction_context", "party_summary", "principal_member_id"],
  "properties": {
    "principal_member_id": {
      "type": "string",
      "description": "The member who would be sending the note."
    },
    "party_summary": {
      "type": "object",
      "required": ["party_id", "name", "tier", "tags"],
      "properties": {
        "party_id": { "type": "string" },
        "name": { "type": "string" },
        "tier": { "type": "integer", "minimum": 1, "maximum": 4 },
        "tags": { "type": "array", "items": { "type": "string" } },
        "relationship_summary": { "type": "string" }
      }
    },
    "interaction_context": {
      "type": "object",
      "required": ["recent_messages", "recent_events"],
      "properties": {
        "recent_messages": {
          "type": "array",
          "description": "Last 10 messages in the thread, chronological.",
          "items": {
            "type": "object",
            "required": ["from_member_or_party", "text", "when"],
            "properties": {
              "from_member_or_party": { "type": "string" },
              "text": { "type": "string" },
              "when": { "type": "string", "format": "date-time" }
            }
          }
        },
        "recent_events": {
          "type": "array",
          "description": "Calendar/hosting/favor events involving this party in the last 14 days.",
          "items": {
            "type": "object",
            "required": ["kind", "summary", "when"],
            "properties": {
              "kind": {
                "type": "string",
                "enum": ["hosting_us", "hosting_them", "gift_received", "favor_received", "calendar_event"]
              },
              "summary": { "type": "string" },
              "when": { "type": "string", "format": "date-time" }
            }
          }
        }
      }
    }
  }
}
```

### `schemas/output.schema.json`

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "classify_thank_you_candidate output",
  "type": "object",
  "required": ["is_candidate", "confidence", "reasons"],
  "properties": {
    "is_candidate": { "type": "boolean" },
    "confidence": { "type": "number", "minimum": 0.0, "maximum": 1.0 },
    "urgency": {
      "type": "string",
      "enum": ["within_24h", "this_week", "within_month", "no_rush"]
    },
    "suggested_medium": {
      "type": "string",
      "enum": ["text", "email", "handwritten_card", "small_gift"]
    },
    "reasons": {
      "type": "array",
      "minItems": 1,
      "items": { "type": "string" }
    }
  },
  "if": { "properties": { "is_candidate": { "const": true } } },
  "then": { "required": ["urgency", "suggested_medium"] }
}
```

### `prompt.jinja2`

```jinja
You are a careful classifier deciding whether {{ principal_display_name }}
should send a thank-you note to {{ party_summary.name }}.

Context on the relationship:
- Closeness tier: {{ party_summary.tier }} ({{ tier_label(party_summary.tier) }})
- Relationship: {{ party_summary.relationship_summary or "no summary available" }}
- Party tags: {{ party_summary.tags | join(", ") or "—" }}

Recent thread (chronological):
{% for msg in interaction_context.recent_messages %}
  [{{ msg.when }}] {{ msg.from_member_or_party }}: {{ msg.text | truncate(220) }}
{% endfor %}

Recent events involving this party (last 14 days):
{% for e in interaction_context.recent_events %}
  - {{ e.when }}: {{ e.kind }} — {{ e.summary }}
{% else %}
  - (none)
{% endfor %}

Decide whether a thank-you note is warranted, following the criteria in
the SKILL.md document. Specifically:

- Hosting hospitality, substantial favors, significant gifts, or
  above-scope professional kindness count.
- Transactional, reciprocal-among-close-friends, coparent exchanges,
  already-thanked threads, and standard-service-provider interactions
  do NOT count.
- If the principal is visibly frustrated in the thread, return
  is_candidate=false with a reason; do not propose a note.

Return ONLY valid JSON matching the output schema. Specifically:
{
  "is_candidate": <bool>,
  "confidence": <float 0-1>,
  "urgency": <"within_24h"|"this_week"|"within_month"|"no_rush">,
  "suggested_medium": <"text"|"email"|"handwritten_card"|"small_gift">,
  "reasons": [<short strings>]
}

If is_candidate is false, omit urgency and suggested_medium.

Confidence guidance:
- 0.9+ when the triggering event is unambiguous (hosting, gift, significant favor)
- 0.7-0.9 when the signal is clear but the relationship context adds uncertainty
- 0.5-0.7 when the signal is weak (could be either way; err toward false negative)
- <0.5 always returns is_candidate=false

Return the JSON and nothing else.
```

### `handler.py`

```python
"""
Handler for classify_thank_you_candidate.

The skill runner invokes run() with validated inputs. This handler:
  1. Fetches additional party metadata if needed (cheap DB read).
  2. Renders the prompt template.
  3. Calls the model router with the rendered prompt.
  4. Parses and validates the response.
  5. Returns the output dict.

The skill runner handles correlation-ID threading, recording of the
prompt/response in the replay archive, and output schema validation.
"""

import json
import logging
from jinja2 import Environment, FileSystemLoader, select_autoescape

from adminme_platform.skills import SkillBase, SkillOutputValidationError
from adminme_platform.models import call_model
from adminme_platform.projections import parties as parties_proj

log = logging.getLogger(__name__)


TIER_LABELS = {
    1: "household/self",
    2: "close friend",
    3: "regular contact / service provider",
    4: "acquaintance",
}


class ClassifyThankYouCandidate(SkillBase):

    skill_id = "classify_thank_you_candidate"
    skill_version = "1.3.0"

    def __init__(self):
        super().__init__()
        self._env = Environment(
            loader=FileSystemLoader(self.pack_dir),
            autoescape=select_autoescape(),
        )
        self._env.filters["tier_label"] = lambda t: TIER_LABELS.get(t, "unknown")
        self._template = self._env.get_template("prompt.jinja2")

    async def run(self, inputs: dict, ctx) -> dict:
        # Resolve principal display name from projection (cheap local DB read).
        principal = await parties_proj.member_by_id(
            tenant_id=ctx.tenant_id,
            member_id=inputs["principal_member_id"],
        )
        principal_display_name = principal["display_name"] if principal else "the principal"

        prompt = self._template.render(
            principal_display_name=principal_display_name,
            party_summary=inputs["party_summary"],
            interaction_context=inputs["interaction_context"],
            tier_label=TIER_LABELS.get,
        )

        response = await call_model(
            preferred="claude-haiku-4-5",
            fallback="claude-opus-4-7",
            prompt=prompt,
            max_tokens=400,
            temperature=0.2,
            correlation_id=ctx.correlation_id,
        )

        # Parse. Models occasionally wrap in code fences; strip them.
        cleaned = _strip_code_fence(response.text.strip())
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as e:
            log.warning("skill response not valid JSON: %s", cleaned[:200])
            # Defensive fallback: return false-candidate low-confidence.
            return {
                "is_candidate": False,
                "confidence": 0.0,
                "reasons": ["skill_parse_error"],
            }

        # The skill runner validates output against schema after we return;
        # but we do one soft check here so our error message is better.
        if parsed.get("is_candidate") is True and not parsed.get("urgency"):
            log.warning("is_candidate=true without urgency; coercing to false")
            parsed = {
                "is_candidate": False,
                "confidence": parsed.get("confidence", 0.0),
                "reasons": parsed.get("reasons", []) + ["missing_urgency"],
            }

        return parsed


def _strip_code_fence(text: str) -> str:
    if text.startswith("```"):
        # strip ```json ... ``` or ``` ... ```
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text
```

### `tests/fixtures/kleins_hosted_us.yaml`

```yaml
name: kleins_hosted_dinner_thank_you
description: |
  The Klein family hosted the Stices for dinner Friday. The relationship's
  hosting balance already leans their way (4-to-1). Skill should propose
  a candidate with urgency this_week, medium handwritten_card.

input:
  principal_member_id: stice-james
  party_summary:
    party_id: p-klein
    name: Tom and Dana Klein
    tier: 3
    tags: ["friends", "neighbors"]
    relationship_summary: "Morningside neighbors. Close enough to trade kid pickups occasionally. Host us more than we host them (4-to-1 lifetime)."
  interaction_context:
    recent_messages:
      - from_member_or_party: "Dana Klein"
        when: "2026-04-19T09:04:00-04:00"
        text: "So great to see y'all last night! Let me know when charlie wants to come swim :)"
      - from_member_or_party: "stice-laura"
        when: "2026-04-19T11:22:00-04:00"
        text: "We had the best time. The lamb was incredible."
    recent_events:
      - kind: hosting_them
        when: "2026-04-18T19:00:00-04:00"
        summary: "Stices for dinner at Kleins'. ~3 hrs, full dinner, included kids."

expected_output:
  is_candidate: true
  confidence_min: 0.80
  urgency: this_week
  suggested_medium: handwritten_card
  reasons_must_include_any_of:
    - "hosting hospitality"
    - "hosted us"
    - "hosting imbalance"
```

### `tests/fixtures/reciprocal_coffee_not_candidate.yaml`

```yaml
name: reciprocal_coffee_with_close_friend_not_candidate
description: |
  Kate and James met for coffee. Tier 2 close friend. Reciprocal activity,
  not hosting. Skill should return is_candidate=false.

input:
  principal_member_id: stice-james
  party_summary:
    party_id: p-kate
    name: Kate Sullivan
    tier: 2
    tags: ["close_friend", "college"]
    relationship_summary: "College friend, 12 years. Lives in Virginia-Highland. See each other regularly; hosting balance even."
  interaction_context:
    recent_messages:
      - from_member_or_party: "p-kate"
        when: "2026-04-20T08:00:00-04:00"
        text: "Coffee this morning was great! see u sat"
    recent_events:
      - kind: calendar_event
        when: "2026-04-20T07:30:00-04:00"
        summary: "Coffee with Kate at Bald Cafe — 60 min, reciprocal"

expected_output:
  is_candidate: false
  confidence_min: 0.70
  reasons_must_include_any_of:
    - "reciprocal"
    - "not hosting"
    - "standard interaction"
```

### `tests/fixtures/coparent_pickup_skip.yaml`

```yaml
name: coparent_pickup_skip
description: |
  Mike did James a favor by switching kid pickup timing — not a thank-you
  candidate (coparent skip rule).

input:
  principal_member_id: stice-james
  party_summary:
    party_id: p-mike
    name: Mike (coparent)
    tier: 3
    tags: ["coparent", "charlie_dad"]
    relationship_summary: "Charlie's dad. Non-user. Logistical relationship only."
  interaction_context:
    recent_messages:
      - from_member_or_party: "p-mike"
        when: "2026-04-20T16:00:00-04:00"
        text: "Picking up C Friday at 4 instead of 5 if that works. Got a thing."
    recent_events: []

expected_output:
  is_candidate: false
  confidence_min: 0.75
  reasons_must_include_any_of:
    - "coparent"
    - "coparenting logistics"
```

**Key takeaways for Claude Code:**

- A skill is a pack directory with a manifest, a SKILL.md (human/LLM-authored context), a prompt template, input/output schemas, and a handler.
- The SKILL.md is *not* the prompt. It's documentation. The prompt renders from a separate template.
- Schemas are JSON Schema 2020-12. The platform validates inputs before calling `run()` and outputs after.
- `call_model` abstracts model routing; the skill declares a preferred + fallback. Instance config can override.
- Test fixtures compare on structure + content-match, not exact-string match. `confidence_min` and `reasons_must_include_any_of` make tests robust to small prompt-driven wording changes.
- Replay archives (not shown here but referenced in pack.yaml) record the exact model response for each fixture at skill-version-release time. CI diffs new fixture runs against the archive to catch regressions.
- A skill never emits events directly. It returns a value; the calling pipeline decides what events to emit.

---

## 4. Projection: `parties`

**What it is.** The CRM projection. Reads `party.*` and `identifier.*` events from the log; maintains a SQLite-backed read model of people, organizations, households, and the identifiers (emails, phones, iMessage handles, addresses) that belong to them. Also derives tier, summary, and last-interaction fields from associated interaction events.

**Why this projection is the reference example.** It's the canonical read model in the Hearth CRM spine. It handles event-driven state (parties are created, merged, split, tagged), cross-projection derivation (tier comes from the scoring pipeline; summary comes from the summarization pipeline), and full replay from the event log. Its shape generalizes — other projections (commitments, artifacts, tasks) follow the same skeleton.

**Pack root.** This is a built-in projection, not a third-party pack. Lives in `adminme/projections/parties/`.

### Schema

```sql
-- ~/.adminme/adminme/projections/parties/schema.sql
-- Version 4. Migrations live in migrations/.

CREATE TABLE IF NOT EXISTS parties (
  party_id           TEXT PRIMARY KEY,
  tenant_id          TEXT NOT NULL,
  kind               TEXT NOT NULL CHECK (kind IN ('person','organization','household','group')),
  display_name       TEXT NOT NULL,
  given_name         TEXT,
  family_name        TEXT,
  org_name           TEXT,
  notes_free_text    TEXT,
  created_event_id   TEXT NOT NULL,
  created_at_ms      INTEGER NOT NULL,
  updated_at_ms      INTEGER NOT NULL,
  merged_into_party_id TEXT,         -- non-null if this party was merged away
  deleted_at_ms      INTEGER,
  tier               INTEGER,         -- derived by closeness_scoring pipeline
  tier_confidence    REAL,
  summary_text       TEXT,            -- derived by relationship_summarization
  summary_computed_at_ms INTEGER,
  FOREIGN KEY (merged_into_party_id) REFERENCES parties(party_id)
);

CREATE INDEX IF NOT EXISTS idx_parties_tenant_kind ON parties(tenant_id, kind);
CREATE INDEX IF NOT EXISTS idx_parties_tenant_name ON parties(tenant_id, display_name);
CREATE INDEX IF NOT EXISTS idx_parties_merged ON parties(merged_into_party_id) WHERE merged_into_party_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS party_identifiers (
  identifier_id      TEXT PRIMARY KEY,
  party_id           TEXT NOT NULL,
  tenant_id          TEXT NOT NULL,
  kind               TEXT NOT NULL CHECK (kind IN
                       ('email','phone','imessage_handle','google_id','apple_id',
                        'postal_address','url','handle_generic')),
  value              TEXT NOT NULL,
  value_normalized   TEXT NOT NULL,   -- phone E.164; email lowercased; etc.
  verified           INTEGER DEFAULT 0,
  is_primary         INTEGER DEFAULT 0,
  sensitivity        TEXT NOT NULL DEFAULT 'normal'
                     CHECK (sensitivity IN ('normal','sensitive','privileged')),
  created_event_id   TEXT NOT NULL,
  revoked_at_ms      INTEGER,
  FOREIGN KEY (party_id) REFERENCES parties(party_id)
);

CREATE INDEX IF NOT EXISTS idx_pid_tenant_kind_normval
  ON party_identifiers(tenant_id, kind, value_normalized)
  WHERE revoked_at_ms IS NULL;
CREATE INDEX IF NOT EXISTS idx_pid_party ON party_identifiers(party_id);

CREATE TABLE IF NOT EXISTS party_tags (
  party_id           TEXT NOT NULL,
  tenant_id          TEXT NOT NULL,
  tag                TEXT NOT NULL,
  source             TEXT NOT NULL,   -- 'manual' | 'pipeline:tag_miner@v2' | ...
  created_at_ms      INTEGER NOT NULL,
  PRIMARY KEY (party_id, tag)
);

CREATE TABLE IF NOT EXISTS party_relationships (
  relationship_id    TEXT PRIMARY KEY,
  tenant_id          TEXT NOT NULL,
  party_a            TEXT NOT NULL,
  party_b            TEXT NOT NULL,
  label              TEXT NOT NULL,   -- 'spouse','parent','friend','colleague',...
  direction          TEXT NOT NULL CHECK (direction IN ('mutual','a_to_b','b_to_a')),
  since_date         TEXT,            -- ISO date or null
  notes              TEXT,
  created_event_id   TEXT NOT NULL,
  revoked_at_ms      INTEGER
);

CREATE INDEX IF NOT EXISTS idx_rel_a ON party_relationships(party_a, tenant_id);
CREATE INDEX IF NOT EXISTS idx_rel_b ON party_relationships(party_b, tenant_id);

-- Projection checkpoint: last event consumed.
CREATE TABLE IF NOT EXISTS projection_checkpoint (
  projection_id      TEXT PRIMARY KEY,
  last_event_id      TEXT NOT NULL,
  last_event_at_ms   INTEGER NOT NULL,
  updated_at_ms      INTEGER NOT NULL
);
```

### Event handlers

```python
# adminme/projections/parties/handlers.py
"""
Event handlers for the `parties` projection.

Each handler is idempotent: running the same event twice produces the
same state. This is critical for replay (rebuild projection from
scratch by replaying the entire event log).
"""

from typing import Optional
from adminme_platform.projections.base import ProjectionBase, register_handler


class PartiesProjection(ProjectionBase):

    projection_id = "parties"
    projection_version = "4"

    consumed_event_types = [
        "party.created",
        "party.updated",
        "party.merged",
        "party.deleted",
        "identifier.added",
        "identifier.verified",
        "identifier.revoked",
        "identifier.primary_changed",
        "party.tag.added",
        "party.tag.removed",
        "relationship.added",
        "relationship.revoked",
        "party.tier.computed",           # from closeness_scoring pipeline
        "party.summary.computed",        # from relationship_summarization pipeline
    ]

    # ---- party lifecycle ----

    @register_handler("party.created")
    async def on_party_created(self, event: dict) -> None:
        p = event["payload"]
        await self.db.execute(
            """
            INSERT INTO parties (
              party_id, tenant_id, kind, display_name,
              given_name, family_name, org_name,
              notes_free_text, created_event_id, created_at_ms, updated_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(party_id) DO NOTHING
            """,
            (
                p["party_id"], event["tenant_id"], p["kind"], p["display_name"],
                p.get("given_name"), p.get("family_name"), p.get("org_name"),
                p.get("notes_free_text"), event["event_id"],
                event["event_at_ms"], event["event_at_ms"],
            ),
        )

    @register_handler("party.updated")
    async def on_party_updated(self, event: dict) -> None:
        p = event["payload"]
        # Only update fields present in payload. COALESCE pattern.
        await self.db.execute(
            """
            UPDATE parties SET
              display_name = COALESCE(?, display_name),
              given_name = COALESCE(?, given_name),
              family_name = COALESCE(?, family_name),
              org_name = COALESCE(?, org_name),
              notes_free_text = COALESCE(?, notes_free_text),
              updated_at_ms = ?
            WHERE party_id = ? AND tenant_id = ?
            """,
            (
                p.get("display_name"), p.get("given_name"), p.get("family_name"),
                p.get("org_name"), p.get("notes_free_text"),
                event["event_at_ms"], p["party_id"], event["tenant_id"],
            ),
        )

    @register_handler("party.merged")
    async def on_party_merged(self, event: dict) -> None:
        # Source party is marked merged into target. Downstream consumers
        # (indexes, tasks, commitments) handle via the merged_into pointer.
        # We do NOT delete rows or cascade here — event log is source of truth.
        p = event["payload"]
        await self.db.execute(
            """
            UPDATE parties
               SET merged_into_party_id = ?, updated_at_ms = ?
             WHERE party_id = ? AND tenant_id = ?
            """,
            (p["into_party_id"], event["event_at_ms"], p["from_party_id"], event["tenant_id"]),
        )

    @register_handler("party.deleted")
    async def on_party_deleted(self, event: dict) -> None:
        # Soft-delete: set deleted_at_ms. Hard delete only from cleanup tool
        # with explicit principal action; event-driven deletion leaves the row.
        p = event["payload"]
        await self.db.execute(
            """
            UPDATE parties SET deleted_at_ms = ?
             WHERE party_id = ? AND tenant_id = ?
            """,
            (event["event_at_ms"], p["party_id"], event["tenant_id"]),
        )

    # ---- identifiers ----

    @register_handler("identifier.added")
    async def on_identifier_added(self, event: dict) -> None:
        p = event["payload"]
        await self.db.execute(
            """
            INSERT INTO party_identifiers (
              identifier_id, party_id, tenant_id, kind, value,
              value_normalized, verified, is_primary, sensitivity,
              created_event_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(identifier_id) DO NOTHING
            """,
            (
                p["identifier_id"], p["party_id"], event["tenant_id"],
                p["kind"], p["value"], _normalize(p["kind"], p["value"]),
                int(p.get("verified", False)),
                int(p.get("is_primary", False)),
                p.get("sensitivity", "normal"),
                event["event_id"],
            ),
        )

    @register_handler("identifier.verified")
    async def on_identifier_verified(self, event: dict) -> None:
        p = event["payload"]
        await self.db.execute(
            "UPDATE party_identifiers SET verified = 1 WHERE identifier_id = ? AND tenant_id = ?",
            (p["identifier_id"], event["tenant_id"]),
        )

    @register_handler("identifier.revoked")
    async def on_identifier_revoked(self, event: dict) -> None:
        p = event["payload"]
        await self.db.execute(
            """
            UPDATE party_identifiers SET revoked_at_ms = ?
             WHERE identifier_id = ? AND tenant_id = ?
            """,
            (event["event_at_ms"], p["identifier_id"], event["tenant_id"]),
        )

    @register_handler("identifier.primary_changed")
    async def on_identifier_primary_changed(self, event: dict) -> None:
        p = event["payload"]
        # Clear is_primary on all identifiers of the same kind for this party,
        # then set it on the specified one.
        await self.db.execute(
            """
            UPDATE party_identifiers SET is_primary = 0
             WHERE party_id = ? AND tenant_id = ? AND kind = ?
            """,
            (p["party_id"], event["tenant_id"], p["kind"]),
        )
        await self.db.execute(
            "UPDATE party_identifiers SET is_primary = 1 WHERE identifier_id = ?",
            (p["identifier_id"],),
        )

    # ---- tags ----

    @register_handler("party.tag.added")
    async def on_tag_added(self, event: dict) -> None:
        p = event["payload"]
        await self.db.execute(
            """
            INSERT INTO party_tags (party_id, tenant_id, tag, source, created_at_ms)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(party_id, tag) DO UPDATE SET source = excluded.source
            """,
            (p["party_id"], event["tenant_id"], p["tag"], p["source"], event["event_at_ms"]),
        )

    @register_handler("party.tag.removed")
    async def on_tag_removed(self, event: dict) -> None:
        p = event["payload"]
        await self.db.execute(
            "DELETE FROM party_tags WHERE party_id = ? AND tag = ? AND tenant_id = ?",
            (p["party_id"], p["tag"], event["tenant_id"]),
        )

    # ---- relationships ----

    @register_handler("relationship.added")
    async def on_relationship_added(self, event: dict) -> None:
        p = event["payload"]
        await self.db.execute(
            """
            INSERT INTO party_relationships (
              relationship_id, tenant_id, party_a, party_b,
              label, direction, since_date, notes, created_event_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(relationship_id) DO NOTHING
            """,
            (
                p["relationship_id"], event["tenant_id"],
                p["party_a"], p["party_b"], p["label"],
                p.get("direction", "mutual"), p.get("since_date"),
                p.get("notes"), event["event_id"],
            ),
        )

    @register_handler("relationship.revoked")
    async def on_relationship_revoked(self, event: dict) -> None:
        p = event["payload"]
        await self.db.execute(
            "UPDATE party_relationships SET revoked_at_ms = ? WHERE relationship_id = ?",
            (event["event_at_ms"], p["relationship_id"]),
        )

    # ---- derived fields from pipelines ----

    @register_handler("party.tier.computed")
    async def on_tier_computed(self, event: dict) -> None:
        p = event["payload"]
        await self.db.execute(
            """
            UPDATE parties
               SET tier = ?, tier_confidence = ?, updated_at_ms = ?
             WHERE party_id = ? AND tenant_id = ?
            """,
            (p["tier"], p.get("confidence", 1.0), event["event_at_ms"],
             p["party_id"], event["tenant_id"]),
        )

    @register_handler("party.summary.computed")
    async def on_summary_computed(self, event: dict) -> None:
        p = event["payload"]
        await self.db.execute(
            """
            UPDATE parties
               SET summary_text = ?, summary_computed_at_ms = ?
             WHERE party_id = ? AND tenant_id = ?
            """,
            (p["summary_text"], event["event_at_ms"], p["party_id"], event["tenant_id"]),
        )


def _normalize(kind: str, value: str) -> str:
    """Kind-specific normalization for cross-row lookups."""
    if kind == "email":
        return value.lower().strip()
    if kind in ("phone", "imessage_handle"):
        # Strip to digits, prepend + for E.164 if length suggests US.
        digits = "".join(c for c in value if c.isdigit())
        if len(digits) == 10:
            return f"+1{digits}"
        if len(digits) == 11 and digits.startswith("1"):
            return f"+{digits}"
        return f"+{digits}" if digits else value
    return value.strip()
```

### Query functions (the public API of the projection)

```python
# adminme/projections/parties/queries.py
"""
Read-only functions that the rest of the platform (pipelines, APIs,
skills) uses to query the projection. No event-emission here; read-only.
"""

from typing import Optional
from adminme_platform.projections.base import projection_db


async def resolve_by_identifier(
    tenant_id: str,
    kind: str,
    value: str,
) -> Optional[dict]:
    """
    Given an identifier (e.g. kate@icloud.com), return the owning party.
    Follows merge pointers so callers always get the final party.
    Returns None if no party owns this identifier.
    """
    db = projection_db("parties")
    from .handlers import _normalize
    normalized = _normalize(kind, value)
    row = await db.fetchone(
        """
        SELECT p.*
          FROM party_identifiers pi
          JOIN parties p ON p.party_id = pi.party_id
         WHERE pi.tenant_id = ?
           AND pi.kind = ?
           AND pi.value_normalized = ?
           AND pi.revoked_at_ms IS NULL
         LIMIT 1
        """,
        (tenant_id, kind, normalized),
    )
    if row is None:
        return None
    return await _follow_merge(row)


async def _follow_merge(row: dict) -> dict:
    if row.get("merged_into_party_id") is None:
        return await _hydrate(row)
    db = projection_db("parties")
    target = await db.fetchone(
        "SELECT * FROM parties WHERE party_id = ?", (row["merged_into_party_id"],),
    )
    if target is None:
        # Data integrity issue; return original with a flag.
        hydrated = await _hydrate(row)
        hydrated["_merge_dangling"] = True
        return hydrated
    return await _follow_merge(target)


async def _hydrate(row: dict) -> dict:
    db = projection_db("parties")
    tags = await db.fetchall(
        "SELECT tag, source FROM party_tags WHERE party_id = ?",
        (row["party_id"],),
    )
    row["tags"] = [t["tag"] for t in tags]
    row["tag_sources"] = {t["tag"]: t["source"] for t in tags}
    return row


async def resolve_routing_owner(
    tenant_id: str,
    channel: str,
    chat_guid: str,
) -> Optional[dict]:
    """
    Find which member is the routing owner for a given channel thread.
    Used by commitment_extraction to determine who owes the response.

    The routing config lives in a separate projection (`channel_routing`),
    but parties exposes a convenience wrapper.
    """
    from adminme_platform.projections import channel_routing
    return await channel_routing.routing_owner(
        tenant_id=tenant_id, channel=channel, chat_guid=chat_guid,
    )


async def member_by_id(tenant_id: str, member_id: str) -> Optional[dict]:
    """Members are a subset of parties with role != null."""
    db = projection_db("parties")
    return await db.fetchone(
        """
        SELECT p.*, pt.tag as role
          FROM parties p
          LEFT JOIN party_tags pt ON pt.party_id = p.party_id AND pt.tag LIKE 'role:%'
         WHERE p.party_id = ? AND p.tenant_id = ?
        """,
        (member_id, tenant_id),
    )


async def list_by_tier(tenant_id: str, tier: int, limit: int = 200) -> list[dict]:
    db = projection_db("parties")
    return await db.fetchall(
        """
        SELECT * FROM parties
         WHERE tenant_id = ? AND tier = ? AND deleted_at_ms IS NULL
         ORDER BY display_name ASC LIMIT ?
        """,
        (tenant_id, tier, limit),
    )


async def search(tenant_id: str, query: str, limit: int = 40) -> list[dict]:
    db = projection_db("parties")
    pattern = f"%{query.lower()}%"
    return await db.fetchall(
        """
        SELECT DISTINCT p.* FROM parties p
          LEFT JOIN party_identifiers pi ON pi.party_id = p.party_id
         WHERE p.tenant_id = ?
           AND p.deleted_at_ms IS NULL
           AND (
             LOWER(p.display_name) LIKE ?
             OR LOWER(p.notes_free_text) LIKE ?
             OR LOWER(pi.value_normalized) LIKE ?
           )
         ORDER BY p.display_name ASC
         LIMIT ?
        """,
        (tenant_id, pattern, pattern, pattern, limit),
    )
```

**Key takeaways for Claude Code:**

- Projections have two parts: handlers (consume events, write to the projection DB) and queries (read from the projection DB, return to callers). Never write to the DB from a query function. Never read from the event log in a handler.
- Handlers must be idempotent. `ON CONFLICT DO NOTHING` / `ON CONFLICT DO UPDATE` is the standard pattern. Replaying the same event twice must not change state.
- Soft-delete: set a `deleted_at_ms` column, never DELETE. The event log is the source of truth; projection rows are derived views that can be rebuilt.
- Merge pointers, not cascades. A merged party's row stays; queries follow the pointer. This preserves historical identifiers that reference the merged-away party.
- `register_handler("event.type")` is the dispatch mechanism. One handler per event type. The base class reads the checkpoint table to know where to resume on restart.
- Derived fields (`tier`, `summary_text`) come from pipeline events, not inline computation. This keeps the projection simple and makes the derivation reproducible.

---

## 5. Event type: `commitment.proposed`

**What it is.** An event emitted by the `commitment_extraction` pipeline (and occasionally by other sources: manual user entry, voice capture) proposing a commitment. A `commitment.proposed` event is a proposal, not a confirmation. It enters the commitments projection as a `pending` commitment that the inbox surfaces to a principal for approval.

**Why this event type is the reference example.** It's the most upstream event in the Hearth commitment flow and demonstrates the full event-shape contract: required envelope fields, payload schema, source provenance, relationships to other events, version compatibility.

### Schema file location

`adminme/events/schemas/commitment.proposed.v3.json`

### Schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "commitment.proposed",
  "description": "A pipeline (or user) proposes that a commitment exists. Pending approval until confirmed or dismissed.",
  "type": "object",
  "required": ["type", "event_id", "event_at_ms", "tenant_id", "payload", "source"],
  "properties": {
    "type": { "const": "commitment.proposed" },
    "version": { "const": 3 },
    "event_id": {
      "type": "string",
      "pattern": "^ev_[a-z0-9]{16,32}$",
      "description": "Globally unique ID. Content-hashed or ULID."
    },
    "event_at_ms": {
      "type": "integer",
      "description": "Wall-clock time of emission, milliseconds since epoch."
    },
    "tenant_id": { "type": "string" },
    "correlation_id": {
      "type": "string",
      "description": "Set by the upstream write; allows tracing this proposal back to the originating user action or incoming message."
    },
    "source": {
      "type": "object",
      "required": ["pipeline"],
      "properties": {
        "pipeline": { "type": "string", "description": "e.g. 'commitment_extraction'" },
        "pipeline_version": { "type": "string" },
        "skills": {
          "type": "array",
          "items": { "type": "string", "description": "skill_id@version used in decision" }
        },
        "source_event_id": {
          "type": "string",
          "description": "The messaging.received or capture.created event that produced this proposal"
        }
      }
    },
    "payload": {
      "type": "object",
      "required": [
        "kind",
        "owed_by_member_id",
        "owed_to_party_id",
        "text_summary",
        "confidence",
        "strength"
      ],
      "properties": {
        "kind": {
          "type": "string",
          "enum": ["reply", "task", "appointment", "payment", "document_return", "visit", "other"],
          "description": "What kind of commitment. Drives UI rendering and default due-date calculation."
        },
        "owed_by_member_id": {
          "type": "string",
          "description": "The household member who owes the commitment."
        },
        "owed_to_party_id": {
          "type": "string",
          "description": "The party the commitment is owed to. Can be another member or an external party."
        },
        "text_summary": {
          "type": "string",
          "maxLength": 500,
          "description": "One-sentence, UI-facing summary. First-person from the owed_by perspective."
        },
        "suggested_due": {
          "type": "string",
          "format": "date",
          "description": "ISO date. Optional — if missing, UI uses kind-based default (reply=3d, task=7d, etc.)."
        },
        "urgency": {
          "type": "string",
          "enum": ["today", "this_week", "this_month", "no_rush"],
          "default": "this_week"
        },
        "confidence": {
          "type": "number",
          "minimum": 0.0,
          "maximum": 1.0,
          "description": "Combined confidence from classify+extract skills."
        },
        "strength": {
          "type": "string",
          "enum": ["confident", "weak"],
          "description": "Above or below the review_threshold. UI shows weak proposals with a visible confidence score; confident proposals are surfaced plainly."
        },
        "source_interaction_id": { "type": "string" },
        "source_message_preview": { "type": "string", "maxLength": 240 },
        "classify_reasons": {
          "type": "array",
          "items": { "type": "string" },
          "description": "Human-readable classification reasons from the skill. Shown in the inbox proposal card."
        }
      }
    }
  }
}
```

### Example event (Stice instance, Kate → James)

```json
{
  "type": "commitment.proposed",
  "version": 3,
  "event_id": "ev_0j5k8m2n4p6q8r",
  "event_at_ms": 1745231405123,
  "tenant_id": "stice-household",
  "correlation_id": "c_m1_abc123",
  "source": {
    "pipeline": "commitment_extraction",
    "pipeline_version": "4.2.0",
    "skills": [
      "classify_commitment_candidate@3.2.1",
      "extract_commitment_fields@2.1.0"
    ],
    "source_event_id": "ev_received_bb_g_12345"
  },
  "payload": {
    "kind": "reply",
    "owed_by_member_id": "stice-james",
    "owed_to_party_id": "p-kate",
    "text_summary": "Reply to Kate's Saturday kitchen walk-through proposal",
    "suggested_due": "2026-04-26",
    "urgency": "this_week",
    "confidence": 0.87,
    "strength": "confident",
    "source_interaction_id": "int-bb_g_12345",
    "source_message_preview": "Hey! Any interest in swinging by Sat around 2? Want to pick your brain on the island layout before we commit. Wine on me.",
    "classify_reasons": [
      "contains scheduling proposal",
      "awaits sender-relevant response",
      "sender is in principal's close tier"
    ]
  }
}
```

### Consumers

Who reads `commitment.proposed` events:

- **`commitments` projection** — inserts a row in `commitments` with `status='pending'`. The inbox reads from this projection.
- **`inbox_surface` projection** — appends a proposal card to the relevant member's inbox list.
- **`noise_filtering` pipeline** — does not re-process proposals, but tracks proposal rate per source-interaction to detect loops.
- **Test fixtures** — many other pipelines' tests include `commitment.proposed` events as setup to exercise downstream flows.

### Downstream events this triggers (directly or indirectly)

- `commitment.confirmed` (when a principal approves the proposal in the inbox)
- `commitment.dismissed` (when a principal dismisses)
- `commitment.edited` (when a principal edits before approving)
- `commitment.expired` (background job: proposals older than 14 days that weren't acted on)

### Compatibility and versioning

- **v1** (deprecated): no `strength` field. Projection handler treats missing `strength` as `confident`. Event log keeps v1 events verbatim; the handler knows both schemas.
- **v2** (deprecated): added `strength`; `classify_reasons` was called `reasons`. The projection rename is handled in the handler.
- **v3** (current): renamed `reasons` → `classify_reasons` to distinguish from future `extract_reasons`. Added `urgency` as required.

When a breaking schema change is needed:
1. Bump the event version (3 → 4).
2. Define both schemas (v3 stays on disk; new events emit v4).
3. Update all projection handlers to accept both versions. Old events in the log must still replay.
4. Update all pipelines that emit the event to emit v4.
5. Never rewrite old events in the log. The log is immutable.

### Failure modes

- **Orphaned proposal.** `owed_by_member_id` refers to a member that was removed. The commitments projection handler should insert the row anyway (non-destructive), but the inbox surface filters it out (member deleted → no inbox).
- **Dangling source_event_id.** The referenced messaging event doesn't exist or was in a namespace the reader doesn't have. Should be rare; fail open (show the proposal without the source preview).
- **Confidence out of range.** Schema enforces 0.0-1.0; a skill that returns 1.1 fails validation at emission time, before the event hits the log.

### Testing requirements

Any pipeline that emits `commitment.proposed` must include a fixture proving:
- All required fields are populated
- `owed_by_member_id` resolves to a real member in the fixture's tenant
- `owed_to_party_id` resolves to a real party
- `confidence` and `strength` are consistent (weak ↔ < review_threshold, confident ↔ ≥ review_threshold)
- `source.skills` list is populated iff skills were actually called

**Key takeaways for Claude Code:**

- Event types have explicit schemas, versioned independently from the platform itself.
- Schemas use JSON Schema 2020-12. Validation happens at emission time (before write to log) and at consumption time (in projection handlers, for extra safety).
- Every event has the same envelope: `type`, `version`, `event_id`, `event_at_ms`, `tenant_id`, optional `correlation_id`, optional `source`, and `payload`. Envelope is constant; payload is type-specific.
- Old event versions stay in the log forever. Projection handlers must accept all known versions of any event type they consume.
- Breaking changes bump the event version; non-breaking (adding optional fields) do not.
- `source.pipeline_version` and `source.skills` record exact versions at emission time. Replay + audit requires this.

---

## 6. Profile pack: `adhd_executive` (James's profile)

**What it is.** The profile pack assigned to James. Defines his view mode (carousel), reward tier distribution (variable_ratio), paralysis detection window, proactive nudge cap, guilt-language filter, and the specific today-stream layout. Compiled at install time from JSX to a bundle the Node console serves.

**Why this profile is the reference example.** It's the most complex built-in profile pack and exercises every profile-pack extension point: views (JSX), config, reward sampling distribution, pipeline toggles, per-profile skill overrides, tests.

**Pack root.** `~/.adminme/packs/profiles/adhd-executive/`

### `pack.yaml`

```yaml
pack:
  id: profile:adhd_executive
  name: ADHD Executive
  version: 1.4.2
  kind: profile
  author: built-in
  license: Apache-2.0
  min_platform: 0.4.0

description: |
  For members with ADHD-Primarily Inattentive or similar executive-
  function differences. The view minimizes paralysis (one task at a
  time, explicit micro-step), amplifies momentum (variable-ratio
  rewards, endowed-progress dots), and filters guilt language out of
  all rendered text. Tuned for principals who benefit from doing over
  deciding — the carousel shows *the next thing to do*, not *everything
  you haven't done yet*.

views:
  today: views/today.jsx
  # The today view is JSX; esbuild compiles at install time to
  # compiled/today.bundle.js that the Node console serves statically.

reward_distribution:
  # Probability of each tier when a task completes. Calibrated so most
  # completions feel like "small wins" and a rare one lights up.
  # Tier templates live in the active persona pack.
  done: 0.60
  warm: 0.25
  delight: 0.10
  jackpot: 0.05

pipelines:
  # On/off toggles for per-profile behavior. These modulate shared
  # platform pipelines — they don't replace them.
  paralysis_detection:
    enabled: true
    idle_threshold_minutes: 90     # inactivity before paralysis check
    active_window:                 # only run between these local hours
      start: "15:00"
      end: "17:00"
  whatnow_ranking:
    enabled: true
    weighting:
      urgency: 0.3
      energy_match: 0.4            # match to current_energy
      context_continuity: 0.3
  reward_dispatch:
    enabled: true
    max_per_hour: 6
  morning_digest:
    enabled: true
    delivery_time_local: "06:30"
    delivery_channel: "imessage"
    max_items: 6
    force_micro_step: true

nudge_caps:
  proactive_per_day: 15            # across ALL nudge pipelines combined
  reward_per_hour: 6
  paralysis_nudges_per_day: 2

text_filters:
  # Words/patterns removed or softened from any text shown to the member.
  # Applied by the rendering layer, not by skills (so the text in logs
  # is unfiltered for audit).
  guilt_filter:
    enabled: true
    remove_patterns:
      - "should have"
      - "forgot to"
      - "didn't get to"
      - "again"         # as in "you're late again" → "you're late"
    soften_patterns:
      "overdue": "past due"
      "late": "past the due date"
      "behind on": "still on"

skills_overrides:
  # Per-profile skill parameter overrides. The base skill stays the same;
  # the profile tweaks parameters.
  "classify_capture_intent@^2.0.0":
    temperature: 0.4               # slightly higher = more forgiving routing
  "generate_task_micro_step@^1.5.0":
    style: "terse_imperative"      # vs. "coaching" (default) or "verbose"
    require_time_estimate: true

view_config:
  carousel:
    show_progress_dots: true
    endow_progress_count: 3        # start the day with 3 dots filled
    show_next_preview: false       # don't show the next task; one at a time
    show_micro_step: true
    show_energy_gate: true         # display "low/med/high" tag
    carousel_transition_ms: 240

tests:
  fixtures: tests/fixtures/
  render_snapshots: tests/snapshots/
```

### `views/today.jsx`

```jsx
// ~/.adminme/packs/profiles/adhd-executive/views/today.jsx
//
// Compiled at install time by esbuild into compiled/today.bundle.js.
// Rendered by the Node console at /today for any member assigned this
// profile.
//
// Data comes from /api/today?view_as={memberId} (see CONSOLE_PATTERNS
// section 2). The view is pure rendering + local UI state; no direct
// data fetching from inside the component.

import React, { useState, useEffect, useCallback } from 'react';
import { Card, Pill, Button } from '@adminme/ui';     // built-in UI kit with PIB v5 tokens
import { fireReward, fireRateLimit } from '@adminme/rewards';

export default function AdhdExecutiveToday({ session, data, config }) {
  const [carouselIdx, setCarouselIdx] = useState(0);
  const [viewMode, setViewMode] = useState('carousel');
  const [energy, setEnergy] = useState('medium');

  // data.tasks is already privacy-filtered and ordered by the whatnow_ranking
  // pipeline. If tasks is empty, the server sent an empty-state payload.
  const tasks = data.tasks || [];
  const current = tasks[carouselIdx];

  const completeTask = useCallback(async (taskId) => {
    const resp = await fetch('/api/tasks/complete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: taskId }),
    });
    if (resp.status === 429) {
      fireRateLimit();
      return;
    }
    if (!resp.ok) return;
    const body = await resp.json();
    if (body.reward_preview) {
      fireReward(
        body.reward_preview.tier,
        body.reward_preview.message,
        body.reward_preview.sub,
      );
    }
    // Advance carousel
    setCarouselIdx((i) => (i + 1) % Math.max(tasks.length, 1));
  }, [tasks.length]);

  const deferTask = useCallback(() => {
    setCarouselIdx((i) => (i + 1) % Math.max(tasks.length, 1));
  }, [tasks.length]);

  const snoozeTask = useCallback(async (taskId) => {
    await fetch('/api/tasks/snooze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: taskId, hours: 2 }),
    });
    setCarouselIdx((i) => (i + 1) % Math.max(tasks.length, 1));
  }, []);

  // Energy state is persisted server-side on change so whatnow_ranking
  // can re-order. We just call the API; the next poll reflects the change.
  const setEnergyAndPersist = useCallback((next) => {
    setEnergy(next);
    fetch('/api/members/me/energy', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ energy: next }),
    });
  }, []);

  if (tasks.length === 0) {
    return <EmptyState />;
  }

  if (viewMode === 'compressed') {
    return (
      <CompressedView
        tasks={tasks}
        onComplete={completeTask}
        onModeChange={setViewMode}
      />
    );
  }

  return (
    <div className="adhd-today">
      <Header
        greeting={data.greeting}
        sub={data.sub}
        viewMode={viewMode}
        onViewModeChange={setViewMode}
      />

      <div className="layout">
        <CarouselCard
          task={current}
          index={carouselIdx}
          total={tasks.length}
          onComplete={() => completeTask(current.id)}
          onDefer={deferTask}
          onSnooze={() => snoozeTask(current.id)}
          onPrev={() => setCarouselIdx((i) => (i - 1 + tasks.length) % tasks.length)}
          onNext={() => setCarouselIdx((i) => (i + 1) % tasks.length)}
          progressDots={config.view_config.carousel.show_progress_dots}
          endowCount={config.view_config.carousel.endow_progress_count}
          completedToday={data.velocity || 0}
        />

        <Sidebar
          energy={energy}
          onEnergy={setEnergyAndPersist}
          schedule={data.schedule}
          waitingOn={data.waiting_on}
          velocity={data.velocity}
        />
      </div>
    </div>
  );
}

function Header({ greeting, sub, viewMode, onViewModeChange }) {
  return (
    <div className="today-header">
      <div>
        <h1 className="today-greet">{greeting}</h1>
        <p className="today-sub">{sub}</p>
      </div>
      <div className="view-mode-toggle" role="tablist">
        <button
          role="tab"
          aria-selected={viewMode === 'carousel'}
          className={viewMode === 'carousel' ? 'active' : ''}
          onClick={() => onViewModeChange('carousel')}
        >
          Carousel
        </button>
        <button
          role="tab"
          aria-selected={viewMode === 'compressed'}
          className={viewMode === 'compressed' ? 'active' : ''}
          onClick={() => onViewModeChange('compressed')}
        >
          Compressed
        </button>
      </div>
    </div>
  );
}

function CarouselCard({
  task, index, total, onComplete, onDefer, onSnooze, onPrev, onNext,
  progressDots, endowCount, completedToday,
}) {
  const metaPills = (task.meta || []).map(([label, variant]) => (
    <Pill key={label} variant={variant}>{label}</Pill>
  ));

  const dotsFilled = Math.min(endowCount + completedToday, 5);

  return (
    <Card variant="carousel" className="carousel-card">
      <Pill variant="dim" className="time-pill">{task.time}</Pill>

      <div className="context">{task.context}</div>
      <h2 className="title">{task.title}</h2>

      {task.micro_step && (
        <p className="micro">{task.micro_step}</p>
      )}

      <div className="meta">{metaPills}</div>

      {progressDots && (
        <ProgressDots filled={dotsFilled} total={5} />
      )}

      <div className="actions">
        <Button variant="primary" size="large" onClick={onComplete}>
          Done
        </Button>
        <Button variant="ghost" onClick={onDefer}>Not now</Button>
        <Button variant="ghost" onClick={onSnooze}>Snooze 2h</Button>
      </div>

      <div className="nav">
        <Button variant="ghost" size="small" onClick={onPrev}>← Previous</Button>
        <span className="counter">{index + 1} of {total}</span>
        <Button variant="ghost" size="small" onClick={onNext}>Next →</Button>
      </div>
    </Card>
  );
}

function ProgressDots({ filled, total }) {
  return (
    <div className="progress-dots">
      <span>Today's streak:</span>
      <span className="dot-row">
        {Array.from({ length: total }, (_, i) => (
          <span key={i} className={`dot ${i < filled ? 'filled' : ''}`} />
        ))}
      </span>
      <span className="caption">{filled} of {total} to unlock the 4-day streak</span>
    </div>
  );
}

function CompressedView({ tasks, onComplete, onModeChange }) {
  // ...implementation omitted for brevity; see console reference HTML for layout...
  return <div className="compressed-view">{/* ... */}</div>;
}

function Sidebar({ energy, onEnergy, schedule, waitingOn, velocity }) {
  // ...omitted; see console reference...
  return <aside className="sidebar">{/* ... */}</aside>;
}

function EmptyState() {
  return (
    <Card>
      <p>Nothing on the plate right now. Take a break — or tell me what you want to knock out.</p>
    </Card>
  );
}
```

### Reward sampling (consumed by the reward_dispatch pipeline)

```yaml
# ~/.adminme/packs/profiles/adhd-executive/reward_sampling.yaml
#
# The reward_dispatch pipeline reads this when a task completes for a
# member assigned this profile. Distribution must sum to 1.0.

distribution:
  done: 0.60
  warm: 0.25
  delight: 0.10
  jackpot: 0.05

# Optional: variance modulation. When a member is on a hot streak
# (3+ completes in the last 10 minutes), slightly bias toward higher
# tiers to amplify momentum. When paralysis was recently detected,
# slightly bias toward 'done' to avoid over-stimulation.
streak_modifier:
  active_threshold_completes_10min: 3
  done: -0.10
  warm: +0.00
  delight: +0.05
  jackpot: +0.05

paralysis_modifier:
  active_minutes_since: 60
  done: +0.10
  warm: +0.00
  delight: -0.05
  jackpot: -0.05
```

### Profile tests

```yaml
# ~/.adminme/packs/profiles/adhd-executive/tests/fixtures/render_smoke.yaml

name: render_adhd_executive_today_with_six_tasks
description: |
  Smoke test: the today view renders without error given six tasks
  and standard velocity/schedule data.

session:
  auth_role: principal
  view_member_id: stice-james
  profile_id: profile:adhd_executive

data:
  greeting: "Good morning, James."
  sub: "Six things on the plate today. Three of them are small."
  tasks:
    - id: t-001
      time: "now · 09:42"
      context: "from inbox · kate"
      title: "Text Kate back about Saturday kitchen walk-through"
      micro_step: "Open iMessage, pick 'yes 2pm' or 'Sunday better,' send."
      meta: [["low energy", "blush"], ["tiny", "teal"], ["social", "dim"]]
    # ... more tasks ...
  velocity: 3
  schedule:
    - time: "09:00"
      title: "Laura: OB appointment"
      sub: "Emory, 32-week check-in"
  waiting_on:
    - "Reliable Restoration — blackwater scope revision · 5 days"

assertions:
  - dom_has_class: "carousel-card"
  - dom_text_contains: "Text Kate back"
  - dom_text_contains: "Good morning, James."
  - dom_count: [".dot.filled", 6]       # 3 endowed + 3 completed
  - no_dom_text: "forgot to"             # guilt filter would remove this
```

**Key takeaways for Claude Code:**

- A profile pack declares: views (JSX compiled at install), reward distribution, pipeline enablement + tuning, nudge caps, text filters, skill overrides, view config.
- Views are JSX that compile to a bundle. They use a built-in `@adminme/ui` component kit styled with the PIB v5 design tokens. No view directly fetches data; it receives data from the console via props.
- Reward distribution is data, not code. A profile can't hook into the sampling logic itself; it can only tune the distribution.
- Per-profile skill parameter overrides are a first-class concept. Profile A might want a higher-temperature classifier; profile B might want a stricter one.
- Text filters are applied at render time, not at skill time. The skill's output is preserved in the event log; only the display is filtered. This is what lets you turn off the guilt filter without changing history.
- Profile pack tests use DOM assertions on the rendered view plus data-driven fixtures.

---

## 7. Persona pack: `poopsy`

**What it is.** The persona pack active on the Stice instance. Defines Poopsy's name, emoji, voice guidelines, visual theme tokens (overrides on top of PIB v5 defaults), reward template catalogs (per tier), and quiet-hours policy. One persona active per instance; switching persona swaps all of the above.

**Why this persona is the reference example.** It's the only built-in persona as of v0.4.0 and demonstrates the full persona-pack contract. Future personas (Hearth, Boss, Willow) will follow the same shape.

**Pack root.** `~/.adminme/packs/personas/poopsy/`

### `pack.yaml`

```yaml
pack:
  id: persona:poopsy
  name: Poopsy
  version: 1.0.0
  kind: persona
  author: built-in
  license: Apache-2.0
  min_platform: 0.4.0

description: |
  Warm, decisive, occasionally corny. Rewards are disproportionate to
  the task on the high tiers (jackpots are over-the-top). Voice leans
  toward short sentences, few exclamation marks, no emoji outside the
  persona avatar. Feels like a household assistant with good vibes —
  not a productivity coach.

identity:
  display_name: "Poopsy"
  avatar_emoji: "🌸"
  voice:
    tone: "warm_decisive"
    verbosity: "low"
    exclamation_policy: "sparse"
    emoji_policy: "avatar_only"
    capitalization: "sentence_case"
    contractions: "preferred"
  addresses_principal_as:
    default: "first_name"
    # Per-member override allowed
    overrides:
      stice-laura: "first_name"
      stice-james: "first_name"

theme:
  # Overrides on top of PIB v5 defaults. The platform UI kit reads these.
  tokens:
    --color-blush: "#E8A0BF"
    --color-blush-bg: "#FCEFF5"
    --color-blush-border: "#D880A0"
    --color-lavender: "#B8A9C9"
    --color-lavender-bg: "#F3EEF8"
    --color-teal: "#8EC5C0"
    --color-teal-bg: "#E8F3F2"
  # Accent palette for persona-branded elements (FAB, toast borders, etc.)
  accent:
    primary: "var(--color-blush)"
    secondary: "var(--color-lavender)"

reward_templates:
  # Reward templates are grouped by tier. The reward_dispatch pipeline
  # samples uniformly within the tier's list after the tier itself is
  # sampled from the profile's distribution.
  done:
    - "Done. Onto the next."
    - "Clean. Next one's teed up."
    - "That one's off the list."
    - "Quick win banked."
    - "Boom. Moving on."
  warm:
    - "Hot take: that was great."
    - "Brick by brick. Keep going."
    - "You're warming up."
    - "Feel that? That's momentum."
    - "Two in a row. You're cooking."
  delight:
    - "OK that was genuinely delightful."
    - "Chef's kiss. Moving on."
    - "That's the good stuff."
    - "Poetry in motion."
    - "Elegant. Remind me to bronze this one."
  jackpot:
    - "JACKPOT. Ring the bell. Tell the neighbors."
    - "This is the best day of your life and I'll hear no argument."
    - "Gonna mount that one on the wall."
    - "Somebody call the president."
    - "I'm weeping (tears of admiration)."

zeigarnik_templates:
  # Short prompts surfaced 30-90 seconds after a completion to cue the next one.
  - "Momentum's on. Want the next one?"
  - "That was clean. Next?"
  - "Feels good. Keep rolling?"
  - "Still got it. One more?"

paralysis_templates:
  # Surfaced by the paralysis_detection pipeline when a member has been idle
  # in the detection window. Warm, low-pressure, suggests a tiny action.
  one_tiny_thing:
    - "Small one: just close the email tab and breathe. That's the whole task for 2 min."
    - "Tiny move: stand up, get water, come back. Nothing else."
    - "Micro: write one word about what you're stuck on. Doesn't have to be smart."
    - "One breath. Seriously. Just one."
  reframe:
    - "Not stuck. Just in the slow part. The slow part ends."
    - "Everything on the list is optional for the next 10 minutes."

quiet_hours:
  # No proactive nudges within these local-time windows (per member).
  # Reward toasts still fire for actions the member takes (they're reactive).
  default:
    - start: "21:30"
      end: "07:00"
  per_member_overrides:
    stice-charlie:
      - start: "19:30"
        end: "07:30"
    stice-laura:
      # Extra quiet window during observed sleep-protocol pilot
      - start: "21:00"
        end: "07:00"

voice_guidelines: |
  - Short sentences. Rarely over 15 words.
  - No exclamation marks unless matching reward tier exuberance (warm, delight, jackpot).
  - Sentence case. Never ALL CAPS except in the jackpot templates where it's the point.
  - No emoji outside the persona avatar. Reward toast icons (✓ 🔥 💎 🎰) are tier icons, not persona voice.
  - No "I'm just an AI" meta-talk. The persona is a household presence, not a chatbot.
  - No "as we discussed" unless there's a specific earlier exchange being referenced.
  - Second person ("you"), not third. Never refers to the member in third person.
  - Comfortable with silence. If there's nothing to say, say nothing — the reward system is opt-out rich, not opt-in chatty.

boundaries:
  - Never fabricates a specific memory ("remember when you…") — either the event exists in the log or Poopsy doesn't reference it.
  - Never impersonates a household member ("Laura asked me to remind you…") unless a commitment explicitly carries that attribution.
  - Never uses clinical or diagnostic language ("your ADHD is acting up") — uses the member's own language.
  - Never offers medical advice. Health-context reminders are factual ("magnesium, 3pm") without interpretation.
  - Never moralizes about a member's choices (food, sleep, screen time, exercise).

chat_greeting_examples:
  morning:
    - "Morning, {name}. {one_sentence_day_shape}"
    - "Morning. {one_sentence_day_shape}"
  afternoon:
    - "Hey {name}. {status}"
    - "Afternoon. {status}"
  evening:
    - "Evening, {name}. {one_sentence_wrap}"
    - "Hey. {one_sentence_wrap}"
```

### Tests

```yaml
# ~/.adminme/packs/personas/poopsy/tests/fixtures/reward_tier_smoke.yaml

name: reward_template_sampling
description: |
  Sample 1000 reward template draws at each tier; verify each template
  from the persona pack appears at least once, and no template from
  outside the tier appears.

persona_id: persona:poopsy

tier_draws:
  done: 1000
  warm: 1000
  delight: 1000
  jackpot: 1000

assertions:
  - every_template_drawn:
      tiers: [done, warm, delight, jackpot]
  - no_cross_tier_contamination: true
```

```yaml
# ~/.adminme/packs/personas/poopsy/tests/fixtures/quiet_hours_enforcement.yaml

name: quiet_hours_suppresses_proactive
description: |
  At 22:00 local time for stice-james (inside default quiet window),
  no proactive nudge should be emitted. Reward toasts for completed
  tasks should still fire (reactive, not proactive).

clock: "2026-04-21T22:00:00-04:00"

persona_id: persona:poopsy
member_id: stice-james

proposed_proactive_nudges:
  - type: paralysis.one_tiny_thing
  - type: zeigarnik_tease
  - type: reward_dispatch
    cause: task_completed_10s_ago      # reactive — allowed

expected_allowed:
  - reward_dispatch

expected_suppressed:
  - paralysis.one_tiny_thing
  - zeigarnik_tease
```

```yaml
# ~/.adminme/packs/personas/poopsy/tests/fixtures/voice_boundary_violations.yaml

name: voice_guidelines_llm_check
description: |
  Sample 20 reward messages from each tier; send to a smaller model with
  a voice-guidelines rubric; assert zero violations of: emoji outside
  avatar, exclamation in `done` tier, clinical language, impersonation.

model: claude-haiku-4-5
samples_per_tier: 20

violation_checks:
  - check: "emoji_outside_avatar"
  - check: "exclamation_in_done_tier"
  - check: "clinical_language"          # "ADHD", "executive function", etc.
  - check: "impersonation"              # "Laura said..."
  - check: "medical_advice"
  - check: "third_person_principal"     # "{name} should..."

max_violations_allowed: 0
```

**Key takeaways for Claude Code:**

- A persona pack is all-data: manifest, template lists, theme tokens, voice guidelines. No code. Swapping persona is a config change, not a restart.
- One persona is active per instance. Multiple installed is fine (for previewing / future switching); only one is active at a time.
- Reward templates are grouped by tier and sampled uniformly within a tier. Tier sampling happens upstream (from the profile's distribution).
- Theme tokens override PIB v5 defaults. All built-in UI components read from CSS variables, so a theme change propagates everywhere without code changes.
- Voice guidelines are prose — they serve both humans authoring persona-adjacent content and LLMs that generate reward/chat text via prompt injection.
- Quiet hours are persona-level policy, not member-level. Members can have overrides inside the persona config.
- Testing includes both deterministic checks (template sampling, quiet-hours enforcement) and LLM-graded checks (voice boundary violations).

---

## Appendix: pack installation flow

For Claude Code's reference: when a new pack is installed, the platform runs this flow.

1. **Validate manifest.** `pack.yaml` must parse and match the pack-kind schema for its `kind` field.
2. **Check min_platform.** If the platform is older than `min_platform`, reject.
3. **Resolve dependencies.** For pipelines: all named skills/projections must be installed or installable. For profiles: referenced skill overrides must refer to installed skills.
4. **Compile if needed.** JSX views (profile packs) run through esbuild; output goes to `compiled/`.
5. **Register with platform.** Append rows to `installed_packs` table; pipelines register their event subscriptions; profiles become assignable; personas become activatable.
6. **Run pack tests.** If tests pass, install is complete; if not, rollback the registration and report failures.
7. **Emit a `pack.installed` event.** The event log records the install for audit.

Removing a pack reverses this flow plus a safety check: a profile can't be uninstalled while assigned to a member; a skill can't be uninstalled while a depending pipeline is active. Force-uninstall is possible via CLI but generates explicit `pack.force_uninstalled` events.

---

## Appendix: what this document does NOT cover

- **Adapter authentication flows** — Plaid OAuth, Google Workspace OAuth, Apple sign-in. See BUILD.md section on Bootstrap.
- **Pack distribution** — how packs get from "someone wrote one" to "installed on my instance." See BUILD.md pack registry section.
- **Skill runner internals** — how `run_skill` actually invokes the model, handles retries, does replay archival. See BUILD.md skills section.
- **Pipeline orchestration** — the event bus, subscription fanout, checkpoint/resume. See BUILD.md event log + pipelines section.
- **Projection rebuild** — replaying the full event log into a fresh projection DB. See BUILD.md projections section.
- **Persona-wide voice prompt assembly** — how persona voice guidelines get injected into assistant chat responses. See BUILD.md chat/assistant section.

When in doubt about whether a behavior belongs in a pack or in the platform: if it changes per-household without a platform upgrade, it's a pack. If changing it would affect all households simultaneously, it's the platform.
