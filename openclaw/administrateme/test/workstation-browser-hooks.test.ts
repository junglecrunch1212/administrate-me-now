import vm from "node:vm";
import { afterEach, describe, expect, it, vi } from "vitest";
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
function fixture() {
  vi.stubEnv("ADMINME_OPENCLAW_SHARED_SECRET", secret);
  const hooks = new Map<string, any>();
  let middleware: any;
  const config = { browser: { enabled: true, defaultProfile: "chrome", allowSystemProfileImport: false, evaluateEnabled: true, profiles: { chrome: { driver: "extension" } } } };
  const request = vi.fn(async (path: string, body: any): Promise<any> => {
    if (path.endsWith("/policy")) return { bound: true, mode: "read", resource };
    if (path.endsWith("/capture")) return { status: "verified", observation_event_id: "observed-fixture", excerpt: body.excerpt, truncated: body.truncated, provider_effect_count: 0 };
    if (path.endsWith("/failure")) return { status: "blocked" };
    throw new Error("unexpected endpoint");
  });
  const api = { on: (name: string, callback: any) => hooks.set(name, callback),
    registerAgentToolResultMiddleware: (callback: any, options: any) => { middleware = callback; expect(options).toEqual({ matcher: ["browser"], runtimes: ["openclaw", "codex"] }); },
    logger: { warn: vi.fn() }, runtime: { config: { current: () => config } },
  } as unknown as OpenClawPluginApi;
  const runtime = { transport: { request, config: readPluginConfig({ serviceWorkEnabled: true }) }, trackedSessions: new Map() } as unknown as PluginRuntime;
  registerWorkstationBrowserHooks(api, runtime);
  const context = { agentId: serviceAgentId(), sessionKey: serviceSessionKey(secret, undefined, "browser-worker"), toolCallId: "call-browser", runId: "worker" };
  const before = (params: any = { action: "snapshot" }, toolName = "browser") => hooks.get("before_tool_call")({ toolName, params, toolCallId: context.toolCallId }, context);
  const run = async (account: string, text: string) => {
    const prepared = await before();
    let bodyRead = false;
    const evaluated = vm.runInNewContext(`(${prepared.params.request.fn})()`, {
      window: { location: { href: resource.url } },
      document: { querySelectorAll: () => [{ textContent: account }], get body() { bodyRead = true; return { innerText: text }; } },
    });
    const result = await middleware({ toolName: "browser", toolCallId: context.toolCallId, result: { details: { targetId: resource.native_target_id, result: evaluated } } }, context);
    return { bodyRead, result };
  };
  return { before, request, run, middleware, context, config, hooks };
}

afterEach(() => vi.unstubAllEnvs());
describe("installed browser source boundary", () => {
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
    expect(await f.before({}, "exec")).toMatchObject({ block: true });
    expect(await f.before({}, "administrateme_service_context")).toMatchObject({ block: true });
    f.config.browser.allowSystemProfileImport = true;
    expect(await f.before()).toMatchObject({ block: true });
  });
});
