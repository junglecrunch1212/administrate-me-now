# Rendered console-reference evidence

Status: **PASS**

Run from the repository root:

```powershell
npm.cmd ci --prefix tools/console-reference-harness --ignore-scripts
npm.cmd test --prefix tools/console-reference-harness
```

The harness serves the repository root only on `127.0.0.1:4173`, launches installed Microsoft Edge headlessly through pinned `playwright-core@1.62.0`, freezes time/randomness, disables motion and remote fonts, exercises all twelve behaviors declared by the reference HTML, and exits nonzero on any failed interaction, DOM assertion, JavaScript console error, uncaught page error, or failed request.

Reference SHA-256: `98ac268f39c0d367f5c904ab2af5e0e35389eae8ff6bffd4cede7ef8bfdd0a3b`

Browser: Microsoft Edge `151.0.4129.59`

Artifacts:

- `desktop.png` — 1440×1000, SHA-256 `6f71236a1d7a8469ff1fe423f95bb41b9f2b211c09d270ef2400b3a3978bf3cf`
- `narrow.png` — 390×844, SHA-256 `97d1fe1129cfe2f6bc94d0797037d6fb6e7464ac0218d8dd11b49f25fc597701`
- `interaction-results.md` / `interaction-results.json` — twelve behavior results
- `dom-assertions.json` — individual selector/state assertions
- `javascript-console.json` — console errors, uncaught page errors, and request failures
- `run-manifest.json` — source/runtime/determinism metadata and artifact hashes
