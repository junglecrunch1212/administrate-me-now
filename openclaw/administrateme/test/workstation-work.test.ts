import { createHash } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";
import { readPluginConfig } from "../src/config.js";
import { registerHooks } from "../src/hooks.js";
import { registerWorkstationWorkTool, registerWorkstationWorkHooks, allowedScratchpadCall } from "../src/workstation-work.js";
import { serviceAgentId, serviceSessionKey } from "../src/service-private.js";
import type { PluginRuntime } from "../src/runtime.js";

const agent = "adminme-0123456789abcdef";
const actor = "+12025550101"; // fixture:tenant_data:ok
const account = "fixture-workstation";
const member = { agentId: agent, sessionKey: `agent:${agent}:imessage:${account}:direct:${actor}`, runId: "member-run" };
const text = "Draft a private scratchpad to organize the garage.";
const secret = "fixture-workstation-native-secret-not-production";
const toolName = "administrateme_workstation_work";
const bound = { bound: true, mode: "create" as const, artifact_request_id: "artifact-request", artifact_task_id: "cos-task:artifact" };

function fixture() {
  vi.stubEnv("ADMINME_OPENCLAW_SHARED_SECRET", secret);
  const hooks = new Map<string, Array<(...args: any[]) => any>>();
  const tools = new Map<string, (context: any) => any>();
  let completed = false;
  const note = { task_id: bound.artifact_task_id, last_event_id: "event-note", title: "Garage", notes: "Sort, label, and store." };
  const request = vi.fn(async (path: string, body: any): Promise<any> => {
    if (path.endsWith("/forward")) return { intent_event_id: "source-fixture" };
    if (path.endsWith("/admit")) return { status: "ready", mode: "create", worker_request_id: body.run_nonce,
      artifact_request_id: bound.artifact_request_id, artifact_task_id: bound.artifact_task_id,
      follow_up_source_id: "source-fixture", instruction: text };
    if (path.endsWith("/policy")) return bound;
    if (path.endsWith("/read-back")) { completed = true; return { verified: true }; }
    if (path.endsWith("/result")) return { status: completed ? "verified" : "pending_or_unknown", provider_effect_count: 0 };
    throw new Error("unexpected fixture route");
  });
  const invoke = async (name: string, event: any, context: any) => {
    let result;
    for (const fn of hooks.get(name) ?? []) { const value = await fn(event, context); if (value) result = value; }
    return result;
  };
  const run = vi.fn(async (params: any) => {
    const worker = { agentId: serviceAgentId(), sessionKey: params.sessionKey, runId: "worker-run" };
    expect(params.deliver).toBe(false);
    expect(params.lane).toBe("subagent");
    expect(params.sessionKey).not.toBe(member.sessionKey);
    expect(await invoke("before_prompt_build", { prompt: params.message, messages: [] }, worker)).toEqual({ toolsAllow: ["administrateme_service_private_work", "administrateme_service_private_state"] });
    await invoke("after_tool_call", { toolName: "administrateme_service_private_state", params: { task_id: note.task_id }, result: { details: { work: [note] } } }, worker);
    return { runId: "worker-run" };
  });
  const waitForRun = vi.fn(async () => ({ status: "ok" }));
  const api = { config: {}, on: (name: string, fn: any) => hooks.set(name, [...(hooks.get(name) ?? []), fn]),
    registerTool: (factory: any, options: any) => tools.set(options.name, factory),
    logger: { error: vi.fn(), info: vi.fn(), warn: vi.fn() },
    runtime: { subagent: { run, waitForRun }, channel: { outbound: { loadAdapter: vi.fn() } } } } as unknown as OpenClawPluginApi;
  const runtime = { transport: { request, config: readPluginConfig({ serviceWorkEnabled: true, memberAgentIds: [agent], standingSelfReplyBindings: [], forwardIntakeAccounts: [{ channelId: "imessage", accountId: account }] }) }, trackedSessions: new Map() } as unknown as PluginRuntime;
  registerHooks(api, runtime); registerWorkstationWorkTool(api, runtime); registerWorkstationWorkHooks(api, runtime);
  const start = async () => {
    await invoke("message_received", { from: actor, senderId: actor, content: text, messageId: "message-fixture" }, { channelId: "imessage", accountId: account, ...member });
    await invoke("before_agent_reply", { cleanedBody: text }, member);
    return invoke("before_tool_call", { toolName, toolCallId: "call-fixture", params: {} }, member);
  };
  return { start, invoke, run, waitForRun, request, note, tool: tools.get(toolName)!(member) };
}

