/** Actual ce67 database/session writer and reopen proof, hosted synthetic state only. */
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import path from "node:path";
import { readFile } from "node:fs/promises";

assert.equal(process.env.GITHUB_ACTIONS, "true");
assert.equal(process.env.RUNNER_ENVIRONMENT, "github-hosted");
const [source, mode] = process.argv.slice(2);
assert(["seed", "reopen"].includes(mode));
assert.equal(path.basename(source), "ce67ffb70e83e5ebb56ea02e814e577153f84f61");
assert.equal(JSON.parse(await readFile(path.join(source, "package.json"))).version, "2026.7.2");
const state = process.env.OPENCLAW_STATE_DIR;
assert(state && path.isAbsolute(state));
assert.equal(process.env.OPENCLAW_CONFIG_PATH, path.join(state, "openclaw.json"));
const requirePinned = createRequire(path.join(source, "package.json"));
const { createJiti } = requirePinned("jiti");
const jiti = createJiti(path.join(source, "package.json"), { tsconfigPaths: path.join(source, "tsconfig.json") });
const load = (relative) => jiti.import(path.join(source, relative));
const shared = await load("src/state/openclaw-state-db.ts");
const agent = await load("src/state/openclaw-agent-db.ts");
const store = await load("src/config/sessions/session-accessor.sqlite-entry-store.ts");
assert.equal(shared.OPENCLAW_STATE_SCHEMA_VERSION, 6);
assert.equal(agent.OPENCLAW_AGENT_SCHEMA_VERSION, 16);
const stateDb = shared.openOpenClawStateDatabase({ env: process.env });
assert.equal(stateDb.db.prepare("PRAGMA user_version").get().user_version, 6);
try {
  for (const [index, id] of ["adminme-fixture-a", "adminme-fixture-b", "am-service-fixture"].entries()) {
    const opened = agent.openOpenClawAgentDatabase({ agentId: id, env: process.env });
    const key = `agent:${id}:main`;
    const entry = { sessionId: `00000000-0000-4000-8000-00000000000${index}`, updatedAt: 1793000000000 + index, displayName: `Retained fixture ${index}` };
    if (mode === "seed") {
      agent.runOpenClawAgentWriteTransaction((database) => store.writeSessionEntry(database, key, entry), { agentId: id, env: process.env });
    }
    const retained = store.readExactSessionEntryRow(opened, key);
    assert(retained);
    assert.equal(retained.entry.sessionId, entry.sessionId);
    assert.equal(retained.entry.displayName, entry.displayName);
    assert.equal(opened.db.prepare("PRAGMA user_version").get().user_version, 16);
  }
} finally {
  agent.closeOpenClawAgentDatabases();
  shared.closeOpenClawStateDatabase();
}
console.log(`Actual ce67 ${mode}: shared6 / three agent16 stores and retained sessions PASS`);
