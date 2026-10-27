/** Hosted synthetic Gateway diagnostic only; never substitutes DNS answers. */
import assert from "node:assert/strict";
import dns from "node:dns/promises";
import { syncBuiltinESMExports } from "node:module";

assert.equal(process.env.GITHUB_ACTIONS, "true");
assert.equal(process.env.RUNNER_ENVIRONMENT, "github-hosted");
assert.equal(process.env.ADMINME_SYNTHETIC_BROWSER_DNS_TRACE, "1");
// The original probe's Gateway environment excludes these three variables.
// Restore that exact environment before OpenClaw's CI-dependent code loads.
delete process.env.GITHUB_ACTIONS;
delete process.env.RUNNER_ENVIRONMENT;
delete process.env.ADMINME_SYNTHETIC_BROWSER_DNS_TRACE;

const lookup = dns.lookup;
const hosts = new Map([["example.com", "fixture_page"], ["mail.google.com", "gmail_self"]]);
let sequence = 0;
function record(entry) {
  // No address, caller options, hostname outside the two synthetic destinations,
  // error text, credential or page content is retained. Observation cannot throw.
  try { process.stderr.write(`SYNTHETIC_BROWSER_DNS ${JSON.stringify({ ...entry, at_ms: Date.now() })}\n`); }
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
