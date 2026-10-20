#!/usr/bin/env node
/**
 * Dedicated CoS iMessage transport boundary. OpenClaw owns the channel;
 * AdministrateMe admits one exact approved response immediately before send.
 * Every other mutation (including CLI sends, reactions and group operations)
 * is refused. No message content or credentials are written to diagnostics.
 */
import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { lstatSync, readFileSync, realpathSync } from "node:fs";
import { isAbsolute } from "node:path";
import { pathToFileURL } from "node:url";
import { createInterface } from "node:readline";

export const READ_METHODS = new Set([
  "initialize", "status", "chats.list", "chats.get", "messages.history",
  "messages.after", "watch.subscribe", "watch.unsubscribe",
]);
const MAX_LINE = 128 * 1024;
const SHA256 = /^[a-f0-9]{64}$/;

export function validateSend(params) {
  if (!params || typeof params !== "object" || Array.isArray(params)) return false;
  if (Object.keys(params).some((key) =>
    !["to", "text", "service", "region", "transport"].includes(key))) return false;
  return typeof params.to === "string"
    && /^\+[1-9][0-9]{7,14}$/.test(params.to)
    && typeof params.text === "string"
    && params.text.trim().length > 0 && params.text.length <= 8000
    && !/[\u0000-\u0008\u000b-\u001f\u007f]/.test(params.text)
    && params.service === "imessage"
    && (params.region === undefined || params.region === "US")
    && params.transport === "applescript";
}

export function loadGuardConfig(filename, env = process.env) {
  if (!filename || !isAbsolute(filename)) throw new Error("guard configuration missing");
  const stat = lstatSync(filename);
  if (!stat.isFile() || stat.isSymbolicLink() || (stat.mode & 0o077)
    || stat.uid !== process.getuid?.()) throw new Error("guard configuration permissions");
  const config = JSON.parse(readFileSync(filename, "utf8"));
  if (config.contract !== "cos-imessage-guard-v1"
    || typeof config.account_id !== "string" || !config.account_id
    || !isAbsolute(config.cli_path ?? "") || !isAbsolute(config.db_path ?? "")
    || !SHA256.test(config.cli_sha256 ?? "")
    || !/^[A-Z][A-Z0-9_]{1,100}$/.test(config.shared_secret_env ?? "")) {
    throw new Error("guard configuration invalid");
  }
  const url = new URL(config.api_base_url);
  if (url.protocol !== "http:" || url.hostname !== "127.0.0.1"
    || url.username || url.password || url.pathname !== "/" || url.search || url.hash) {
    throw new Error("guard requires a loopback API");
  }
  const binary = lstatSync(config.cli_path);
  if (!binary.isFile() || binary.isSymbolicLink() || (binary.mode & 0o022)
    || realpathSync(config.cli_path) !== config.cli_path
    || createHash("sha256").update(readFileSync(config.cli_path)).digest("hex")
      !== config.cli_sha256) throw new Error("guard transport pin invalid");
  const secret = env[config.shared_secret_env];
  if (typeof secret !== "string" || secret.length < 24) {
    throw new Error("guard transport authorization unavailable");
  }
  return { ...config, secret };
}

