import { readFile, readdir, writeFile, mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const pluginRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = path.resolve(pluginRoot, "..", "..");
const workspace = process.env.ADMINME_WORKSPACE_OUTPUT
  ? path.resolve(process.env.ADMINME_WORKSPACE_OUTPUT)
  : path.join(pluginRoot, "workspace");
const personaId = process.env.ADMINME_PERSONA_ID || "quiet_assistant";
if (!/^[a-z][a-z0-9_]*$/.test(personaId)) {
  throw new Error(`Invalid ADMINME_PERSONA_ID: ${personaId}`);
}
const personaPath = path.join(repoRoot, "packs", "personas", personaId, "pack.json");
const personaPack = JSON.parse(await readFile(personaPath, "utf8"));
if (personaPack.kind !== "persona" || personaPack.id !== personaId || typeof personaPack.soul !== "string") {
  throw new Error(`Invalid persona extension: ${personaPath}`);
}
const programRoot = path.join(repoRoot, "bootstrap", "openclaw", "programs");
const programNames = (await readdir(programRoot)).filter((name) => name.endsWith(".md")).sort();
const layout = JSON.parse(await readFile(path.join(repoRoot, "bootstrap/openclaw/workspace-layout.json"), "utf8"));
const names = [...layout.priority, ...layout.background];
if (layout.version !== 1 || !Number.isInteger(layout.max_composed_bytes) || layout.max_composed_bytes < 1000 || layout.max_composed_bytes > 20000
  || layout.priority.length === 0 || names.some((name) => typeof name !== "string" || !/^[a-z][a-z0-9_]*\.md$/.test(name))
  || new Set(names).size !== names.length || JSON.stringify([...names].sort()) !== JSON.stringify(programNames)) {
  throw new Error("Invalid or incomplete workspace instruction layout");
}
const programs = await Promise.all(
  layout.priority.map(async (name) => `\n<!-- source: bootstrap/openclaw/programs/${name} -->\n${await readFile(path.join(programRoot, name), "utf8")}`),
);
const compiledPrograms = programs.join("\n").trimEnd();
const referenceIndex = "\n# Background program references\n\n"
  + "On a matching standing-order trigger, read the exact program below in the installed administrateme-household skill's programs/ directory (relative to its SKILL.md). These retained programs govern their background workflows; they do not replace the current-request authority above.\n\n"
  + layout.background.map((name) => `- \`programs/${name}\`\n`).join("");
const compiled = `# AdministrateMe standing orders\n\nGenerated deterministically from version-controlled program sources. Scheduling remains in OpenClaw automations; these instructions do not create a second scheduler.\n${compiledPrograms}\n${referenceIndex}`;
if (Buffer.byteLength(compiled, "utf8") > layout.max_composed_bytes) throw new Error("Compiled instructions exceed the native context budget");
// Tests use an isolated output directory and never rewrite packaged references.
const references = process.env.ADMINME_WORKSPACE_OUTPUT ? path.join(workspace, "skill-references") : path.join(pluginRoot, "skills/administrateme-household/programs");
await mkdir(references, { recursive: true });
for (const name of layout.background) await writeFile(path.join(references, name), await readFile(path.join(programRoot, name)));

await mkdir(workspace, { recursive: true });
await writeFile(
  path.join(workspace, "SOUL.md"),
  `<!-- GENERATED from packs/personas/${personaId}/pack.json; do not add member data here. -->\n${personaPack.soul.trim()}\n\nYou do not become a household member, parent, guardian, clinician, lawyer, or financial decision-maker. Never impersonate a principal, reveal one member's private record to another, or turn generic conversation into household truth. OpenClaw approval and AdministrateMe domain approval are separate gates.\n`,
);
await writeFile(
  path.join(workspace, "IDENTITY.md"),
  "# AdministrateMe identity\n\nThis workspace is the per-instance AdministrateMe assistant identity. Persona sources are instance-level, never household authorization.\n",
);
await writeFile(
  path.join(workspace, "USER.md"),
  "# Member context boundary\n\nBootstrap may add member-specific preferences only in a per-member workspace overlay. Do not put a member profile, identifier, credential, or private record in this instance persona package.\n",
);
await writeFile(
  path.join(workspace, "AGENTS.md"),
  compiled,
);
await writeFile(
  path.join(workspace, "standing-orders.md"),
  "# Standing-order boundary\n\nThe authoritative schedules are in ../automations.json. Each trigger invokes only `administrateme_standing_order`; AdministrateMe records the typed trigger and retains domain truth while OpenClaw owns scheduling and execution.\n",
);

console.log(`Compiled ${programNames.length} standing-order programs and persona ${personaId}.`);
