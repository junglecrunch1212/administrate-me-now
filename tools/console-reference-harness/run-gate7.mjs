import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { access, mkdir, mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright-core";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(scriptDir, "..", "..");
const evidenceDir = process.env.ADMINME_GATE7_EVIDENCE_DIR
  ? resolve(process.env.ADMINME_GATE7_EVIDENCE_DIR)
  : join(repoRoot, "docs", "refactor", "evidence", "gate-7", "visual");
const basePort = Number.parseInt(process.env.ADMINME_GATE7_VISUAL_PORT ?? "3347", 10);
const fixedNow = process.env.ADMINME_GATE7_VISUAL_NOW ?? "2026-08-13T14:00:00Z";
const maxRuntimeMs = Number.parseInt(
  process.env.ADMINME_GATE7_MAX_RUNTIME_MS ?? String(15 * 60 * 1000),
  10,
);
if (!Number.isSafeInteger(maxRuntimeMs) || maxRuntimeMs <= 0) {
  throw new Error("ADMINME_GATE7_MAX_RUNTIME_MS must be a positive integer");
}
const identities = {
  principal: "visual-principal.ts.net",
  adult: "visual-adult.ts.net",
  child: "visual-child.ts.net",
  screen: "visual-screen.ts.net",
};
const results = [];
const consoleErrors = [];
const pageErrors = [];
const requestFailures = [];
const screenshots = {};
let expected404Console = 0;
let observedExpected404Console = 0;
let expected409Console = 0;
let observedExpected409Console = 0;
let expected503Console = 0;
let observedExpected503Console = 0;
let contextSequence = 0;
let browserDescription = "installed Chromium browser via playwright-core@1.62.0";
const deterministicRasterArgs = [
  "--deterministic-mode",
  "--disable-font-subpixel-positioning",
  "--disable-gpu",
  "--disable-gpu-rasterization",
  "--disable-lcd-text",
  "--disable-skia-runtime-opts",
  "--font-render-hinting=none",
  "--force-color-profile=srgb",
  "--force-device-scale-factor=1",
  "--hide-scrollbars",
];

function record(name, pass, detail) {
  results.push({ name, status: pass ? "PASS" : "FAIL", detail });
  console.log(`[Gate 7] ${pass ? "PASS" : "FAIL"}: ${name}`);
  if (!pass) throw new Error(`${name}: ${detail}`);
}

async function launchEdge() {
  for (const executablePath of [
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
  ]) {
    try {
      await access(executablePath);
      browserDescription = `${executablePath.includes("Google Chrome") ? "Google Chrome" : "Microsoft Edge"} via playwright-core@1.62.0`;
      console.log(`[Gate 7] Launching ${browserDescription}`);
      return await chromium.launch({
        args: deterministicRasterArgs,
        executablePath,
        headless: true,
        timeout: 20_000,
      });
    } catch {}
  }
  browserDescription = "Microsoft Edge via playwright-core@1.62.0";
  console.log(`[Gate 7] Launching ${browserDescription}`);
  return chromium.launch({
    args: deterministicRasterArgs,
    channel: "msedge",
    headless: true,
    timeout: 20_000,
  });
}

async function waitForServer(baseUrl) {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try {
      const response = await fetch(`${baseUrl}/api/system/live`);
      if (response.ok) return;
    } catch {}
    await new Promise((resolveWait) => setTimeout(resolveWait, 250));
  }
  throw new Error(`Gate 7 fixture server did not become ready at ${baseUrl}`);
}