export async function runGuard(config, args, dependencies = {}) {
  const stdout = dependencies.stdout ?? process.stdout;
  const stdin = dependencies.stdin ?? process.stdin;
  const spawnChild = dependencies.spawn ?? spawn;
  const fetchApi = dependencies.fetch ?? fetch;
  if (JSON.stringify(args) === '["rpc","--help"]') {
    stdout.write("CoS guarded imsg rpc --json [--db PATH]\n");
    return;
  }
  if (JSON.stringify(args) === '["--version"]') {
    stdout.write("CoS imsg guard v1\n");
    return;
  }
  if (!(JSON.stringify(args) === '["rpc","--json"]'
    || (args.length === 4 && args[0] === "rpc" && args[1] === "--json"
      && args[2] === "--db" && args[3] === config.db_path))) {
    throw new Error("guard refuses CLI operation");
  }
  // Only the pinned provider process gets provider RPC. It never inherits the
  // backend secret; the relay is the sole holder of transport admission.
  // Gateway credentials may also exist under aliases such as
  // OPENCLAW_GATEWAY_TOKEN. Allow only basic OS context into the provider.
  const childEnv = { PATH: "/usr/bin:/bin" };
  for (const key of ["HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL"]) {
    if (process.env[key]) childEnv[key] = process.env[key];
  }
  const child = spawnChild(config.cli_path, ["rpc", "--json", "--db", config.db_path], {
    stdio: ["pipe", "pipe", "pipe"], env: childEnv,
  });
  const pending = new Map();
  let serial = 0;
  let stopping = false;
  const write = (value) => stdout.write(`${JSON.stringify(value)}\n`);
  const refusal = (id) => write({
    jsonrpc: "2.0", id, error: { code: -32010, message: "CoS transport refused" },
  });
  const post = async (suffix, body) => {
    const response = await fetchApi(new URL(`/api/openclaw/v1/cos-responses/${suffix}`,
      config.api_base_url), {
      method: "POST", redirect: "error",
      headers: { authorization: `Bearer ${config.secret}`, "content-type": "application/json" },
      body: JSON.stringify(body), signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) throw new Error("CoS transport admission unavailable");
    return await response.json();
  };
  const terminal = async (entry, result) => {
    if (!entry.claim) return true;
    const success = result && !result.error && result.result
      && (result.result.ok === true || result.result.status === "sent")
      && (typeof result.result.guid === "string" || typeof result.result.id === "number");
    await post("terminal", {
      claim_id: entry.claim,
      status: success ? "sent" : "unknown",
      receipt_digest: createHash("sha256").update(JSON.stringify(result ?? {})).digest("hex"),
    });
    return Boolean(success);
  };
  const stop = async () => {
    if (stopping) return;
    stopping = true;
    await Promise.allSettled([...pending.values()].map(async (entry) => {
      clearTimeout(entry.timer);
      await terminal(entry, null);
      if (entry.hasId) refusal(entry.id);
    }));
    pending.clear();
    child.stdin.destroy();
    child.kill("SIGTERM");
  };
  const incoming = createInterface({ input: stdin, crlfDelay: Infinity });
  const outgoing = createInterface({ input: child.stdout, crlfDelay: Infinity });
  // Serialize admission so duplicate request identifiers cannot overwrite a
  // claimed send. Child RPC identifiers are private monotonically increasing IDs.
  let admitted = Promise.resolve();
  incoming.on("line", (line) => {
    admitted = admitted.then(async () => {
      if (stopping) return;
      let request;
      try {
        if (Buffer.byteLength(line) > MAX_LINE) throw new Error("oversized");
        request = JSON.parse(line);
        if (!request || Array.isArray(request) || request.jsonrpc !== "2.0"
          || typeof request.method !== "string"
          || Object.keys(request).some((key) => !["jsonrpc", "id", "method", "params"].includes(key))
          || (request.id !== undefined && request.id !== null
            && typeof request.id !== "string" && !Number.isSafeInteger(request.id))
          || (request.params !== undefined && (!request.params
            || typeof request.params !== "object" || Array.isArray(request.params)))) {
          throw new Error("invalid");
        }
      } catch {
        refusal(null);
        return;
      }
      const hasId = Object.hasOwn(request, "id");
      let claim;
      if (request.method === "send") {
        if (!hasId || !validateSend(request.params)) {
          if (hasId) refusal(request.id);
          return;
        }
        try {
          const decision = await post("claim", {
            account_id: config.account_id,
            recipient: request.params.to,
            body: request.params.text,
          });
          if (typeof decision.claim_id !== "string" || !decision.claim_id) {
            throw new Error("claim unavailable");
          }
          claim = decision.claim_id;
        } catch {
          refusal(request.id);
          return;
        }
      } else if (!READ_METHODS.has(request.method)) {
        if (hasId) refusal(request.id);
        return;
      }
      if (pending.size >= 128) {
        if (claim) await terminal({ claim }, null).catch(() => {});
        if (hasId) refusal(request.id);
        return;
      }
      const childId = String(++serial);
      const entry = { id: request.id, hasId, claim };
      entry.timer = setTimeout(() => { void stop(); }, claim ? 45000 : 15000);
      pending.set(childId, entry);
      child.stdin.write(`${JSON.stringify({ ...request, id: childId })}\n`);
    }).catch(() => stop());
  });
  outgoing.on("line", (line) => {
    void (async () => {
      if (stopping) return;
      if (Buffer.byteLength(line) > MAX_LINE) return stop();
      let response;
      try { response = JSON.parse(line); } catch { return stop(); }
      if (!response || typeof response !== "object" || Array.isArray(response)
        || response.jsonrpc !== "2.0") return stop();
      if (response.method && !Object.hasOwn(response, "id")) {
        if (typeof response.method !== "string") return stop();
        write(response);
        return;
      }
      if (!Object.hasOwn(response, "id")
        || (typeof response.id !== "string" && !Number.isSafeInteger(response.id))
        || (Object.hasOwn(response, "result") === Object.hasOwn(response, "error"))) {
        return stop();
      }
      const entry = pending.get(String(response.id));
      if (!entry) return stop();
      clearTimeout(entry.timer);
      pending.delete(String(response.id));
      try {
        const proven = await terminal(entry, response);
        if (entry.hasId) {
          if (proven) write({ ...response, id: entry.id });
          else refusal(entry.id);
        }
      } catch {
        if (entry.hasId) refusal(entry.id);
        await stop();
      }
    })().catch(() => stop());
  });
  // Provider diagnostics can contain message text; never echo them.
  child.stderr.on("data", () => {});
  child.on("error", () => { void stop(); });
  child.stdin.on("error", () => { void stop(); });
  child.stdout.on("error", () => { void stop(); });
  incoming.on("close", () => { void admitted.then(() => child.stdin.end()); });
  await new Promise((resolve) => child.on("close", () => {
    void stop().finally(() => { incoming.close(); outgoing.close(); resolve(); });
  }));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const config = loadGuardConfig(process.env.ADMINISTRATEME_COS_IMSG_GUARD_CONFIG);
    await runGuard(config, process.argv.slice(2));
  } catch {
    process.stderr.write("CoS iMessage transport unavailable; no action admitted.\n");
    process.exitCode = 1;
  }
}
