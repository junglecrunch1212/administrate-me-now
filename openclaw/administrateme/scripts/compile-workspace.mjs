import { readFile, readdir, writeFile, mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const pluginRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = path.resolve(pluginRoot, "..", "..");
const workspace = path.join(pluginRoot, "workspace");
const persona = await readFile(path.join(pluginRoot, "persona", "quiet-assistant.md"), "utf8");
const programRoot = path.join(repoRoot, "bootstrap", "openclaw", "programs");
const programNames = (await readdir(programRoot)).filter((name) => name.endsWith(".md")).sort();
const programs = await Promise.all(
  programNames.map(async (name) => `\n<!-- source: bootstrap/openclaw/programs/${name} -->\n${await readFile(path.join(programRoot, name), "utf8")}`),
);
const compiledPrograms = programs.join("\n").trimEnd();

await mkdir(workspace, { recursive: true });
await writeFile(
  path.join(workspace, "SOUL.md"),
  `<!-- GENERATED from persona/quiet-assistant.md; do not add member data here. -->\n${persona.trim()}\n`,
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
  `# AdministrateMe standing orders\n\nGenerated deterministically from version-controlled program sources. Scheduling remains in OpenClaw automations; these instructions do not create a second scheduler.\n${compiledPrograms}\n`,
);
await writeFile(
  path.join(workspace, "standing-orders.md"),
  "# Standing-order boundary\n\nThe authoritative schedules are in ../automations.json. Each trigger invokes only `administrateme_standing_order`; AdministrateMe records the typed trigger and retains domain truth while OpenClaw owns scheduling and execution.\n",
);

console.log(`Compiled ${programNames.length} standing-order programs and one instance persona.`);