async function startServer(port, modelConfigured) {
  const instance = await mkdtemp(join(tmpdir(), `adminme-gate7-visual-${port}-`));
  const fixturePython = process.env.ADMINME_GATE7_PYTHON;
  const child = spawn(
    fixturePython ?? (process.platform === "win32" ? "poetry.exe" : "poetry"),
    fixturePython ? ["tools/gate7_visual_server.py"] : ["run", "python", "tools/gate7_visual_server.py"],
    {
      cwd: repoRoot,
      env: {
        ...process.env,
        PYTHONPATH: ".;bridge",
        ADMINME_GATE7_VISUAL_INSTANCE: instance,
        ADMINME_GATE7_VISUAL_PORT: String(port),
        ADMINME_GATE7_VISUAL_MODEL: modelConfigured ? "1" : "0",
        ADMINME_GATE7_VISUAL_NOW: fixedNow,
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  let output = "";
  child.stdout.on("data", (chunk) => { output += String(chunk); });
  child.stderr.on("data", (chunk) => { output += String(chunk); });
  const baseUrl = `http://127.0.0.1:${port}`;
  try {
    await waitForServer(baseUrl);
    await new Promise((resolveWait) => setTimeout(resolveWait, 100));
    if (child.exitCode !== null) {
      throw new Error(`Gate 7 fixture exited before owning ${baseUrl}\n${output.slice(-2000)}`);
    }
  } catch (error) {
    child.kill();
    throw new Error(`${error.message}\n${output.slice(-2000)}`);
  }
  console.log(`[Gate 7] Fixture server ready at ${baseUrl}`);
  return { baseUrl, child, instance, output: () => output };
}

async function stopServer(server) {
  if (process.platform === "win32") {
    const killer = spawn("taskkill", ["/PID", String(server.child.pid), "/T", "/F"], {
      stdio: "ignore",
    });
    await new Promise((resolveWait) => killer.once("close", resolveWait));
  } else {
    server.child.kill("SIGTERM");
    await Promise.race([
      new Promise((resolveWait) => server.child.once("close", resolveWait)),
      new Promise((resolveWait) => setTimeout(resolveWait, 2500)),
    ]);
    if (server.child.exitCode === null && server.child.signalCode === null) {
      server.child.kill("SIGKILL");
      await new Promise((resolveWait) => server.child.once("close", resolveWait));
    }
  }
  await rm(server.instance, {
    recursive: true,
    force: true,
    maxRetries: 20,
    retryDelay: 100,
  });
}

function headers(identity) {
  return { "Tailscale-User-Login": identity, "X-Correlation-Id": "gate7:visual" };
}

async function jsonRequest(baseUrl, path, identity, method = "GET", body) {
  const response = await fetch(`${baseUrl}${path}`, {
    method,
    headers: { ...headers(identity), ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const value = await response.json().catch(() => ({}));
  return { response, value };
}

async function settle(baseUrl) {
  const result = await jsonRequest(
    baseUrl,
    "/__gate7/settle",
    identities.principal,
    "POST",
  );
  if (!result.response.ok || result.value.settled !== true) {
    throw new Error(`Gate 7 fixture did not settle: ${JSON.stringify(result.value)}`);
  }
}

async function seed(baseUrl) {
  const message = await jsonRequest(
    baseUrl,
    "/api/comms/v1/demo/canonical-message",
    identities.principal,
    "POST",
    {
      request_id: "gate7-visual-message",
      native_message_id: "gate7-visual-native",
      body: "Could you reply today with the calm household update?",
      received_at: "2026-08-04T12:05:00Z",
    },
  );
  record("canonical fixture seeded", message.response.ok, message.response.status);
  await settle(baseUrl);
  const task = await jsonRequest(
    baseUrl,
    "/api/member/v1/tasks",
    identities.principal,
    "POST",
    {
      request_id: "gate7-visual-household-task",
      title: "Return the signed school form",
      due_date: "2026-08-04T16:00:00-04:00",
      energy: "low",
      effort: "small",
      domain: "school",
      item_type: "task",
      micro_script: "Open the form and check the signature line",
      notes: "Canonical task seeded for cross-surface visual proof.",
    },
  );
  record("canonical task fixture seeded", task.response.ok, task.value);
  await settle(baseUrl);
  const triageTask = await jsonRequest(
    baseUrl,
    "/__gate7/seed-triage-task",
    identities.principal,
    "POST",
  );
  record("Triage task fixture seeded", triageTask.response.ok && triageTask.value.seeded === 1, triageTask.value);
  await settle(baseUrl);
  const triageGoogleTask = await jsonRequest(
    baseUrl,
    "/__gate7/seed-triage-google-task",
    identities.principal,
    "POST",
  );
  record("Google Tasks Triage fixture seeded", triageGoogleTask.response.ok && triageGoogleTask.value.seeded === 2, triageGoogleTask.value);
  await settle(baseUrl);
  const triageContinuity = await jsonRequest(
    baseUrl,
    "/__gate7/seed-triage-continuity",
    identities.principal,
    "POST",
  );
  record("Triage continuity fixtures seeded", triageContinuity.response.ok && triageContinuity.value.seeded === 9, triageContinuity.value);
  await settle(baseUrl);
  const triageFinalReady = await jsonRequest(
    baseUrl,
    "/__gate7/seed-triage-final-ready",
    identities.adult,
    "POST",
  );
  record("isolated final Ready fixture seeded", triageFinalReady.response.ok && triageFinalReady.value.seeded === 1, triageFinalReady.value);
  await settle(baseUrl);
  const calendar = await jsonRequest(
    baseUrl,
    "/__gate7/seed-calendar",
    identities.principal,
    "POST",
  );
  record("calendar fixture seeded", calendar.response.ok && calendar.value.seeded === 7, calendar.value);
  await settle(baseUrl);
  const notes = await jsonRequest(
    baseUrl,
    "/__gate7/seed-notes",
    identities.principal,
    "POST",
  );
  record("Notes fixture seeded", notes.response.ok && notes.value.seeded === 4, notes.value);
  await settle(baseUrl);
  const finance = await jsonRequest(
    baseUrl,
    "/__gate7/seed-finance",
    identities.principal,
    "POST",
  );
  record("Finance fixture seeded", finance.response.ok && finance.value.seeded === 10, finance.value);
  await settle(baseUrl);
}

async function newContext(browser, identity, viewport, reducedMotion = "no-preference") {
  contextSequence += 1;
  const context = await browser.newContext({
    viewport,
    reducedMotion,
    extraHTTPHeaders: headers(identity),
    colorScheme: "light",
  });
  await context.addInitScript(({ instant, contextId }) => {
    const RealDate = Date;
    const frozen = new RealDate(instant).getTime();
    class FixedDate extends RealDate {
      constructor(...args) {
        super(...(args.length ? args : [frozen]));
      }
      static now() { return frozen; }
    }
    Object.defineProperty(globalThis, "Date", { configurable: true, value: FixedDate });
    Object.defineProperty(globalThis.crypto, "randomUUID", {
      configurable: true,
      value: () => {
        const prior = /^adminme-gate7:(\d+)$/.exec(globalThis.name);
        const uuidSequence = Number(prior?.[1] || "0") + 1;
        globalThis.name = `adminme-gate7:${uuidSequence}`;
        const suffix = (BigInt(contextId) * 1000000n + BigInt(uuidSequence)).toString(16).padStart(12, "0");
        return `00000000-0000-4000-8000-${suffix}`;
      },
    });
  }, { instant: fixedNow, contextId: contextSequence });
  const page = await context.newPage();
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    if (expected404Console > 0 && message.text().includes("404")) {
      expected404Console -= 1;
      observedExpected404Console += 1;
      return;
    }
    if (expected409Console > 0 && message.text().includes("409")) {
      expected409Console -= 1;
      observedExpected409Console += 1;
      return;
    }
    if (expected503Console > 0 && message.text().includes("503")) {
      expected503Console -= 1;
      observedExpected503Console += 1;
      return;
    }
    consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("requestfailed", (request) => requestFailures.push(`${request.url()} ${request.failure()?.errorText}`));
  return { context, page };
}

async function screenshot(page, name) {
  const path = join(evidenceDir, name);
  const productionPositioning = await page.evaluate(async () => {
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
    document.documentElement.style.setProperty("scroll-behavior", "auto", "important");
    document.documentElement.style.setProperty("overflow-anchor", "none", "important");
    document.body.style.setProperty("overflow-anchor", "none", "important");
    for (let cycle = 0; cycle < 3; cycle += 1) {
      globalThis.scrollTo({ left: 0, top: 0, behavior: "instant" });
      await new Promise((resolveFrame) => requestAnimationFrame(() => requestAnimationFrame(resolveFrame)));
    }
    const rail = document.querySelector(".rail");
    const chatFab = document.querySelector(".chat-fab");
    const railStyle = rail ? getComputedStyle(rail) : null;
    const chatFabStyle = chatFab ? getComputedStyle(chatFab) : null;
    const railRect = rail?.getBoundingClientRect();
    const chatFabRect = chatFab?.getBoundingClientRect();
    return {
      viewportWidth: globalThis.innerWidth,
      viewportHeight: globalThis.innerHeight,
      scrollX: globalThis.scrollX,
      scrollY: globalThis.scrollY,
      railPosition: railStyle?.position,
      railTop: railStyle?.top,
      railRenderedTop: railRect?.top,
      railLeftGap: railRect?.left,
      railRightGap: railRect ? globalThis.innerWidth - railRect.right : null,
      chatFabDisplay: chatFabStyle?.display,
      chatFabPosition: chatFabStyle?.position,
      chatFabStyleRight: chatFabStyle?.right,
      chatFabStyleBottom: chatFabStyle?.bottom,
      chatFabTop: chatFabRect?.top,
      chatFabRightGap: chatFabRect ? globalThis.innerWidth - chatFabRect.right : null,
      chatFabBottomGap: chatFabRect ? globalThis.innerHeight - chatFabRect.bottom : null,
    };
  });
  const expectedRailPosition = productionPositioning.viewportWidth <= 720 ? "sticky" : "static";
  record(
    `${name} production floating layout`,
    productionPositioning.scrollX === 0
      && productionPositioning.scrollY === 0
      && productionPositioning.railPosition === expectedRailPosition
      && (expectedRailPosition !== "sticky"
        || productionPositioning.railTop === "0px"
          && Math.abs(productionPositioning.railRenderedTop) < 0.01
          && Math.abs(productionPositioning.railLeftGap) < 0.01
          && Math.abs(productionPositioning.railRightGap) < 0.01)
      && productionPositioning.chatFabPosition === "fixed"
      && (productionPositioning.chatFabDisplay === "none"
        ? productionPositioning.chatFabStyleRight === "16px"
          && productionPositioning.chatFabStyleBottom === "16px"
        : Math.abs(productionPositioning.chatFabRightGap - 16) < 0.01
          && Math.abs(productionPositioning.chatFabBottomGap - 16) < 0.01),
    productionPositioning,
  );
  const fixedOverlayCaptureStyle = productionPositioning.chatFabDisplay === "none"
    ? ""
    : `
      .chat-fab {
        bottom: auto !important;
        position: absolute !important;
        right: ${productionPositioning.chatFabRightGap}px !important;
        top: ${productionPositioning.chatFabTop}px !important;
      }
    `;
  const stickyRailCaptureStyle = expectedRailPosition === "sticky"
    ? ".rail { position: static !important; }"
    : "";
  await page.screenshot({
    path,
    fullPage: true,
    style: `
      .skip-link { display: none !important; }
      ${fixedOverlayCaptureStyle}
      ${stickyRailCaptureStyle}
      html {
        scrollbar-width: none !important;
      }
      ::-webkit-scrollbar {
        display: none !important;
        height: 0 !important;
        width: 0 !important;
      }
      *, *::before, *::after {
        animation: none !important;
        caret-color: transparent !important;
        text-rendering: geometricPrecision !important;
        transition: none !important;
        -webkit-font-smoothing: antialiased !important;
      }
    `,
  });
  const metadata = await stat(path);
  const digest = createHash("sha256").update(await readFile(path)).digest("hex");
  screenshots[name] = { bytes: metadata.size, sha256: digest };
}

async function visit(page, baseUrl, path, expectedTitle) {
  const response = await page.goto(`${baseUrl}${path}`, { waitUntil: "networkidle" });
  record(`${path} HTTP`, response?.status() === 200, response?.status());
  await page.locator("#surface[aria-busy=false]").waitFor();
  const title = await page.locator("#page-title").textContent();
  record(`${path} title`, title === expectedTitle, title);
}

async function auditAccessibility(page, label) {
  const audit = await page.evaluate(() => {
    const controls = [...document.querySelectorAll("button, a, textarea")];
    const unnamedControls = controls.filter((node) => {
      const labelValue = node.getAttribute("aria-label") || node.textContent?.trim();
      if (node instanceof HTMLTextAreaElement) {
        return !node.labels?.length && !labelValue;
      }
      return !labelValue;
    });
    const unnamed = unnamedControls.length;
    const unnamedExamples = unnamedControls.slice(0, 5).map((node) => node.outerHTML.slice(0, 240));
    const overflow = document.documentElement.scrollWidth > document.documentElement.clientWidth + 1;
    const headings = [...document.querySelectorAll("h1")].length;
    const toMs = (value) => {
      const first = value.split(",")[0].trim();
      if (first.endsWith("ms")) return Number.parseFloat(first);
      if (first.endsWith("s")) return Number.parseFloat(first) * 1000;
      return 0;
    };
    const motion = [...document.querySelectorAll("*")].some((node) => {
      const style = getComputedStyle(node);
      return toMs(style.animationDuration) > 0.1 || toMs(style.transitionDuration) > 0.1;
    });
    const luminance = (hex) => {
      const channels = hex.match(/[0-9a-f]{2}/gi).map((value) => Number.parseInt(value, 16) / 255);
      const linear = channels.map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
      return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
    };
    const contrast = (foreground, background) => {
      const values = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
      return (values[0] + 0.05) / (values[1] + 0.05);
    };
    const contrastPairs = [
      contrast("222421", "f8f2e8"),
      contrast("686861", "fffdf8"),
      contrast("45386d", "ded8f2"),
      contrast("625e58", "e8e5df"),
      contrast("46545f", "e6eaed"),
    ];
    const landmarks = document.documentElement.lang === "en"
      && Boolean(document.querySelector("main#main"))
      && Boolean(document.querySelector("nav[aria-label='Primary']"))
      && Boolean(document.querySelector("#surface[aria-live='polite']"));
    return { unnamed, unnamedExamples, overflow, headings, motion, landmarks, minContrast: Math.min(...contrastPairs) };
  });
  await page.evaluate(() => {
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
  });
  await page.keyboard.press("Tab");
  const keyboard = await page.evaluate(() => {
    const active = document.activeElement;
    if (!(active instanceof HTMLElement)) return { tag: "none", visible: false, outline: 0 };
    const style = getComputedStyle(active);
    return {
      tag: active.tagName.toLowerCase(),
      visible: active.matches(":focus-visible"),
      outline: Number.parseFloat(style.outlineWidth),
    };
  });
  record(`${label} named controls`, audit.unnamed === 0, audit);
  record(`${label} no horizontal overflow`, audit.overflow === false, audit);
  record(`${label} single page heading`, audit.headings === 1, audit);
  record(`${label} screen-reader landmarks`, audit.landmarks, audit);
  record(`${label} contrast tokens`, audit.minContrast >= 4.5, audit.minContrast);
  record(`${label} keyboard focus indicator`, keyboard.visible && keyboard.outline >= 3, keyboard);
  return { ...audit, keyboard };
}

async function run() {
  console.log(`[Gate 7] Starting with a ${maxRuntimeMs} ms hard runtime bound`);
  await mkdir(evidenceDir, { recursive: true });
  const server = await startServer(basePort, true);
  let degraded;
  const browser = await launchEdge();
  browserDescription = `${browserDescription}; engine ${browser.version()}`;
  console.log(`[Gate 7] Browser ready: ${browserDescription}`);
  try {
    await seed(server.baseUrl);
    console.log("[Gate 7] Canonical fixtures seeded; beginning browser assertions");

    const principal = await newContext(browser, identities.principal, { width: 1440, height: 1000 });
    const surfaceLabels = {
      today: "Today", triage: "Triage", comms: "Comms", reminders: "Reminders", people: "People", notes: "Notes",
      finance: "Finance", calendar: "Calendar", scoreboard: "Scoreboard", settings: "Settings",
    };
    let routedTaskBucket = "scheduled";
    let routedTaskHref = "";
    let releaseStaleTriageLoad;
    let staleTriageLoadStarted;
    let staleTriageLoadSettled;
    let staleTriageLoadRoute;
    let releaseTriageDecision;
    let triageDecisionRoute;
    let releaseTriageEnrichment;
    let triageEnrichmentStarted;
    let triageEnrichmentRoute;
    let triageEnrichmentBody;
    let triageInitialPayload;
    let triageCursorRequestCount = 0;
    for (const [surface, label] of Object.entries(surfaceLabels)) {
      if (surface === "triage") {
        let initialResponseModified = false;
        let resolveStaleTriageLoadStarted;
        staleTriageLoadStarted = new Promise((resolveStarted) => { resolveStaleTriageLoadStarted = resolveStarted; });
        const staleLoadRelease = new Promise((resolveRelease) => { releaseStaleTriageLoad = resolveRelease; });
        staleTriageLoadSettled = new Promise((resolveSettled) => {
          staleTriageLoadRoute = async (route) => {
            const url = new URL(route.request().url());
            if (url.searchParams.has("triage_cursor")) {
              triageCursorRequestCount += 1;
              resolveStaleTriageLoadStarted();
              await staleLoadRelease;
              await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(triageInitialPayload) });
              resolveSettled();
              return;
            }
            {
              const response = await route.fetch();
              const payload = await response.json();
              if (!initialResponseModified) {
                triageInitialPayload = structuredClone(payload);
                initialResponseModified = true;
              }
              payload.triage.has_more = true;
              payload.triage.next_cursor = "gate7-delayed-pre-decision-page";
              await route.fulfill({ response, json: payload });
              return;
            }
          };
        });
        await principal.page.route("**/api/member/v1/surfaces/triage?*", staleTriageLoadRoute);
        const triageDecisionRelease = new Promise((resolveRelease) => { releaseTriageDecision = resolveRelease; });
        triageDecisionRoute = async (route) => {
          await triageDecisionRelease;
          await route.continue();
        };
        await principal.page.route("**/api/member/v1/triage/*/approve", triageDecisionRoute);
        let resolveTriageEnrichmentStarted;
        triageEnrichmentStarted = new Promise((resolveStarted) => { resolveTriageEnrichmentStarted = resolveStarted; });
        const triageEnrichmentRelease = new Promise((resolveRelease) => { releaseTriageEnrichment = resolveRelease; });
        triageEnrichmentRoute = async (route) => {
          triageEnrichmentBody = route.request().postData();
          resolveTriageEnrichmentStarted();
          await triageEnrichmentRelease;
          await route.continue();
        };
        await principal.page.route("**/api/member/v1/triage/*/enrich", triageEnrichmentRoute);
      }
      await visit(principal.page, server.baseUrl, `/member/${surface}`, label);
      if (surface === "triage") {
        await principal.page.evaluate(() => {
          window.__gate7OriginalFetch = window.fetch;
          window.__gate7FetchCounts = { cursorApplicationTurns: 0, enrich: 0, intents: 0, triageCursor: 0 };
          const originalFetch = window.fetch.bind(window);
          window.fetch = async (input, options) => {
            const url = typeof input === "string" ? input : input?.url || String(input);
            if (url.includes("/enrich")) window.__gate7FetchCounts.enrich += 1;
            if (url.includes("/triage/intents")) window.__gate7FetchCounts.intents += 1;
            if (url.includes("triage_cursor=")) window.__gate7FetchCounts.triageCursor += 1;
            const response = await originalFetch(input, options);
            if (url.includes("triage_cursor=")) {
              const originalJson = response.json.bind(response);
              response.json = async () => {
                const body = await originalJson();
                setTimeout(() => { window.__gate7FetchCounts.cursorApplicationTurns += 1; }, 0);
                return body;
              };
            }
            return response;
          };
        });
      }
      if (surface === "today") {
        record("Today defaults to Day Brief", await principal.page.getByRole("button", { name: "Day Brief" }).getAttribute("aria-pressed") === "true", await principal.page.locator("#surface").textContent());
        record("Today offers Carousel and What Now", await principal.page.getByRole("button", { name: "Carousel" }).count() === 1 && await principal.page.getByRole("button", { name: "What Now?" }).count() === 1, await principal.page.locator(".today-controls").textContent());
        record("Today hides internal profile and member identifiers", !(await principal.page.locator("#mode-label").textContent()).includes("carousel") && !(await principal.page.locator("#mode-label").textContent()).includes("visual_principal"), await principal.page.locator("#mode-label").textContent());
        const decisionCard = principal.page.locator(".day-item-clarifying-question").filter({ hasText: "Send the completed enrollment form" });
        const decisionText = await decisionCard.textContent();
        record("Today decisions name the question, context, source, and consequence boundary", decisionText.includes("Should AdministrateMe add this task") && decisionText.includes("What happened:") && decisionText.includes("Why it is here:") && decisionText.includes("Your choices") && decisionText.includes("Correct"), decisionText);
        await decisionCard.getByRole("button", { name: "Review" }).click();
        await principal.page.waitForURL(/\/member\/triage\?.*item=gate7-visual-triage-task/);
        await principal.page.locator('[data-triage-item="gate7-visual-triage-task"]').waitFor();
        record("Today Review opens the exact Triage candidate and matter", principal.page.url().includes("matter=") && principal.page.url().includes("view=all_candidates") && await principal.page.locator('[data-triage-item="gate7-visual-triage-task"]').count() === 1, principal.page.url());
        await principal.page.goBack();
        await principal.page.getByRole("button", { name: "Day Brief" }).waitFor();
        await screenshot(principal.page, "surface-today-day-brief-desktop.png");
        await principal.page.getByRole("button", { name: "Carousel" }).click();
        await principal.page.locator(".carousel-card").waitFor();
        record("Today Carousel is interactive", await principal.page.getByRole("button", { name: "Day Brief" }).getAttribute("aria-pressed") === "false", await principal.page.locator("#surface").textContent());
        let carouselDecisionText = await principal.page.locator(".carousel-card").textContent();
        for (let attempt = 0; attempt < 24 && !carouselDecisionText.includes("Should AdministrateMe add this task"); attempt += 1) {
          const later = principal.page.getByRole("button", { name: "Later" });
          if (await later.count() === 0) break;
          await later.click();
          carouselDecisionText = await principal.page.locator(".carousel-card").textContent();
        }
        record("Today Carousel carries the same decision-grade context", carouselDecisionText.includes("Should AdministrateMe add this task") && carouselDecisionText.includes("What happened:") && carouselDecisionText.includes("Why it is here:") && carouselDecisionText.includes("Your choices") && carouselDecisionText.includes("Correct"), carouselDecisionText);
        await screenshot(principal.page, "surface-today-carousel-desktop.png");
      } else if (surface === "triage") {
        const inventoryHeading = await principal.page.locator(".triage-inventory h2").textContent();
        const inventoryTotal = Number.parseInt(inventoryHeading || "0", 10);
        const allCandidatesLabel = await principal.page.getByRole("button", { name: /^All candidates/ }).textContent();
        const triageControlsNote = await principal.page.locator(".comms-controls .state-note").textContent();
        record("Triage distinguishes loaded lens counts from complete inventory", inventoryTotal > 0 && allCandidatesLabel.includes(` / ${inventoryTotal}`) && triageControlsNote.toLowerCase().includes("filter counts"), JSON.stringify({ inventoryHeading, allCandidatesLabel, triageControlsNote }));
        await principal.page.locator(".triage-master-button").filter({ hasText: "Send the completed enrollment form" }).click();
        record("Triage renders typed task properties", await principal.page.getByLabel("Due date").count() === 1 && await principal.page.getByText("Available from", { exact: true }).count() === 1 && await principal.page.getByText("Energy needed", { exact: true }).count() === 1 && await principal.page.getByText("First tiny step", { exact: true }).count() === 1, await principal.page.locator(".triage-detail").textContent());
        record("Triage removed machine-format date entry", !(await principal.page.locator(".triage-detail").textContent()).includes("ISO 8601"), await principal.page.locator(".triage-detail").textContent());
        await screenshot(principal.page, "surface-triage-task-desktop.png");
        await principal.page.getByRole("button", { name: "Load next matters" }).click();
        await staleTriageLoadStarted;
        const preDecisionFetchCounts = await principal.page.evaluate(() => ({ ...window.__gate7FetchCounts }));
        await principal.page.getByLabel("First tiny step").fill("Open the enrollment portal and attach the signed form");
        await principal.page.getByRole("button", { name: "Save & confirm" }).click();
        await triageEnrichmentStarted;
        const readyLens = principal.page.getByRole("button", { name: /^Ready/ });
        const recentLens = principal.page.getByRole("button", { name: /^Recent/ });
        const lockedBeforeEnrichment = await principal.page.locator("#surface button, #surface input, #surface select, #surface textarea, #add-for-review").evaluateAll((controls) => controls.filter((control) => !control.disabled).length) === 0;
        await recentLens.dispatchEvent("click");
        await principal.page.locator("#add-for-review").dispatchEvent("click");
        await principal.page.locator('[data-triage-item="gate7-visual-triage-task"] form').dispatchEvent("submit");
        await principal.page.locator("[data-triage-more]").dispatchEvent("click");
        const preEnrichmentReleaseCounts = await principal.page.evaluate(() => ({ ...window.__gate7FetchCounts }));
        record("Save and confirm locks Triage before enrichment and serializes the full operation", lockedBeforeEnrichment && !await principal.page.locator("#action-dialog").evaluate((dialog) => dialog.open) && await readyLens.getAttribute("aria-pressed") === "true" && await recentLens.getAttribute("aria-pressed") === "false" && preEnrichmentReleaseCounts.enrich === 1 && preEnrichmentReleaseCounts.intents === preDecisionFetchCounts.intents && preEnrichmentReleaseCounts.triageCursor === preDecisionFetchCounts.triageCursor, JSON.stringify({ lockedBeforeEnrichment, preDecisionFetchCounts, preEnrichmentReleaseCounts, url: principal.page.url() }));
        releaseTriageEnrichment();
        try {
          await principal.page.getByText("Confirming this candidate through the household gate…").waitFor();
        } catch (error) {
          throw new Error(`${error.message}\nTriage banner: ${await principal.page.locator("#state-banner").textContent()}\nEnrichment body: ${triageEnrichmentBody}`);
        }
        const enabledTriageControls = await principal.page.locator("#surface button, #surface input, #surface select, #surface textarea, #add-for-review").evaluateAll((controls) => controls.filter((control) => !control.disabled).length);
        await recentLens.dispatchEvent("click");
        await principal.page.locator("#add-for-review").dispatchEvent("click");
        const heldForm = principal.page.locator('[data-triage-item="gate7-visual-triage-task"] form');
        await heldForm.dispatchEvent("submit");
        await principal.page.locator("[data-triage-more]").dispatchEvent("click");
        const duringDecisionFetchCounts = await principal.page.evaluate(() => ({ ...window.__gate7FetchCounts }));
        const decisionIntervalStayedLocked = enabledTriageControls === 0
          && await readyLens.getAttribute("aria-pressed") === "true"
          && await recentLens.getAttribute("aria-pressed") === "false"
          && duringDecisionFetchCounts.enrich === preDecisionFetchCounts.enrich + 1
          && duringDecisionFetchCounts.intents === preDecisionFetchCounts.intents
          && duringDecisionFetchCounts.triageCursor === preDecisionFetchCounts.triageCursor;
        record("A pending decision locks every Triage control and rejects new lens, form, and pagination work", decisionIntervalStayedLocked && triageCursorRequestCount === 1, JSON.stringify({ enabledTriageControls, preDecisionFetchCounts, duringDecisionFetchCounts, triageCursorRequestCount, url: principal.page.url() }));
        releaseTriageDecision();
        await principal.page.locator("#state-banner").getByRole("link", { name: "Open Reminders" }).waitFor();
        record("Triage saves visible edits before confirmation", (await principal.page.locator("#state-banner").textContent()).includes("Confirmed and added to Reminders"), await principal.page.locator("#state-banner").textContent());
        record("Triage confirmation continues the active work queue", await principal.page.getByRole("button", { name: /^Ready/ }).getAttribute("aria-pressed") === "true" && await principal.page.getByRole("heading", { name: "Matters awaiting review" }).count() === 1 && !principal.page.url().includes("item=") && !principal.page.url().includes("matter="), principal.page.url());
        releaseStaleTriageLoad();
        await staleTriageLoadSettled;
        await principal.page.waitForFunction(() => window.__gate7FetchCounts.cursorApplicationTurns === 1);
        record("Pagination begun before a decision cannot resurrect its resolved candidate", triageCursorRequestCount === 1 && await principal.page.locator(".triage-master-button").filter({ hasText: "Send the completed enrollment form" }).count() === 0 && (await principal.page.locator("#state-banner").textContent()).includes("Confirmed and added to Reminders"), JSON.stringify({ triageCursorRequestCount, surface: await principal.page.locator("#surface").textContent() }));
        await principal.page.unroute("**/api/member/v1/surfaces/triage?*", staleTriageLoadRoute);
        await principal.page.unroute("**/api/member/v1/triage/*/approve", triageDecisionRoute);
        await principal.page.unroute("**/api/member/v1/triage/*/enrich", triageEnrichmentRoute);
        await principal.page.locator(".triage-master-button").filter({ hasText: "Review the household insurance quote" }).click();
        let releaseNoRenderLoad;
        let resolveNoRenderLoadStarted;
        let resolveNoRenderLoadSettled;
        const noRenderLoadStarted = new Promise((resolveStarted) => { resolveNoRenderLoadStarted = resolveStarted; });
        const noRenderLoadRelease = new Promise((resolveRelease) => { releaseNoRenderLoad = resolveRelease; });
        const noRenderLoadSettled = new Promise((resolveSettled) => { resolveNoRenderLoadSettled = resolveSettled; });
        let noRenderCursorRequests = 0;
        const noRenderLoadRoute = async (route) => {
          if (!new URL(route.request().url()).searchParams.has("triage_cursor")) {
            await route.continue();
            return;
          }
          noRenderCursorRequests += 1;
          resolveNoRenderLoadStarted();
          await noRenderLoadRelease;
          await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(triageInitialPayload) });
          resolveNoRenderLoadSettled();
        };
        let rejectNoRenderSave = true;
        const noRenderSaveFailureRoute = async (route) => {
          if (!rejectNoRenderSave) {
            await route.continue();
            return;
          }
          rejectNoRenderSave = false;
          expected409Console += 1;
          await route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: "Fixture rejected this enrichment before recording it." }) });
        };
        await principal.page.route("**/api/member/v1/surfaces/triage?*", noRenderLoadRoute);
        await principal.page.route("**/api/member/v1/triage/*/enrich", noRenderSaveFailureRoute);
        await principal.page.getByRole("button", { name: "Load next matters" }).click();
        await noRenderLoadStarted;
        await principal.page.getByLabel("Review title").fill("Review the household insurance quote after the broker replies");
        await principal.page.getByRole("button", { name: "Save details" }).click();
        await principal.page.getByText("The proposal was not changed.", { exact: false }).waitFor();
        const failedEnrichmentText = await principal.page.locator("#state-banner").textContent();
        const paginationNormalized = await principal.page.locator("[data-triage-more]").isEnabled()
          && await principal.page.locator("[data-triage-more]").textContent() === "Load next matters";
        await principal.page.getByRole("button", { name: /^Needs details/ }).click();
        const failureSurvivedLensChange = await principal.page.locator("#state-banner").isVisible()
          && await principal.page.locator("#state-banner").textContent() === failedEnrichmentText;
        await principal.page.getByRole("button", { name: /^Ready/ }).click();
        await principal.page.locator(".triage-master-button").filter({ hasText: "Review the household insurance quote" }).click();
        releaseNoRenderLoad();
        await noRenderLoadSettled;
        await principal.page.waitForFunction(() => window.__gate7FetchCounts.cursorApplicationTurns === 2);
        const noRenderFetchCounts = await principal.page.evaluate(() => ({ ...window.__gate7FetchCounts }));
        record("Invalidated pagination restores its semantic control state after a no-render failure", paginationNormalized && noRenderCursorRequests === 1 && noRenderFetchCounts.cursorApplicationTurns === 2 && await principal.page.locator(".triage-master-button").filter({ hasText: "Send the completed enrollment form" }).count() === 0, JSON.stringify({ paginationNormalized, noRenderCursorRequests, noRenderFetchCounts, banner: await principal.page.locator("#state-banner").textContent() }));
        record("A non-continuation Triage failure survives a lens change after prior continuation feedback", failureSurvivedLensChange, JSON.stringify({ failedEnrichmentText, banner: await principal.page.locator("#state-banner").textContent() }));
        await principal.page.evaluate(() => {
          window.fetch = window.__gate7OriginalFetch;
          delete window.__gate7OriginalFetch;
          delete window.__gate7FetchCounts;
        });
        await principal.page.unroute("**/api/member/v1/surfaces/triage?*", noRenderLoadRoute);
        await principal.page.unroute("**/api/member/v1/triage/*/enrich", noRenderSaveFailureRoute);
        let unreadyDecisionRequests = 0;
        let unreadyExactRefresh = false;
        const unreadyEnrichmentRoute = async (route) => {
          const response = await route.fetch();
          const payload = await response.json();
          payload.triage.review_state = { ...payload.triage.review_state, state: "needs_details", missing: ["due"] };
          await route.fulfill({ response, json: payload });
        };
        const unreadyDecisionRoute = async (route) => {
          unreadyDecisionRequests += 1;
          await route.continue();
        };
        const unreadySurfaceRoute = async (route) => {
          const response = await route.fetch();
          const payload = await response.json();
          const requested = new URL(route.request().url()).searchParams.get("triage_item");
          unreadyExactRefresh = requested === "gate7-visual-next-task";
          const updateCandidate = (candidate) => candidate.proposal_id === "gate7-visual-next-task"
            ? { ...candidate, review_state: { ...candidate.review_state, state: "needs_details", missing: ["due"] } }
            : candidate;
          payload.items = (payload.items || []).filter((candidate) => unreadyExactRefresh || candidate.proposal_id !== "gate7-visual-next-task").map(updateCandidate);
          payload.triage.matters = (payload.triage.matters || []).map((matter) => ({
            ...matter,
            candidates: (matter.candidates || []).filter((candidate) => unreadyExactRefresh || candidate.proposal_id !== "gate7-visual-next-task").map(updateCandidate),
          })).filter((matter) => matter.candidates.length > 0);
          await route.fulfill({ response, json: payload });
        };
        await principal.page.route("**/api/member/v1/triage/*/enrich", unreadyEnrichmentRoute);
        await principal.page.route("**/api/member/v1/triage/*/approve", unreadyDecisionRoute);
        await principal.page.route("**/api/member/v1/surfaces/triage?*", unreadySurfaceRoute);
        await principal.page.getByLabel("Review title").fill("Review the household insurance quote after a readiness check");
        await principal.page.getByRole("button", { name: "Save & confirm" }).click();
        await principal.page.getByText("This candidate needs more review before it can be confirmed.", { exact: false }).waitFor();
        const readinessTitle = await principal.page.getByLabel("Review title").inputValue();
        record("Save and confirm reconciles changed readiness and exact off-page identity before attempting a decision", unreadyDecisionRequests === 0 && unreadyExactRefresh && readinessTitle.includes("readiness check") && await principal.page.locator('[data-triage-item="gate7-visual-next-task"]').count() === 1 && await principal.page.getByRole("button", { name: /^Needs details/ }).getAttribute("aria-pressed") === "true", JSON.stringify({ unreadyDecisionRequests, unreadyExactRefresh, readinessTitle, url: principal.page.url(), banner: await principal.page.locator("#state-banner").textContent() }));
        await principal.page.unroute("**/api/member/v1/triage/*/enrich", unreadyEnrichmentRoute);
        await principal.page.unroute("**/api/member/v1/triage/*/approve", unreadyDecisionRoute);
        await principal.page.unroute("**/api/member/v1/surfaces/triage?*", unreadySurfaceRoute);
        await principal.page.goto(`${server.baseUrl}/member/triage?view=ready`);
        await principal.page.getByRole("heading", { name: "Triage", level: 1 }).waitFor();
        await principal.page.locator(".triage-master-button").filter({ hasText: "Review the household insurance quote" }).click();
        let rejectedSavedDecision = false;
        let rejectedExactRefresh = false;
        const savedDecisionFailureRoute = async (route) => {
          if (!rejectedSavedDecision) {
            rejectedSavedDecision = true;
            expected409Console += 1;
            await route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: "Fixture rejected this decision after saving its details." }) });
            return;
          }
          await route.continue();
        };
        const savedDecisionRefreshRoute = async (route) => {
          const response = await route.fetch();
          const payload = await response.json();
          const requestedExact = new URL(route.request().url()).searchParams.get("triage_item") === "gate7-visual-next-task";
          rejectedExactRefresh ||= requestedExact;
          if (!requestedExact) {
            payload.items = (payload.items || []).filter((candidate) => candidate.proposal_id !== "gate7-visual-next-task");
            payload.triage.matters = (payload.triage.matters || []).map((matter) => ({
              ...matter,
              candidates: (matter.candidates || []).filter((candidate) => candidate.proposal_id !== "gate7-visual-next-task"),
            })).filter((matter) => matter.candidates.length > 0);
          }
          await route.fulfill({ response, json: payload });
        };
        await principal.page.route("**/api/member/v1/triage/*/approve", savedDecisionFailureRoute);
        await principal.page.route("**/api/member/v1/surfaces/triage?*", savedDecisionRefreshRoute);
        const savedTitle = "Review the updated household insurance quote";
        await principal.page.getByLabel("Review title").fill(savedTitle);
        await principal.page.getByRole("button", { name: "Save & confirm" }).click();
        await principal.page.getByText("Nothing was confirmed or dismissed.", { exact: false }).waitFor();
        const retainedSavedTitle = await principal.page.getByLabel("Review title").inputValue();
        await principal.page.getByRole("button", { name: "Confirm and route" }).click();
        await principal.page.getByText("Confirmed and added to Reminders", { exact: false }).waitFor();
        record("A decision failure after enrichment preserves exact off-page input and retries its current revision", retainedSavedTitle === savedTitle && rejectedSavedDecision && rejectedExactRefresh && await principal.page.getByRole("button", { name: /^Ready/ }).getAttribute("aria-pressed") === "true" && await principal.page.locator(".triage-master-button").filter({ hasText: savedTitle }).count() === 0, JSON.stringify({ retainedSavedTitle, rejectedSavedDecision, rejectedExactRefresh, url: principal.page.url(), banner: await principal.page.locator("#state-banner").textContent() }));
        await principal.page.unroute("**/api/member/v1/triage/*/approve", savedDecisionFailureRoute);
        await principal.page.unroute("**/api/member/v1/surfaces/triage?*", savedDecisionRefreshRoute);
        await principal.page.locator(".triage-master-button").filter({ hasText: "Ignore the promotional digest" }).click();
        await principal.page.getByLabel("Why this candidate should leave Triage").selectOption("spam");
        await principal.page.getByRole("button", { name: "Dismiss & teach" }).click();
        await principal.page.getByText("Your reversible feedback was recorded. The next decision in this view is ready.").waitFor();
        record("Triage dismissal records feedback without leaving the work queue", await principal.page.getByRole("button", { name: /^Ready/ }).getAttribute("aria-pressed") === "true" && await principal.page.locator(".triage-master-button").filter({ hasText: "Ignore the promotional digest" }).count() === 0 && await principal.page.locator(".triage-master-button[aria-current=true]").count() === 1 && !principal.page.url().includes("item=") && !principal.page.url().includes("matter="), principal.page.url());
        const continuationBannerText = await principal.page.locator("#state-banner").textContent();
        await principal.page.getByRole("button", { name: /^Ready/ }).click();
        record("Selecting the active Triage lens preserves its continuation feedback", await principal.page.locator("#state-banner").textContent() === continuationBannerText && await principal.page.locator("#state-banner").isVisible(), await principal.page.locator("#state-banner").textContent());
        await screenshot(principal.page, "surface-triage-queue-continuity-desktop.png");
        const bootstrap = await jsonRequest(server.baseUrl, "/api/member/v1/bootstrap", identities.principal);
        const triageAfterConfirmation = await jsonRequest(server.baseUrl, "/api/member/v1/surfaces/triage", identities.principal);
        const recentTaskDecision = triageAfterConfirmation.value.recent_items?.find((value) => value.title === "Send the completed enrollment form");
        record("Triage keeps the selected member bound to the signed-in identity", bootstrap.response.ok && recentTaskDecision?.route?.member_id === bootstrap.value.member.id, JSON.stringify({ member: bootstrap.value.member, route: recentTaskDecision?.route }));
        const materializedEvidence = recentTaskDecision?.materialized_event_ids?.[0]
          ? await jsonRequest(server.baseUrl, `/api/member/v1/evidence/${encodeURIComponent(recentTaskDecision.materialized_event_ids[0])}`, identities.principal)
          : { response: { ok: false }, value: { missing: "materialized event id" } };
        const routedReminders = await jsonRequest(server.baseUrl, "/api/member/v1/surfaces/reminders", identities.principal);
        const routedTaskRecord = routedReminders.value.cockpit?.tasks?.find((value) => value.title === "Send the completed enrollment form");
        record("Triage receipt is destination-readable before return", routedReminders.response.ok && materializedEvidence.response.ok && Boolean(routedTaskRecord), JSON.stringify({ recent: recentTaskDecision, evidence: materializedEvidence.value, reminders: routedReminders.value }));
        routedTaskBucket = routedTaskRecord.bucket;
        await principal.page.getByRole("button", { name: /^Recent/ }).click();
        const recentBannerText = await principal.page.locator("#state-banner").textContent();
        record("Changing Triage lenses clears view-relative continuation feedback", await principal.page.locator("#state-banner").isHidden() && !recentBannerText.includes("next decision in this view"), recentBannerText);
        await principal.page.locator(".triage-master-button").filter({ hasText: "Archive the outdated enrollment reminder" }).click();
        const eligibilityReceipt = await principal.page.locator(".triage-detail").filter({ hasText: "Archive the outdated enrollment reminder" }).textContent();
        record("Recent distinguishes eligibility replacement from learned sender rules without inventing provenance", eligibilityReceipt.includes("Retired after context or eligibility changed") && eligibilityReceipt.includes("historical lifecycle evidence") && eligibilityReceipt.includes("not a sender-wide rule created by this dismissal") && (await principal.page.locator(".triage-master").textContent()).includes("Read-only audit history"), eligibilityReceipt);
        await screenshot(principal.page, "surface-triage-eligibility-replacement-desktop.png");
        await principal.page.locator(".triage-master-button").filter({ hasText: "Send the completed enrollment form" }).click();
        const destinationLink = principal.page.locator(".triage-detail").getByRole("link", { name: "Open Reminders" });
        routedTaskHref = await destinationLink.getAttribute("href") || "";
        record("Recent decision retains destination receipt", await destinationLink.count() === 1 && routedTaskHref.includes("item=task%3A"), await principal.page.locator(".triage-detail").textContent());
        await screenshot(principal.page, "surface-triage-routed-recent-desktop.png");
        const needsDetails = principal.page.getByRole("button", { name: /^Needs details/ });
        await needsDetails.click();
        await principal.page.waitForFunction(() => [...document.querySelectorAll(".comms-filter")].some((node) => node.textContent?.startsWith("Needs details") && node.getAttribute("aria-pressed") === "true"));
        await principal.page.locator(".triage-master-button").filter({ hasText: "Renew the annual registration" }).click();
        record("Google Tasks uses only its reviewed date capability", await principal.page.getByLabel("Scheduled date date").count() === 1 && await principal.page.getByLabel("Scheduled date time (optional)").count() === 0 && await principal.page.getByText("Available from", { exact: true }).count() === 0 && (await principal.page.locator(".triage-detail").textContent()).includes("discards due time"), await principal.page.locator(".triage-detail").textContent());
        await screenshot(principal.page, "surface-triage-google-tasks-desktop.png");
      } else if (surface === "comms") {
        record("Comms identifies deterministic private focus", await principal.page.locator("#mode-label").textContent() === "Private · deterministic focus", await principal.page.locator("#mode-label").textContent());
        record("Comms renders one member-bound account and thread", await principal.page.locator(".account-filters .account-filter").count() === 2 && await principal.page.locator(".comms-thread").count() === 1, await principal.page.locator("#surface").textContent());
        record("Comms explains focus without a model", (await principal.page.locator(".focus-reason").textContent()).includes("Why it is here"), await principal.page.locator(".focus-reason").textContent());
        record("Comms reply requires independent approval", (await principal.page.locator(".comms-compose").textContent()).includes("pending provider action") && await principal.page.getByRole("button", { name: "Save reply for approval" }).count() === 1, await principal.page.locator(".comms-compose").textContent());
        await screenshot(principal.page, "surface-comms-focus-desktop.png");
        await principal.page.getByRole("button", { name: /^Quieted/ }).click();
        record("Comms quiet lens is interactive", (await principal.page.locator(".comms-list").textContent()).includes("Nothing in this lens"), await principal.page.locator(".comms-list").textContent());
        await principal.page.getByRole("button", { name: /^Focus/ }).click();
      } else if (surface === "reminders") {
        record("Reminders identifies typed local and provider truth", await principal.page.locator("#mode-label").textContent() === "Private · canonical tasks + provider truth · deterministic", await principal.page.locator("#mode-label").textContent());
        record("Reminders exposes stable activation lenses", await principal.page.getByRole("button", { name: /^Now/ }).count() === 1 && await principal.page.getByRole("button", { name: /^Places/ }).count() === 1, await principal.page.locator("#surface").textContent());
        record("Reminders projects direct canonical tasks without provider claims", await principal.page.locator(".reminder-item").filter({ hasText: "Return the signed school form" }).count() === 1 && await principal.page.locator(".task-quick-add").count() === 1, await principal.page.locator("#surface").textContent());
        const routedLens = routedTaskBucket === "now" ? "Now" : routedTaskBucket === "anytime" ? "Anytime" : "Scheduled";
        await principal.page.getByRole("button", { name: new RegExp(`^${routedLens}`) }).click();
        const routedTask = principal.page.locator(".reminder-item").filter({ hasText: "Send the completed enrollment form" });
        await routedTask.click();
        record("Reminders receives the enriched Triage task", await routedTask.count() === 1 && (await principal.page.locator(".task-detail").textContent()).includes("Open the enrollment portal and attach the signed form"), await principal.page.locator("#surface").textContent());
        record("Reminders translates the internal task lifecycle into member language", (await principal.page.locator(".task-detail").textContent()).includes("StatusOpen") && !(await principal.page.locator(".task-detail").textContent()).includes("StatusInbox"), await principal.page.locator(".task-detail").textContent());
        await screenshot(principal.page, "surface-reminders-routed-task-desktop.png");
        await principal.page.goto(`${server.baseUrl}${routedTaskHref}`);
        await principal.page.locator(".task-detail").waitFor();
        record("Triage receipt deep-link opens its exact Reminders task", (await principal.page.getByRole("button", { name: new RegExp(`^${routedLens}`) }).getAttribute("aria-pressed")) === "true" && (await principal.page.locator(".task-detail").textContent()).includes("Send the completed enrollment form"), principal.page.url());
        await principal.page.getByRole("button", { name: /^Now/ }).click();
        record("Reminders does not invent a writable provider", (await principal.page.locator(".reminder-quick-add").textContent()).includes("Connect a writable"), await principal.page.locator(".reminder-quick-add").textContent());
        await screenshot(principal.page, "surface-reminders-desktop.png");
      } else if (surface === "calendar") {
        record("Calendar identifies deterministic policy", await principal.page.locator("#mode-label").textContent() === "Private · deterministic calendar policy", await principal.page.locator("#mode-label").textContent());
        record("Calendar defaults to a spatial Week", await principal.page.locator(".calendar-time-grid").count() === 1 && await principal.page.getByRole("button", { name: "Week" }).getAttribute("aria-pressed") === "true", await principal.page.locator("#surface").textContent());
        record("Calendar exposes source and capacity lenses", await principal.page.locator(".calendar-source-filter").count() === 2 && await principal.page.getByRole("button", { name: "Capacity colors" }).getAttribute("aria-pressed") === "true", await principal.page.locator(".calendar-source-panel").textContent());
        record("Calendar separates all-day and timed events", await principal.page.locator(".calendar-all-day-cell .calendar-event-block").count() === 1 && await principal.page.locator(".calendar-day-lane .calendar-event-block").count() === 1, await principal.page.locator(".calendar-board").textContent());
        const hardCount = await principal.page.locator(".calendar-stat-hard dd").textContent();
        const householdVisibility = principal.page.locator(".calendar-source-filter input").nth(1);
        await householdVisibility.uncheck();
        record("Calendar visibility is view-only", await principal.page.locator(".calendar-all-day-cell .calendar-event-block").count() === 0 && await principal.page.locator(".calendar-stat-hard dd").textContent() === hardCount, await principal.page.locator(".calendar-source-panel").textContent());
        await householdVisibility.check();
        await principal.page.locator(".calendar-day-lane .calendar-event-block").click();
        record("Calendar detail preserves timezone and buffer provenance", (await principal.page.locator(".calendar-event-detail").textContent()).includes("1:00 PM") && (await principal.page.locator(".calendar-event-detail").textContent()).includes("30 min before · 15 min after"), await principal.page.locator(".calendar-event-detail").textContent());
        await screenshot(principal.page, "surface-calendar-week-desktop.png");
        await principal.page.getByRole("button", { name: "Month" }).click();
        await principal.page.locator(".calendar-month-grid").waitFor();
        record("Calendar Month is an anchored six-week grid", await principal.page.locator(".calendar-month-day").count() === 42, await principal.page.locator(".calendar-range-title").textContent());
        await screenshot(principal.page, "surface-calendar-month-desktop.png");
        await principal.page.getByRole("button", { name: "Agenda" }).click();
        await principal.page.locator(".calendar-agenda").waitFor();
        record("Calendar Agenda remains available", await principal.page.locator(".calendar-agenda-day").count() >= 1, await principal.page.locator(".calendar-agenda").textContent());
        await principal.page.getByRole("button", { name: "Find time" }).click();
        await principal.page.locator(".calendar-find").waitFor();
        record("Find time offers explicit duration", await principal.page.getByRole("button", { name: "60 min" }).getAttribute("aria-pressed") === "true", await principal.page.locator(".calendar-find").textContent());
        await principal.page.getByRole("button", { name: "Calendar settings" }).click();
        await principal.page.locator(".calendar-policy-overview").waitFor();
        record("Calendar policy editing is progressively disclosed", await principal.page.locator(".calendar-rule-disclosure").getAttribute("open") === null && await principal.page.getByText("Protected morning", { exact: true }).count() === 1, await principal.page.locator("#surface").textContent());
      } else if (surface === "notes") {
        record("Notes identifies private authorized recall", await principal.page.locator("#mode-label").textContent() === "Private · authorized recall · deterministic Triage", await principal.page.locator("#mode-label").textContent());
        record("Notes preserves provider sources and master list", await principal.page.locator(".notes-source-button").count() === 3 && await principal.page.locator(".notes-master-button").count() === 2, await principal.page.locator("#surface").textContent());
        await principal.page.locator(".notes-master-button").filter({ hasText: "School planning call" }).click();
        record("Notes detail retains exact source identity", (await principal.page.locator(".notes-detail").textContent()).includes("Meeting notes"), await principal.page.locator(".notes-detail").textContent());
        const noteTriageHref = await principal.page.getByRole("link", { name: "Review 1 in Triage" }).getAttribute("href") || "";
        record("Notes links exact candidates to Triage without inline approval", noteTriageHref.includes("item=") && noteTriageHref.includes("matter=") && noteTriageHref.includes("view=all_candidates") && await principal.page.getByRole("button", { name: "Confirm and route" }).count() === 0, noteTriageHref);
        record("Notes sharing is explicit and read-search only", await principal.page.locator(".notes-share-form select").count() === 1 && await principal.page.locator(".notes-share-form input").inputValue() === "Read and search" && await principal.page.getByRole("button", { name: "Share note" }).count() === 1, await principal.page.locator(".notes-sharing").textContent());
        await principal.page.locator(".notes-search input").fill("autumn pickup");
        await principal.page.getByRole("button", { name: "Search", exact: true }).click();
        await principal.page.waitForFunction(() => document.querySelectorAll(".notes-master-button").length === 1 && document.querySelector(".notes-master")?.textContent?.includes("autumn pickup"));
        record("Notes deterministic search is interactive", await principal.page.locator(".notes-master-button").count() === 1 && (await principal.page.locator(".notes-master").textContent()).includes("autumn pickup"), await principal.page.locator(".notes-master").textContent());
        await principal.page.reload();
        await principal.page.locator(".notes-master-button").waitFor();
        record("Notes search state survives a URL reload", await principal.page.locator(".notes-master-button").count() === 1 && await principal.page.locator(".notes-search input").inputValue() === "autumn pickup", principal.page.url());
        await principal.page.getByRole("button", { name: "Clear", exact: true }).click();
        await principal.page.getByRole("button", { name: "Meetings", exact: true }).click();
        record("Notes meeting lens is presentation-only", await principal.page.locator(".notes-master-button").count() === 1, await principal.page.locator(".notes-master").textContent());
        await screenshot(principal.page, "surface-notes-meetings-desktop.png");
      } else if (surface === "people") {
        record("People identifies deterministic confirmed identity", await principal.page.locator("#mode-label").textContent() === "Private · confirmed identity · deterministic cues", await principal.page.locator("#mode-label").textContent());
        await screenshot(principal.page, "surface-people-desktop.png");
      } else if (surface === "finance") {
        record("Finance identifies deterministic planning and no bank writes", await principal.page.locator("#mode-label").textContent() === "Private · deterministic planning · no bank writes", await principal.page.locator("#mode-label").textContent());
        record("Finance leads with cash safety and one explained review", await principal.page.locator(".finance-hero-stats dd").count() === 4 && (await principal.page.locator(".finance-hero").textContent()).includes("Review Groceries"), await principal.page.locator(".finance-hero").textContent());
        record("Finance compares explicit scenarios without changing baseline", await principal.page.locator(".finance-scenario-button").count() === 2 && await principal.page.locator(".finance-scenario-button").first().getAttribute("aria-pressed") === "true", await principal.page.locator(".finance-scenarios").textContent());
        record("Finance shows goals as planning without moving money", (await principal.page.locator(".finance-goals").textContent()).includes("never move funds") && await principal.page.locator(".finance-goal-list li").count() === 1, await principal.page.locator(".finance-goals").textContent());
        await screenshot(principal.page, "surface-finance-overview-desktop.png");
        await principal.page.getByRole("button", { name: "Spending", exact: true }).click();
        record("Finance Spending shows categorized provider movement", (await principal.page.locator(".finance-transactions").textContent()).includes("Neighborhood market") && (await principal.page.locator(".finance-budget").textContent()).includes("Review"), await principal.page.locator("#surface").textContent());
        await principal.page.reload();
        record("Finance view survives URL reload", await principal.page.getByRole("button", { name: "Spending", exact: true }).getAttribute("aria-pressed") === "true", principal.page.url());
        await principal.page.getByRole("button", { name: "Data health", exact: true }).click();
        record("Finance data health exposes source and workbook truth", (await principal.page.locator(".finance-health").textContent()).includes("Latest source") && (await principal.page.locator(".finance-health").textContent()).includes("Plaid owns provider facts"), await principal.page.locator(".finance-health").textContent());
        await screenshot(principal.page, "surface-finance-health-desktop.png");
      } else if (surface === "scoreboard") {
        const scoreboardText = await principal.page.locator("#surface").textContent();
        record("Scoreboard receives the confirmed canonical task", scoreboardText.includes("Send the completed enrollment form"), scoreboardText);
        record("Scoreboard presents the internal task inbox status as Open", scoreboardText.includes("Open") && !scoreboardText.includes("Inbox"), scoreboardText);
        const scoreboardTaskLink = principal.page.locator(".item").filter({ hasText: "Send the completed enrollment form" }).getByRole("link", { name: "Open item" });
        record("Scoreboard next steps retain an exact Reminders destination", await scoreboardTaskLink.count() === 1 && (await scoreboardTaskLink.getAttribute("href") || "").includes("/member/reminders?item=task%3A"), await scoreboardTaskLink.getAttribute("href"));
        await screenshot(principal.page, "surface-scoreboard-desktop.png");
      } else if (surface === "settings") {
        record("Settings identifies the deterministic control plane", await principal.page.locator("#mode-label").textContent() === "Private · deterministic control plane", await principal.page.locator("#mode-label").textContent());
        record("Settings exposes all nine control areas", await principal.page.locator(".settings-nav button").count() === 9, await principal.page.locator(".settings-nav").textContent());
        record("Settings separates read identity from action identity", (await principal.page.locator(".settings-identity").textContent()).includes("Authenticated") && (await principal.page.locator(".settings-identity").textContent()).includes("Actions execute as"), await principal.page.locator(".settings-identity").textContent());
        await principal.page.getByRole("button", { name: "Connections", exact: true }).click();
        record("Settings connection truth explains independent axes", (await principal.page.locator("#surface").textContent()).includes("Connection truth has four separate axes"), await principal.page.locator("#surface").textContent());
        await principal.page.reload();
        await principal.page.locator(".settings-nav").waitFor();
        record("Settings control area survives URL reload", await principal.page.getByRole("button", { name: "Connections", exact: true }).getAttribute("aria-pressed") === "true", principal.page.url());
        await principal.page.getByRole("button", { name: "CoS behavior", exact: true }).click();
        record("Settings offers only adult support profiles", await principal.page.locator(".profile-choice").count() === 3, await principal.page.locator(".profile-grid").textContent());
        await screenshot(principal.page, "surface-settings-behavior-desktop.png");
      } else {
        record(`${surface} member context`, await principal.page.locator("#mode-label").textContent() === "Private member view", await principal.page.locator("#mode-label").textContent());
        await screenshot(principal.page, `surface-${surface}-desktop.png`);
      }
    }
    record("ten principal navigation items", await principal.page.locator("#primary-nav .nav-link").count() === 10, await principal.page.locator("#primary-nav .nav-link").count());
    await auditAccessibility(principal.page, "desktop");
    await visit(principal.page, server.baseUrl, "/member/triage?view=ready", "Triage");
    await principal.page.locator(".triage-master-button").filter({ hasText: "Attach the insurance note to its existing task" }).click();
    const dispositionDecisionRoute = async (route) => {
      expected409Console += 1;
      await route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: "Fixture changed this candidate's disposition before recording the decision." }) });
    };
    const dispositionSurfaceRoute = async (route) => {
      const response = await route.fetch();
      const payload = await response.json();
      const updateCandidate = (candidate) => candidate.proposal_id === "gate7-visual-disposition-task"
        ? { ...candidate, decision_disposition: "evidence_only", review_state: { ...candidate.review_state, state: "ready" } }
        : candidate;
      payload.items = (payload.items || []).map(updateCandidate);
      payload.triage.matters = (payload.triage.matters || []).map((matter) => ({
        ...matter,
        candidates: (matter.candidates || []).map(updateCandidate),
      }));
      await route.fulfill({ response, json: payload });
    };
    await principal.page.route("**/api/member/v1/triage/gate7-visual-disposition-task/approve", dispositionDecisionRoute);
    await principal.page.route("**/api/member/v1/surfaces/triage?*", dispositionSurfaceRoute);
    await principal.page.getByRole("button", { name: "Confirm and route" }).click();
    await principal.page.getByText("Nothing was confirmed or dismissed.", { exact: false }).waitFor();
    record("Exact reconciliation keeps a non-decision candidate visible without substituting or permitting duplicate truth", await principal.page.getByRole("button", { name: /^All candidates/ }).getAttribute("aria-pressed") === "true" && principal.page.url().includes("item=gate7-visual-disposition-task") && await principal.page.locator('[data-triage-item="gate7-visual-disposition-task"]').filter({ hasText: "Attach the insurance note to its existing task" }).count() === 1 && await principal.page.getByRole("button", { name: "Confirm and route" }).isDisabled() && (await principal.page.locator(".triage-detail").textContent()).includes("supporting evidence"), JSON.stringify({ url: principal.page.url(), surface: await principal.page.locator("#surface").textContent() }));
    await principal.page.unroute("**/api/member/v1/triage/gate7-visual-disposition-task/approve", dispositionDecisionRoute);
    await principal.page.unroute("**/api/member/v1/surfaces/triage?*", dispositionSurfaceRoute);
    await principal.page.goto(`${server.baseUrl}/member/triage?view=ready`);
    await principal.page.locator(".triage-master-button").filter({ hasText: "Reconcile the guarded camp form" }).click();
    const guardedArm = await jsonRequest(server.baseUrl, "/__gate7/fail-next-triage-materialization", identities.principal, "POST");
    const guardedRequestIds = [];
    const guardedDecisionRoute = async (route) => {
      guardedRequestIds.push(JSON.parse(route.request().postData() || "{}").request_id);
      await route.continue();
    };
    expected409Console += 1;
    await principal.page.route("**/api/member/v1/triage/gate7-visual-guarded-task/approve", guardedDecisionRoute);
    await principal.page.getByRole("button", { name: "Confirm and route" }).click();
    await principal.page.getByText("Confirmed and added to Reminders", { exact: false }).waitFor();
    const guardedSurface = await jsonRequest(server.baseUrl, "/api/member/v1/surfaces/triage", identities.principal);
    const guardedReceipt = guardedSurface.value.recent_items?.find((item) => item.proposal_id === "gate7-visual-guarded-task");
    record("A post-confirmation guard refusal resumes with one idempotency key until local materialization is receipted", guardedArm.response.ok && guardedRequestIds.length === 2 && guardedRequestIds[0] === guardedRequestIds[1] && guardedReceipt?.state === "confirmed" && guardedReceipt?.resolution_reason === "confirmed_local" && guardedReceipt?.materialized_event_ids?.length === 1, JSON.stringify({ guardedRequestIds, guardedReceipt }));
    await principal.page.unroute("**/api/member/v1/triage/gate7-visual-guarded-task/approve", guardedDecisionRoute);
    await principal.page.goto(`${server.baseUrl}/member/triage?view=ready`);
    await principal.page.locator(".triage-master-button").filter({ hasText: "Verify the summer camp packing list" }).click();
    let ambiguousCommitStatus = 0;
    const ambiguousDecisionRoute = async (route) => {
      const response = await route.fetch();
      ambiguousCommitStatus = response.status();
      expected503Console += 1;
      await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Fixture interrupted the response after commit." }) });
    };
    await principal.page.route("**/api/member/v1/triage/gate7-visual-ambiguous-task/approve", ambiguousDecisionRoute);
    await principal.page.getByRole("button", { name: "Confirm and route" }).click();
    await principal.page.getByText("response was interrupted, but Triage reconciled this exact candidate as confirmed", { exact: false }).waitFor();
    record("A lost confirmation response reconciles the exact terminal receipt without claiming rejection", ambiguousCommitStatus === 200 && await principal.page.getByRole("button", { name: /^Recent/ }).getAttribute("aria-pressed") === "true" && principal.page.url().includes("item=gate7-visual-ambiguous-task") && await principal.page.locator('[data-triage-item="gate7-visual-ambiguous-task"]').filter({ hasText: "Verify the summer camp packing list" }).count() === 1 && !(await principal.page.locator("#state-banner").textContent()).includes("Nothing was confirmed"), JSON.stringify({ ambiguousCommitStatus, url: principal.page.url(), banner: await principal.page.locator("#state-banner").textContent() }));
    await principal.page.unroute("**/api/member/v1/triage/gate7-visual-ambiguous-task/approve", ambiguousDecisionRoute);
    await principal.page.goto(`${server.baseUrl}/member/triage?view=ready`);
    await principal.page.locator(".triage-master-button").filter({ hasText: "Review the caregiver authorization receipt" }).click();
    const mismatchedBrowserRequestIds = [];
    let mismatchedOtherStatus = 0;
    const mismatchedReceiptRoute = async (route) => {
      const browserBody = JSON.parse(route.request().postData() || "{}");
      mismatchedBrowserRequestIds.push(browserBody.request_id);
      const other = await jsonRequest(
        server.baseUrl,
        "/api/member/v1/triage/gate7-visual-mismatched-receipt-task/approve",
        identities.principal,
        "POST",
        { ...browserBody, request_id: "gate7-other-member-request" },
      );
      mismatchedOtherStatus = other.response.status;
      expected503Console += 1;
      await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Fixture interrupted a different member request." }) });
    };
    await principal.page.route("**/api/member/v1/triage/gate7-visual-mismatched-receipt-task/approve", mismatchedReceiptRoute);
    await principal.page.getByRole("button", { name: "Confirm and route" }).click();
    await principal.page.getByText("could not prove an action-specific receipt", { exact: false }).waitFor();
    record("A terminal receipt from another request cannot settle the browser's interrupted decision", mismatchedOtherStatus === 200 && mismatchedBrowserRequestIds.length === 2 && mismatchedBrowserRequestIds[0] === mismatchedBrowserRequestIds[1] && (await principal.page.locator("#state-banner").textContent()).includes("Reload this page before continuing") && await principal.page.getByRole("button", { name: /^Ready/ }).isDisabled(), JSON.stringify({ mismatchedOtherStatus, mismatchedBrowserRequestIds, banner: await principal.page.locator("#state-banner").textContent() }));
    await principal.page.unroute("**/api/member/v1/triage/gate7-visual-mismatched-receipt-task/approve", mismatchedReceiptRoute);
    await principal.page.goto(`${server.baseUrl}/member/triage?view=ready`);
    await principal.page.locator(".triage-master-button").filter({ hasText: "Review the system-transition dismissal" }).click();
    await principal.page.getByLabel("Why this candidate should leave Triage").selectOption("not_actionable");
    const systemDismissDecisionRoute = async (route) => {
      expected503Console += 1;
      await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Fixture interrupted dismissal while extraction changed state." }) });
    };
    const systemDismissSurfaceRoute = async (route) => {
      const response = await route.fetch();
      const payload = await response.json();
      const updateCandidate = (candidate) => candidate.proposal_id === "gate7-visual-system-dismiss-task"
        ? { ...candidate, state: "dismissed", resolution_reason: "candidates_extracted", dismissal_event_id: null, dismissal_request_id: null }
        : candidate;
      payload.items = (payload.items || []).map(updateCandidate);
      payload.recent_items = (payload.recent_items || []).map(updateCandidate);
      payload.triage.matters = (payload.triage.matters || []).map((matter) => ({
        ...matter,
        candidates: (matter.candidates || []).map(updateCandidate),
      }));
      await route.fulfill({ response, json: payload });
    };
    await principal.page.route("**/api/member/v1/triage/gate7-visual-system-dismiss-task/dismiss", systemDismissDecisionRoute);
    await principal.page.route("**/api/member/v1/surfaces/triage?*", systemDismissSurfaceRoute);
    await principal.page.getByRole("button", { name: "Dismiss & teach" }).click();
    await principal.page.getByText("could not prove an action-specific receipt", { exact: false }).waitFor();
    record("A system dismissal cannot impersonate the member's interrupted dismissal receipt", (await principal.page.locator("#state-banner").textContent()).includes("Reload this page before continuing") && !(await principal.page.locator("#state-banner").textContent()).includes("reconciled this exact candidate as dismissed") && await principal.page.getByRole("button", { name: /^Ready/ }).isDisabled(), await principal.page.locator("#state-banner").textContent());
    await principal.page.unroute("**/api/member/v1/triage/gate7-visual-system-dismiss-task/dismiss", systemDismissDecisionRoute);
    await principal.page.unroute("**/api/member/v1/surfaces/triage?*", systemDismissSurfaceRoute);
    await principal.context.close();

    const principalPhone = await newContext(browser, identities.principal, { width: 390, height: 844 }, "reduce");
    for (const [surface, label] of Object.entries(surfaceLabels)) {
      await visit(principalPhone.page, server.baseUrl, `/member/${surface}`, label);
      if (surface === "triage") {
        const triagePhoneMetrics = await principalPhone.page.evaluate(() => ({ viewport: window.innerWidth, document: document.documentElement.scrollWidth }));
        record("Triage phone has no horizontal document overflow", triagePhoneMetrics.document <= triagePhoneMetrics.viewport, JSON.stringify(triagePhoneMetrics));
        await principalPhone.page.getByRole("button", { name: /^Needs details/ }).click();
        await principalPhone.page.locator(".triage-master-button").filter({ hasText: "Renew the annual registration" }).click();
        record("Triage phone retains the provider capability ceiling", await principalPhone.page.getByLabel("Scheduled date date").count() === 1 && await principalPhone.page.getByLabel("Scheduled date time (optional)").count() === 0 && (await principalPhone.page.locator(".triage-detail").textContent()).includes("discards due time"), await principalPhone.page.locator(".triage-detail").textContent());
      } else if (surface === "comms") {
        record("Comms phone retains focus explanation and reply approval", await principalPhone.page.locator(".focus-reason").count() === 1 && await principalPhone.page.getByRole("button", { name: "Save reply for approval" }).count() === 1, await principalPhone.page.locator("#surface").textContent());
      } else if (surface === "reminders") {
        record("Reminders phone retains typed task controls", await principalPhone.page.locator(".reminder-item").filter({ hasText: "Return the signed school form" }).count() === 1 && await principalPhone.page.locator(".task-quick-add").count() === 1, await principalPhone.page.locator("#surface").textContent());
      } else if (surface === "notes") {
        record("Notes phone retains sources, search, and source evidence", await principalPhone.page.locator(".notes-source-button").count() === 3 && await principalPhone.page.locator(".notes-search input").count() === 1 && await principalPhone.page.getByRole("button", { name: "View source evidence" }).count() === 1, await principalPhone.page.locator("#surface").textContent());
      } else if (surface === "settings") {
        record("Settings phone retains all control areas", await principalPhone.page.locator(".settings-nav button").count() === 9, await principalPhone.page.locator(".settings-nav").textContent());
        await principalPhone.page.getByRole("button", { name: "Connections", exact: true }).click();
        record("Settings phone retains connection truth", (await principalPhone.page.locator("#surface").textContent()).includes("Connection truth has four separate axes"), await principalPhone.page.locator("#surface").textContent());
      }
      await auditAccessibility(principalPhone.page, `narrow ${surface}`);
      const screenshotName = surface === "comms" ? "surface-comms-focus-narrow.png" : surface === "settings" ? "surface-settings-connections-narrow.png" : `surface-${surface}-narrow.png`;
      await screenshot(principalPhone.page, screenshotName);
    }
    await principalPhone.context.close();

    const adult = await newContext(browser, identities.adult, { width: 820, height: 1180 }, "reduce");
    await adult.page.goto(`${server.baseUrl}/member/today`);
    await adult.page.getByRole("heading", { name: "Today", level: 1 }).waitFor();
    const intentArm = await jsonRequest(server.baseUrl, "/__gate7/fail-next-intent-after-capture", identities.adult, "POST");
    let releaseTodayIntent;
    let resolveTodayIntentStarted;
    const todayIntentStarted = new Promise((resolveStarted) => { resolveTodayIntentStarted = resolveStarted; });
    const todayIntentRelease = new Promise((resolveRelease) => { releaseTodayIntent = resolveRelease; });
    const todayIntentRequestIds = [];
    const todayIntentRoute = async (route) => {
      const body = JSON.parse(route.request().postData() || "{}");
      todayIntentRequestIds.push(body.client_request_id);
      if (todayIntentRequestIds.length === 1) {
        resolveTodayIntentStarted();
        await todayIntentRelease;
      }
      await route.continue();
    };
    await adult.page.route("**/api/member/v1/triage/intents", todayIntentRoute);
    await adult.page.locator("#add-for-review").click();
    const todayIntentText = "Maybe something about the school folder";
    await adult.page.getByLabel("Thought to hold").fill(todayIntentText);
    await adult.page.locator(".add-for-review-form").getByRole("button", { name: "Add for review" }).click();
    await todayIntentStarted;
    await adult.page.keyboard.press("Escape");
    const todayDialogStayedOpen = await adult.page.locator("#action-dialog").evaluate((dialog) => dialog.open);
    expected503Console += 1;
    releaseTodayIntent();
    await adult.page.waitForFunction(() => !document.querySelector("#action-dialog")?.open, null, { timeout: 5_000 }).catch(() => {});
    const todayIntentBanner = await adult.page.locator("#state-banner").textContent();
    const todayDialogOpenAfterRetry = await adult.page.locator("#action-dialog").evaluate((dialog) => dialog.open);
    record("Shared Add for review preserves its form outside Triage and retries post-capture failure with one identity", intentArm.response.ok && todayDialogStayedOpen && !todayDialogOpenAfterRetry && todayIntentRequestIds.length === 2 && todayIntentRequestIds[0] === todayIntentRequestIds[1] && (todayIntentBanner.includes("saved in Triage") || todayIntentBanner.startsWith("Saved.")), JSON.stringify({ todayDialogStayedOpen, todayDialogOpenAfterRetry, todayIntentRequestIds, banner: todayIntentBanner }));
    await adult.page.unroute("**/api/member/v1/triage/intents", todayIntentRoute);

    const unknownOutcomeRequestIds = [];
    const unknownOutcomeRoute = async (route) => {
      unknownOutcomeRequestIds.push(JSON.parse(route.request().postData() || "{}").client_request_id);
      expected503Console += 1;
      await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Fixture cannot determine whether capture occurred." }) });
    };
    await adult.page.route("**/api/member/v1/triage/intents", unknownOutcomeRoute);
    await adult.page.locator("#add-for-review").click();
    await adult.page.getByLabel("Thought to hold").fill("Unknown transport outcome from Today");
    await adult.page.locator(".add-for-review-form").getByRole("button", { name: "Add for review" }).click();
    const unknownOutcomeRecovery = adult.page.getByRole("button", { name: "Check Triage before retrying" });
    await unknownOutcomeRecovery.waitFor();
    await unknownOutcomeRecovery.click();
    await adult.page.waitForURL(/\/member\/triage\?.*view=all_candidates/);
    record("Unknown Add outcome outside Triage navigates to inventory before resubmission", unknownOutcomeRequestIds.length === 2 && unknownOutcomeRequestIds[0] === unknownOutcomeRequestIds[1] && adult.page.url().includes("view=all_candidates"), JSON.stringify({ unknownOutcomeRequestIds, url: adult.page.url() }));
    await adult.page.unroute("**/api/member/v1/triage/intents", unknownOutcomeRoute);
    await adult.page.goto(`${server.baseUrl}/member/today`);
    await adult.page.getByRole("heading", { name: "Today", level: 1 }).waitFor();

    const todayProposalArm = await jsonRequest(server.baseUrl, "/__gate7/fail-next-intent-proposals", identities.adult, "POST");
    const todayRecoveryRequestIds = [];
    const todayRecoveryRoute = async (route) => {
      todayRecoveryRequestIds.push(JSON.parse(route.request().postData() || "{}").client_request_id);
      const response = await route.fetch();
      if (response.status() === 409) expected409Console += 1;
      await route.fulfill({ response });
    };
    await adult.page.route("**/api/member/v1/triage/intents", todayRecoveryRoute);
    await adult.page.locator("#add-for-review").click();
    const todayRecoveryText = "Call the school office about the caregiver authorization tomorrow";
    await adult.page.getByLabel("Thought to hold").fill(todayRecoveryText);
    await adult.page.getByLabel("Type hint (optional)").selectOption("task");
    await adult.page.locator(".add-for-review-form").getByRole("button", { name: "Add for review" }).click();
    const todayRecoveryButton = adult.page.getByRole("button", { name: "Open saved Triage item" });
    await todayRecoveryButton.waitFor();
    await todayRecoveryButton.click();
    await adult.page.waitForURL(/\/member\/triage\?.*view=needs_interpretation.*item=intent_/);
    const todayRecoveredItem = adult.page.locator("[data-triage-item^='intent_']").filter({ hasText: todayRecoveryText });
    await todayRecoveredItem.waitFor();
    record("Post-capture recovery outside Triage opens the exact saved holding item", todayProposalArm.response.ok && todayRecoveryRequestIds.length === 2 && todayRecoveryRequestIds[0] === todayRecoveryRequestIds[1] && await todayRecoveredItem.count() === 1 && adult.page.url().includes("view=needs_interpretation"), JSON.stringify({ todayRecoveryRequestIds, url: adult.page.url(), item: await todayRecoveredItem.textContent() }));
    await adult.page.unroute("**/api/member/v1/triage/intents", todayRecoveryRoute);
    await adult.page.goto(`${server.baseUrl}/member/triage`);
    await adult.page.getByRole("heading", { name: "Triage", level: 1 }).waitFor();

    let definiteIntentRequests = 0;
    const definiteIntentRoute = async (route) => {
      definiteIntentRequests += 1;
      expected409Console += 1;
      await route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: "Fixture rejected this Add-for-review request before durable capture." }) });
    };
    await adult.page.route("**/api/member/v1/triage/intents", definiteIntentRoute);
    await adult.page.locator("#add-for-review").click();
    const definiteIntentText = "Keep this text after a definite pre-capture refusal";
    await adult.page.getByLabel("Thought to hold").fill(definiteIntentText);
    await adult.page.locator(".add-for-review-form").getByRole("button", { name: "Add for review" }).click();
    await adult.page.getByText("Your thought was not saved; the text is still here.", { exact: false }).waitFor();
    const definiteState = {
      requests: definiteIntentRequests,
      dialogOpen: await adult.page.locator("#action-dialog").evaluate((dialog) => dialog.open),
      retainedText: await adult.page.getByLabel("Thought to hold").inputValue(),
      submitEnabled: await adult.page.locator(".add-for-review-form").getByRole("button", { name: "Add for review" }).isEnabled(),
      recoveryCount: await adult.page.getByRole("button", { name: "Reload and reconcile" }).count(),
      banner: await adult.page.locator("#state-banner").textContent(),
    };
    record("A definite pre-capture Add refusal does not retry or claim ambiguous durable state", definiteState.requests === 1 && definiteState.dialogOpen && definiteState.retainedText === definiteIntentText && definiteState.submitEnabled && definiteState.recoveryCount === 0, JSON.stringify(definiteState));
    await adult.page.unroute("**/api/member/v1/triage/intents", definiteIntentRoute);
    await adult.page.getByRole("button", { name: "Cancel" }).click();

    const triageProposalArm = await jsonRequest(server.baseUrl, "/__gate7/fail-next-intent-proposals", identities.adult, "POST");
    let releaseFailedIntent;
    let resolveFailedIntentStarted;
    const failedIntentStarted = new Promise((resolveStarted) => { resolveFailedIntentStarted = resolveStarted; });
    const failedIntentRelease = new Promise((resolveRelease) => { releaseFailedIntent = resolveRelease; });
    const retainedIntentRequestIds = [];
    const failedIntentRoute = async (route) => {
      const body = JSON.parse(route.request().postData() || "{}");
      retainedIntentRequestIds.push(body.client_request_id);
      if (retainedIntentRequestIds.length === 1) {
        resolveFailedIntentStarted();
        await failedIntentRelease;
      }
      const response = await route.fetch();
      if (response.status() === 409) expected409Console += 1;
      await route.fulfill({ response });
    };
    await adult.page.route("**/api/member/v1/triage/intents", failedIntentRoute);
    await adult.page.locator("#add-for-review").click();
    const retainedIntentText = "Retained exact-text form for the refresh-failure lock proof";
    await adult.page.getByLabel("Thought to hold").fill(retainedIntentText);
    await adult.page.getByLabel("Type hint (optional)").selectOption("task");
    await adult.page.locator(".add-for-review-form").getByRole("button", { name: "Add for review" }).click();
    await failedIntentStarted;
    await adult.page.keyboard.press("Escape");
    const dialogStayedOpenDuringIntent = await adult.page.locator("#action-dialog").evaluate((dialog) => dialog.open);
    releaseFailedIntent();
    const recoveryButton = adult.page.getByRole("button", { name: "Open saved Triage item" });
    await recoveryButton.waitFor();
    const recoveryState = {
      dialogStayedOpenDuringIntent,
      dialogOpen: await adult.page.locator("#action-dialog").evaluate((dialog) => dialog.open),
      retainedText: await adult.page.getByLabel("Thought to hold").inputValue(),
      enabled: await recoveryButton.isEnabled(),
      focused: await recoveryButton.evaluate((button) => button === document.activeElement),
      status: await adult.page.locator("[data-add-recovery-status]").textContent(),
      dialog: await adult.page.locator("#action-dialog").textContent(),
      banner: await adult.page.locator("#state-banner").textContent(),
    };
    record("A proven post-capture Add failure retries one identity and retains exact text with accessible recovery", triageProposalArm.response.ok && recoveryState.dialogStayedOpenDuringIntent && recoveryState.dialogOpen && recoveryState.retainedText === retainedIntentText && recoveryState.enabled && recoveryState.focused && recoveryState.status.includes("Open the saved Triage item") && retainedIntentRequestIds.length === 2 && retainedIntentRequestIds[0] === retainedIntentRequestIds[1], JSON.stringify({ ...recoveryState, retainedIntentRequestIds }));
    await adult.page.unroute("**/api/member/v1/triage/intents", failedIntentRoute);
    await recoveryButton.click();
    await adult.page.waitForURL(/\/member\/triage\?.*view=needs_interpretation.*item=intent_/);
    const triageRecoveredItem = adult.page.locator("[data-triage-item^='intent_']").filter({ hasText: retainedIntentText });
    await triageRecoveredItem.waitFor();
    record("Post-capture recovery inside Triage opens the exact saved holding item", await triageRecoveredItem.count() === 1 && adult.page.url().includes("view=needs_interpretation"), JSON.stringify({ url: adult.page.url(), item: await triageRecoveredItem.textContent() }));
    await adult.page.goto(`${server.baseUrl}/member/triage?view=ready`);
    await adult.page.getByRole("heading", { name: "Triage", level: 1 }).waitFor();
    let blockedRefreshFailureIntentRequests = 0;
    const blockedRefreshFailureIntentRoute = async (route) => {
      blockedRefreshFailureIntentRequests += 1;
      await route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "This guarded request should never occur." }) });
    };
    await adult.page.route("**/api/member/v1/triage/intents", blockedRefreshFailureIntentRoute);
    await adult.page.locator(".triage-master-button").filter({ hasText: "Review the only ready household item" }).click();
    await adult.page.getByLabel("Why this candidate should leave Triage").selectOption("not_actionable");
    let failNextTriageRefresh = true;
    const failTriageRefresh = async (route) => {
      if (failNextTriageRefresh && route.request().method() === "GET") {
        failNextTriageRefresh = false;
        await route.fulfill({ status: 200, contentType: "application/json", body: "null" });
        return;
      }
      await route.continue();
    };
    await adult.page.route("**/api/member/v1/surfaces/triage?*", failTriageRefresh);
    await adult.page.getByRole("button", { name: "Dismiss & teach" }).click();
    await adult.page.getByText(/The decision was recorded, but Triage could not refresh:/).waitFor();
    const refreshFailureText = await adult.page.locator("#state-banner").textContent();
    await adult.page.getByRole("button", { name: /^Ready/ }).dispatchEvent("click");
    await adult.page.getByRole("button", { name: /^Recent/ }).dispatchEvent("click");
    await adult.page.locator("#add-for-review").dispatchEvent("click");
    record("Post-write refresh failure latches Triage until a successful reload", await adult.page.locator("#state-banner").textContent() === refreshFailureText && await adult.page.getByRole("button", { name: /^Ready/ }).getAttribute("aria-pressed") === "true" && await adult.page.getByRole("button", { name: /^Ready/ }).isDisabled() && await adult.page.locator(".triage-review-form button:not([disabled])").count() === 0 && await adult.page.locator("#add-for-review").isDisabled() && !await adult.page.locator("#action-dialog").evaluate((dialog) => dialog.open) && blockedRefreshFailureIntentRequests === 0, JSON.stringify({ banner: await adult.page.locator("#state-banner").textContent(), readyDisabled: await adult.page.getByRole("button", { name: /^Ready/ }).isDisabled(), blockedRefreshFailureIntentRequests }));
    await adult.page.unroute("**/api/member/v1/triage/intents", blockedRefreshFailureIntentRoute);
    await adult.page.unroute("**/api/member/v1/surfaces/triage?*", failTriageRefresh);
    await adult.page.reload();
    await adult.page.getByRole("heading", { name: "Triage", level: 1 }).waitFor();
    record("Triage final Ready dismissal reports the empty refreshed queue truthfully", await adult.page.locator("#state-banner").isHidden() && await adult.page.getByRole("button", { name: /^Ready/ }).getAttribute("aria-pressed") === "true" && await adult.page.locator(".triage-master-button").count() === 0 && (await adult.page.locator(".triage-master").textContent()).includes("Nothing is ready to confirm"), await adult.page.locator("#state-banner").textContent());
    await screenshot(adult.page, "surface-triage-final-ready-caught-up-tablet.png");
    await visit(adult.page, server.baseUrl, "/member/today", "Today");
    record("tablet Day Brief default", await adult.page.getByRole("button", { name: "Day Brief" }).getAttribute("aria-pressed") === "true", await adult.page.locator("#surface").textContent());
    record("observation mode visible", await adult.page.locator("#observation-pill").textContent() === "Observation mode", await adult.page.locator("#observation-pill").textContent());
    await screenshot(adult.page, "mode-compressed-observation-tablet.png");
    await adult.page.locator("#chat-open").click();
    await adult.page.locator("#chat-input").fill("What is one kind next step?");
    const chatResponsePromise = adult.page.waitForResponse((response) =>
      response.url().includes("/api/member/v1/chat/stream")
      && response.request().method() === "POST");
    await adult.page.locator("#chat-send").click();
    const chatResponse = await chatResponsePromise;
    await adult.page.locator(".message.assistant").filter({ hasText: "next small step" }).last().waitFor();
    await adult.page.locator("#chat-state").filter({ hasText: "Ready" }).waitFor();
    await chatResponse.finished();
    record("adult OpenClaw stream rendered", await adult.page.locator(".message.assistant").last().textContent() === "I'm here. I can help you choose the next small step.", await adult.page.locator(".message.assistant").last().textContent());
    await screenshot(adult.page, "adult-chat-streamed-tablet.png");
    const browserPersistence = await adult.page.evaluate(async () => ({
      localStorageKeys: Object.keys(localStorage),
      sessionStorageKeys: Object.keys(sessionStorage),
      cacheNames: "caches" in globalThis ? await caches.keys() : [],
      serviceWorkers: "serviceWorker" in navigator ? (await navigator.serviceWorker.getRegistrations()).length : 0,
      secretInDocument: document.documentElement.outerHTML.includes("gate7-visual-secret-never-rendered"),
    }));
    record(
      "browser stores no transcript, session, cache, worker, or service credential",
      browserPersistence.localStorageKeys.length === 0
        && browserPersistence.sessionStorageKeys.length === 0
        && browserPersistence.cacheNames.length === 0
        && browserPersistence.serviceWorkers === 0
        && browserPersistence.secretInDocument === false,
      browserPersistence,
    );
    const reduced = await auditAccessibility(adult.page, "tablet reduced-motion");
    record("reduced motion honored", reduced.motion === false, reduced);

    const quest = await jsonRequest(server.baseUrl, "/api/member/v1/temporary-surfaces", identities.adult, "POST", {
      title: "30-day Garage Quest",
      purpose: "A private, disposable lens over the work already here.",
      queries: [{ name: "what_now" }, { name: "tasks.open" }, { name: "lists.open" }, { name: "calendar.week" }, { name: "finance.recent" }],
      action_verbs: ["task.complete", "task.defer"],
      required_capabilities: ["product:core", "projection:tasks"],
      compatible_modes: ["carousel", "compressed"],
    });
    record("private quest created", quest.response.status === 201, quest.response.status);
    const questId = quest.value.surface.stable_id;
    await visit(adult.page, server.baseUrl, `/member/quests/${questId}`, "30-day Garage Quest");
    await screenshot(adult.page, "quest-private-compressed.png");
    const principalDenied = await jsonRequest(server.baseUrl, `/api/member/v1/temporary-surfaces/${questId}/evaluate`, identities.principal);
    record("private quest undiscoverable", principalDenied.response.status === 404 && !JSON.stringify(principalDenied.value).includes(questId), principalDenied.response.status);
    const promoted = await jsonRequest(server.baseUrl, `/api/member/v1/temporary-surfaces/${questId}/promote`, identities.adult, "POST", { audience_member_ids: ["visual_principal"] });
    record("quest promoted by explicit review", promoted.response.ok, promoted.response.status);
    const promotedContext = await newContext(browser, identities.principal, { width: 1280, height: 900 });
    await visit(promotedContext.page, server.baseUrl, `/member/quests/${questId}`, "30-day Garage Quest");
    await screenshot(promotedContext.page, "quest-promoted-carousel.png");
    await promotedContext.context.close();
    await jsonRequest(server.baseUrl, `/api/member/v1/temporary-surfaces/${questId}/archive`, identities.adult, "POST");
    expected404Console += 1;
    await adult.page.goto(`${server.baseUrl}/member/quests/${questId}`, { waitUntil: "networkidle" });
    await adult.page.locator(".state-card").waitFor();
    record("archived quest has no navigation entry", await adult.page.locator(`a[href='/member/quests/${questId}']`).count() === 0, await adult.page.locator("#primary-nav").textContent());
    await screenshot(adult.page, "quest-archived-bookmark.png");

    const expiringQuest = await jsonRequest(server.baseUrl, "/api/member/v1/temporary-surfaces", identities.adult, "POST", {
      title: "Weekend Reset Quest",
      purpose: "A short-lived lens that disappears without changing household facts.",
      queries: [{ name: "what_now" }, { name: "tasks.open" }],
      action_verbs: ["task.complete"],
      required_capabilities: ["product:core", "projection:tasks"],
      compatible_modes: ["compressed"],
      expires_at: new Date(new Date(fixedNow).getTime() + 1000).toISOString(),
    });
    record("expiring quest created", expiringQuest.response.status === 201, expiringQuest.response.status);
    const expiringQuestId = expiringQuest.value.surface.stable_id;
    const advancedClock = await jsonRequest(server.baseUrl, "/__gate7/advance-clock", identities.adult, "POST");
    record(
      "fixture clock advanced past quest expiry",
      advancedClock.response.ok && advancedClock.value.expired.includes(expiringQuestId),
      JSON.stringify(advancedClock.value),
    );
    expected404Console += 1;
    await adult.page.goto(`${server.baseUrl}/member/quests/${expiringQuestId}`, { waitUntil: "networkidle" });
    await adult.page.locator(".state-card").waitFor();
    record("expired quest has no navigation entry", await adult.page.locator(`a[href='/member/quests/${expiringQuestId}']`).count() === 0, await adult.page.locator("#primary-nav").textContent());
    await screenshot(adult.page, "quest-expired-bookmark.png");
    await adult.context.close();

    const child = await newContext(browser, identities.child, { width: 390, height: 844 }, "reduce");
    await visit(child.page, server.baseUrl, "/member/today", "Today");
    record("child has two routes", await child.page.locator("#primary-nav .nav-link").count() === 2, await child.page.locator("#primary-nav").textContent());
    await screenshot(child.page, "mode-child-today-narrow.png");
    await visit(child.page, server.baseUrl, "/member/scoreboard", "Scoreboard");
    await screenshot(child.page, "mode-child-scoreboard-narrow.png");
    await auditAccessibility(child.page, "narrow child");
    const childFinance = await jsonRequest(server.baseUrl, "/member/finance", identities.child);
    record("child direct finance denial", childFinance.response.status === 404, childFinance.response.status);
    await child.context.close();

    const screen = await newContext(browser, identities.screen, { width: 1024, height: 768 });
    await visit(screen.page, server.baseUrl, "/member/scoreboard", "Scoreboard");
    record("shared screen has scoreboard only", await screen.page.locator("#primary-nav .nav-link").count() === 1, await screen.page.locator("#primary-nav").textContent());
    await screenshot(screen.page, "shared-screen-scoreboard.png");
    await screen.context.close();

    degraded = await startServer(basePort + 1, false);
    const degradedAdult = await newContext(browser, identities.adult, { width: 1280, height: 900 });
    await visit(degradedAdult.page, degraded.baseUrl, "/member/today", "Today");
    if (await degradedAdult.page.locator("#chat-open").isVisible()) {
      await degradedAdult.page.locator("#chat-open").click();
    }
    record("degraded chat explicit", (await degradedAdult.page.locator("#chat-state").textContent()).includes("no model provider"), await degradedAdult.page.locator("#chat-state").textContent());
    record("domain view remains usable during model loss", await degradedAdult.page.locator("#surface > *").count() >= 2, await degradedAdult.page.locator("#surface").textContent());
    await screenshot(degradedAdult.page, "adult-chat-degraded-domain-healthy.png");
    await degradedAdult.context.close();

    record("no browser console errors", consoleErrors.length === 0, consoleErrors);
    record("expected unavailable-view 404s handled", observedExpected404Console === 2, observedExpected404Console);
    record("expected controlled Triage conflicts and ambiguous response handled", observedExpected409Console === 9 && observedExpected503Console === 8, JSON.stringify({ observedExpected409Console, observedExpected503Console }));
    record("no uncaught page errors", pageErrors.length === 0, pageErrors);
    record("no failed browser requests", requestFailures.length === 0, requestFailures);
  } finally {
    console.log("[Gate 7] Closing browser and fixture servers");
    const browserClosed = await Promise.race([
      browser.close().then(() => true),
      new Promise((resolveWait) => setTimeout(() => resolveWait(false), 5000)),
    ]);
    if (!browserClosed) {
      console.warn("[Gate 7] Browser close exceeded 5000 ms; forcing runner exit after evidence cleanup");
    }
    await stopServer(server);
    if (degraded) await stopServer(degraded);
  }

  const report = {
    schema_version: 1,
    generated_at: new Date().toISOString(),
    status: results.every((item) => item.status === "PASS") ? "PASS" : "FAIL",
    browser: browserDescription,
    viewports: ["1440x1000", "1280x900", "1024x768", "820x1180", "390x844"],
    states: ["day-brief", "carousel", "compressed", "child", "observation", "degraded", "chat-streamed", "quest-private", "quest-promoted", "quest-expired", "quest-archived"],
    results,
    screenshots,
    console_errors: consoleErrors,
    page_errors: pageErrors,
    request_failures: requestFailures,
  };
  await writeFile(join(evidenceDir, "visual-results.json"), `${JSON.stringify(report, null, 2)}\n`);
  const lines = [
    "# Gate 7 rendered portal evidence", "", `Status: **${report.status}**`, "",
    "Run from the repository root:", "", "```powershell", "npm.cmd run test:gate7 --prefix tools/console-reference-harness", "```", "",
    `The harness starts credential-free loopback fixtures, uses ${browserDescription}, exercises every domain surface and role boundary, streams the OpenClaw fixture, evaluates private/promoted/expired/archived quest states, and checks responsive/reduced-motion/keyboard naming invariants.`, "",
    "## Checks", "",
    ...results.map((item) => `- ${item.status}: ${item.name}`), "", "## Screenshots", "",
    ...Object.entries(screenshots).map(([name, meta]) => `- \`${name}\` — ${meta.bytes} bytes, SHA-256 \`${meta.sha256}\``),
  ];
  await writeFile(join(evidenceDir, "README.md"), `${lines.join("\n")}\n`);
  await rm(join(evidenceDir, "visual-failure.txt"), { force: true });
  if (report.status !== "PASS") process.exitCode = 1;
}

let hardTimeout;
const boundedRun = Promise.race([
  run(),
  new Promise((_, reject) => {
    hardTimeout = setTimeout(
      () => reject(new Error(`Gate 7 exceeded its ${maxRuntimeMs} ms hard runtime bound`)),
      maxRuntimeMs,
    );
  }),
]);

boundedRun.then(
  () => {
    clearTimeout(hardTimeout);
    process.exit(process.exitCode ?? 0);
  },
  async (error) => {
    clearTimeout(hardTimeout);
    await mkdir(evidenceDir, { recursive: true });
    await writeFile(join(evidenceDir, "visual-failure.txt"), `${error.stack || error}\n`);
    console.error(error);
    process.exit(1);
  },
);
