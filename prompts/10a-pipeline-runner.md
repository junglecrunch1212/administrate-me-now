# Prompt 10a: Pipeline runner

**Phase:** BUILD.md L4 (pipelines).
**Depends on:** Prompt 09b. Skill runner proven.
**Estimated duration:** 2-3 hours.
**Stop condition:** Pipeline runner can register pipeline packs, subscribe them to the bus, dispatch events, and survive process restart. A trivial pipeline pack works end-to-end.

---

## Read first

1. `ADMINISTRATEME_BUILD.md` **"L4: PIPELINES"** — the reactive vs. standing-order distinction is load-bearing.
2. `ADMINISTRATEME_REFERENCE_EXAMPLES.md` §2 (commitment_extraction pipeline — structure, not yet implementing it).
3. `docs/openclaw-cheatsheet.md` question 3 (standing order registration).
4. `docs/reference/openclaw/` — any file covering the standing-order registration API. Do NOT use WebFetch to look this up live; read the mirrored copy.

## Operating context

Pipelines live in `packs/pipelines/<name>/` (built-in) or `~/.adminme/packs/pipelines/<name>/` (installed at runtime). Each pack has `pack.yaml`, `pipeline.py` (with a class implementing the Pipeline protocol), `tests/`, and optionally `fixtures/`.

Two runners:

1. **Reactive runner** (in-process) — subscribes to the event bus per the pack's `triggers.events` list. When a matching event lands, calls the handler.
2. **Standing-order registration** — for pipeline packs with `triggers.schedule` (cron-like) or `triggers.proactive` (principal-facing), registers them with OpenClaw as standing orders. OpenClaw triggers them; the handler is the pipeline's entrypoint.

## Objective

Build `adminme/pipelines/runner.py`. Build `adminme/pipelines/base.py` with the Pipeline protocol. Install a trivial test pipeline to prove the loop.

## Out of scope

- Do NOT implement the actual pipelines (commitment_extraction, thank_you, morning_digest, etc.). They come in 10b and 10c.

## Deliverables

### Pipeline protocol

```python
class Pipeline(Protocol):
    id: str
    version: str
    triggers: Triggers  # {events: [...], schedule: cron|None, proactive: bool}

    async def handle(self, event: dict, ctx: PipelineContext) -> None: ...
```

### `adminme/pipelines/runner.py`

```python
class PipelineRunner:
    def __init__(self, bus: EventBus, event_log: EventLog,
                 openclaw_client: OpenClawClient, session_factory: SessionFactory): ...

    async def discover(self, packs_root: Path) -> None:
        """Walk packs/pipelines/ and ~/.adminme/packs/pipelines/; load each pack.yaml."""

    async def start_reactive(self) -> None:
        """Subscribe each reactive pipeline to the bus."""

    async def register_proactive_with_openclaw(self) -> None:
        """For each pipeline with schedule or proactive=true, register as OpenClaw standing order."""

    async def status(self) -> dict: ...
```

### Trivial test pipeline

`packs/pipelines/echo_logger/`:
- Subscribes to `messaging.received`.
- On event: emits `test.logged` event with a count.

Used only for testing the runner.

### Tests

- `tests/unit/test_pipeline_discovery.py` — discovery + pack.yaml parsing.
- `tests/unit/test_pipeline_dispatch.py` — event arrives, handler called with correct context.
- `tests/integration/test_pipeline_runner.py` — bus + runner + 2 pipelines + 100 events → correct fanout, checkpoints persist.

## Verification

```bash
poetry run pytest tests/unit/test_pipeline_*.py tests/integration/test_pipeline_runner.py -v
git commit -m "phase 10a: pipeline runner"
```

## Stop

> Pipeline runner in. Ready for 10b (reactive pipelines).

