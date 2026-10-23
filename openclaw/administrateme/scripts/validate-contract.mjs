import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = path.resolve(root, "..", "..");
const readJson = async (name) => JSON.parse(await readFile(path.join(root, name), "utf8"));
const pkg = await readJson("package.json");
const manifest = await readJson("openclaw.plugin.json");
const automations = await readJson("automations.json");
const expectedVersion = "2026.7.2";
const expectedCommit = "ce67ffb70e83e5ebb56ea02e814e577153f84f61";
const expectedNode = ">=24.15.0 <25";
const expectedTools = [
  "administrateme_state",
  "administrateme_what_now",
  "administrateme_capabilities",
  "administrateme_settings_context",
  "administrateme_capability_request",
  "administrateme_policy_proposal",
  "administrateme_add_for_review",
  "administrateme_review",
  "administrateme_lifecycle",
  "administrateme_correct",
  "administrateme_service_work",
  "administrateme_service_work_state",
  "administrateme_prepare_delegated_action",
  "administrateme_execute_delegated_action",
  "administrateme_observe_delegated_action",
  "administrateme_execute_pending_provider_action",
  "administrateme_prepare_google_tasks_create_once",
  "administrateme_execute_google_tasks_create_once",
  "administrateme_google_tasks_create_state",
  "administrateme_skill",
  "administrateme_notification_result",
  "administrateme_cos_response",
  "administrateme_standing_order",
  "administrateme_self_reminder",
  "administrateme_self_reminder_fire",
];

function assert(condition, message) {
  if (!condition) throw new Error(`OpenClaw contract invalid: ${message}`);
}

assert(manifest.id === "administrateme", "manifest id");
assert(pkg.peerDependencies.openclaw === expectedVersion, "exact peer version");
assert(pkg.openclaw.compat.pluginApi === expectedVersion, "plugin API pin");
assert(pkg.openclaw.compat.minGatewayVersion === expectedVersion, "gateway pin");
assert(pkg.openclaw.build.openclawCommit === expectedCommit, "source commit pin");
assert(pkg.engines.node === expectedNode, "exact OpenClaw Node engine");
assert(pkg.openclaw.extensions[0] === "./src/index.ts", "source entrypoint");
assert(pkg.openclaw.runtimeExtensions[0] === "./dist/index.js", "runtime entrypoint");
assert(JSON.stringify(manifest.contracts.tools) === JSON.stringify(expectedTools), "typed tool declarations");
assert(expectedTools.every((name) => name.startsWith("administrateme_")), "tool namespace");
assert(manifest.dashboard.dataBindings.length === 11, "ten domain views plus health binding");
assert(manifest.dashboard.actionVerbs.length === 2, "dashboard write bindings");
assert(manifest.commandAliases.every((entry) => entry.kind === "runtime-slash"), "slash alias kind");
assert(
  Object.values(manifest.toolMetadata).every((entry) => entry.optional === true),
  "side-effect tool exposure",
);
assert(manifest.configSchema.additionalProperties === false, "strict plugin config");
assert(manifest.configSchema.properties.baseUrl.pattern.includes("127"), "loopback transport schema");
assert(automations.schema_version === 1 && automations.entries.length === 5, "standing orders");
assert(automations.entries.every((entry) => entry.tool === "administrateme_standing_order"), "standing-order tool seam");
const cronSource = await readFile(path.join(repoRoot, "bootstrap", "openclaw", "cron.yaml"), "utf8");
for (const entry of automations.entries) {
  assert(cronSource.includes(`program_id: ${entry.program_id}`), `automation program drift: ${entry.program_id}`);
  assert(cronSource.includes(`cron: "${entry.schedule}"`), `automation schedule drift: ${entry.program_id}`);
  assert(cronSource.includes(`message: "${entry.message}"`), `automation message drift: ${entry.program_id}`);
}

const sourceNames = (await readdir(path.join(root, "src"))).filter((name) => name.endsWith(".ts"));
const source = (await Promise.all(sourceNames.map((name) => readFile(path.join(root, "src", name), "utf8")))).join("\n");
assert(!source.includes('api.on("llm_input"'), "privileged model input hook forbidden");
assert(!source.includes('api.on("llm_output"'), "privileged model output hook forbidden");
assert(!source.includes("senderIsOwner"), "senderIsOwner cannot authorize household access");
assert(!source.includes("/tools/invoke"), "operator token invoke path forbidden");
assert(source.includes('scope: "operator.read"') && source.includes('scope: "operator.write"'), "dashboard RPC scopes");
assert(!source.includes("Authorization: `Bearer ${process.env"), "inline credential handling forbidden");

console.log(`Validated native OpenClaw ${expectedVersion} contract at ${expectedCommit}.`);
