// Synthetic transport fixtures exercise the production Settings assets. They
// contain no Google credential or live account; server consent guards are
// covered separately by the production composition tests.
export async function auditGoogleConnections({ page, stalePage, otherPage, baseUrl, record, screenshot, auditAccessibility, expectHandledFailure, sourceVersion }) {
  const endpoint = "/api/member/v1/settings/google-connections";
  const match = /\/api\/member\/v1\/settings\/google-connections(?:\/[^?]*)?(?:\?.*)?$/;
  const services = [
    { id: "gmail", label: "Gmail", read_description: "Read your messages in your private console.", write_description: "Send or change messages only with the required approvals." },
    { id: "calendar", label: "Google Calendar", read_description: "Read calendars available to your account.", write_description: "Create and change events with the required approvals." },
    { id: "tasks", label: "Google Tasks", read_description: "Read your task lists.", write_description: "Create and change tasks with the required approvals." },
  ];
  const selected = (gmailRead = true, calendarRead = true) => ({ gmail: { read: gmailRead, write: false }, calendar: { read: calendarRead, write: false }, tasks: { read: false, write: false } });
  const account = { id: "synthetic-google-account", email: "member@example.invalid", workspace: "personal", revision: "a".repeat(64), services: Object.fromEntries(Object.entries(selected()).map(([id, access]) => [id, { ...access, setup: access.read ? "connected" : "not_configured", runtime: id === "gmail" ? "unknown" : id === "calendar" ? "running" : "stopped" }])), cos_context_access: false };
  const model = { available: true, reason: null, services, accounts: [account], cos_permission_href: "/member/settings?section=cos-work" };
  const requests = new Map(); const posts = []; let loseResponse = false; let mismatchRequest = false; let failRead = false; let forceAuthorization = false; let invalidAuthorization = false; let googleNavigationCount = 0; let duplicateNext = false;
  const consent = (requestId) => ({ request_id: requestId, state: "pending", message: "Continue to Google to review your chosen access.", authorization_url: `https://accounts.google.com/o/oauth2/v2/auth?state=synthetic-${requestId}`, expires_at: new Date(Date.now() + 15 * 60_000).toISOString() });
  const handler = async (route) => {
    const request = route.request(); const url = new URL(request.url());
    if (request.method() === "GET") {
      if (failRead) { failRead = false; await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Synthetic connection status outage" }) }); return; }
      if (url.pathname.includes("/requests/")) {
        const id = url.pathname.split("/").at(-1); let result = requests.get(id) || { request_id: id, state: "pending", message: "The exact saved request is still being checked." };
        if (mismatchRequest) result = { ...result, request_id: "11111111-1111-4111-8111-111111111111" };
        if (invalidAuthorization) result = { ...result, authorization_url: "https://accounts.google.com.evil.invalid/o/oauth2/v2/auth" };
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(result) }); return;
      }
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(model) }); return;
    }
    const body = request.postDataJSON(); posts.push({ path: url.pathname, body });
    if (duplicateNext) { duplicateNext = false; await route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: { code: "account_already_connected", reason: "This account was connected in another window." } }) }); return; }
    if (url.pathname.endsWith("/permissions") && body.expected_revision !== account.revision) { await route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: { code: "stale_revision", reason: "Google permissions changed" } }) }); return; }
    const authorizing = url.pathname.endsWith("/authorize") || forceAuthorization;
    let result;
    if (authorizing) { result = consent(body.request_id); }
    else {
      account.revision = (account.revision[0] === "a" ? "b" : "c").repeat(64);
      for (const [id, access] of Object.entries(body.services)) account.services[id] = { ...account.services[id], ...access };
      result = { request_id: body.request_id, state: "configured", account_id: account.id };
    }
    requests.set(body.request_id, result);
    const lost = loseResponse; loseResponse = false;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(lost ? {} : result) });
  };
  for (const target of [page, stalePage]) await target.route(match, handler);
  // Even an accidental click can never reach Google from this isolated test.
  await page.route("https://accounts.google.com/**", async (route) => { googleNavigationCount += 1; await route.fulfill({ status: 200, contentType: "text/html", body: "<title>Synthetic consent boundary</title><p>No external request was made.</p>" }); });
  const section = (target = page) => target.locator(".google-connections");
  const status = (target = page) => section(target).getByRole("status");
  const card = (target = page) => target.locator('.google-account[data-account-id="synthetic-google-account"]');
  const dialog = (target = page) => target.locator("#action-dialog");
  const goto = async (target = page, suffix = "") => { await target.goto(`${baseUrl}/member/settings?section=connections${suffix}`, { waitUntil: "networkidle" }); await status(target).filter({ hasText: /permissions and each|unconfirmed|Continue to Google|exact saved request|permission choice|denied|expired/ }).waitFor(); };
  const review = async (target = page) => { await card(target).getByRole("button", { name: "Review Google permissions", exact: true }).click(); };
  const confirm = async (target = page) => { await dialog(target).getByRole("button", { name: "Save Google permissions", exact: true }).click(); };
  await goto();
  const versions = await page.locator("link[href*='/member-assets/member.css'], script[src*='/member-assets/member.js']").evaluateAll((nodes) => nodes.map((node) => new URL(node.href || node.src).searchParams.get("v")));
  record("Google connection browser uses exact candidate member assets", versions.length === 2 && versions.every((value) => value === sourceVersion));
  record("Google Connections separates three supported services and actual sync from setup", await card().locator(".google-service-choice").count() === 3 && (await card().textContent()).includes("Sync: Unknown") && (await card().textContent()).includes("Sync: Running") && posts.length === 0 && !(await section().textContent()).includes("Google Drive"));
  record("Google connection does not grant CoS context or cross-member sharing", (await section().textContent()).includes("Personal inbox obligation extraction stays off") && (await card().textContent()).includes("review your private CoS permissions") && await section().getByRole("link", { name: "Review my CoS permissions", exact: true }).getAttribute("href") === "/member/settings?section=cos-work");
  await page.locator(".google-add-form summary").click();
  const form = page.locator(".google-connection-form");
  record("New Google connection defaults only Gmail and Calendar read with every write off", await form.getByRole("checkbox", { name: "Gmail Read", exact: true }).isChecked() && await form.getByRole("checkbox", { name: "Google Calendar Read", exact: true }).isChecked() && !await form.getByRole("checkbox", { name: "Google Tasks Read", exact: true }).isChecked() && await form.locator('input[name$="-write"]:checked').count() === 0 && await form.getByRole("checkbox", { name: "Google Tasks Write", exact: true }).isDisabled());
  await form.getByRole("checkbox", { name: "Google Calendar Write", exact: true }).check(); await form.getByRole("checkbox", { name: "Google Calendar Read", exact: true }).uncheck();
  record("Turning Google read off also clears and disables write", !await form.getByRole("checkbox", { name: "Google Calendar Write", exact: true }).isChecked() && await form.getByRole("checkbox", { name: "Google Calendar Write", exact: true }).isDisabled());
  await form.getByRole("textbox", { name: "Google account email", exact: true }).fill("member@example.invalid");
  await form.getByRole("button", { name: "Review Google access", exact: true }).click();
  record("Existing Google account is directed to its permission controls without a second enrollment", posts.length === 0 && (await status().textContent()).includes("already connected") && !await dialog().isVisible());
  await goto(stalePage);
  await card().getByRole("checkbox", { name: "Gmail Read", exact: true }).uncheck(); await review();
  record("Google permission review binds account and selected services before any mutation", posts.length === 0 && (await dialog().textContent()).includes("member@example.invalid") && (await dialog().textContent()).includes("Gmail: Off") && (await dialog().textContent()).includes("Google Calendar: Read only"));
  await dialog().getByRole("button", { name: "Go back", exact: true }).click();
  record("Cancelling Google review leaves permissions untouched", posts.length === 0 && account.services.gmail.read);
  await review(); await confirm(); await status().filter({ hasText: "permission choice was saved" }).waitFor();
  record("Google permission reduction posts exact revision and retains untouched service choices", posts.length === 1 && posts[0].body.expected_revision === "a".repeat(64) && !posts[0].body.services.gmail.read && !posts[0].body.services.gmail.write && posts[0].body.services.calendar.read && (await card().textContent()).includes("Read off · Write off"));
  await review(stalePage); expectHandledFailure(409); await confirm(stalePage); await status(stalePage).filter({ hasText: "permissions and each" }).waitFor();
  record("Stale Google permission tab refreshes without silently resubmitting", posts.length === 2 && (await stalePage.locator("#state-banner").textContent()).includes("changed in another window") && !await card(stalePage).getByRole("checkbox", { name: "Gmail Read", exact: true }).isChecked());

  loseResponse = true; mismatchRequest = true;
  await card().getByRole("checkbox", { name: "Google Calendar Read", exact: true }).uncheck(); await review(); await confirm(); await status().filter({ hasText: "unconfirmed" }).waitFor();
  const unknownCount = posts.length; const lastBody = posts.at(-1).body;
  await goto();
  record("Mismatched Google result stays locked across reload without duplicate POST", posts.length === unknownCount && await page.locator(".google-connection-form").count() === 0 && (await status().textContent()).includes("did not match"));
  mismatchRequest = false; await section().getByRole("button", { name: "Check Google connection status", exact: true }).click(); await status().filter({ hasText: "permission choice was saved" }).waitFor();
  record("Exact Google request GET settles a lost response without replay", posts.length === unknownCount && !lastBody.services.calendar.read && !await card().getByRole("checkbox", { name: "Google Calendar Read", exact: true }).isChecked());

  forceAuthorization = true; loseResponse = true;
  await card().getByRole("checkbox", { name: "Google Calendar Read", exact: true }).check(); await card().getByRole("checkbox", { name: "Google Calendar Write", exact: true }).check(); await review(); await confirm(); await status().filter({ hasText: "Continue to Google" }).waitFor();
  const consentRequest = posts.at(-1).body.request_id; const consentCount = posts.length;
  record("More Google access waits for account consent and does not claim connected", requests.get(consentRequest).state === "pending" && posts.at(-1).body.services.calendar.write && await card().getByRole("button", { name: "Review Google permissions", exact: true }).isDisabled() && await section().getByRole("link", { name: "Continue to Google", exact: true }).isVisible() && !account.services.calendar.write);
  await goto();
  record("Lost Google authorize response resumes its original consent link after reload", posts.length === consentCount && (await section().getByRole("link", { name: "Continue to Google", exact: true }).getAttribute("href")).includes(consentRequest) && googleNavigationCount === 0);
  invalidAuthorization = true; await section().getByRole("button", { name: "Check Google connection status", exact: true }).click(); await status().filter({ hasText: "Continue to Google" }).waitFor();
  record("Google continuation refuses a lookalike external origin", await section().getByRole("link", { name: "Continue to Google", exact: true }).count() === 0 && googleNavigationCount === 0);
  invalidAuthorization = false;
  requests.set(consentRequest, { request_id: consentRequest, state: "failed", message: "Google access was denied or a different account was selected." });
  await goto(page, `&google_request=${consentRequest}`);
  record("Denied or wrong-account Google callback unlocks truthful repair without granting access", (await status().textContent()).includes("denied") && !account.services.calendar.write && !page.url().includes("google_request") && !await card().getByRole("button", { name: "Review Google permissions", exact: true }).isDisabled());
  forceAuthorization = false;

  await page.locator(".google-add-form summary").click();
  await form.getByRole("textbox", { name: "Google account email", exact: true }).fill("race@example.invalid");
  await form.getByRole("button", { name: "Review Google access", exact: true }).click(); duplicateNext = true; expectHandledFailure(409);
  const duplicateCount = posts.length;
  await dialog().getByRole("button", { name: "Prepare Google sign-in", exact: true }).click(); await status().filter({ hasText: "permissions and each" }).waitFor();
  record("Concurrent duplicate Google enrollment rejection unlocks without an unknown-request GET or repeated POST", posts.length === duplicateCount + 1 && (await page.locator("#state-banner").textContent()).includes("already connected") && !await card().getByRole("button", { name: "Review Google permissions", exact: true }).isDisabled());
  await page.locator(".google-add-form summary").click();
  await form.getByRole("textbox", { name: "Google account email", exact: true }).fill("new-member@example.invalid");
  await form.getByRole("combobox", { name: "Account use", exact: true }).selectOption("work");
  await form.getByRole("button", { name: "Review Google access", exact: true }).click();
  record("Google add review identifies expected account workspace and default read-only access", (await dialog().textContent()).includes("new-member@example.invalid") && (await dialog().textContent()).includes("Work") && (await dialog().textContent()).includes("Gmail: Read only") && (await dialog().textContent()).includes("Google Tasks: Off"));
  await dialog().getByRole("button", { name: "Prepare Google sign-in", exact: true }).click(); await status().filter({ hasText: "Continue to Google" }).waitFor();
  const addRequest = posts.at(-1).body;
  record("Google sign-in request binds exact member-entered email and chosen service permissions", posts.at(-1).path === `${endpoint}/authorize` && addRequest.expected_account_id === "new-member@example.invalid" && addRequest.workspace === "work" && JSON.stringify(addRequest.services) === JSON.stringify(selected()) && googleNavigationCount === 0);
  await section().getByRole("link", { name: "Continue to Google", exact: true }).click();
  await page.getByText("No external request was made.", { exact: true }).waitFor();
  record("Google continuation navigates only after the member chooses the prepared consent link", googleNavigationCount === 1 && page.url().startsWith("https://accounts.google.com/o/oauth2/v2/auth?"));
  const newAccount = { ...structuredClone(account), id: "synthetic-new-google-account", email: addRequest.expected_account_id, workspace: addRequest.workspace, revision: "d".repeat(64) };
  for (const [id, access] of Object.entries(addRequest.services)) newAccount.services[id] = { ...access, setup: access.read ? "connected" : "not_configured", runtime: "unknown" };
  model.accounts.push(newAccount); requests.set(addRequest.request_id, { request_id: addRequest.request_id, state: "configured", account_id: newAccount.id });
  await goto(page, `&google_request=${addRequest.request_id}`);
  record("Successful Google callback confirms exact account without claiming a completed sync", !page.url().includes("google_request") && (await status().textContent()).includes("account setup alone does not confirm") && (await page.locator('.google-account[data-account-id="synthetic-new-google-account"]').textContent()).includes("Sync: Unknown") && posts.at(-1).body.request_id === addRequest.request_id);
  const expiredId = "22222222-2222-4222-8222-222222222222";
  requests.set(expiredId, { request_id: expiredId, state: "expired", message: "This Google request expired. Review your choices to start again." });
  await goto(page, `&google_request=${expiredId}`);
  record("Expired Google handoff clears only after exact status confirmation", (await status().textContent()).includes("expired") && !await card().getByRole("button", { name: "Review Google permissions", exact: true }).isDisabled());
  failRead = true; expectHandledFailure(503); await section().getByRole("button", { name: "Check Google connection status", exact: true }).click(); await status().filter({ hasText: "unconfirmed" }).waitFor();
  record("Unavailable Google status locks mutations instead of assuming permissions", await page.locator(".google-account-permissions, .google-connection-form").count() === 0);
  await section().getByRole("button", { name: "Check Google connection status", exact: true }).click(); await status().filter({ hasText: "permissions and each" }).waitFor();
  let otherReads = 0; otherPage.on("request", (request) => { if (new URL(request.url()).pathname.startsWith(endpoint)) otherReads += 1; });
  // Isolated fixture ingress only; never use these headers against a deployment.
  await otherPage.setExtraHTTPHeaders({ "Tailscale-User-Login": "visual-adult.ts.net", "X-AdminMe-View-Member": "visual_principal" });
  await otherPage.goto(`${baseUrl}/member/settings?section=connections`, { waitUntil: "networkidle" });
  await otherPage.getByText("Return to your own private view to connect Google or change its permissions.", { exact: true }).waitFor();
  record("View-as never fetches another member Google accounts or offers mutations", otherReads === 0 && await section(otherPage).locator("input,button,select").count() === 0);
  model.available = false; model.reason = "Google sign-in is not configured on this installation."; await goto(); await page.locator(".google-add-form summary").click();
  record("Google setup unavailability explains the cause while existing permissions stay reviewable", (await section().textContent()).includes(model.reason) && await form.getByRole("button", { name: "Review Google access", exact: true }).isDisabled() && !await card().getByRole("button", { name: "Review Google permissions", exact: true }).isDisabled());
  model.available = true;
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 }); await goto(); await page.locator(".google-add-form summary").click();
    record(`Google account controls fit the ${width} viewport`, await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await auditAccessibility(page, `Google Connections ${width}`); await screenshot(page, `google-connections-${width}.png`);
  }
  await page.unroute(match, handler); await stalePage.unroute(match, handler);
}
