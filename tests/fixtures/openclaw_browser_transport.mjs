/** Real disposable Chrome + synthetic extension transport; no live profile/data. */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { access, mkdir, readFile, rm } from "node:fs/promises";
import net from "node:net";
import path from "node:path";

export async function startBrowserTransport({ directory, state, relayPort, gmailSelf = false }) {
  assert.equal(process.env.GITHUB_ACTIONS, "true", "Candidate browser proof is hosted-only");
  const candidates = process.platform === "darwin"
    ? ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    : ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium", "/usr/bin/chromium-browser"];
  let executable;
  for (const candidate of candidates) { try { await access(candidate); executable = candidate; break; } catch {} }
  assert(executable, "Hosted native-browser proof requires the runner's installed Chrome");
  const listener = net.createServer(); await new Promise((resolve) => listener.listen(0, "127.0.0.1", resolve));
  const port = listener.address().port; await new Promise((resolve) => listener.close(resolve));
  const profile = path.join(directory, "synthetic-chrome-profile"); await mkdir(profile);
  const chrome = spawn(executable, ["--headless=new", "--no-sandbox", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    "--disable-background-networking", "--disable-component-update", "--disable-sync", `--user-data-dir=${profile}`,
    `--remote-debugging-port=${port}`, "--remote-debugging-address=127.0.0.1", "about:blank"], { stdio: "ignore" });
  const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  async function until(check, label) {
    const end = Date.now() + 30000;
    while (Date.now() < end) { const result = await check(); if (result) return result; await pause(100); }
    throw new Error(`Synthetic browser timeout: ${label}`);
  }
  let socket, relay; const pending = new Map(); let nextId = 1, rootSession;
  const errors = [], bodyReads = [], url = gmailSelf
    ? "https://mail.google.com/mail/u/0/#inbox" : "https://example.com/cos-workstation-fixture";
  const requestUrl = url.split("#")[0];
  const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const id = nextId++; pending.set(id, { resolve, reject }); socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
  try {
    const version = await until(async () => { try { return await (await fetch(`http://127.0.0.1:${port}/json/version`)).json(); } catch { return null; } }, "Chrome CDP");
    socket = new WebSocket(version.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { socket.addEventListener("open", resolve, { once: true }); socket.addEventListener("error", reject, { once: true }); });
    socket.addEventListener("message", async ({ data }) => {
      const frame = JSON.parse(String(data));
      if (frame.id) { const row = pending.get(frame.id); if (row) { pending.delete(frame.id); frame.error ? row.reject(new Error(frame.error.message)) : row.resolve(frame.result ?? {}); } return; }
      if (frame.method === "Fetch.requestPaused" && frame.sessionId === rootSession) {
        try {
          assert.equal(frame.params.request.url, requestUrl, "No outside browser navigation is permitted by the fixture");
          // Only main-document frame metadata is needed; CSP forbids outside
          // frame loading while preserving the genuinely zero-height roster.
          const html = gmailSelf
            ? `<!doctype html><html><head><title>Inbox (22) - cos@example.invalid - Synthetic household Mail</title><link rel="icon" href="data:,"><meta http-equiv="Content-Security-Policy" content="frame-src 'none'"></head><body><header id="gb"><a id="account" role="button" aria-label="Google Account: Synthetic Staff (cos@example.invalid)">cos@example.invalid</a></header><div role="navigation" jscontroller="s6IIOd"><div jscontroller="m50PKc"><div jsname="hSRGPd" role="link" aria-label="Chat, 1 unread message">Chat</div></div></div><div gh="chat"><div id="talk_roster"><iframe id="gtn-roster-iframe-id" name="gtn-roster-iframe-id" title="Chat" aria-label="Chat" style="height:0;width:256px;border:0" src="https://chat.google.com/u/0/frame?origin=https%3A%2F%2Fmail.google.com"></iframe></div></div><main>Review the garage plan on Friday.</main></body></html>`
            : '<!doctype html><html><head><title>Synthetic CoS workspace</title><link rel="icon" href="data:,"></head><body><span id="account">cos@example.invalid</span><span id="workspace">Synthetic household</span><main>Review the garage plan on Friday.</main></body></html>';
          await send("Fetch.fulfillRequest", { requestId: frame.params.requestId, responseCode: 200,
            responseHeaders: [{ name: "Content-Type", value: "text/html" }], body: Buffer.from(html).toString("base64") }, rootSession);
        } catch (error) { errors.push(error.message); await send("Fetch.failRequest", { requestId: frame.params.requestId, errorReason: "BlockedByClient" }, rootSession); }
      }
      if (frame.sessionId === rootSession && relay?.readyState === WebSocket.OPEN && frame.method !== "Fetch.requestPaused") {
        relay.send(JSON.stringify({ type: "cdpEvent", tabId: 1, method: frame.method, params: frame.params }));
      }
    });
    const { targetId } = await send("Target.createTarget", { url: "about:blank" });
    ({ sessionId: rootSession } = await send("Target.attachToTarget", { targetId, flatten: true }));
    await send("Fetch.enable", { patterns: [{ urlPattern: "*", requestStage: "Request" }] }, rootSession);
    await send("Page.enable", {}, rootSession);
    await send("Page.navigate", { url }, rootSession);
    await until(async () => (await send("Runtime.evaluate", { expression: "document.querySelector('#account')?.textContent", returnByValue: true }, rootSession)).result?.value === "cos@example.invalid", "synthetic signed-in page");
    return {
      targetId, url, errors, bodyReads,
      resource: { resource_id: "fixture-browser-resource", inventory_binding_id: "fixture-browser", binding_revision: 1, policy_revision: 1, target_evidence_reference: "synthetic-native-target", native_target_id: targetId, profile: "chrome", url,
        expected_identity: { account: "cos@example.invalid", workspace: "Synthetic household", ...(gmailSelf ? { role: "account user" } : {}) },
        ...(gmailSelf ? { identity_verifier: "gmail_self_mailbox_v1" } : {}),
        identity_probes: gmailSelf ? {} : { account: { selector: "#account", attribute: "textContent", extraction: "exact" }, workspace: { selector: "#workspace", attribute: "textContent", extraction: "exact" } }, route_sha256: "a".repeat(64) },
      async armWrongAccountProof() {
        assert(gmailSelf);
        const result = await send("Runtime.evaluate", { expression: `(() => {
          document.querySelector("#account").setAttribute("aria-label", "Google Account: Other Staff (other@example.invalid)");
          globalThis.fixturePrivateBodyReads = 0;
          Object.defineProperty(document.body, "innerText", { get() {
            globalThis.fixturePrivateBodyReads += 1;
            throw new Error("Wrong-account mailbox text was accessed");
          } });
          return true;
        })()`, returnByValue: true }, rootSession);
        assert.equal(result.result?.value, true);
      },
      async privateBodyReadCount() {
        return (await send("Runtime.evaluate", { expression: "globalThis.fixturePrivateBodyReads", returnByValue: true }, rootSession)).result?.value;
      },
      async connectRelay() {
        const token = await until(async () => { try { return (await readFile(path.join(state, "credentials/browser-extension-relay.secret"), "utf8")).trim(); } catch { return null; } }, "relay secret");
        assert.match(token, /^[0-9a-f]{64}$/);
        await until(async () => { try { return (await fetch(`http://127.0.0.1:${relayPort}/json/version`, { headers: { authorization: `Bearer ${token}` } })).status === 503; } catch { return false; } }, "relay endpoint");
        relay = new WebSocket(`ws://127.0.0.1:${relayPort}/extension`, ["openclaw-extension-relay", `openclaw-extension-token.${token}`]);
        await new Promise((resolve, reject) => { relay.addEventListener("open", resolve, { once: true }); relay.addEventListener("error", reject, { once: true }); });
        relay.addEventListener("message", async ({ data }) => {
          const command = JSON.parse(String(data));
          if (command.type === "ping") { relay.send(JSON.stringify({ type: "pong" })); return; }
          try {
            let result = {};
            if (command.type === "attach") { assert.equal(command.tabId, 1); result = { targetId }; }
            else if (command.type === "cdp") {
              assert.equal(command.tabId, 1); assert.equal(command.sessionId, undefined, "no extra browser target");
              if (command.method === "Runtime.callFunctionOn" && String(command.params?.functionDeclaration).includes("innerText")) bodyReads.push(command.params.functionDeclaration);
              result = await send(command.method, command.params, rootSession);
            } else if (command.type !== "detach") throw new Error("Fixture forbids browser creation/navigation/close operations");
            relay.send(JSON.stringify({ type: "result", seq: command.seq, result }));
          } catch (error) { errors.push(error.message); relay.send(JSON.stringify({ type: "error", seq: command.seq, message: "Synthetic extension command failed" })); }
        });
        relay.send(JSON.stringify({ type: "hello", userAgent: "Synthetic Chrome fixture", browserVersion: version.Browser,
          extensionVersion: "0.0.0-fixture", tabs: [{ tabId: 1, url, title: "Synthetic CoS workspace", active: true }] }));
        await until(async () => (await fetch(`http://127.0.0.1:${relayPort}/json/version`, { headers: { authorization: `Bearer ${token}` } })).ok, "paired synthetic extension");
      },
      async close() { relay?.close(); socket?.close(); chrome.kill("SIGTERM"); await Promise.race([new Promise((resolve) => chrome.once("exit", resolve)), pause(5000)]); if (chrome.exitCode === null) chrome.kill("SIGKILL"); await rm(profile, { recursive: true, force: true }); },
    };
  } catch (error) { relay?.close(); socket?.close(); chrome.kill("SIGKILL"); throw error; }
}