afterEach(() => vi.unstubAllEnvs());
describe("native private workstation handoff", () => {
  it("keeps member intake, service worker and verified return identities distinct", async () => {
    const f = fixture();
    expect(await f.start()).toBeUndefined();
    const result = await f.tool.execute("call-fixture", { action: "create", instruction: text });
    expect(result.details).toMatchObject({ status: "verified", provider_effect_count: 0 });
    expect(f.run).toHaveBeenCalledOnce();
    expect(f.waitForRun).toHaveBeenCalledWith({ runId: "worker-run", timeoutMs: 45000 });
    const readBack = f.request.mock.calls.find(([path]) => path.endsWith("/read-back"))![1];
    expect(readBack.provenance.actor_id).toBe("service-principal:chief-of-staff");
    expect(readBack.provenance.account_id).toBeUndefined();
    expect(readBack.content_sha256).toBe(createHash("sha256").update(JSON.stringify([f.note.task_id, f.note.last_event_id, f.note.title, f.note.notes])).digest("hex"));
    expect(f.request.mock.calls.at(-1)![1].provenance.actor_id).toBe(actor);
  });

  it("does not report acceptance or worker claims as read-back success", async () => {
    const f = fixture(); await f.start();
    f.run.mockImplementation(async () => ({ runId: "accepted-only" }));
    f.waitForRun.mockImplementation(async () => ({ status: "timeout" }));
    const result = await f.tool.execute("call-fixture", { action: "create", instruction: text });
    expect(result.details).toMatchObject({ status: "pending_or_unknown" });
    expect(f.request.mock.calls.some(([path]) => path.endsWith("/read-back"))).toBe(false);
  });

  it("checks durable policy for every service tool and rejects other effects or notes", async () => {
    const f = fixture();
    const worker = { agentId: serviceAgentId(), sessionKey: serviceSessionKey(secret, undefined, "worker"), runId: "worker" };
    for (const toolName of ["exec", "bash", "apply_patch", "codex_apps.google_calendar.get_profile", "mcp__fixture__read", "browser", "administrateme_execute_pending_provider_action", "administrateme_service_context"]) {
      expect(await f.invoke("before_tool_call", { toolName, params: {} }, worker)).toMatchObject({ block: true });
    }
    expect(await f.invoke("before_tool_call", { toolName: "administrateme_service_private_state", params: { task_id: "another-note" } }, worker)).toMatchObject({ block: true });
    expect(await f.invoke("before_tool_call", { toolName: "administrateme_service_private_state", params: { task_id: bound.artifact_task_id } }, worker)).toBeUndefined();
    f.request.mockRejectedValueOnce(new Error("offline"));
    expect(await f.invoke("before_tool_call", { toolName: "administrateme_service_private_state", params: { task_id: bound.artifact_task_id } }, worker)).toMatchObject({ block: true });
  });

  it("allows only exact creation and read parameters", () => {
    const create = { request_id: bound.artifact_request_id, title: "Draft", record_kind: "scratchpad" };
    expect(allowedScratchpadCall(bound, "administrateme_service_private_work", create)).toBe(true);
    expect(allowedScratchpadCall({ ...bound, mode: "read" }, "administrateme_service_private_work", create)).toBe(false);
    expect(allowedScratchpadCall(bound, "administrateme_service_private_work", { ...create, source_event_ids: ["other"] })).toBe(false);
    expect(allowedScratchpadCall(bound, "administrateme_service_private_state", { task_id: bound.artifact_task_id, after_event_id: "other" })).toBe(false);
  });
});
