/**
 * Hosted source-install gate: exercise the exact pinned OpenClaw normalizer.
 * Uses only synthetic text and pure formatting functions. No provider client,
 * account configuration, Messages database or channel send is constructed.
 */
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import ts from "typescript";

const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const sourceRoot = path.resolve(process.argv[2]);
const versions = JSON.parse(await readFile(path.join(repository, "bootstrap/versions.json"), "utf8"));
const corpus = JSON.parse(await readFile(path.join(repository, "tests/fixtures/cos_imessage_text.json"), "utf8"));
assert.equal(path.basename(sourceRoot), versions.openclaw.commit);
assert.equal(corpus.openclaw_commit, versions.openclaw.commit);
const pkg = JSON.parse(await readFile(path.join(sourceRoot, "package.json"), "utf8"));
assert.equal(pkg.version, versions.openclaw.version);
const requirePinned = createRequire(path.join(sourceRoot, "package.json"));
const sdkUrl = pathToFileURL(requirePinned.resolve("openclaw/plugin-sdk/text-chunking")).href;
const sdk = await import(sdkUrl);
const sender = await readFile(path.join(sourceRoot, "extensions/imessage/src/send.ts"), "utf8");
for (const step of [
  "message = convertMarkdownTables(message, tableMode)",
  "message = stripInlineDirectiveTagsForDelivery(message).text",
  "extractMarkdownFormatRuns(message)",
]) assert.ok(sender.includes(step), `Pinned sender contract changed: ${step}`);
const source = await readFile(path.join(sourceRoot, "extensions/imessage/src/markdown-format.ts"), "utf8");
assert.ok(source.includes('from "openclaw/plugin-sdk/text-chunking"'));
const compiled = ts.transpileModule(
  source.replace('from "openclaw/plugin-sdk/text-chunking"', `from ${JSON.stringify(sdkUrl)}`),
  { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ESNext } },
).outputText;
const directory = await mkdtemp(path.join(tmpdir(), "adminme-imessage-format-"));
try {
  const modulePath = path.join(directory, "pinned-format.mjs");
  await writeFile(modulePath, compiled, { mode: 0o600 });
  const { extractMarkdownFormatRuns } = await import(pathToFileURL(modulePath).href);
  const cases = [
    ...corpus.cases,
    { text: "a".repeat(8000), allowed: true },
    { text: "😀".repeat(4000), allowed: true },
  ];
  for (const row of cases) {
    const tables = sdk.convertMarkdownTables(row.text, "bullets");
    const directives = sdk.stripInlineDirectiveTagsForDelivery(tables).text;
    const actual = extractMarkdownFormatRuns(directives);
    if (row.allowed) {
      assert.equal(actual.text, row.text, "Admitted text changed in pinned sender");
      assert.deepEqual(actual.ranges, [], "Admitted text acquired native formatting");
    } else if (row.transformed) {
      assert.ok(actual.text !== row.text || actual.ranges.length > 0,
        "Expected pinned transformation is missing from regression corpus");
    }
  }
  // Exercise the production standing-reply escape function against the actual
  // pinned sender pipeline, including punctuation that exact manual replies reject.
  const selfReplySource = await readFile(path.join(repository, "openclaw/administrateme/src/self-replies.ts"), "utf8");
  const selfReplyModule = path.join(directory, "self-replies.mjs");
  await writeFile(selfReplyModule, ts.transpileModule(selfReplySource, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ESNext },
  }).outputText);
  const { escapeReplyText, replyParts } = await import(pathToFileURL(selfReplyModule).href);
  const literalCases = [...corpus.cases.map((row) => row.text.replace(/\s+/gu, " ").trim()),
    "**bold** _italic_ ~~strike~~ `code` <u>underline</u> [link](https://example.invalid/?x=1&y=2)",
    "[[reply_to_current]] [[audio_as_voice]] MEDIA:https://example.invalid/a.jpg",
    Array.from({length: 95}, (_, i) => String.fromCharCode(i + 32)).join(""),
    ...replyParts([{role: "user", content: "Question"}, {role: "assistant", content: "😀 a_b **x** ".repeat(1000)}]),
  ];
  for (const text of literalCases) {
    const escaped = escapeReplyText(text);
    const tables = sdk.convertMarkdownTables(escaped, "bullets");
    const directives = sdk.stripInlineDirectiveTagsForDelivery(tables).text;
    const actual = extractMarkdownFormatRuns(directives);
    assert.equal(actual.text, text, "Standing reply text changed in pinned sender");
    assert.deepEqual(actual.ranges, [], "Standing reply acquired native formatting");
  }
  console.log(`Verified ${cases.length} exact and ${literalCases.length} standing reply cases against pinned OpenClaw formatter.`);
} finally {
  await rm(directory, { recursive: true, force: true });
}
