import vm from "node:vm";
import { afterEach, describe, expect, it, vi } from "vitest";
import { resolveGlobalMap } from "openclaw/plugin-sdk/global-singleton";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";
import { registerWorkstationBrowserHooks } from "../src/workstation-browser-hooks.js";
import { serviceAgentId, serviceSessionKey } from "../src/service-private.js";
import { readPluginConfig } from "../src/config.js";
import type { PluginRuntime } from "../src/runtime.js";

const secret = "synthetic-browser-native-secret-not-production";
const resource = {
  resource_id: "planning", inventory_binding_id: "cos-browser", binding_revision: 1, policy_revision: 1,
  native_target_id: "real-fixture-native-target", target_evidence_reference: "fixture:operator-native-tab",
  profile: "chrome", url: "https://work.example.invalid/planning", expected_identity: { account: "cos@fixture.invalid" },
  identity_probes: { account: { selector: "#account", attribute: "textContent", extraction: "exact" } },
  route_sha256: "f".repeat(64),
};
function fixture(options: {
  baseUrl?: string; secretEnv?: string; secretValue?: string; sessionKey?: string;
  register?: typeof registerWorkstationBrowserHooks;
} = {}) {
  const secretEnv = options.secretEnv ?? "ADMINME_OPENCLAW_SHARED_SECRET";
  const secretValue = options.secretValue ?? secret;
  vi.stubEnv(secretEnv, secretValue);
  const hooks = new Map<string, any>();
  let middleware: any;
  const config = { browser: { enabled: true, defaultProfile: "chrome", allowSystemProfileImport: false, evaluateEnabled: true, profiles: { chrome: { driver: "extension" } } } };
  const request = vi.fn(async (path: string, body: any): Promise<any> => {
    if (path.endsWith("/policy")) return { bound: true, mode: "read", resource };
    if (path.endsWith("/capture")) return { status: "verified", observation_event_id: "observed-fixture", excerpt: body.excerpt, truncated: body.truncated, provider_effect_count: 0 };
    if (path.endsWith("/failure")) return { status: "blocked" };
    throw new Error("unexpected endpoint");
  });
  const api = { on: (name: string, callback: any, options: any) => {
    expect(name).not.toBe("before_prompt_build");
    if (name === "before_tool_call") expect(options).toEqual({ priority: 100 });
    hooks.set(name, callback);
  },
    registerAgentToolResultMiddleware: (callback: any, options: any) => { middleware = callback; expect(options).toEqual({ matcher: ["browser"], runtimes: ["openclaw", "codex"] }); },
    logger: { warn: vi.fn() }, runtime: { config: { current: () => config } },
  } as unknown as OpenClawPluginApi;
  const runtime = { transport: { request, config: readPluginConfig({ serviceWorkEnabled: true, sharedSecretEnv: secretEnv, ...(options.baseUrl ? { baseUrl: options.baseUrl } : {}) }) }, trackedSessions: new Map() } as unknown as PluginRuntime;
  (options.register ?? registerWorkstationBrowserHooks)(api, runtime);
  const context = { agentId: serviceAgentId(), sessionKey: options.sessionKey ?? serviceSessionKey(secretValue, undefined, "browser-worker"), toolCallId: "call-browser", runId: "worker" };
  const before = (params: any = { action: "snapshot" }, toolName = "browser") => hooks.get("before_tool_call")({ toolName, params, toolCallId: context.toolCallId }, context);
  const observe = (prepared: any, account = "cos@fixture.invalid", text = "private excerpt") => {
    let bodyRead = false;
    const evaluated = vm.runInNewContext(`(${prepared.params.request.fn})()`, {
      window: { location: { href: resource.url } },
      document: { querySelectorAll: () => [{ textContent: account }], get body() { bodyRead = true; return { innerText: text }; } },
    });
    return { bodyRead, event: { toolName: "browser", toolCallId: context.toolCallId, result: { details: { targetId: resource.native_target_id, result: evaluated } } } };
  };
  const run = async (account: string, text: string) => {
    const { bodyRead, event } = observe(await before(), account, text);
    return { bodyRead, result: await middleware(event, context) };
  };
  return { before, request, run, observe, middleware, context, config, hooks };
}

