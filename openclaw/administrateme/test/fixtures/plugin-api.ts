import type { GatewayHandlerOptions } from "../../src/sdk-types.js";
import type { AnyAgentTool, OpenClawPluginApi, OpenClawPluginCommandDefinition, OpenClawPluginService, OpenClawPluginToolContext } from "openclaw/plugin-sdk/plugin-entry";
import { vi } from "vitest";
import plugin from "../../src/index.js";

type ToolFactory = (context: OpenClawPluginToolContext) => AnyAgentTool | AnyAgentTool[];
type GatewayRegistration = {
  handler: (options: GatewayHandlerOptions) => void | Promise<void>;
  scope: string | undefined;
};
type HookRegistration = {
  handler: (event: Record<string, unknown>, context: Record<string, unknown>) => unknown;
  options?: Record<string, unknown>;
};

export function fakeApi(
  config: Record<string, unknown> = {},
  registrationMode = "full",
) {
  const tools = new Map<string, ToolFactory>();
  const commands = new Map<string, OpenClawPluginCommandDefinition>();
  const gateways = new Map<string, GatewayRegistration>();
  const hooks = new Map<string, HookRegistration[]>();
  const services: OpenClawPluginService[] = [];
  const api = {
    id: "administrateme",
    name: "AdministrateMe",
    version: "2.0.0-gate8",
    source: "fixture",
    registrationMode: registrationMode as OpenClawPluginApi["registrationMode"],
    pluginConfig: config,
    logger: {
      debug: vi.fn(),
      info: vi.fn(),
      warn: vi.fn(),
      error: vi.fn(),
    },
    registerTool(factory: AnyAgentTool | ToolFactory, opts?: { name?: string }) {
      if (typeof factory !== "function" || !opts?.name) throw new Error("expected named factory");
      tools.set(opts.name, factory);
    },
    registerCommand(command: OpenClawPluginCommandDefinition) {
      commands.set(command.name, command);
    },
    registerGatewayMethod(
      method: string,
      handler: GatewayRegistration["handler"],
      opts?: { scope?: "operator.read" | "operator.write" | "operator.admin" },
    ) {
      gateways.set(method, { handler, scope: opts?.scope });
    },
    registerAgentToolResultMiddleware: vi.fn(),
    registerService(service: OpenClawPluginService) {
      services.push(service);
    },
    on(hook: string, handler: HookRegistration["handler"], options?: Record<string, unknown>) {
      const registrations = hooks.get(hook) ?? [];
      registrations.push({ handler, ...(options ? { options } : {}) });
      hooks.set(hook, registrations);
    },
  } as unknown as OpenClawPluginApi;
  plugin.register(api);
  return { api, tools, commands, gateways, hooks, services };
}
