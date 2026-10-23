// Browser rendering and transport uncertainty only. Rows here are synthetic;
// genuine native relay ownership and decision binding have separate CI tests.
export async function auditAppleNativeApprovals({ page, baseUrl, record, screenshot }) {
  const endpoint = "/api/member/v1/chat/reply-approvals";
  let rows = []; let ambiguous = false; const decisions = [];
  const route = async (intercept) => {
    const request = intercept.request();
    if (request.method() === "GET") { await intercept.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: rows }) }); return; }
    decisions.push(request.postDataJSON());
    await intercept.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ambiguous ? {} : { resolved: true, sent: false }) });
  };
  await page.route(`**${endpoint}`, route);
  const now = await page.evaluate(() => Date.now());
  const sample = (provider, suffix, extra = {}) => ({ kind: "provider_action", provider, operation: provider === "apple_calendar" ? "create_event" : provider === "apple_reminders" ? "create_reminder" : "create_note", approval_id: `plugin:apple-ui-${suffix}`, revision: "a".repeat(64), expires_at_ms: now + 120000, can_allow: true, destination: "Example selected location", review: [{ label: "Title", value: `Exact ${suffix} action` }, { label: "Text", value: "Unmodified <script>literal text</script> & detail." }, { label: "Time", value: "2035-01-02T12:00:00Z" }], ...extra });
  const refresh = () => page.evaluate(() => refreshChatReplyApprovals(() => true));
  const clear = () => page.evaluate(() => clearChatReplyApprovals());
  const panel = page.locator(".apple-action-approval");
  await page.locator("#chat-open").click();
  for (const [provider, suffix, decision, width] of [["apple_calendar", "calendar", "allow-once", 1440], ["apple_notes", "notes", "deny", 390]]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    rows = [sample(provider, suffix)]; await refresh();
    await panel.getByRole("heading", { name: "Allow this Apple change?", exact: true }).waitFor();
    record(`Native ${provider} review displays exact action instead of reply labels`, (await panel.textContent()).includes(`Exact ${suffix} action`) && (await panel.textContent()).includes(rows[0].destination) && (await panel.textContent()).includes(rows[0].review[1].value) && await panel.locator("script").count() === 0 && await panel.getByRole("button").count() === 2 && await panel.getByRole("button", { name: /reply|send|always/i }).count() === 0);
    record(`Native ${provider} card fits at ${width}`, await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await screenshot(page, `apple-native-${suffix}-${width}.png`);
    const button = panel.getByRole("button", { name: decision === "allow-once" ? "Allow this change once" : "Don’t change Apple", exact: true });
    await button.focus(); record(`Native ${provider} decision is keyboard reachable`, await button.evaluate((node) => node === document.activeElement)); await button.click();
    await panel.getByRole("status").filter({ hasText: decision === "allow-once" ? "allowed once" : "declined" }).waitFor();
    record(`Native ${provider} sends only its exact one-time decision`, decisions.at(-1).approval_id === rows[0].approval_id && decisions.at(-1).revision === rows[0].revision && decisions.at(-1).decision === decision && JSON.stringify(Object.keys(decisions.at(-1)).sort()) === JSON.stringify(["approval_id", "decision", "revision"]) && await panel.getByRole("button", { name: "Allow this change once", exact: true }).isDisabled());
    rows = []; await refresh();
  }
  rows = [sample("apple_reminders", "unknown")]; ambiguous = true; await refresh();
  await panel.getByRole("button", { name: "Allow this change once", exact: true }).click();
  await panel.getByRole("status").filter({ hasText: "No execution decision is confirmed" }).waitFor();
  const unknownCount = decisions.length;
  await clear(); await refresh();
  record("Unknown native Apple decision stays locked when polling recreates its card", decisions.length === unknownCount && await panel.getByRole("button", { name: "Allow this change once", exact: true }).isDisabled() && await panel.getByRole("button", { name: "Don’t change Apple", exact: true }).isDisabled());
  await page.reload({ waitUntil: "networkidle" }); await page.locator("#chat-open").click(); await refresh();
  record("Unknown native Apple decision survives navigation without replay", decisions.length === unknownCount && await panel.getByRole("button", { name: "Allow this change once", exact: true }).isDisabled() && (await panel.textContent()).includes("will not retry"));
  rows = [sample("apple_calendar", "changed", { can_allow: false })]; await refresh();
  record("Changed Apple approval cannot be allowed", await panel.getByRole("button", { name: "Allow this change once", exact: true }).isDisabled() && (await panel.textContent()).includes("changed or expired"));
  rows = [sample("apple_calendar", "expiry", { expires_at_ms: now + 50 })]; await refresh();
  await panel.getByRole("status").filter({ hasText: "This execution approval expired" }).waitFor();
  record("Expired native Apple approval disables both decisions without retry", decisions.length === unknownCount && await panel.getByRole("button", { name: "Allow this change once", exact: true }).isDisabled() && await panel.getByRole("button", { name: "Don’t change Apple", exact: true }).isDisabled());
  rows = [{ approval_id: "plugin:reply-ui-preserved", revision: "b".repeat(64), expires_at_ms: now + 120000, can_allow: true, kind: "cos_response", channel: "imessage", recipient: "Synthetic recipient", subject: "", body: "Synthetic reply text" }]; await refresh();
  const reply = page.locator(".cos-send-approval");
  record("Existing CoS reply approval retains its labels and exact text", await reply.getByRole("heading", { name: "Ready to send this reply?", exact: true }).count() === 1 && await reply.getByRole("button", { name: "Allow this reply once", exact: true }).count() === 1 && await reply.getByRole("button", { name: "Don’t send", exact: true }).count() === 1 && (await reply.textContent()).includes("Synthetic reply text"));
  rows = []; await refresh(); await page.locator("#chat-close").click(); await page.unroute(`**${endpoint}`, route);
}
