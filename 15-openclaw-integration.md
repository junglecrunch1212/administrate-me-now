# Prompt 15: OpenClaw integration code (Phase A)

**Phase:** BUILD.md "OPENCLAW IS THE ASSISTANT SUBSTRATE" + cross-cutting integration work. **Phase A only** — builds the code that will integrate with OpenClaw at runtime. The actual live integration happens during Phase B bootstrap on the Mac Mini.
**Depends on:** Prompt 14d.
**Estimated duration:** 3-4 hours.
**Stop condition:** Persona compiler produces valid SOUL.md; audit tool validates a mock OpenClaw state; integration code is in place and importable; the `bootstrap/pack_install_order.yaml`, `bootstrap/plugin_install_order.yaml`, and `bootstrap/standing_order_registration.yaml` files exist and list everything the bootstrap wizard needs to install. End-to-end test uses mocked OpenClaw and passes.

---

## Read first (required)

1. `ADMINISTRATEME_BUILD.md` **"OPENCLAW IS THE ASSISTANT SUBSTRATE"** section — the four seams.
2. `ADMINISTRATEME_REFERENCE_EXAMPLES.md` §7 (persona pack — `poopsy-v1`, includes SOUL.md compilation instructions).
3. `docs/openclaw-cheatsheet.md` questions 2-4 (slash commands, standing orders, plugins) + question 7 (approval-gates interaction with guardedWrite).
4. `docs/reference/openclaw/` — any files covering persona-activation API, SOUL.md expected shape, and the audit endpoints. Mirror only; no WebFetch.

## Operating context

By now, code for every OpenClaw integration point exists in the repo:
- Slash command handler code in 13a/13b.
- Standing order handler code in 10c.
- Plugin code in 12.
- Skill packs in 09a/09b/10b/10c.
- Registration queues (`bootstrap/pack_install_order.yaml`, etc.) populated incrementally by each prompt.

**Nothing has been live-registered with a real OpenClaw gateway — there isn't one in the Phase A sandbox.** All of that registration happens in Phase B, driven by the bootstrap wizard (prompt 16).

Your job in this prompt is to:
1. Build the **persona compiler** — code that turns a persona pack into a SOUL.md file.
2. Build the **OpenClaw audit tool** — runtime code that, once running on the Mac Mini, queries OpenClaw and flags any expected-but-missing registrations.
3. Write the **canonical persona pack** (`poopsy-v1`) that the audit expects to find.
4. Consolidate the registration-queue files so the bootstrap wizard has a complete manifest of what to install.
5. Write an **end-to-end mock test** that exercises the chat → OpenClaw → skill → projection → response loop using a mocked OpenClaw gateway.

## Out of scope

- No real OpenClaw gateway contact. The sandbox has none.
- No production persona-pack installation into a running OpenClaw. That's Phase B.
- Additional persona packs beyond `poopsy-v1`.
- Tenant-specific profile packs (installed during bootstrap).

## Deliverables

### Persona compiler

