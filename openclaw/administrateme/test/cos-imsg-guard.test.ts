/** Real disposable child process and loopback admission API; no Apple access. */
import { spawn as spawnProcess, type ChildProcessWithoutNullStreams } from "node:child_process";
import { createHash } from "node:crypto";
import { once } from "node:events";
import { createServer } from "node:http";
import { chmod, mkdtemp, readFile, realpath, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createInterface } from "node:readline";
import { PassThrough } from "node:stream";
import { afterEach, describe, expect, it, vi } from "vitest";

// Dynamic URL imports the actual deployed JS entry point without a TypeScript shim.
const guardUrl = new URL("../bin/cos-imsg-guard.mjs", import.meta.url).href;
const { loadGuardConfig, runGuard } = await import(guardUrl);
const SECRET = "fixture-guard-secret-not-a-live-credential";
const RECIPIENT = "+12025550101"; // fixture:tenant_data:ok
const liveHarnesses: Array<{ close: () => Promise<void> }> = [];

const childSource = `
import { appendFileSync } from "node:fs";
import { createInterface } from "node:readline";
const input = createInterface({ input: process.stdin });
input.on("line", (line) => {
  const request = JSON.parse(line);
  appendFileSync(process.env.FIXTURE_AUDIT, JSON.stringify({
    ...request, secretInherited: Boolean(process.env.TEST_COS_GUARD_SECRET),
    guardConfigInherited: Boolean(process.env.ADMINISTRATEME_COS_IMSG_GUARD_CONFIG),
    gatewayAliasInherited: Boolean(process.env.OPENCLAW_GATEWAY_TOKEN),
    standardSharedInherited: Boolean(process.env.ADMINME_OPENCLAW_SHARED_SECRET),
    runtimeInjectionInherited: Boolean(process.env.NODE_OPTIONS),
    homeInherited: process.env.HOME === process.env.FIXTURE_EXPECTED_HOME,
    path: process.env.PATH
  }) + "\\n");
  if (request.method === "send" && process.env.FIXTURE_MODE === "silent") return;
  if (request.method === "send" && process.env.FIXTURE_MODE === "malformed") {
    process.stdout.write("invalid provider JSON\\n"); return;
  }
  if (request.method === "send" && process.env.FIXTURE_MODE === "null") {
    process.stdout.write("null\\n"); return;
  }
  if (request.method === "send" && process.env.FIXTURE_MODE === "batch") {
    process.stdout.write("[]\\n"); return;
  }
  if (request.method === "send" && process.env.FIXTURE_MODE === "missing-receipt") {
    process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id: request.id, result: { ok: true } }) + "\\n"); return;
  }
  const response = request.method === "send" && process.env.FIXTURE_MODE === "error"
    ? { jsonrpc: "2.0", id: request.id, error: { code: -1, message: "synthetic failure" } }
    : { jsonrpc: "2.0", id: request.id, result: { ok: true, guid: "fixture-provider-guid" } };
  process.stderr.write("fixture private provider diagnostic must not be relayed\\n");
  process.stdout.write(JSON.stringify(response) + "\\n");
});
`;

type ApiCall = { path: string; body: Record<string, unknown>; authorization: string };
type RpcResponse = { id: string | number | null; result?: Record<string, unknown>; error?: Record<string, unknown> };