afterEach(() => {
  resolveGlobalMap(Symbol.for("administrateme.workstation-browser.pending.v1")).clear();
  vi.unstubAllEnvs();
});
describe("installed browser source boundary", () => {
  it("shares exact admission across separately loaded registrations and consumes it once", async () => {
    const admission = fixture();
    const { event } = admission.observe(await admission.before());
    vi.resetModules();
    const separate = await import("../src/workstation-browser-hooks.js");
    const resultGuard = fixture({ register: separate.registerWorkstationBrowserHooks });
    expect((await resultGuard.middleware(event, resultGuard.context)).result.details.status).toBe("verified");
    expect(admission.request.mock.calls.some(([path]) => path.endsWith("/capture"))).toBe(false);
    expect(resultGuard.request.mock.calls.filter(([path]) => path.endsWith("/capture"))).toHaveLength(1);
    const replay = await admission.middleware(event, admission.context);
    expect(replay.result.details.status).toBe("blocked");
    expect(JSON.stringify(replay)).not.toContain("private excerpt");
    expect(admission.request.mock.calls.some(([path]) => path.endsWith("/capture"))).toBe(false);
  });
  it.each(["backend", "secret", "session", "call"])("does not share or consume an admission in another %s scope", async (scope) => {
    const admission = fixture();
    const { event } = admission.observe(await admission.before());
    const unrelated = fixture({
      ...(scope === "backend" ? { baseUrl: "http://127.0.0.1:3331" } : {}),
      ...(scope === "secret" ? { secretEnv: "BROWSER_OTHER_TEST_SECRET", secretValue: "synthetic-other-browser-secret-not-production", sessionKey: admission.context.sessionKey } : {}),
      ...(scope === "session" ? { sessionKey: serviceSessionKey(secret, undefined, "other-browser-worker") } : {}),
    });
    const result = await unrelated.middleware({ ...event, ...(scope === "call" ? { toolCallId: "other-call" } : {}) }, unrelated.context);
    expect(result.result.details.status).toBe("blocked");
    expect(JSON.stringify(result)).not.toContain("private excerpt");
    expect(unrelated.request.mock.calls.some(([path]) => path.endsWith("/capture"))).toBe(false);
    expect((await admission.middleware(event, admission.context)).result.details.status).toBe("verified");
  });
  it("expires shared receipts after sixty seconds and never restores consumed authority", async () => {
    const now = vi.spyOn(Date, "now").mockReturnValue(1000);
    const admission = fixture();
    const { event } = admission.observe(await admission.before());
    const resultGuard = fixture();
    now.mockReturnValue(61001);
    expect((await resultGuard.middleware(event, resultGuard.context)).result.details.status).toBe("blocked");
    now.mockReturnValue(1000);
    expect((await admission.middleware(event, admission.context)).result.details.status).toBe("blocked");
    expect(resultGuard.request.mock.calls.some(([path]) => path.endsWith("/capture"))).toBe(false);
    expect(admission.request.mock.calls.some(([path]) => path.endsWith("/capture"))).toBe(false);
  });
  it("bounds process-wide admissions to one hundred receipts", async () => {
    const admission = fixture();
    const first = admission.observe(await admission.before()).event;
    for (let index = 1; index <= 100; index++) {
      admission.context.toolCallId = `call-browser-${index}`;
      expect(await admission.before()).toHaveProperty("params");
    }
    const last = admission.observe(await admission.before()).event;
    const resultGuard = fixture();
    expect((await resultGuard.middleware(first, resultGuard.context)).result.details.status).toBe("blocked");
    expect((await resultGuard.middleware(last, resultGuard.context)).result.details.status).toBe("verified");
  });
  it.each(["revoked", "capture", "identity"])("still withholds a cross-registration result after %s failure", async (failure) => {
    const admission = fixture();
    const { event } = admission.observe(await admission.before(), failure === "identity" ? "another@fixture.invalid" : "cos@fixture.invalid");
    const resultGuard = fixture();
    resultGuard.request.mockImplementation(async (path: string): Promise<any> => {
      if (path.endsWith("/policy")) return failure === "revoked" ? { bound: false } : { bound: true, mode: "read", resource };
      if (path.endsWith("/capture")) throw new Error("source store unavailable");
      return { status: "blocked" };
    });
    const result = await resultGuard.middleware(event, resultGuard.context);
    expect(result.result.details.status).toBe("blocked");
    expect(JSON.stringify(result)).not.toContain("private excerpt");
    expect(resultGuard.request.mock.calls.filter(([path]) => path.endsWith("/capture"))).toHaveLength(failure === "capture" ? 1 : 0);
    expect((await admission.middleware(event, admission.context)).result.details.status).toBe("blocked");
  });
  it("retains verified native result before exposing it, with a complete bounded envelope", async () => {
    const f = fixture();
    const { result, bodyRead } = await f.run("cos@fixture.invalid", "🙂".repeat(3000));
    expect(bodyRead).toBe(true);
    expect(result.result.details).toMatchObject({ status: "verified", truncated: true });
    expect(Buffer.byteLength(result.result.details.excerpt, "utf8")).toBe(2000);
    expect(Buffer.byteLength(JSON.stringify(result.result), "utf8")).toBeLessThan(9500);
    const captured = f.request.mock.calls.find(([path]) => path.endsWith("/capture"))![1];
    expect(captured.provenance.actor_id).toBe("service-principal:chief-of-staff");
    expect(captured.provenance.account_id).toBeUndefined();
    expect(captured.route_sha256).toBe(resource.route_sha256);
  });
  it("does not read the page body under another actual signed-in account", async () => {
    const f = fixture();
    const { result, bodyRead } = await f.run("another@fixture.invalid", "private data never read");
    expect(bodyRead).toBe(false);
    expect(result.result.details.status).toBe("blocked");
    expect(f.request.mock.calls.some(([path]) => path.endsWith("/capture"))).toBe(false);
    expect(f.request.mock.calls.some(([path]) => path.endsWith("/failure"))).toBe(true);
  });
  it("withholds unbound, revoked and failed-to-capture result content", async () => {
    const f = fixture();
    const fabricated = { toolName: "browser", toolCallId: "unbound", result: { details: { private: "never expose" } } };
    expect(JSON.stringify(await f.middleware(fabricated, f.context))).not.toContain("never expose");
    f.request.mockImplementation(async (path: string): Promise<any> => {
      if (path.endsWith("/policy")) return { bound: true, mode: "read", resource };
      if (path.endsWith("/capture")) throw new Error("source store unavailable");
      return { status: "blocked" };
    });
    expect((await f.run("cos@fixture.invalid", "private excerpt")).result.result.details.status).toBe("blocked");
  });
  it("rejects other native operations, alternate routes and unrelated worker tools", async () => {
    const f = fixture();
    for (const params of [{ action: "navigate", url: "https://other.invalid/" }, { action: "snapshot", profile: "user" }, { action: "snapshot", targetId: "other" }, { action: "act", request: { kind: "evaluate", fn: "() => secret" } }]) {
      expect(await f.before(params)).toMatchObject({ block: true });
    }
    for (const tool of ["exec", "bash", "apply_patch", "codex_apps.google_calendar.get_profile", "mcp__fixture__read"]) {
      expect(await f.before({}, tool)).toMatchObject({ block: true });
    }
    expect(await f.before({}, "administrateme_service_context")).toMatchObject({ block: true });
    f.config.browser.allowSystemProfileImport = true;
    expect(await f.before()).toMatchObject({ block: true });
  });
});
