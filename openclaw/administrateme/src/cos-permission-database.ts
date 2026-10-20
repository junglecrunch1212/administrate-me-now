import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync, realpathSync, statSync } from "node:fs";
import { basename, dirname, isAbsolute } from "node:path";

const CONTRACT = "cos-messages-database-preflight-v1";
const TARGET = "service_messages_database";
type Status = "ready" | "not_found" | "unavailable" | "invalid_schema" | "unsupported" | "failed" | "timed_out";
export type DatabaseCheck = {
  contract: string; target: string; status: Status; ready: boolean;
  helper_sha256: string | null; python_sha256: string | null;
};
let active: ReturnType<typeof spawn> | undefined;

function pin(name: string): { path: string; sha256: string } {
  const path = process.env[`ADMINISTRATEME_PERMISSION_${name}`] ?? "";
  const sha256 = process.env[`ADMINISTRATEME_PERMISSION_${name}_SHA256`] ?? "";
  if (!isAbsolute(path) || realpathSync(path) !== path || !/^[a-f0-9]{64}$/.test(sha256)) {
    throw new Error("Database helper is unavailable");
  }
  const stat = statSync(path);
  if (!stat.isFile() || (stat.mode & 0o022) ||
      (stat.uid !== 0 && stat.uid !== process.getuid?.()) ||
      createHash("sha256").update(readFileSync(path)).digest("hex") !== sha256) {
    throw new Error("Database helper pin refused");
  }
  return { path, sha256 };
}

export function stopDatabaseCheck(): void { active?.kill("SIGKILL"); }

/** Fixed schema-only helper. Called only after authenticated operator checks. */
export async function checkDatabase(): Promise<DatabaseCheck> {
  const base: DatabaseCheck = {
    contract: CONTRACT, target: TARGET, status: "failed", ready: false,
    helper_sha256: null, python_sha256: null,
  };
  if (active) return base;
  let child: ReturnType<typeof spawn> | undefined;
  try {
    const helper = pin("DATABASE_HELPER");
    const python = pin("PYTHON");
    if (basename(helper.path) !== "cos_messages_database.py" || basename(dirname(helper.path)) !== "bootstrap") {
      return base;
    }
    base.helper_sha256 = helper.sha256; base.python_sha256 = python.sha256;
    const env: NodeJS.ProcessEnv = { PATH: "/usr/bin:/bin" };
    for (const key of ["HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL"]) {
      if (process.env[key]) env[key] = process.env[key];
    }
    child = spawn(python.path, ["-I", "-B", helper.path], {
      env, stdio: ["ignore", "pipe", "ignore"], shell: false,
    });
    active = child;
    const owned = child;
    return await new Promise<DatabaseCheck>((resolve) => {
      let output = "";
      let failed: "timed_out" | "failed" | undefined;
      let killTimer: ReturnType<typeof setTimeout> | undefined;
      const terminate = (status: "timed_out" | "failed") => {
        if (failed) return;
        failed = status;
        owned.kill("SIGTERM");
        killTimer = setTimeout(() => owned.kill("SIGKILL"), 1_000);
      };
      const timer = setTimeout(() => terminate("timed_out"), 10_000);
      owned.stdout?.on("data", (chunk: Buffer) => {
        output += chunk.toString("utf8");
        if (Buffer.byteLength(output) > 4096) { output = ""; terminate("failed"); }
      });
      owned.on("error", () => { failed = "failed"; });
      owned.on("close", (code) => {
        clearTimeout(timer);
        if (killTimer) clearTimeout(killTimer);
        if (!failed && code === 0) {
          try {
            const row = JSON.parse(output) as Record<string, unknown>;
            if (row && !Array.isArray(row) &&
                Object.keys(row).sort().join(",") === "contract,ready,status,target" &&
                row.contract === CONTRACT && row.target === TARGET &&
                ["ready", "not_found", "unavailable", "invalid_schema", "unsupported"].includes(String(row.status)) &&
                row.ready === (row.status === "ready")) {
              resolve({ ...base, status: row.status as Status, ready: row.ready as boolean });
              return;
            }
          } catch { /* Raw diagnostics are never returned. */ }
        }
        resolve({ ...base, status: failed ?? "failed" });
      });
    });
  } catch { return base; }
  finally { if (child && active === child) active = undefined; }
}
