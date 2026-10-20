/** Synthetic OS boundary: this suite never loads CoreServices or requests TCC. */
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import { createHash, createHmac, randomBytes } from "node:crypto";
import { chmodSync, mkdtempSync, mkdirSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { OpenClawPluginApi, GatewayHandlerOptions } from "openclaw/plugin-sdk/plugin-entry";
import type { PluginRuntime } from "../src/runtime.js";

const spawn = vi.hoisted(() => vi.fn());
vi.mock("node:child_process", () => ({ spawn }));
vi.mock("../src/cos-permission-database.js", () => ({
  checkDatabase: async () => ({ status: "not_found", ready: false }),
  stopDatabaseCheck: () => {},
}));
import { registerCosPermissionPreflight } from "../src/cos-permission-preflight.js";

const SECRET = "synthetic-permission-secret-not-a-live-credential";
const CONTRACT = "cos-apple-events-preflight-v1";
const directories: string[] = [];
let handler: (options: GatewayHandlerOptions) => unknown;
let child: EventEmitter & { stdout: PassThrough; kill: ReturnType<typeof vi.fn> };

function ticket(operation = "check", overrides: Record<string, unknown> = {}) {
  const row = { operation, nonce: randomBytes(16).toString("hex"), expires_at_ms: Date.now() + 5_000, ...overrides };
  return { ...row, signature: createHmac("sha256", SECRET).update(
    [CONTRACT, row.operation, row.nonce, String(row.expires_at_ms)].join("|"),
  ).digest("hex") };
}

function invoke(params: Record<string, unknown>, scopes = ["operator.admin"]) {
  const respond = vi.fn();
  const done = handler({ params, client: { connect: { scopes } }, respond });
  return { respond, done };
}

function finish(operation = "check", status = "needs_consent", osStatus = -1744) {
  child.stdout.emit("data", Buffer.from(JSON.stringify({
    contract: CONTRACT, operation, target: "com.apple.MobileSMS", status,
    os_status: osStatus, stage: "permission",
  })));
  child.emit("close", 0);
}

beforeEach(() => {
  const root = realpathSync(mkdtempSync(join(tmpdir(), "permission-fixture-")));
  directories.push(root);
  mkdirSync(join(root, "bootstrap"));
  const helper = join(root, "bootstrap/cos_apple_events.py");
  writeFileSync(helper, "# synthetic helper; never executed\n", { mode: 0o600 });
  for (const [name, value] of Object.entries({ HELPER: helper, PYTHON: realpathSync(process.execPath) })) {
    vi.stubEnv(`ADMINISTRATEME_PERMISSION_${name}`, value);
    vi.stubEnv(`ADMINISTRATEME_PERMISSION_${name}_SHA256`, createHash("sha256").update(readFileSync(value)).digest("hex"));
  }
  vi.stubEnv("PERMISSION_FIXTURE_SECRET", SECRET);
  vi.stubEnv("PYTHONPATH", "/must-not-inherit");
  vi.stubEnv("DYLD_INSERT_LIBRARIES", "/must-not-inherit");
  child = Object.assign(new EventEmitter(), {
    stdout: new PassThrough(), kill: vi.fn(() => true),
  });
  spawn.mockReturnValue(child);
  const registerGatewayMethod = vi.fn((method, callback, options) => {
    expect(method).toBe("administrateme.cosPermissions.preflight");
    expect(options).toEqual({ scope: "operator.admin" });
    handler = callback;
  });
  registerCosPermissionPreflight({
    registerGatewayMethod, registerService: vi.fn(),
  } as unknown as OpenClawPluginApi, {
    transport: { config: { sharedSecretEnv: "PERMISSION_FIXTURE_SECRET" } },
  } as PluginRuntime);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllEnvs();
  vi.clearAllMocks();
  for (const root of directories.splice(0)) rmSync(root, { recursive: true, force: true });
});

it("requires exact admin scope, ticket, operation and schema before any child", async () => {
  for (const [params, scopes] of [
    [ticket(), ["operator.read", "operator.write"]],
    [{ ...ticket(), target: "another.application" }, ["operator.admin"]],
    [ticket("send"), ["operator.admin"]],
    [ticket("check", { expires_at_ms: Date.now() - 1 }), ["operator.admin"]],
    [{ ...ticket(), signature: "0".repeat(64) }, ["operator.admin"]],
  ] as Array<[Record<string, unknown>, string[]]>) {
    const call = invoke(params, scopes);
    await call.done;
    expect(call.respond.mock.calls[0]?.[0]).toBe(false);
    expect(call.respond.mock.calls[0]?.[2]?.details).toEqual({ stage: "authorization" });
  }
  expect(spawn).not.toHaveBeenCalled();
});

it("pins child coordinates, sanitizes environment, signs exact result and consumes nonce", async () => {
  const authorization = ticket();
  const call = invoke(authorization);
  expect(spawn).toHaveBeenCalledOnce();
  const [binary, args, options] = spawn.mock.calls[0]!;
  expect(binary).toBe(realpathSync(process.execPath));
  expect(args).toEqual(["-I", "-B", process.env.ADMINISTRATEME_PERMISSION_HELPER, "--operation", "check"]);
  expect(options.shell).toBe(false);
  expect(options.env.PERMISSION_FIXTURE_SECRET).toBeUndefined();
  expect(options.env.PYTHONPATH).toBeUndefined();
  expect(options.env.DYLD_INSERT_LIBRARIES).toBeUndefined();
  finish();
  await call.done;
  const [ok, result] = call.respond.mock.calls[0]!;
  expect(ok).toBe(true);
  expect(result).toMatchObject({ operation: "check", status: "needs_consent", os_status: -1744, nonce: authorization.nonce });
  const { signature, ...unsigned } = result;
  expect(signature).toBe(createHmac("sha256", SECRET).update(JSON.stringify(unsigned)).digest("hex"));
  const replay = invoke(authorization);
  await replay.done;
  expect(replay.respond.mock.calls[0]?.[0]).toBe(false);
  expect(spawn).toHaveBeenCalledOnce();
});

it("refuses changed helper bytes before execution", async () => {
  writeFileSync(process.env.ADMINISTRATEME_PERMISSION_HELPER!, "# tampered\n");
  const call = invoke(ticket());
  await call.done;
  expect(call.respond.mock.calls[0]?.[0]).toBe(false);
  expect(call.respond.mock.calls[0]?.[2]?.details).toEqual({ stage: "helper_pin" });
  expect(spawn).not.toHaveBeenCalled();
});

it("reports only the refused pin stage for a writable interpreter fixture", async () => {
  const binary = join(directories[0]!, "synthetic-python");
  writeFileSync(binary, "synthetic interpreter; never executed\n", { mode: 0o600 });
  chmodSync(binary, 0o620);
  vi.stubEnv("ADMINISTRATEME_PERMISSION_PYTHON", binary);
  vi.stubEnv("ADMINISTRATEME_PERMISSION_PYTHON_SHA256", createHash("sha256").update(readFileSync(binary)).digest("hex"));
  const call = invoke(ticket());
  await call.done;
  expect(call.respond.mock.calls).toEqual([[false, undefined, {
    code: "PERMISSION_PREFLIGHT_UNAVAILABLE", message: "Permission preflight unavailable",
    details: { stage: "python_pin" },
  }]]);
  expect(spawn).not.toHaveBeenCalled();
});

it("explicit request waits at most 180 seconds then kills only its child", async () => {
  vi.useFakeTimers();
  child.kill.mockImplementation((signal) => {
    if (signal === "SIGKILL") child.emit("close", null);
    return true;
  });
  const call = invoke(ticket("request"));
  expect(spawn.mock.calls[0]?.[1]).toContain("request");
  const duplicate = invoke(ticket("request"));
  await duplicate.done;
  expect(duplicate.respond.mock.calls[0]?.[0]).toBe(false);
  await vi.advanceTimersByTimeAsync(179_999);
  expect(child.kill).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(1_001);
  await call.done;
  expect(child.kill.mock.calls).toEqual([["SIGTERM"], ["SIGKILL"]]);
  expect(call.respond.mock.calls[0]?.[1]).toMatchObject({ status: "timed_out", os_status: null, stage: "child" });
});

it("rejects a helper's contradictory authorization claim", async () => {
  const call = invoke(ticket());
  finish("check", "authorized", -1743);
  await call.done;
  expect(call.respond.mock.calls[0]?.[1]).toMatchObject({ status: "failed", os_status: null });
});
