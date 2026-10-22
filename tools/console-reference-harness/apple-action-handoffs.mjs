// Focused browser boundary: real action handlers consume synthetic immutable
// approval receipts; chat transport does not contact OpenClaw or any provider.
export async function auditAppleActionHandoffs({ page, baseUrl, record }) {
  const chats = []; const reminderRequests = []; const triageRequests = [];
  const chat = async (route) => { chats.push(route.request().postDataJSON()); await route.fulfill({ status: 200, contentType: "text/event-stream", body: 'data: {"kind":"delta","text":"Synthetic handoff only; no execution decision or provider effect."}\n\ndata: {"kind":"final"}\n\n' }); };
  const native = async (route) => { await route.fulfill({ status: 200, contentType: "application/json", body: '{"items":[]}' }); };
  const reminders = async (route) => {
    const body = route.request().postDataJSON(); reminderRequests.push(body);
    await route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify({ accepted: true, state: "awaiting_openclaw_approval", domain_approval_event_id: `synthetic-domain-${body.provider}` }) });
  };
  const triage = async (route) => {
    triageRequests.push(route.request().postDataJSON());
    const id = new URL(route.request().url()).pathname.split("/").at(-2);
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ state: "approved_awaiting_openclaw", pending_provider_action_event_id: `synthetic-domain-${id}`, destination: { state: "awaiting_openclaw_approval", label: "Apple destination", surface: "calendar", href: "/member/calendar" } }) });
  };
  await page.route("**/api/member/v1/chat/stream", chat); await page.route("**/api/member/v1/chat/reply-approvals", native);
  await page.route("**/api/member/v1/reminders/commands", reminders); await page.route("**/api/member/v1/triage/apple-handoff-*/approve", triage);
  const banner = page.locator("#state-banner");
  const button = banner.getByRole("button", { name: "Continue to execution approval", exact: true });
  async function continueOnce(provider, approvalId) {
    const before = chats.length;
    await button.waitFor();
    record(`${provider} action approval waits for an explicit execution handoff`, !await button.isDisabled() && chats.length === before && !(await banner.textContent()).includes(approvalId));
    await button.click(); await page.waitForFunction(() => !document.querySelector("#chat-send").disabled);
    record(`${provider} execution handoff names only its immutable domain receipt`, chats.length === before + 1 && chats.at(-1).message.includes("administrateme_execute_pending_provider_action") && chats.at(-1).message.includes(approvalId) && await button.isDisabled() && !(await page.locator("#chat-messages").textContent()).includes(approvalId));
    await page.locator("#chat-close").click();
    await page.evaluate(({ provider, approvalId }) => { const banner = document.querySelector("#state-banner"); banner.replaceChildren(); appendAppleExecutionHandoff(banner, provider, approvalId); }, { provider, approvalId });
    record(`${provider} rerender cannot repeat the same execution handoff`, chats.length === before + 1 && await button.isDisabled() && (await banner.textContent()).includes("already requested"));
  }
  await page.goto(`${baseUrl}/member/reminders`, { waitUntil: "networkidle" });
  await page.evaluate(() => runReminderCommand({ provider: "apple_reminders", provider_account_id: "synthetic-reminder-account", list_external_id: "synthetic-list", item_external_id: "synthetic-reminder", last_event_id: "synthetic-reminder-revision", provider_revision: "synthetic-native-revision" }, "complete_reminder"));
  record("Apple Reminders action preserves its normal approval request", reminderRequests.length === 1 && reminderRequests[0].provider === "apple_reminders" && reminderRequests[0].operation === "complete_reminder" && chats.length === 0);
  await continueOnce("apple_reminders", "synthetic-domain-apple_reminders");
  await page.evaluate(() => runReminderCommand({ provider: "google_tasks", provider_account_id: "synthetic-google-account", list_external_id: "synthetic-list", item_external_id: "synthetic-task", last_event_id: "synthetic-task-revision", provider_revision: "synthetic-native-revision" }, "complete_task"));
  record("Google task approval flow gains no Apple execution control", reminderRequests.length === 2 && await button.count() === 0 && chats.length === 1);
  for (const provider of ["apple_calendar", "apple_reminders"]) {
    await page.goto(`${baseUrl}/member/triage`, { waitUntil: "networkidle" });
    const proposalId = `apple-handoff-${provider}`;
    await page.evaluate(({ provider, proposalId }) => decideTriage({ proposal_id: proposalId, last_event_id: "synthetic-triage-revision", route: { provider } }, "approve", []), { provider, proposalId });
    record(`${provider} Triage approval exposes its separate execution handoff`, await button.count() === 1 && triageRequests.at(-1).expected_last_event_id === "synthetic-triage-revision");
    await continueOnce(provider, `synthetic-domain-${proposalId}`);
  }
  await page.evaluate(() => { const banner = document.querySelector("#state-banner"); banner.replaceChildren(); appendAppleExecutionHandoff(banner, "google_calendar", "synthetic-google-approval"); });
  record("Calendar execution helper refuses non-Apple provider receipts", await button.count() === 0 && chats.length === 3);
  await page.unroute("**/api/member/v1/chat/stream", chat); await page.unroute("**/api/member/v1/chat/reply-approvals", native);
  await page.unroute("**/api/member/v1/reminders/commands", reminders); await page.unroute("**/api/member/v1/triage/apple-handoff-*/approve", triage);
}