async function harness({ claims = 0, mode = "normal", claimFailure = false } = {}) {
  const directory = await mkdtemp(join(tmpdir(), "cos-imsg-guard-fixture-"));
  const childPath = join(directory, "provider.mjs");
  const auditPath = join(directory, "provider-requests.jsonl");
  await writeFile(childPath, childSource, { mode: 0o600 });
  const calls: ApiCall[] = [];
  let remainingClaims = claims;
  const server = createServer((request, response) => {
    const chunks: Buffer[] = [];
    request.on("data", (chunk: Buffer) => chunks.push(chunk));
    request.on("end", () => {
      const body = JSON.parse(Buffer.concat(chunks).toString("utf8")) as Record<string, unknown>;
      calls.push({ path: request.url ?? "", body, authorization: request.headers.authorization ?? "" });
      response.setHeader("content-type", "application/json");
      if (request.headers.authorization !== `Bearer ${SECRET}`) {
        response.writeHead(403).end("{}"); return;
      }
      if (request.url?.endsWith("/claim")) {
        if (claimFailure || remainingClaims <= 0) {
          response.writeHead(claimFailure ? 503 : 403).end("{}"); return;
        }
        remainingClaims -= 1;
        response.end(JSON.stringify({ claim_id: `fixture-claim-${claims - remainingClaims}` }));
      } else {
        response.end(JSON.stringify({ recorded: true }));
      }
    });
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("fixture listener missing");
  vi.stubEnv("TEST_COS_GUARD_SECRET", SECRET);
  vi.stubEnv("OPENCLAW_GATEWAY_TOKEN", SECRET);
  vi.stubEnv("ADMINME_OPENCLAW_SHARED_SECRET", SECRET);
  vi.stubEnv("NODE_OPTIONS", "--trace-warnings");
  vi.stubEnv("ADMINISTRATEME_COS_IMSG_GUARD_CONFIG", "fixture-config-not-for-child");
  const input = new PassThrough();
  const output = new PassThrough();
  const responses: RpcResponse[] = [];
  const waiting: Array<(value: RpcResponse) => void> = [];
  let text = "";
  output.on("data", (chunk: Buffer) => { text += chunk.toString("utf8"); });
  const lines = createInterface({ input: output });
  lines.on("line", (line) => {
    const value = JSON.parse(line) as RpcResponse;
    const waiter = waiting.shift();
    if (waiter) waiter(value); else responses.push(value);
  });
  let child: ChildProcessWithoutNullStreams | undefined;
  let spawnCount = 0;
  const config = {
    account_id: "fixture-cos-account",
    api_base_url: `http://127.0.0.1:${address.port}`,
    cli_path: process.execPath,
    db_path: join(directory, "no-apple-database"),
    shared_secret_env: "TEST_COS_GUARD_SECRET",
    secret: SECRET,
  };
  const completion = runGuard(config, ["rpc", "--json"], {
    stdin: input,
    stdout: output,
    spawn: (_command: string, args: string[], options: { env: NodeJS.ProcessEnv }) => {
      expect(args).toEqual(["rpc", "--json", "--db", config.db_path]);
      spawnCount += 1;
      child = spawnProcess(process.execPath, [childPath], {
        stdio: ["pipe", "pipe", "pipe"],
        env: { ...options.env, FIXTURE_AUDIT: auditPath, FIXTURE_MODE: mode, FIXTURE_EXPECTED_HOME: process.env.HOME },
      });
      return child;
    },
  }) as Promise<void>;
  let closed = false;
  const close = async () => {
    if (closed) return;
    closed = true;
    input.end();
    child?.kill("SIGTERM");
    await completion;
    lines.close();
    output.end();
    server.closeAllConnections();
    await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
    await rm(directory, { recursive: true, force: true });
  };
  liveHarnesses.push({ close });
  return {
    config, calls, completion, close,
    get spawnCount() { return spawnCount; },
    get output() { return text; },
    send(value: unknown) { input.write(`${typeof value === "string" ? value : JSON.stringify(value)}\n`); },
    next: async () => {
      const queued = responses.shift();
      if (queued) return queued;
      return await new Promise<RpcResponse>((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error("guard fixture response timed out")), 4000);
        waiting.push((value) => { clearTimeout(timer); resolve(value); });
      });
    },
    requests: async () => {
      const contents = await readFile(auditPath, "utf8").catch(() => "");
      return contents.trim() ? contents.trim().split("\n").map((line) => JSON.parse(line) as Record<string, unknown>) : [];
    },
  };
}

function sendRequest(id: string | number = "send-1") {
  return { jsonrpc: "2.0", id, method: "send", params: { to: RECIPIENT, text: "Fixture reply", service: "imessage", transport: "applescript" } };
}

afterEach(async () => {
  for (const value of liveHarnesses.splice(0)) await value.close();
  vi.unstubAllEnvs();
});

