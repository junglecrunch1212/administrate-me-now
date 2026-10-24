import { afterEach, expect, it, vi } from "vitest";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";
import { readPluginConfig } from "../src/config.js";
import type { PluginRuntime } from "../src/runtime.js";
import { serviceAgentId, serviceSessionKey } from "../src/service-private.js";
import { registerWorkstationInventory, WORKSTATION_INVENTORY_TOOL } from "../src/workstation.js";

const secret = "fixture-workstation-secret-at-least-24-characters";
function fixture() {
  const tools = new Map<string, (context: any) => any>();
  const request = vi.fn(async () => ({ contract: "administrateme-workstation-inventory-v2", provider_effect_count: 0 }));
  const api = { registerTool: (factory: any, options: any) => tools.set(options.name, factory) } as unknown as OpenClawPluginApi;
  const runtime = { transport: { request, config: readPluginConfig({}) } } as unknown as PluginRuntime;
  registerWorkstationInventory(api, runtime);
  return { tool: tools.get(WORKSTATION_INVENTORY_TOOL)!, request };
}

afterEach(() => { vi.unstubAllEnvs(); });

it("uses the actual native member or service context with no identity parameters", async () => {
  vi.stubEnv("ADMINME_OPENCLAW_SHARED_SECRET", secret);
  const f = fixture();
  const member = f.tool({ requesterSenderId: "actor-a", messageChannel: "imessage", agentAccountId: "cos-account", agentId: "member-agent" });
  await member.execute("read", {});
  expect(f.request.mock.calls[0]).toEqual(["/api/openclaw/v1/tools/workstation-inventory", {
    provenance: expect.objectContaining({ actor_id: "actor-a", channel_id: "imessage", account_id: "cos-account" }),
  }]);
  const service = f.tool({ agentId: serviceAgentId(), sessionKey: serviceSessionKey(secret) });
  await service.execute("read", {});
  expect(f.request.mock.calls[1]).toEqual(["/api/openclaw/v1/tools/workstation-inventory", {
    provenance: expect.objectContaining({ actor_id: "service-principal:chief-of-staff", channel_id: "administrateme-service" }),
  }]);
  expect(member.parameters.additionalProperties).toBe(false);
  expect(Object.keys(member.parameters.properties).sort()).toEqual(["binding_id", "limit", "offset", "section", "snapshot", "view"]);
});

it("rejects wrong service sessions and ambient tool contexts before transport", async () => {
  vi.stubEnv("ADMINME_OPENCLAW_SHARED_SECRET", secret);
  const f = fixture();
  await expect(f.tool({ agentId: serviceAgentId(), sessionKey: "untrusted" }).execute("read", {})).rejects.toThrow();
  await expect(f.tool({}).execute("read", {})).rejects.toThrow();
  expect(f.request).not.toHaveBeenCalled();
});


it("passes bounded page selection but never lets arguments replace native provenance", async () => {
  vi.stubEnv("ADMINME_OPENCLAW_SHARED_SECRET", secret);
  const f = fixture();
  const service = f.tool({ agentId: serviceAgentId(), sessionKey: serviceSessionKey(secret) });
  const query = { view: "details", section: "identities", binding_id: "fixture-route-17", offset: 40, snapshot: "a".repeat(64) };
  await service.execute("details", query);
  expect(f.request.mock.calls[0]).toEqual(["/api/openclaw/v1/tools/workstation-inventory", {
    ...query, provenance: expect.objectContaining({ actor_id: "service-principal:chief-of-staff" }),
  }]);
});

it("fails closed before returning an oversized complete native result", async () => {
  vi.stubEnv("ADMINME_OPENCLAW_SHARED_SECRET", secret);
  const f = fixture();
  f.request.mockResolvedValueOnce({ contract: "x".repeat(5000), provider_effect_count: 0 });
  const service = f.tool({ agentId: serviceAgentId(), sessionKey: serviceSessionKey(secret) });
  await expect(service.execute("large", {})).rejects.toThrow("native output budget");
});
