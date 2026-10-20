/** Every child is synthetic. Never invokes SQLite, imsg, Contacts, or macOS APIs. */
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import { createHash } from "node:crypto";
import { mkdtempSync, mkdirSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const spawn = vi.hoisted(() => vi.fn());
vi.mock("node:child_process", () => ({ spawn }));
import { checkDatabase, stopDatabaseCheck } from "../src/cos-permission-database.js";

const directories: string[] = [];
let child: EventEmitter & { stdout: PassThrough; kill: ReturnType<typeof vi.fn> };
const result = {
  contract: "cos-messages-database-preflight-v1",
  target: "service_messages_database", status: "ready", ready: true,
};

beforeEach(() => {
  const root = realpathSync(mkdtempSync(join(tmpdir(), "database-permission-fixture-")));
  directories.push(root);
  mkdirSync(join(root, "bootstrap"));
  const helper = join(root, "bootstrap/cos_messages_database.py");
  writeFileSync(helper, "# synthetic helper; never executed\n", { mode: 0o600 });
  for (const [name, value] of Object.entries({ DATABASE_HELPER: helper, PYTHON: realpathSync(process.execPath) })) {
    vi.stubEnv(`ADMINISTRATEME_PERMISSION_${name}`, value);
    vi.stubEnv(`ADMINISTRATEME_PERMISSION_${name}_SHA256`, createHash("sha256").update(readFileSync(value)).digest("hex"));
  }
  vi.stubEnv("ADMINME_OPENCLAW_SHARED_SECRET", "must-not-inherit");
  vi.stubEnv("PYTHONPATH", "/must-not-inherit");
  child = Object.assign(new EventEmitter(), { stdout: new PassThrough(), kill: vi.fn(() => true) });
  spawn.mockReturnValue(child);
});

afterEach(() => {
  vi.useRealTimers(); vi.unstubAllEnvs(); vi.clearAllMocks();
  for (const root of directories.splice(0)) rmSync(root, { recursive: true, force: true });
});

it("checks only the fixed helper with no caller path and emits no diagnostic data", async () => {
  const pending = checkDatabase();
  expect(spawn).toHaveBeenCalledOnce();
  const [binary, args, options] = spawn.mock.calls[0]!;
  expect(binary).toBe(realpathSync(process.execPath));
  expect(args).toEqual(["-I", "-B", process.env.ADMINISTRATEME_PERMISSION_DATABASE_HELPER]);
  expect(options.shell).toBe(false);
  expect(options.stdio).toEqual(["ignore", "pipe", "ignore"]);
  expect(options.env.ADMINME_OPENCLAW_SHARED_SECRET).toBeUndefined();
  expect(options.env.PYTHONPATH).toBeUndefined();
  child.stdout.emit("data", Buffer.from(JSON.stringify(result)));
  child.emit("close", 0);
  expect(await pending).toEqual({
    ...result,
    helper_sha256: process.env.ADMINISTRATEME_PERMISSION_DATABASE_HELPER_SHA256,
    python_sha256: process.env.ADMINISTRATEME_PERMISSION_PYTHON_SHA256,
  });
});

it.each([
  { ...result, ready: false },
  { ...result, status: "unavailable" },
  { ...result, path: "synthetic-private-coordinate" },
  null,
])("rejects malformed or contradictory helper results", async (row) => {
  const pending = checkDatabase();
  child.stdout.emit("data", Buffer.from(JSON.stringify(row)));
  child.emit("close", 0);
  expect(await pending).toMatchObject({ ready: false, status: "failed" });
});

it("refuses tampered helper pins before starting a child", async () => {
  vi.stubEnv("ADMINISTRATEME_PERMISSION_DATABASE_HELPER_SHA256", "0".repeat(64));
  expect(await checkDatabase()).toMatchObject({ ready: false, status: "failed" });
  expect(spawn).not.toHaveBeenCalled();
});

it("bounds silence and output and kills an active probe during service shutdown", async () => {
  vi.useFakeTimers();
  let pending = checkDatabase();
  await vi.advanceTimersByTimeAsync(10_000);
  expect(child.kill).toHaveBeenCalledWith("SIGTERM");
  await vi.advanceTimersByTimeAsync(1_000);
  expect(child.kill).toHaveBeenCalledWith("SIGKILL");
  child.emit("close", null);
  expect(await pending).toMatchObject({ ready: false, status: "timed_out" });
  pending = checkDatabase();
  child.stdout.emit("data", Buffer.from("x".repeat(4097)));
  child.emit("close", null);
  expect(await pending).toMatchObject({ ready: false, status: "failed" });
  pending = checkDatabase();
  stopDatabaseCheck();
  expect(child.kill).toHaveBeenCalledWith("SIGKILL");
  child.emit("close", null);
  expect(await pending).toMatchObject({ ready: false, status: "failed" });
});
