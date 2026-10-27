/** Hosted-only setup proof: real --import and Worker re-entry, no network lookup. */
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import dns, { lookup as namedLookup } from "node:dns/promises";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { syncBuiltinESMExports } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { isMainThread, parentPort, threadId, Worker, workerData } from "node:worker_threads";

const ownUrl = new URL(import.meta.url);
const stateKey = Symbol.for("administrateme.fixture.dns-setup");
const marker = "ADMINME_SYNTHETIC_BROWSER_DNS_TRACE";

if (ownUrl.searchParams.has("stub")) {
  // Loaded BEFORE the real observer in each isolate; no native DNS can run.
  assert.equal(typeof process.env[marker], "string");
  const state = { calls: [], next: undefined };
  globalThis[stateKey] = state;
  dns.lookup = function (...args) {
    assert(state.next, "Unexpected DNS lookup in setup proof");
    const next = state.next; state.next = undefined;
    const call = { receiver: this, args, error: next.error };
    state.calls.push(call);
    if (next.kind === "throw") throw next.error;
    call.promise = next.kind === "reject" ? Promise.reject(next.error) : Promise.resolve(next.value);
    return call.promise;
  };
  syncBuiltinESMExports();
} else if (process.argv[2] === "--child" || workerData?.dnsSetup === true) {
  const expected = isMainThread ? process.argv[3] : workerData.fixture;
  assert.equal(process.env[marker], expected, "Preload must not consume its admission marker");
  assert.equal(process.env.OPENCLAW_STATE_DIR, path.join(expected, "state"));
  assert.equal(process.env.OPENCLAW_CONFIG_PATH, path.join(expected, "openclaw.json"));
  assert.equal(process.env.GITHUB_ACTIONS, undefined);
  assert.equal(process.env.RUNNER_ENVIRONMENT, undefined);
  if (!isMainThread) assert.deepEqual(process.execArgv, workerData.execArgv, "Worker must inherit the real preloads");
  const state = globalThis[stateKey];
  assert(state, "Fake DNS must precede the real observer");
  assert.equal(namedLookup, dns.lookup, "Named builtin export must retain the observer");
  for (const host of ["EXAMPLE.COM", "mail.google.com", "unobserved.example.invalid"]) {
    for (const kind of ["resolve", "reject", "throw"]) {
      const receiver = Object.freeze({ fixture: true });
      const options = Object.freeze({ all: true, family: 4 });
      const extra = Object.freeze({ unchanged: true });
      const value = Object.freeze([{ address: "192.0.2.42", family: 4 }]);
      const error = new Error("Synthetic DNS result");
      state.next = { kind, value, error };
      const before = state.calls.length;
      if (kind === "throw") {
        assert.throws(() => Reflect.apply(namedLookup, receiver, [host, options, extra]), (caught) => caught === error);
      } else {
        const returned = Reflect.apply(namedLookup, receiver, [host, options, extra]);
        assert.equal(returned, state.calls.at(-1).promise, "Observer must return the original Promise");
        if (kind === "resolve") assert.equal(await returned, value, "Original result identity must survive");
        else await assert.rejects(returned, (caught) => caught === error);
      }
      assert.equal(state.calls.length, before + 1, "Observer must call the original exactly once");
      const call = state.calls.at(-1);
      assert.equal(call.receiver, receiver);
      assert.equal(call.args.length, 3);
      assert.equal(call.args[0], host);
      assert.equal(call.args[1], options);
      assert.equal(call.args[2], extra);
    }
  }
  const result = { thread_id: threadId, is_main_thread: isMainThread, lookup_cases: state.calls.length };
  if (isMainThread) {
    // No env or execArgv overrides: this is the native inheritance that failed.
    const worker = new Worker(ownUrl, { workerData: { dnsSetup: true, fixture: expected, execArgv: process.execArgv } });
    let workerResult;
    await new Promise((resolve, reject) => {
      worker.once("message", (value) => { workerResult = value; });
      worker.once("error", reject);
      worker.once("exit", (code) => code === 0 ? resolve() : reject(new Error(`Setup worker exit ${code}`)));
    });
    assert(workerResult && !workerResult.is_main_thread);
    assert.equal(workerResult.lookup_cases, 9);
    console.log(JSON.stringify({ main: result, worker: workerResult }));
  } else parentPort.postMessage(result);
} else {
  assert.equal(process.env.GITHUB_ACTIONS, "true", "Setup proof is hosted-only");
  assert.equal(process.env.RUNNER_ENVIRONMENT, "github-hosted", "Setup proof is hosted-only");
  assert(path.isAbsolute(process.env.RUNNER_TEMP ?? ""));
  const repository = fileURLToPath(new URL("../../", ownUrl));
  const preload = new URL("openclaw_browser_dns_trace.mjs", ownUrl);
  const stub = new URL(ownUrl); stub.searchParams.set("stub", "1");
  const versions = JSON.parse(readFileSync(path.join(repository, "bootstrap/versions.json"), "utf8"));
  assert.equal(process.version, `v${versions.node.preferred}`);
  const directory = mkdtempSync(path.join(tmpdir(), "adminme-service-gateway-"));
  const env = Object.fromEntries(["PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "USER", "LOGNAME"].filter((key) => process.env[key] !== undefined).map((key) => [key, process.env[key]]));
  Object.assign(env, { [marker]: directory, OPENCLAW_STATE_DIR: path.join(directory, "state"), OPENCLAW_CONFIG_PATH: path.join(directory, "openclaw.json") });
  const proof = { contract: "synthetic-browser-preload-setup-v1", outcome: "failed",
    candidate_commit: execFileSync("git", ["rev-parse", "HEAD"], { cwd: repository, encoding: "utf8" }).trim(),
    preload_sha256: createHash("sha256").update(readFileSync(preload)).digest("hex"),
    node_version: process.version, platform: process.platform, arch: process.arch, checks: [] };
  function child(overrides = {}, cwd = directory, useStub = true) {
    const childEnv = { ...env, ...overrides };
    for (const key of Object.keys(childEnv)) if (childEnv[key] === undefined) delete childEnv[key];
    return spawnSync(process.execPath, [...(useStub ? ["--import", stub.href] : []), "--import", preload.href, fileURLToPath(ownUrl), "--child", directory], {
      cwd, env: childEnv, encoding: "utf8", timeout: 15_000, maxBuffer: 256 * 1024,
    });
  }
  try {
    const valid = child();
    assert.ifError(valid.error);
    assert.equal(valid.status, 0, valid.stderr);
    const result = JSON.parse(valid.stdout);
    assert.equal(result.main.lookup_cases, 9);
    const traces = valid.stderr.split("\n").filter((line) => line.startsWith("SYNTHETIC_BROWSER_DNS ")).map((line) => JSON.parse(line.slice("SYNTHETIC_BROWSER_DNS ".length)));
    assert.equal(new Set(traces.map((entry) => entry.pid)).size, 1);
    assert.equal(new Set(traces.map((entry) => entry.thread_id)).size, 2);
    for (const context of [result.main, result.worker]) {
      const entries = traces.filter((entry) => entry.thread_id === context.thread_id);
      assert(entries.every((entry) => entry.is_main_thread === context.is_main_thread));
      for (const [phase, count] of Object.entries({ installed: 1, started: 6, settled: 2, rejected: 2, threw: 2 })) {
        assert.equal(entries.filter((entry) => entry.phase === phase).length, count);
      }
      assert.equal(entries.length, 13, "Unselected host must not produce trace records");
      assert(entries.every((entry) => !entry.host || ["fixture_page", "gmail_self"].includes(entry.host)));
    }
    proof.checks.push("child-and-native-worker-reentry", "original-promise-result-rejection-throw-receiver-arguments", "marker-retained-no-ci-flags", "thread-separated-exact-host-trace");
    for (const [name, overrides, cwd] of [
      ["wrong-state", { OPENCLAW_STATE_DIR: path.join(directory, "other-state") }],
      ["wrong-config", { OPENCLAW_CONFIG_PATH: path.join(directory, "other.json") }],
      ["missing-marker", { [marker]: undefined }],
      ["relative-marker", { [marker]: path.basename(directory) }],
      ["wrong-prefix", { [marker]: path.join(directory, "other") }],
      ["wrong-cwd", {}, path.dirname(directory)],
    ]) {
      // The missing-marker case fails in the actual guard before any DNS call;
      // omit the stub's own fixture assertion so it cannot mask that refusal.
      const refused = child(overrides, cwd, name !== "missing-marker");
      assert.ifError(refused.error);
      assert.notEqual(refused.status, 0, name);
      assert.match(refused.stderr, /AssertionError/);
      assert.match(refused.stderr, /openclaw_browser_dns_trace\.mjs:/);
      assert.equal(refused.stdout, "", "Rejected setup cannot report success");
      proof.checks.push(`refused-${name}`);
    }
    proof.outcome = "passed";
    console.log("Verified real preload/Worker re-entry, exact fixture admission and unchanged synthetic DNS behavior; no external lookup.");
  } finally {
    const evidence = path.join(process.env.RUNNER_TEMP, "adminme-browser-gateway-evidence");
    mkdirSync(evidence, { recursive: true });
    writeFileSync(path.join(evidence, "preload-setup.json"), JSON.stringify(proof, null, 2) + "\n", { mode: 0o600 });
    rmSync(directory, { recursive: true, force: true });
  }
}
