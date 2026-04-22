# Diagnostic d02: OpenClaw invocation returns unexpected response shape

**Symptom.** `run_skill()` raises `OpenClawResponseMalformed`. The exception includes the actual response received. It doesn't match what the wrapper expects.

**When to use.** During prompt 09a, 09b, 10b, 10c, or any later prompt that invokes OpenClaw skills. Also: after a local OpenClaw upgrade.

---

## Read first

1. The exception body (includes the raw response received).
2. `docs/openclaw-cheatsheet.md` question 5 (SKILL.md shape) and any notes on skill invocation response.
3. OpenClaw's live API docs: `curl http://127.0.0.1:18789/docs` (if the gateway exposes them).

## Likely causes (ranked)

1. **OpenClaw upgraded** and changed its response shape. Check its release notes. This is the most common cause if wrapper tests passed before but now fail.
2. **Skill version mismatch.** The wrapper sends `skill_version: "1"`, but OpenClaw has both v1 and v2 registered and resolves to v2, which has a different output shape.
3. **Provider error wrapped as success.** OpenClaw occasionally returns 200 with an error body (`{error: ..., invocation_id: null}`) instead of 4xx/5xx. The wrapper treats 200 as success.
4. **Skill installed but corrupted.** `pack.yaml` says output schema X but the actual skill prompt was edited and produces Y. OpenClaw doesn't validate; wrapper catches it as malformed.

## Procedure

1. Paste the actual response into this diagnostic's context. Look at what shape it actually has.
2. Cross-reference with what `docs/openclaw-cheatsheet.md` says OpenClaw returns.
3. If they differ → it's a real OpenClaw API change. Update the wrapper (in `platform/lib/skill_runner/wrapper.py::_parse_openclaw_response`) to handle the new shape. Keep backward compatibility with a version detection on startup.
4. If the response contains an `error` field → the wrapper should classify that as a provider failure, not a shape error. Update to branch on `error` presence.
5. If the response looks correct but the output schema validation fails downstream → the skill pack's output schema is wrong; fix it in `packs/skills/<skill>/output.schema.json` and re-install.

## Fix pattern

Two improvements:

**A.** Add an OpenClaw version check at wrapper boot:

```python
async def verify_openclaw_compatibility() -> None:
    resp = await http.get("/version")
    version = resp.json()["version"]
    if version not in SUPPORTED_OPENCLAW_VERSIONS:
        raise OpenClawIncompatible(version, SUPPORTED_OPENCLAW_VERSIONS)
```

`SUPPORTED_OPENCLAW_VERSIONS` is a constant at the top of the wrapper module. On incompatibility, log loudly and halt skill invocations.

**B.** Structured response parsing that clearly distinguishes:
- Transport error (4xx, 5xx)
- Provider error (200 with error body)
- Schema mismatch (200 with success body, wrong shape)
- Real success.

Each case emits a distinct `skill.call.*` event variant for observability.

## Verify fix

Rerun the test fixture that produced the error. Plus:

```bash
poetry run pytest tests/unit/test_skill_wrapper.py::test_openclaw_malformed_response -v
poetry run pytest tests/unit/test_skill_wrapper.py::test_openclaw_200_with_error_body -v
poetry run pytest tests/unit/test_skill_wrapper.py::test_openclaw_version_check -v
```

## Escalate if

After all of the above, the shape still doesn't match. This suggests OpenClaw itself is misbehaving (bug in the gateway, corrupted installation, wrong port). Restart OpenClaw (`openclaw service restart`) and retry. If still broken, inspect OpenClaw's own logs: `openclaw logs --tail 200`.
