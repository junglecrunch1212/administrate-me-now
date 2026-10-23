import { BRIDGE_EVENT_KINDS, type BridgeEventKind, type PluginConfig } from "./contract.js";

const DEFAULTS: PluginConfig = {
  baseUrl: "http://127.0.0.1:3330",
  sharedSecretEnv: "ADMINME_OPENCLAW_SHARED_SECRET",
  timeoutMs: 5_000,
  bridgeEnabled: false,
  bridgeChannels: [],
  bridgeEventKinds: [...BRIDGE_EVENT_KINDS],
  forwardIntakeAccounts: [],
  modelSessionPrefix: "adminme:",
  dmScope: "per_account_channel_peer",
  standingSelfReplyBindings: [],
  scheduledSelfReminderBindings: [],
  memberAgentIds: [],
  bindingFingerprints: [],
  uniqueAgents: true,
  uniqueAccounts: true,
};

function stringArray(value: unknown): string[] {
  if (!Array.isArray(value) || value.some((entry) => typeof entry !== "string")) {
    return [];
  }
  return value.map((entry) => entry.trim()).filter(Boolean);
}

export function isLoopbackBaseUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return (
      url.protocol === "http:" &&
      url.username === "" &&
      url.password === "" &&
      url.pathname === "/" &&
      url.search === "" &&
      url.hash === "" &&
      ["127.0.0.1", "localhost", "[::1]"].includes(url.hostname)
    );
  } catch {
    return false;
  }
}

export function readPluginConfig(value: Record<string, unknown> | undefined): PluginConfig {
  const raw = value ?? {};
  const intakeAccounts = raw.forwardIntakeAccounts ?? [];
  if (!Array.isArray(intakeAccounts) || intakeAccounts.length > 20) {
    throw new Error("AdministrateMe forwardIntakeAccounts must be a bounded reviewed list");
  }
  const forwardIntakeAccounts: PluginConfig["forwardIntakeAccounts"] = intakeAccounts.map((entry: unknown) => {
    if (!entry || typeof entry !== "object") throw new Error("Invalid CoS intake account binding");
    const row = entry as Record<string, unknown>;
    if ((row.channelId !== "imessage" && row.channelId !== "bluebubbles") ||
        typeof row.accountId !== "string" || !row.accountId.trim() || row.accountId.length > 200 ||
        Object.keys(row).some((key) => key !== "channelId" && key !== "accountId")) {
      throw new Error("CoS intake requires an exact supported channel and account");
    }
    return { channelId: row.channelId, accountId: row.accountId };
  });
  if (new Set(forwardIntakeAccounts.map((entry) => JSON.stringify(entry))).size !== forwardIntakeAccounts.length) {
    throw new Error("Duplicate CoS intake account binding");
  }
  const baseUrl = typeof raw.baseUrl === "string" ? raw.baseUrl : DEFAULTS.baseUrl;
  if (!isLoopbackBaseUrl(baseUrl)) {
    throw new Error("AdministrateMe baseUrl must be an unauthenticated loopback HTTP origin");
  }
  const sharedSecretEnv =
    typeof raw.sharedSecretEnv === "string" ? raw.sharedSecretEnv : DEFAULTS.sharedSecretEnv;
  if (!/^[A-Z][A-Z0-9_]{2,80}$/.test(sharedSecretEnv)) {
    throw new Error("AdministrateMe sharedSecretEnv must name an environment variable");
  }
  const timeoutMs =
    typeof raw.timeoutMs === "number" && Number.isInteger(raw.timeoutMs)
      ? raw.timeoutMs
      : DEFAULTS.timeoutMs;
  if (timeoutMs < 100 || timeoutMs > 30_000) {
    throw new Error("AdministrateMe timeoutMs must be between 100 and 30000");
  }
  const requestedKinds = stringArray(raw.bridgeEventKinds);
  const bridgeEventKinds = (requestedKinds.length ? requestedKinds : DEFAULTS.bridgeEventKinds).map(
    (kind) => {
      if (!(BRIDGE_EVENT_KINDS as readonly string[]).includes(kind)) {
        throw new Error(`Unsupported AdministrateMe bridge event kind: ${kind}`);
      }
      return kind as BridgeEventKind;
    },
  );
  if (raw.dmScope !== undefined && raw.dmScope !== "per_account_channel_peer") {
    throw new Error("AdministrateMe dmScope must be per_account_channel_peer");
  }
  const replies = raw.standingSelfReplyBindings ?? [];
  if (!Array.isArray(replies) || replies.length > 20 ||
      replies.some((value: unknown) => typeof value !== "string" || !/^[0-9a-f]{64}$/.test(value)) ||
      new Set(replies).size !== replies.length) {
    throw new Error("Standing self replies require distinct exact binding fingerprints");
  }
  const reminders = raw.scheduledSelfReminderBindings ?? [];
  if (!Array.isArray(reminders) || reminders.length > 20 ||
      reminders.some((value: unknown) => typeof value !== "string" || !/^[0-9a-f]{64}$/.test(value) || !replies.includes(value)) ||
      new Set(reminders).size !== reminders.length) {
    throw new Error("Scheduled self reminders require distinct enabled private reply bindings");
  }
  const memberAgentIds = stringArray(raw.memberAgentIds);
  const bindingFingerprints = stringArray(raw.bindingFingerprints);
  if (memberAgentIds.some((value) => !/^adminme-[0-9a-f]{16}$/.test(value))) {
    throw new Error("AdministrateMe memberAgentIds contain an invalid agent id");
  }
  if (bindingFingerprints.some((value) => !/^[0-9a-f]{64}$/.test(value))) {
    throw new Error("AdministrateMe bindingFingerprints must be SHA-256 values");
  }
  if (new Set(memberAgentIds).size !== memberAgentIds.length) {
    throw new Error("AdministrateMe memberAgentIds must be unique");
  }
  if (new Set(bindingFingerprints).size !== bindingFingerprints.length) {
    throw new Error("AdministrateMe bindingFingerprints must be unique");
  }
  return {
    baseUrl: baseUrl.replace(/\/$/, ""),
    sharedSecretEnv,
    timeoutMs,
    bridgeEnabled: raw.bridgeEnabled === true,
    bridgeChannels: stringArray(raw.bridgeChannels),
    bridgeEventKinds,
    forwardIntakeAccounts,
    modelSessionPrefix:
      typeof raw.modelSessionPrefix === "string" && raw.modelSessionPrefix
        ? raw.modelSessionPrefix
        : DEFAULTS.modelSessionPrefix,
    dmScope: "per_account_channel_peer",
    standingSelfReplyBindings: replies,
    scheduledSelfReminderBindings: reminders,
    memberAgentIds,
    bindingFingerprints,
    uniqueAgents: new Set(memberAgentIds).size === memberAgentIds.length,
    uniqueAccounts: new Set(bindingFingerprints).size === bindingFingerprints.length,
  };
}
