import { spawn } from "node:child_process";
import { createHash, createHmac, timingSafeEqual } from "node:crypto";
import { readFileSync, realpathSync, statSync } from "node:fs";
import { basename, dirname, isAbsolute } from "node:path";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";
import type { PluginRuntime } from "./runtime.js";
import { checkDatabase, stopDatabaseCheck } from "./cos-permission-database.js";

const CONTRACT = "cos-apple-events-preflight-v1";
const METHOD = "administrateme.cosPermissions.preflight";
const PREFIX = "ADMINISTRATEME_PERMISSION_";
const TARGET = "com.apple.MobileSMS";
const STATUS = new Map<number, string>([
  [0, "authorized"], [-1744, "needs_consent"], [-1743, "denied"],
  [-600, "target_not_running"], [-1742, "target_not_accessible"],
]);
type Operation = "check" | "request";

function pinnedFile(name: string): { path: string; sha256: string } {
  const path = process.env[`${PREFIX}${name}`] ?? "";
  const sha256 = process.env[`${PREFIX}${name}_SHA256`] ?? "";
  if (!isAbsolute(path) || realpathSync(path) !== path || !/^[a-f0-9]{64}$/.test(sha256)) {
    throw new Error("Permission helper coordinates unavailable");
  }
  const stat = statSync(path);
  if (!stat.isFile() || (stat.mode & 0o022) !== 0 ||
      (stat.uid !== 0 && stat.uid !== process.getuid?.()) ||
      createHash("sha256").update(readFileSync(path)).digest("hex") !== sha256) {
    throw new Error("Permission helper pin refused");
  }
  return { path, sha256 };
}

function childEnvironment(): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { PATH: "/usr/bin:/bin" };
  for (const key of ["HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL"]) {
    if (process.env[key]) env[key] = process.env[key];
  }
  return env;
}

function parseResult(text: string, operation: Operation): Record<string, unknown> {
  const row = JSON.parse(text) as Record<string, unknown>;
  if (!row || Array.isArray(row) ||
      Object.keys(row).sort().join(",") !== "contract,operation,os_status,stage,status,target" ||
      row.contract !== CONTRACT || row.operation !== operation || row.target !== TARGET ||
      !["permission", "descriptor", "framework", "platform"].includes(String(row.stage))) {
    throw new Error("Permission helper returned an invalid result");
  }
  const expected = row.stage === "permission" && Number.isInteger(row.os_status)
    ? STATUS.get(Number(row.os_status)) ?? "failed"
    : row.stage === "platform" && row.os_status === null ? "unsupported" : "failed";
  if (row.status !== expected ||
      (["permission", "descriptor"].includes(String(row.stage)) && !Number.isInteger(row.os_status)) ||
      (["framework", "platform"].includes(String(row.stage)) && row.os_status !== null)) {
    throw new Error("Permission helper returned an inconsistent result");
  }
  return row;
}

