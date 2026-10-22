import { auditAppleActionHandoffs } from "./apple-action-handoffs.mjs";
import { auditAppleNativeApprovals } from "./apple-native-approvals.mjs";

// Synthetic transport boundary for the real member Settings assets. This does
// not exercise a live bridge or substitute for production authorization tests.
export async function auditAppleServicePermissions({ page, stalePage, otherPage, baseUrl, record, screenshot, auditAccessibility, expectHandledFailure, sourceVersion }) {
  const endpoint = "/api/member/v1/settings/apple-services";
  const match = /\/api\/member\/v1\/settings\/apple-services(?:\/[^/?]+)?(?:\?.*)?$/;
  const items = [
    { provider: "apple_calendar", label: "Apple Calendar", revision: 0, state: "disabled", read_access: true, write_access: false, reason: "Your selected calendar is connected.", operations: ["create_event", "update_event"], scopes: [{ id: "calendar-scope", label: "Example calendar", account_id: "example-apple", container_id: "example-calendar" }], selected_scope_ids: [], bridge_enrollment_id: "synthetic-bridge", approvals_required: true },
    { provider: "apple_reminders", label: "Apple Reminders", revision: 0, state: "disabled", read_access: true, write_access: false, reason: "Your selected reminder list is connected.", operations: ["create_reminder", "complete_reminder"], scopes: [{ id: "reminder-scope", label: "Example reminders", account_id: "example-apple", container_id: "example-reminders" }], selected_scope_ids: [], bridge_enrollment_id: "synthetic-bridge", approvals_required: true },
    { provider: "apple_notes", label: "Apple Notes", revision: 0, state: "disabled", read_access: true, write_access: false, reason: "Choose one Apple account for writing.", operations: ["create_note", "update_note"], scopes: [{ id: "notes-scope", label: "Example notes", account_id: "example-apple", account_label: "Example iCloud", provider_account_id: "synthetic-notes-account", container_id: "example-notes" }, { id: "local-notes-scope", label: "Local notes", account_id: "example-local", account_label: "On this Mac", provider_account_id: "synthetic-local-notes", container_id: "example-local-notes" }], selected_scope_ids: [], bridge_enrollment_id: "synthetic-bridge", approvals_required: true },
  ];
  const recorded = new Map(); const posts = []; let loseNext = false; let mismatchConfirmation = false; let rejectNext = false; let failNextRead = false;
  function apply(provider, body) {
    const item = items.find((value) => value.provider === provider);
    item.revision += 1; item.state = body.enabled ? "enabled" : "revoked"; item.write_access = body.enabled; item.can_revoke = body.enabled; item.selected_scope_ids = body.scope_ids;
    recorded.set(body.request_id, { state: "recorded", provider, ...body });
  }
  const routeHandler = async (route) => {
    const request = route.request(); const url = new URL(request.url());
    if (request.method() === "GET") {
      if (failNextRead) { failNextRead = false; await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Synthetic temporary status outage" }) }); return; }
      const requestId = url.searchParams.get("request_id"); const provider = url.searchParams.get("provider");
      let confirmation = requestId ? recorded.get(requestId) || { state: "not_recorded", provider, request_id: requestId } : null;
      if (confirmation && mismatchConfirmation && confirmation.state === "recorded") confirmation = { ...confirmation, enabled: !confirmation.enabled };
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items, can_mutate: true, request_confirmation: confirmation }) }); return;
    }
    const provider = decodeURIComponent(url.pathname.slice(endpoint.length + 1)); const body = request.postDataJSON(); posts.push({ provider, body });
    const item = items.find((value) => value.provider === provider);
    if (body.expected_revision !== item.revision) { await route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: { code: "stale_revision", message: "Permissions changed" } }) }); return; }
    if (rejectNext) { rejectNext = false; await route.fulfill({ status: 200, contentType: "application/json", body: "{}" }); return; }
    apply(provider, body);
    const lost = loseNext; loseNext = false;
    await route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify(lost ? {} : { provider, state: item.state, revision: item.revision }) });
  };
  await page.route(match, routeHandler); await stalePage.route(match, routeHandler);
  const card = (provider, target = page) => target.locator(`.apple-service-permission[data-provider="${provider}"]`);
  const status = (target = page) => target.locator(".apple-service-permissions").getByRole("status");
  const dialog = (target = page) => target.locator("#action-dialog");
  const goto = async (target = page) => { await target.goto(`${baseUrl}/member/settings?section=apple-services`, { waitUntil: "networkidle" }); await status(target).filter({ hasText: /current Apple permissions|last permission change|saved request|could not be checked/ }).waitFor(); };
  const enable = async (provider, scopeName, target = page) => { await card(provider, target).getByRole("checkbox", { name: scopeName, exact: true }).check(); await card(provider, target).getByRole("button", { name: "Review enabling writes", exact: true }).click(); };
  const confirm = async (target = page) => { await dialog(target).getByRole("button", { name: "Allow selected writes", exact: true }).click(); };
  await goto();
  const versions = await page.locator("link[href*='/member-assets/member.css'], script[src*='/member-assets/member.js']").evaluateAll((nodes) => nodes.map((node) => new URL(node.href || node.src).searchParams.get("v")));
  record("Apple permission browser uses exact candidate assets", versions.length === 2 && versions.every((value) => value === sourceVersion));
  record("Apple permissions show three distinct services without granting writes", await page.locator(".apple-service-permission").count() === 3 && posts.length === 0 && await page.locator('.apple-service-permission input:checked').count() === 0 && await card("apple_calendar").getByRole("button", { name: "Review enabling writes", exact: true }).isDisabled());
  record("Apple Settings separates reading writing macOS and action approvals", (await card("apple_calendar").textContent()).includes("ReadingConnected") && (await card("apple_calendar").textContent()).includes("WritingOff") && (await card("apple_calendar").textContent()).includes("Managed on your bridge Mac") && (await page.locator(".apple-service-permissions").textContent()).includes("Each action still needs your approval and a separate execution approval"));
  await goto(stalePage);
  await enable("apple_calendar", "Example calendar");
  record("Apple write review identifies exact selected service and locations before mutation", posts.length === 0 && (await dialog().textContent()).includes("Apple Calendar") && (await dialog().textContent()).includes("Example calendar") && !(await dialog().textContent()).includes("Example notes"));
  await dialog().getByRole("button", { name: "Go back", exact: true }).click();
  record("Cancelling Apple permission review makes no change", posts.length === 0 && items[0].write_access === false);
  await card("apple_calendar").getByRole("button", { name: "Review enabling writes", exact: true }).click(); await confirm();
  await status().filter({ hasText: "current Apple permissions" }).waitFor();
  record("Apple permission enable is exact scoped and approval-gated", posts.length === 1 && posts[0].body.enabled === true && posts[0].body.expected_revision === 0 && JSON.stringify(posts[0].body.scope_ids) === JSON.stringify(["calendar-scope"]) && (await card("apple_calendar").textContent()).includes("Allowed in selected locations · approval required") && await card("apple_calendar").getByRole("checkbox").isChecked());
  await enable("apple_calendar", "Example calendar", stalePage); expectHandledFailure(409); await confirm(stalePage);
  await status(stalePage).filter({ hasText: "current Apple permissions" }).waitFor();
  record("Stale Apple permissions refresh without a second grant", items[0].revision === 1 && posts.length === 2 && (await stalePage.locator("#state-banner").textContent()).includes("changed in another window") && (await card("apple_calendar", stalePage).textContent()).includes("Allowed in selected locations"));
  await card("apple_notes").getByRole("checkbox", { name: /^Example notes/ }).check(); await card("apple_notes").getByRole("checkbox", { name: /^Local notes/ }).check();
  record("Apple permissions cannot combine locations across Apple accounts", await card("apple_notes").getByRole("button", { name: "Review enabling writes", exact: true }).isDisabled() && (await card("apple_notes").textContent()).includes("Choose locations from one Apple account at a time"));
  await card("apple_notes").getByRole("checkbox", { name: /^Local notes/ }).uncheck();
  record("One-account Notes scope remains available", !await card("apple_notes").getByRole("button", { name: "Review enabling writes", exact: true }).isDisabled());

  loseNext = true; mismatchConfirmation = true;
  await enable("apple_reminders", "Example reminders"); await confirm();
  await status().filter({ hasText: "last permission change is not confirmed" }).waitFor();
  const postCount = posts.length;
  await goto();
  record("A mismatched Apple receipt cannot settle unknown permission state across reload", posts.length === postCount && await card("apple_reminders").getByRole("checkbox").isDisabled() && await page.getByRole("button", { name: "Retry saved permission change", exact: true }).isHidden());
  mismatchConfirmation = false;
  await page.getByRole("button", { name: "Check Apple permission status", exact: true }).click(); await status().filter({ hasText: "current Apple permissions" }).waitFor();
  record("Exact Apple request reconciliation unlocks without replaying POST", posts.length === postCount && !await card("apple_reminders").getByRole("checkbox").isDisabled() && items[1].revision === 1);

  rejectNext = true;
  await card("apple_notes").getByRole("checkbox", { name: /^Example notes/ }).check(); await card("apple_notes").getByRole("button", { name: "Review enabling writes", exact: true }).click(); await confirm();
  await status().filter({ hasText: "saved request was not recorded" }).waitFor();
  const firstAbsent = posts.at(-1); const absentCount = posts.length;
  await goto();
  record("An absent Apple change stays immutable and requires explicit retry after GET", posts.length === absentCount && await card("apple_notes").getByRole("checkbox", { name: /^Example notes/ }).isDisabled() && !await page.getByRole("button", { name: "Retry saved permission change", exact: true }).isDisabled());
  await page.getByRole("button", { name: "Retry saved permission change", exact: true }).click(); await status().filter({ hasText: "current Apple permissions" }).waitFor();
  record("Apple retry preserves the exact saved request body", posts.length === absentCount + 1 && JSON.stringify(posts.at(-1)) === JSON.stringify(firstAbsent) && items[2].revision === 1);
  await card("apple_calendar").getByRole("button", { name: "Turn off writes", exact: true }).click();
  record("Turning off Apple writing has a separate confirmation", items[0].write_access === true && (await dialog().textContent()).includes("Existing Apple items and read access are unchanged"));
  await dialog().getByRole("button", { name: "Turn off writes", exact: true }).click(); await status().filter({ hasText: "current Apple permissions" }).waitFor();
  record("Apple revocation preserves reading and shows writing off", items[0].state === "revoked" && items[0].read_access === true && posts.at(-1).body.enabled === false && posts.at(-1).body.scope_ids.length === 0 && (await card("apple_calendar").textContent()).includes("WritingOff"));
  items[0].state = "unavailable"; items[0].reason = "Reconnect the selected calendar on your bridge Mac.";
  await goto();
  record("Unavailable Apple capability explains repair without offering activation", await card("apple_calendar").getByRole("checkbox").isDisabled() && await card("apple_calendar").getByRole("button", { name: "Review enabling writes", exact: true }).isDisabled() && (await card("apple_calendar").textContent()).includes("Reconnect the selected calendar"));
  items[0].can_revoke = true;
  await goto();
  record("A stored Apple grant remains revocable during a source outage", !await card("apple_calendar").getByRole("button", { name: "Turn off writes", exact: true }).isDisabled());
  await card("apple_calendar").getByRole("button", { name: "Turn off writes", exact: true }).click(); await dialog().getByRole("button", { name: "Turn off writes", exact: true }).click(); await status().filter({ hasText: "current Apple permissions" }).waitFor();
  record("Revoking unavailable Apple permission prevents later automatic reactivation", items[0].can_revoke === false && items[0].write_access === false && posts.at(-1).body.enabled === false && await card("apple_calendar").getByRole("button", { name: "Turn off writes", exact: true }).isHidden());
  failNextRead = true; expectHandledFailure(503);
  await page.getByRole("button", { name: "Check Apple permission status", exact: true }).click(); await status().filter({ hasText: "could not be checked" }).waitFor();
  record("Apple status outage locks changes instead of guessing authority", await card("apple_reminders").getByRole("button", { name: "Turn off writes", exact: true }).isDisabled());
  await page.getByRole("button", { name: "Check Apple permission status", exact: true }).click(); await status().filter({ hasText: "current Apple permissions" }).waitFor();
  let viewReads = 0; otherPage.on("request", (request) => { if (new URL(request.url()).pathname.startsWith(endpoint)) viewReads += 1; });
  // These test-ingress headers exist only inside this credential-free fixture.
  await otherPage.setExtraHTTPHeaders({ "Tailscale-User-Login": "visual-adult.ts.net", "X-AdminMe-View-Member": "visual_principal" });
  await otherPage.goto(`${baseUrl}/member/settings?section=apple-services`, { waitUntil: "networkidle" });
  await otherPage.getByText("Return to your own private view to see or change your Apple service permissions.", { exact: true }).waitFor();
  record("Apple view-as neither fetches another member permission nor offers controls", viewReads === 0 && await otherPage.locator(".apple-service-permissions input, .apple-service-permissions button").count() === 0);
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 }); await goto();
    await auditAccessibility(page, `Apple service permissions ${width}`); await screenshot(page, `apple-service-permissions-${width}.png`);
  }
  await page.goto(`${baseUrl}/member/settings?section=connections`, { waitUntil: "networkidle" });
  record("Apple permission controls are discoverable from Connections", await page.getByRole("link", { name: "Review Apple service permissions", exact: true }).count() === 1);
  await page.goto(`${baseUrl}/member/settings?section=bridges`, { waitUntil: "networkidle" });
  record("Apple permission controls are discoverable from Devices and bridges", await page.getByRole("link", { name: "Review Apple service permissions", exact: true }).count() === 1);
  const noteEndpoint = "/api/member/v1/notes/commands";
  const noteRoute = /\/api\/member\/v1\/notes\/commands(?:\/status\?.*)?$/;
  const notePosts = []; let noteRequest = null; let noteState = "awaiting_openclaw_approval"; let noteMismatch = true;
  const noteHandler = async (route) => {
    const request = route.request(); const url = new URL(request.url());
    if (request.method() === "POST") {
      noteRequest = request.postDataJSON(); notePosts.push(noteRequest);
      // The request is durably recorded; its direct response has no receipt.
      await route.fulfill({ status: 202, contentType: "application/json", body: "{}" }); return;
    }
    const result = noteRequest ? { request_id: noteRequest.request_id, command_id: noteRequest.request_id, domain_approval_event_id: "synthetic-note-domain-approval", state: noteState, request: { ...noteRequest, ...(noteMismatch ? { body: "A different note must not settle this request" } : {}) } } : { request_id: url.searchParams.get("request_id"), state: "not_recorded" };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(result) });
  };
  await page.route(noteRoute, noteHandler);
  await page.goto(`${baseUrl}/member/notes`, { waitUntil: "networkidle" });
  const newNote = page.locator(".new-apple-note");
  await newNote.getByRole("status").filter({ hasText: "Choose an allowed folder" }).waitFor();
  record("New Apple note offers only granted folders", await newNote.getByRole("combobox", { name: "Apple Notes folder", exact: true }).locator("option").count() === 1 && await newNote.getByRole("combobox").inputValue() === "notes-scope");
  const noteTitle = "A synthetic note"; const noteText = "Plain text <script>never execute this</script> & exact content.";
  await newNote.getByRole("textbox", { name: "Note title (optional)", exact: true }).fill(noteTitle);
  await newNote.getByRole("textbox", { name: "Note text", exact: true }).fill(noteText);
  await newNote.getByRole("button", { name: "Review new note", exact: true }).click();
  record("New Apple note preview preserves exact text and destination without submission", notePosts.length === 0 && (await dialog().textContent()).includes("Example notes") && (await dialog().textContent()).includes(noteTitle) && (await dialog().textContent()).includes(noteText) && await dialog().locator("script").count() === 0 && (await dialog().textContent()).includes("separate execution approval"));
  await dialog().getByRole("button", { name: "Go back", exact: true }).click();
  record("Cancelling a note preserves its draft without approval", notePosts.length === 0 && await newNote.getByRole("textbox", { name: "Note text", exact: true }).inputValue() === noteText);
  await newNote.getByRole("button", { name: "Review new note", exact: true }).click(); await dialog().getByRole("button", { name: "Approve this note", exact: true }).click();
  await newNote.getByRole("status").filter({ hasText: "note approval is not confirmed" }).waitFor();
  await page.reload({ waitUntil: "networkidle" }); await newNote.getByRole("status").filter({ hasText: "note approval is not confirmed" }).waitFor();
  record("Unknown note approval remains locked across reload and cannot accept mismatched text", notePosts.length === 1 && await newNote.getByRole("button", { name: "Review new note", exact: true }).isDisabled() && await newNote.getByRole("button", { name: "Prepare another note", exact: true }).isHidden() && noteRequest.operation === "create_note" && noteRequest.provider_account_id === "synthetic-notes-account" && noteRequest.account_external_id === "example-apple" && noteRequest.folder_external_id === "example-notes" && noteRequest.body === noteText && noteRequest.title === noteTitle);
  noteMismatch = false; await newNote.getByRole("button", { name: "Check note status", exact: true }).click();
  await newNote.getByRole("status").filter({ hasText: "Your exact note is approved" }).waitFor();
  record("Exact note receipt requires independent execution and never claims provider completion", notePosts.length === 1 && (await newNote.getByRole("status").textContent()).includes("separate execution approval is still required") && !await newNote.getByRole("button", { name: "Prepare another note", exact: true }).isHidden());
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    await auditAccessibility(page, `New Apple note ${width}`); await screenshot(page, `new-apple-note-${width}.png`);
  }
  const chatPosts = [];
  const nativeEndpoint = "/api/member/v1/chat/reply-approvals";
  const nativeEmpty = async (route) => { await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) }); };
  const chatRoute = async (route) => {
    chatPosts.push(route.request().postDataJSON());
    await route.fulfill({ status: 200, contentType: "text/event-stream", body: 'data: {"kind":"delta","text":"Synthetic conversation; no native decision or provider effect."}\n\ndata: {"kind":"final"}\n\n' });
  };
  await page.route(`**${nativeEndpoint}`, nativeEmpty); await page.route("**/api/member/v1/chat/stream", chatRoute);
  await newNote.getByRole("button", { name: "Continue to execution approval", exact: true }).click();
  await newNote.getByRole("status").filter({ hasText: "Your exact note is approved" }).waitFor();
  await page.waitForFunction(() => !document.querySelector("#chat-send").disabled);
  record("Notes explicit continuation hands exact immutable approval to the genuine pending-action tool", chatPosts.length === 1 && chatPosts[0].message.includes("administrateme_execute_pending_provider_action") && chatPosts[0].message.includes("synthetic-note-domain-approval") && (await page.locator("#chat-messages").textContent()).includes("Please request execution approval for the exact Apple note I just approved.") && !(await page.locator("#chat-messages").textContent()).includes("synthetic-note-domain-approval") && await newNote.getByRole("button", { name: "Continue to execution approval", exact: true }).isDisabled());
  await page.locator("#chat-close").click(); await page.reload({ waitUntil: "networkidle" });
  await newNote.getByRole("status").filter({ hasText: "Your exact note is approved" }).waitFor();
  record("Notes execution handoff is not repeated after reload", chatPosts.length === 1 && notePosts.length === 1 && await newNote.getByRole("button", { name: "Continue to execution approval", exact: true }).isDisabled());
  await page.unroute("**/api/member/v1/chat/stream", chatRoute); await page.unroute(`**${nativeEndpoint}`, nativeEmpty);
  await auditAppleNativeApprovals({ page, baseUrl, record, screenshot });
  noteState = "unknown"; await newNote.getByRole("button", { name: "Check note status", exact: true }).click();
  await newNote.getByRole("status").filter({ hasText: "provider result is unknown" }).waitFor();
  record("Unknown Apple provider result never offers another note or a resend", notePosts.length === 1 && await newNote.getByRole("button", { name: "Prepare another note", exact: true }).isHidden() && await newNote.getByRole("button", { name: "Retry saved note approval", exact: true }).isHidden());
  await otherPage.goto(`${baseUrl}/member/notes`, { waitUntil: "networkidle" });
  await otherPage.getByText("Return to your own private view to prepare an Apple note.", { exact: true }).waitFor();
  record("View-as cannot prepare an Apple note or retrieve private writing scopes", viewReads === 0 && await otherPage.locator(".new-apple-note input, .new-apple-note textarea, .new-apple-note button").count() === 0);
  await auditAppleActionHandoffs({ page, baseUrl, record });
  await page.unroute(noteRoute, noteHandler);
  await page.unroute(match, routeHandler); await stalePage.unroute(match, routeHandler);
}
