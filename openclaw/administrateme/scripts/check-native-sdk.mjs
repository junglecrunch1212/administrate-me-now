/** Compile the plugin against the exact installed public SDK, never test fixtures.
 * Hosted qualification only. No plugin initialization, model or provider call.
 */
import assert from "node:assert/strict";
import { mkdtemp, readFile, realpath, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

assert.equal(process.env.GITHUB_ACTIONS, "true", "Use isolated hosted CI");
assert.equal(process.env.RUNNER_ENVIRONMENT, "github-hosted", "Use a disposable hosted runner");
assert.equal(process.argv.length, 3, "Expected the exact prepared native source directory");
const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const plugin = path.join(repository, "openclaw/administrateme");
const source = await realpath(process.argv[2]);
const versions = JSON.parse(await readFile(path.join(repository, "bootstrap/versions.json"), "utf8"));
const pkg = JSON.parse(await readFile(path.join(source, "package.json"), "utf8"));
assert.equal(path.basename(source), versions.openclaw.commit);
assert.equal(pkg.version, versions.openclaw.version);
const ts = createRequire(path.join(plugin, "package.json"))("typescript");
const sdkPaths = {};
for (const name of ["plugin-entry", "approval-runtime", "agent-harness-runtime", "global-singleton"]) {
  const entry = pkg.exports[`./plugin-sdk/${name}`];
  assert(entry && typeof entry.default === "string", `Missing public SDK export: ${name}`);
  let relative = entry.types;
  if (name === "global-singleton") {
    // This public runtime export deliberately ships no declaration. Check the
    // exact upstream source signature rather than supplying a local stub.
    assert.equal(relative, undefined);
    relative = "./src/plugin-sdk/global-singleton.ts";
  }
  assert(typeof relative === "string" && relative.startsWith("./"));
  const selected = await realpath(path.resolve(source, relative));
  assert(selected.startsWith(source + path.sep), "SDK types escaped their exact source artifact");
  sdkPaths[`openclaw/plugin-sdk/${name}`] = [selected];
}
const configPath = path.join(plugin, "tsconfig.json");
const config = ts.readConfigFile(configPath, ts.sys.readFile);
assert.equal(config.error, undefined);
const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, plugin, {
  noEmit: true,
  declaration: false,
  sourceMap: false,
  baseUrl: plugin,
  paths: sdkPaths,
  types: ["node"],
  // Validate our use of the actual exported signatures; upstream's dependency
  // declaration internals are qualified by its own build.
  skipLibCheck: true,
}, configPath);
assert.equal(parsed.errors.length, 0, "Plugin compiler configuration is invalid");
const rootNames = ts.sys.readDirectory(path.join(plugin, "src"), [".ts"], undefined, ["**/*.ts"]);
assert(rootNames.length > 0);
const formatHost = {
  getCanonicalFileName: (name) => name,
  getCurrentDirectory: () => plugin,
  getNewLine: () => "\n",
};
function check(files) {
  const program = ts.createProgram({ rootNames: files, options: parsed.options });
  const diagnostics = ts.getPreEmitDiagnostics(program);
  if (diagnostics.length) {
    throw new Error(ts.formatDiagnosticsWithColorAndContext(diagnostics, formatHost));
  }
  assert(!program.getSourceFiles().some((file) => file.fileName.includes("/test/fixtures/")),
    "Local fixtures must not shadow the installed native SDK");
  return program;
}
const program = check(rootNames);
for (const [name, [selected]] of Object.entries(sdkPaths)) {
  assert(program.getSourceFile(selected), `Actual SDK entry was not type-checked: ${name}`);
}
const directory = await mkdtemp(path.join(tmpdir(), "adminme-native-sdk-"));
try {
  const negative = path.join(directory, "reject-invalid-sdk-use.mts");
  await writeFile(negative, `import type { AnyAgentTool, OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";
declare const api: OpenClawPluginApi;
declare const tool: AnyAgentTool;
// @ts-expect-error The real gateway registration requires a callable handler.
api.registerGatewayMethod("administrateme.synthetic", 42);
// @ts-expect-error The real agent-end hook requires a callable handler.
api.on("agent_end", 42);
// @ts-expect-error The real tool execution contract cannot be replaced by a scalar.
tool.execute = 42;
`, { mode: 0o600 });
  // Any/broad-stub resolution produces unused-expect-error diagnostics here.
  check([negative]);
} finally {
  await rm(directory, { recursive: true, force: true });
}
console.log(`PINNED NATIVE SDK: ${rootNames.length} plugin source files compiled against actual ${pkg.version} exports; invalid gateway/hook/tool contracts refused: PASS`);