describe("CoS iMessage process admission boundary", () => {
  it("loads only private config with an exact local provider pin and loopback API", async () => {
    const directory = await realpath(await mkdtemp(join(tmpdir(), "cos-imsg-pin-fixture-")));
    try {
      const binary = join(directory, "synthetic-provider");
      const bytes = Buffer.from("fixture bytes; not an executable provider");
      await writeFile(binary, bytes, { mode: 0o700 });
      const filename = join(directory, "guard.json");
      const config = {
        contract: "cos-imessage-guard-v1",
        account_id: "fixture-cos-account",
        cli_path: binary,
        cli_sha256: createHash("sha256").update(bytes).digest("hex"),
        db_path: join(directory, "no-apple-database"),
        api_base_url: "http://127.0.0.1:3330/",
        shared_secret_env: "TEST_COS_GUARD_SECRET",
      };
      const environment = { TEST_COS_GUARD_SECRET: SECRET };
      await writeFile(filename, JSON.stringify(config), { mode: 0o600 });
      expect(loadGuardConfig(filename, environment)).toMatchObject({ account_id: config.account_id, secret: SECRET });
      expect(() => loadGuardConfig(filename, {})).toThrow("authorization unavailable");
      await chmod(filename, 0o644);
      expect(() => loadGuardConfig(filename, environment)).toThrow("permissions");
      await chmod(filename, 0o600);
      const alias = join(directory, "config-alias");
      await symlink(filename, alias);
      expect(() => loadGuardConfig(alias, environment)).toThrow("permissions");
      await writeFile(filename, JSON.stringify({ ...config, api_base_url: "https://example.test/" }));
      expect(() => loadGuardConfig(filename, environment)).toThrow("loopback");
      await writeFile(filename, JSON.stringify(config));
      await writeFile(binary, "changed fixture bytes");
      expect(() => loadGuardConfig(filename, environment)).toThrow("pin invalid");
      await writeFile(binary, bytes);
      await chmod(binary, 0o722);
      expect(() => loadGuardConfig(filename, environment)).toThrow("pin invalid");
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  it("refuses an unapproved send without forwarding it to the child", async () => {
    const value = await harness();
    value.send(sendRequest());
    expect((await value.next()).error).toBeDefined();
    expect(await value.requests()).toEqual([]);
    expect(value.calls.map((call) => call.path)).toEqual(["/api/openclaw/v1/cos-responses/claim"]);
    expect(value.output).not.toContain(SECRET);
  });

  it("passes read RPC and removes backend authority from the real child environment", async () => {
    const value = await harness();
    value.send({ jsonrpc: "2.0", id: 17, method: "status", params: {} });
    expect((await value.next()).id).toBe(17);
    const requests = await value.requests();
    expect(requests).toHaveLength(1);
    expect(requests[0]).toMatchObject({
      method: "status", secretInherited: false, guardConfigInherited: false,
      gatewayAliasInherited: false, standardSharedInherited: false,
      runtimeInjectionInherited: false, homeInherited: true, path: "/usr/bin:/bin",
    });
    expect(value.calls).toEqual([]);
    expect(value.output).not.toContain("private provider diagnostic");
  });

  it("records a one-use claim and terminal receipt around an actual child send", async () => {
    const value = await harness({ claims: 1 });
    value.send(sendRequest("original-id"));
    expect(await value.next()).toMatchObject({ id: "original-id", result: { ok: true } });
    const requests = await value.requests();
    expect(requests).toHaveLength(1);
    expect(requests[0]).toMatchObject({ id: "1", method: "send" });
    expect(value.calls.map((call) => call.path)).toEqual([
      "/api/openclaw/v1/cos-responses/claim", "/api/openclaw/v1/cos-responses/terminal",
    ]);
    expect(value.calls[1]?.body).toMatchObject({ claim_id: "fixture-claim-1", status: "sent" });
    expect(value.calls[1]?.body.receipt_digest).toMatch(/^[a-f0-9]{64}$/);
    expect(value.calls.every((call) => call.authorization === `Bearer ${SECRET}`)).toBe(true);
    value.send(sendRequest("original-id"));
    expect((await value.next()).error).toBeDefined();
    expect(await value.requests()).toHaveLength(1);
  });

  it("refuses claim API failure without any child send", async () => {
    const value = await harness({ claims: 1, claimFailure: true });
    value.send(sendRequest());
    expect((await value.next()).error).toBeDefined();
    expect(await value.requests()).toEqual([]);
    expect(value.calls).toHaveLength(1);
  });

  it("rejects mutation methods, extra keys, batch requests and non-RPC CLI calls", async () => {
    const value = await harness({ claims: 10 });
    const send = sendRequest();
    const requests = [
      { jsonrpc: "2.0", id: "reaction", method: "react", params: {} },
      { jsonrpc: "2.0", id: "group", method: "chats.create", params: {} },
      { ...send, params: { ...send.params, attachment: "/fixture/file" } },
      { ...send, params: { ...send.params, to: [RECIPIENT] } },
      { ...send, params: { ...send.params, service: "sms" } },
      { ...send, params: { ...send.params, transport: "private-api" } },
      { ...send, unexpected: true },
      [send],
      "{invalid",
    ];
    for (const request of requests) {
      value.send(request);
      expect((await value.next()).error).toBeDefined();
    }
    expect(await value.requests()).toEqual([]);
    expect(value.calls).toEqual([]);
    const spawn = vi.fn();
    await expect(runGuard(value.config, ["send", "--to", RECIPIENT], { spawn })).rejects.toThrow("refuses CLI");
    await expect(runGuard(value.config, ["rpc", "--json", "--db", "/different"], { spawn })).rejects.toThrow("refuses CLI");
    expect(spawn).not.toHaveBeenCalled();
  });

  it.each(["error", "malformed", "null", "batch", "missing-receipt"])("records unknown and never retries a provider %s", async (mode) => {
    const value = await harness({ claims: 1, mode });
    value.send(sendRequest());
    expect((await value.next()).error).toBeDefined();
    expect(await value.requests()).toHaveLength(1);
    expect(value.calls.filter((call) => call.path.endsWith("/terminal"))).toHaveLength(1);
    expect(value.calls.find((call) => call.path.endsWith("/terminal"))?.body.status).toBe("unknown");
  });

  it("turns a silent provider timeout into unknown without replaying the send", async () => {
    const actualSetTimeout = globalThis.setTimeout;
    vi.spyOn(globalThis, "setTimeout").mockImplementation((callback, delay, ...args) =>
      actualSetTimeout(callback, delay === 45_000 ? 250 : delay, ...args));
    const value = await harness({ claims: 1, mode: "silent" });
    value.send({ jsonrpc: "2.0", id: "ready", method: "status", params: {} });
    expect((await value.next()).id).toBe("ready");
    value.send(sendRequest());
    expect((await value.next()).error).toBeDefined();
    await value.completion;
    expect((await value.requests()).filter((request) => request.method === "send")).toHaveLength(1);
    expect(value.calls.filter((call) => call.path.endsWith("/claim"))).toHaveLength(1);
    expect(value.calls.find((call) => call.path.endsWith("/terminal"))?.body.status).toBe("unknown");
  });
});