`platform/lib/persona_compiler.py` — reads a persona pack from `~/.adminme/packs/personas/<id>/` (at runtime — on the Mac Mini that'll be the real path; in tests, point at a fixture directory). Produces SOUL.md per REFERENCE_EXAMPLES.md §7 structure:
- Persona identity header.
- Voice guardrails inlined.
- Forbidden phrase list (don't-say).
- Reward templates by tier (runtime-selected).
- Fallback phrases for OpenClaw's own fallback system.

Output is written to `~/.adminme/openclaw/souls/<tenant_id>/<persona_id>.md`.

### `packs/personas/poopsy-v1/`

Full persona pack per REFERENCE_EXAMPLES.md §7. Include every file the compiler expects. Commit to the repo.

### OpenClaw audit

`platform/lib/openclaw_audit.py`:

```python
async def audit_openclaw(openclaw_client: OpenClawClient, expected: ExpectedRegistrations) -> AuditReport:
    """
    Runtime audit — used during bootstrap and on startup.
    Queries OpenClaw's APIs (plugin list, slash-command list, standing-order list, skill list).
    Compares against ExpectedRegistrations.
    Returns a report listing any drift.
    """
```

`ExpectedRegistrations` is loaded from the registration-queue YAML files below. At startup, AdministrateMe runs the audit; drift emits `openclaw.registration.missing` or `openclaw.registration.unexpected` events; the CLI `adminme openclaw audit` (prompt 17) runs it on demand.

### Registration queue consolidation

Three YAML files at `bootstrap/` (each incrementally populated by earlier prompts; confirm and consolidate):

- `bootstrap/pack_install_order.yaml` — every skill pack + pipeline pack + profile pack + persona pack the bootstrap wizard must install into OpenClaw. Ordered by dependency (skills before pipelines that use them, etc.).
- `bootstrap/plugin_install_order.yaml` — the OpenClaw plugins: `memory_bridge`, `channel_bridge_bluebubbles`, any future bridges.
- `bootstrap/standing_order_registration.yaml` — the six proactive pipelines from prompt 10c, each with handler URL and schedule spec.

Plus `bootstrap/slash_command_registration.yaml` — the 22 slash commands from prompts 13a/13b, each with its handler URL.

If these files are missing or incomplete from earlier prompts, fill them in now. This prompt is also an **audit pass** over the accumulated queue state.

### End-to-end mock test

`tests/integration/test_openclaw_end_to_end.py`:

Uses a full mock OpenClaw HTTP server (use `httpx.MockTransport` or similar). Scenario:
1. Bootstrap mock state from the registration-queue YAMLs.
2. Start platform services in the Phase A sandbox.
3. Simulate a chat message arriving at the console's SSE endpoint.
4. Mock OpenClaw replies with the expected persona-activated, skill-invoked, response-streamed sequence.
5. Assert: correlation ID threaded through; projection updated from the event recorded; response streamed back through console.

### Compiler tests

`tests/unit/test_persona_compiler.py` — persona pack (the `poopsy-v1` fixture) compiles to expected SOUL.md. Byte-stable; re-compilation produces identical output.

### Audit tests

`tests/unit/test_openclaw_audit.py` — mock OpenClaw state with deliberate missing/extra items; audit detects each.

## Verification

```bash
# Phase A tests only — no live OpenClaw
poetry run pytest tests/unit/test_persona_compiler.py tests/unit/test_openclaw_audit.py -v
poetry run pytest tests/integration/test_openclaw_end_to_end.py -v

# Verify the registration queues are complete
for f in bootstrap/pack_install_order.yaml \
         bootstrap/plugin_install_order.yaml \
         bootstrap/slash_command_registration.yaml \
         bootstrap/standing_order_registration.yaml; do
  test -s "$f" && echo "✓ $f" || echo "✗ MISSING: $f"
done

# Compile poopsy-v1 to SOUL.md against a fixture instance dir
poetry run python -c "
from pathlib import Path
from platform.lib.persona_compiler import compile_persona
compile_persona(Path('packs/personas/poopsy-v1'), Path('/tmp/test-soul.md'), tenant_id='test-tenant')
print(Path('/tmp/test-soul.md').read_text()[:500])
"

git add platform/lib/persona_compiler.py platform/lib/openclaw_audit.py \
        packs/personas/poopsy-v1/ bootstrap/*.yaml tests/
git commit -m "phase 15: OpenClaw integration code (Phase A)"
git push
```

## Stop

**Explicit stop message:**

> Phase A OpenClaw integration complete. Persona compiler works on fixture data; registration queues consolidated; audit tool validates mock state. Ready for prompt 16 (bootstrap wizard — the Phase A → Phase B bridge).
>
> Reminder: nothing has been registered with a live OpenClaw yet. That happens when the operator runs `./bootstrap/install.sh` on the Mac Mini during Phase B.
