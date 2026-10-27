/** Hosted synthetic Gateway diagnostic only; never substitutes DNS answers. */
import assert from "node:assert/strict";
import dns from "node:dns/promises";
import { realpathSync } from "node:fs";
import { syncBuiltinESMExports } from "node:module";
import path from "node:path";
import { isMainThread, threadId } from "node:worker_threads";

// The hosted parent asserts runner identity before spawning the Gateway. Native
// workers inherit --import, so retain only this inert, exact fixture coordinate;
// never inject CI flags that could change OpenClaw's runtime behavior.
const fixture = process.env.ADMINME_SYNTHETIC_BROWSER_DNS_TRACE;
assert.equal(typeof fixture, "string");
assert(path.isAbsolute(fixture));
assert(path.basename(fixture).startsWith("adminme-service-gateway-"));
assert.equal(process.env.OPENCLAW_STATE_DIR, path.join(fixture, "state"));
assert.equal(process.env.OPENCLAW_CONFIG_PATH, path.join(fixture, "openclaw.json"));
assert.equal(realpathSync(process.cwd()), realpathSync(fixture));

const lookup = dns.lookup;
const hosts = new Map([["example.com", "fixture_page"], ["mail.google.com", "gmail_self"]]);
let sequence = 0;
function record(entry) {
  // No address, caller options, hostname outside the two synthetic destinations,
  // error text, credential or page content is retained. Observation cannot throw.
  try { process.stderr.write(`SYNTHETIC_BROWSER_DNS ${JSON.stringify({ ...entry, pid: process.pid, thread_id: threadId, is_main_thread: isMainThread, at_ms: Date.now() })}\n`); }
  catch { /* A missing trace is missing evidence, not a changed DNS outcome. */ }
}
dns.lookup = function (...args) {
  const host = typeof args[0] === "string" ? hosts.get(args[0].toLowerCase()) : undefined;
  if (!host) return Reflect.apply(lookup, this, args);
  const lookup_id = ++sequence, started = Date.now();
  record({ phase: "started", lookup_id, host });
  let operation;
  try { operation = Reflect.apply(lookup, this, args); }
  catch (error) { record({ phase: "threw", lookup_id, host, elapsed_ms: Date.now() - started }); throw error; }
  // Observe settlement without replacing the original Promise, result, error,
  // arguments or receiver. No additional lookup, deadline or retry is added.
  void operation.then(
    () => record({ phase: "settled", lookup_id, host, elapsed_ms: Date.now() - started }),
    () => record({ phase: "rejected", lookup_id, host, elapsed_ms: Date.now() - started }),
  );
  return operation;
};
syncBuiltinESMExports();
record({ phase: "installed", api: "node:dns/promises.lookup" });