/** No generic command, target, path, environment, or provider input is accepted. */
export function registerCosPermissionPreflight(api: OpenClawPluginApi, runtime: PluginRuntime): void {
  const used = new Map<string, number>();
  let active: ReturnType<typeof spawn> | undefined;
  let stopping = false;
  api.registerGatewayMethod(METHOD, async ({ params, client, respond }) => {
    let owned: ReturnType<typeof spawn> | undefined;
    // Fixed stage names keep operational refusals diagnosable without exposing
    // paths, credentials, ticket contents, or raw subprocess/filesystem errors.
    let stage = "authorization";
    try {
      // Defense in depth: the pinned Gateway also enforces the registered scope.
      if (!client?.connect?.scopes?.includes("operator.admin") || stopping || active) {
        throw new Error("Permission preflight unavailable");
      }
      const now = Date.now();
      for (const [nonce, expiry] of used) if (expiry <= now) used.delete(nonce);
      if (Object.keys(params).sort().join(",") !== "expires_at_ms,nonce,operation,signature" ||
          typeof params.operation !== "string" || !["check", "request"].includes(params.operation) ||
          typeof params.nonce !== "string" || !/^[a-f0-9]{32}$/.test(params.nonce) ||
          used.has(params.nonce) || typeof params.expires_at_ms !== "number" ||
          !Number.isSafeInteger(params.expires_at_ms) || params.expires_at_ms <= now ||
          params.expires_at_ms > now + 10_000 ||
          typeof params.signature !== "string" || !/^[a-f0-9]{64}$/.test(params.signature)) {
        throw new Error("Permission preflight authorization refused");
      }
      const secret = process.env[runtime.transport.config.sharedSecretEnv];
      if (!secret || secret.length < 24) throw new Error("Permission authorization unavailable");
      const operation = params.operation as Operation;
      const signature = createHmac("sha256", secret).update(
        [CONTRACT, operation, params.nonce, String(params.expires_at_ms)].join("|"),
      ).digest("hex");
      if (!timingSafeEqual(Buffer.from(signature), Buffer.from(params.signature))) {
        throw new Error("Permission preflight authorization refused");
      }
      used.set(params.nonce, params.expires_at_ms);
      stage = "helper_pin";
      const helper = pinnedFile("HELPER");
      stage = "python_pin";
      const python = pinnedFile("PYTHON");
      stage = "helper_location";
      if (basename(helper.path) !== "cos_apple_events.py" || basename(dirname(helper.path)) !== "bootstrap") {
        throw new Error("Permission helper location refused");
      }
      stage = "child_start";
      const started = Date.now();
      owned = spawn(python.path, ["-I", "-B", helper.path, "--operation", operation], {
        env: childEnvironment(), stdio: ["ignore", "pipe", "ignore"], shell: false,
      });
      active = owned;
      const child = owned;
      const raw = await new Promise<Record<string, unknown>>((resolve) => {
        let text = "";
        let failed: "timed_out" | "failed" | undefined;
        let killTimer: ReturnType<typeof setTimeout> | undefined;
        const terminate = (status: "timed_out" | "failed") => {
          if (failed) return;
          failed = status;
          child.kill("SIGTERM");
          killTimer = setTimeout(() => child.kill("SIGKILL"), 1_000);
        };
        const timer = setTimeout(() => terminate("timed_out"), operation === "request" ? 180_000 : 10_000);
        child.stdout?.on("data", (chunk: Buffer) => {
          text += chunk.toString("utf8");
          if (Buffer.byteLength(text) > 4096) { text = ""; terminate("failed"); }
        });
        child.on("error", () => { failed = "failed"; });
        child.on("close", (code) => {
          clearTimeout(timer);
          if (killTimer) clearTimeout(killTimer);
          if (!failed && code === 0) {
            try { resolve(parseResult(text, operation)); return; } catch { /* content-free failure */ }
          }
          resolve({ contract: CONTRACT, operation, target: TARGET, status: failed ?? "failed", os_status: null, stage: "child" });
        });
      });
      stage = "database_check";
      const database = operation === "check" ? await checkDatabase() : null;
      const result = {
        ...raw, nonce: params.nonce, started_at_ms: started, completed_at_ms: Date.now(),
        helper_sha256: helper.sha256, python_sha256: python.sha256, gateway_pid: process.pid,
        database,
      };
      const resultSignature = createHmac("sha256", secret).update(JSON.stringify(result)).digest("hex");
      respond(true, { ...result, signature: resultSignature });
    } catch {
      respond(false, undefined, {
        code: "PERMISSION_PREFLIGHT_UNAVAILABLE", message: "Permission preflight unavailable",
        details: { stage },
      });
    } finally {
      if (owned && active === owned) active = undefined;
    }
  }, { scope: "operator.admin" });
  api.registerService({
    id: "administrateme-permission-preflight",
    start() { stopping = false; },
    stop() {
      stopping = true;
      if (active) active.kill("SIGKILL");
      stopDatabaseCheck();
      used.clear();
    },
  });
}
