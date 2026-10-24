"use strict";

const state = {
  bootstrap: null,
  surface: null,
  controller: null,
  assistantNode: null,
  todayView: "brief",
  carouselIndex: 0,
  carouselItems: [],
  triageView: "ready",
  triageSort: "attention",
  triageSource: "all",
  triageKind: "all",
  triageSelected: null,
  triageMatter: null,
  triageFocusPending: false,
  triageBannerKind: null,
  triageRefreshRequired: false,
  triageLoadGeneration: 0,
  triageCursorRefreshPending: false,
  triageOperationGeneration: 0,
  triageOperationPending: false,
  addForReviewOperationGeneration: 0,
  addForReviewOperationPending: false,
  addForReviewRecoveryRequired: false,
  commsView: "focus",
  commsAccount: "all",
  commsThread: null,
  remindersView: "now",
  remindersAccount: "all",
  remindersList: "all",
  reminderItem: null,
  reminderDeepLinkPending: false,
  calendarView: "week",
  calendarAnchor: null,
  calendarToday: null,
  calendarLens: "capacity",
  calendarHiddenSources: [],
  calendarSelectedEvent: null,
  calendarDuration: 60,
  peopleLens: "all",
  peopleSelected: null,
  peopleQuery: "",
  peopleResults: null,
  peopleDetail: null,
  notesLens: "all",
  notesSource: "all",
  notesSelected: null,
  notesQuery: "",
  notesResults: null,
  financeView: "overview",
  financeScenario: null,
  settingsSection: "overview",
  sourceSettingsPending: false,
  sourceSettingsRefreshRequired: false,
};
const $ = (selector) => document.querySelector(selector);
const dialogReturnFocus = new WeakMap();
const text = (tag, value, className) => {
  const node = document.createElement(tag);
  node.textContent = value == null ? "" : String(value);
  if (className) node.className = className;
  return node;
};

function currentSurface() {
  const parts = location.pathname.split("/").filter(Boolean);
  if (parts[1] === "quests" && parts[2]) return `quest:${parts[2]}`;
  const value = parts[1];
  return value || "today";
}

function replaceQuery(values) {
  const params = new URLSearchParams(location.search);
  for (const [key, value] of Object.entries(values)) {
    if (value == null || value === "") params.delete(key);
    else params.set(key, String(value));
  }
  const query = params.toString();
  history.replaceState(null, "", `${location.pathname}${query ? `?${query}` : ""}`);
}

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", cache: "no-store", ...options });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof body.detail === "string" ? body.detail : body.detail?.reason;
    const error = new Error(detail || "That view is unavailable.");
    error.status = response.status;
    error.body = body;
    throw error;
  }
  return body;
}

function renderNavigation(items) {
  const nav = $("#primary-nav");
  nav.replaceChildren();
  const active = currentSurface();
  for (const item of items) {
    const link = text("a", item.label, "nav-link");
    link.href = item.href;
    if (item.id === active) link.setAttribute("aria-current", "page");
    nav.append(link);
  }
  if (active === "triage") queueMicrotask(() => { nav.scrollLeft = 0; });
}

function combinedNavigation(value) {
  const standard = [...value.navigation];
  for (const quest of value.temporary_surfaces || []) {
    standard.push({ id: `quest:${quest.stable_id}`, label: quest.title, href: quest.href });
  }
  return standard;
}

function card(title, items, emptyCopy = "Nothing needs your attention here.") {
  const section = document.createElement("section");
  section.className = "card";
  section.append(text("h2", title));
  if (!Array.isArray(items) || items.length === 0) {
    section.append(text("p", emptyCopy, "empty"));
    return section;
  }
  const list = document.createElement("ul");
  list.className = "item-list";
  for (const item of items.slice(0, 20)) {
    const li = document.createElement("li");
    li.className = "item";
    li.append(text("strong", item.title || item.display_name || item.description || item.text || item.name || item.kind || "Household item"));
    const status = item.status || item.state;
    const meta = text("span", status ? memberStatusLabel(status) : item.occurred_at || item.due_at || item.due_date || "Ready", "meta");
    if (status) meta.classList.add("state-label", `state-${String(status).replaceAll("_", "-")}`);
    li.append(meta);
    if (item.reason) li.append(text("small", item.reason));
    if (item.surface_link?.href) {
      const open = text("a", "Open item", "link-button"); open.href = item.surface_link.href; li.append(open);
    }
    if (item.last_event_id) {
      const button = text("button", "View evidence", "link-button");
      button.type = "button";
      button.addEventListener("click", () => showEvidence(item.last_event_id));
      li.append(button);
    }
    list.append(li);
  }
  section.append(list);
  return section;
}

function moneyMinor(value, { compact = false } = {}) {
  if (value == null || !Number.isFinite(Number(value))) return "Unavailable";
  const amount = Number(value) / 100;
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: compact ? 0 : 2,
    notation: compact ? "compact" : "standard",
  }).format(amount);
}


function publishedMoneyMinor(value, currency) {
  if (value == null) return "Unknown";
  if (!Number.isSafeInteger(value)) return "Unavailable";
  if (typeof Intl.supportedValuesOf !== "function" || !Intl.supportedValuesOf("currency").includes(currency)) return `${value} ${currency} minor units`;
  const formatter = new Intl.NumberFormat(undefined, { style: "currency", currency, currencyDisplay: "code", numberingSystem: "latn" });
  const digits = formatter.resolvedOptions().maximumFractionDigits;
  const minor = BigInt(value); const scale = 10n ** BigInt(digits);
  const major = minor / scale; const remainder = minor < 0n ? -(minor % scale) : minor % scale;
  // Keep every producer-rounded digit, including near the safe integer bound.
  // Floating-point division can lose a cent at that magnitude.
  const fraction = remainder.toString().padStart(digits, "0");
  const signedMajor = minor < 0n && major === 0n ? -0 : major;
  return formatter.formatToParts(signedMajor).map(part => part.type === "fraction" ? fraction : part.value).join("");
}

function triageReviewView(item) {
  const attentionView = { needs_you: "ready", possibilities: "low_signal", alerts: "alerts", history: "recent" }[item?.attention_view];
  if (state.surface?.human_triage_management?.active && attentionView) return attentionView;
  if (item?.decision_disposition && item.decision_disposition !== "decision") return "all_candidates";
  if (item?.review_state?.state === "needs_interpretation") return "needs_interpretation";
  if (item?.review_state?.state === "needs_details") return "needs_details";
  if (item?.review_state?.state === "ready") return item.queue_lane === "standard" ? "low_signal" : "ready";
  return "all_candidates";
}

function triageLanePresentation(lane) {
  return {
    direct_instruction: ["Your direct instruction", "You explicitly asked AdministrateMe to remember or act on this matter."],
    member_promise: ["Your promise", "This is grounded in an exact commitment you made."],
    scheduled_deadline: ["Scheduled or deadline", "This matter has a verified date or deadline."],
    ordinary_obligation: ["Personal obligation", "The source supports a responsibility that needs your review."],
    waiting_follow_up: ["Waiting or follow-up", "This matter is waiting on someone or has a follow-up point."],
    clarification_conflict: ["Needs clarification", "Evidence conflicts or is not strong enough for an ordinary obligation."],
    alerts_safety: ["Alert or safety", "This is separated from the ordinary work queue."],
    low_confidence_review: ["Needs closer review", "Meaning or ownership needs your review before this can be treated as an obligation."],
    possible_opportunity: ["Possible opportunity", "This is optional or conditional; it does not establish a commitment."],
    security: ["Account or security", "A reviewed security phrase appears in this already-eligible item."],
    time_bound: ["Date or deadline", "This proposal contains an actual date or deadline."],
    financial: ["Money", "This proposal contains a reviewed bill or payment signal."],
    member_priority: ["Your priority", "You or a typed source marked this as a priority."],
    known_human_request: ["Known person request", "A known correspondent with prior interaction made an actionable request."],
    recurring_exception: ["Recurring issue", "A recurrence appears missed, overdue, failed, or exceptional."],
    standard: ["Other review", "No explicit security, date, money, promise, priority, or recurring-exception fact was found."],
  }[lane] || ["Review", "This proposal is waiting for a human decision."];
}

function triageCandidateDate(item) {
  const candidate = item?.candidate || {};
  return candidate.due || candidate.due_at || candidate.due_date || candidate.deadline || candidate.start || candidate.start_at || null;
}

function triageDateLabel(value) {
  if (!value) return null;
  const raw = String(value);
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) {
    const [year, month, day] = raw.split("-").map(Number);
    return new Date(year, month - 1, day).toLocaleDateString(undefined, { dateStyle: "medium" });
  }
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.valueOf())) return String(value);
  const hasTime = raw.includes("T");
  return parsed.toLocaleString(undefined, hasTime ? { dateStyle: "medium", timeStyle: "short" } : { dateStyle: "medium" });
}

function triageRequestParams({ cursor = null } = {}) {
  const params = new URLSearchParams({ triage_lens: "all_candidates", triage_sort: state.triageSort });
  if (state.triageSource !== "all") params.set("triage_source", state.triageSource);
  if (state.triageKind !== "all") params.set("triage_kind", state.triageKind);
  if (cursor) params.set("triage_cursor", cursor);
  params.set("triage_limit", "30");
  return params;
}

async function reloadActiveSurface({ triageOperationToken = null, triageFocus = true, triageReconcileExact = false } = {}) {
  const active = currentSurface();
  if (active.startsWith("quest:")) return;
  let path = `/api/member/v1/surfaces/${encodeURIComponent(active)}`;
  if (active === "calendar") {
    const params = new URLSearchParams({ view: state.calendarView });
    if (state.calendarAnchor) params.set("date", state.calendarAnchor);
    path += `?${params}`;
  }
  if (active === "triage") {
    const params = triageRequestParams();
    if (triageFocus && state.triageSelected) params.set("triage_item", state.triageSelected);
    path += `?${params}`;
  }
  if (active === "notes") {
    state.notesResults = null; state.notesQuery = ""; replaceQuery({ q: null });
  }
  if (active === "people") {
    state.peopleResults = null; state.peopleQuery = ""; state.peopleDetail = null;
    replaceQuery({ q: null });
  }
  const data = await api(path);
  if (active === "triage") {
    if (triageOperationToken != null && !triageOperationIsCurrent(triageOperationToken)) return null;
    if (state.triageRefreshRequired) return null;
    if (triageReconcileExact && state.triageSelected) {
      const exact = [...(data.items || []), ...(data.recent_items || [])].find((candidate) => candidate.proposal_id === state.triageSelected);
      if (data.focus?.state === "resolved") state.triageView = "recent";
      else if (data.focus?.state === "pending" && exact && !triageCandidateMatches(exact, state.triageView)) state.triageView = triageReviewView(exact);
      state.triageMatter = data.focus?.state === "not_found" ? null : data.focus?.matter_id || state.triageMatter;
      state.triageFocusPending = data.focus?.state !== "not_found";
      replaceQuery({ view: state.triageView, item: state.triageSelected, matter: state.triageMatter });
    }
  }
  renderSurface(data, { preserveTriageView: triageReconcileExact });
  return data;
}

function triageInteractionLocked() {
  return state.triageOperationPending || state.triageCursorRefreshPending || state.triageRefreshRequired;
}

function triageLockControls() {
  return document.querySelectorAll(
    "#surface button, #surface input, #surface select, #surface textarea, "
    + "#add-for-review, #action-dialog .add-for-review-form button:not([data-add-recovery]), "
    + "#action-dialog .add-for-review-form input, #action-dialog .add-for-review-form select, "
    + "#action-dialog .add-for-review-form textarea, #action-dialog [data-close-action]",
  );
}

function applyTriageInteractionLock() {
  if (!triageInteractionLocked()) return;
  for (const control of triageLockControls()) {
    if (!control.dataset.triagePriorDisabled) {
      control.dataset.triagePriorDisabled = control.dataset.addForReviewPriorDisabled ?? String(control.disabled);
    }
    control.disabled = true;
  }
}

function releaseTriageInteractionLock() {
  for (const control of document.querySelectorAll("[data-triage-prior-disabled]")) {
    control.disabled = control.dataset.triagePriorDisabled === "true" || addForReviewInteractionLocked();
    delete control.dataset.triagePriorDisabled;
  }
}

function addForReviewInteractionLocked() {
  return state.addForReviewOperationPending || state.addForReviewRecoveryRequired;
}

function addForReviewLockControls() {
  return document.querySelectorAll(
    "#add-for-review, #action-dialog .add-for-review-form button:not([data-add-recovery]), "
    + "#action-dialog .add-for-review-form input, #action-dialog .add-for-review-form select, "
    + "#action-dialog .add-for-review-form textarea, #action-dialog [data-close-action]",
  );
}

function applyAddForReviewInteractionLock() {
  if (!addForReviewInteractionLocked()) return;
  for (const control of addForReviewLockControls()) {
    if (!control.dataset.addForReviewPriorDisabled) {
      control.dataset.addForReviewPriorDisabled = control.dataset.triagePriorDisabled ?? String(control.disabled);
    }
    control.disabled = true;
  }
}

function releaseAddForReviewInteractionLock() {
  for (const control of document.querySelectorAll("[data-add-for-review-prior-disabled]")) {
    control.disabled = control.dataset.addForReviewPriorDisabled === "true" || triageInteractionLocked();
    delete control.dataset.addForReviewPriorDisabled;
  }
}

function beginAddForReviewOperation() {
  if (addForReviewInteractionLocked()) return null;
  state.addForReviewOperationPending = true;
  state.addForReviewOperationGeneration += 1;
  applyAddForReviewInteractionLock();
  return state.addForReviewOperationGeneration;
}

function addForReviewOperationIsCurrent(token) {
  return state.addForReviewOperationPending && token === state.addForReviewOperationGeneration;
}

function finishAddForReviewOperation(token) {
  if (!addForReviewOperationIsCurrent(token)) return;
  state.addForReviewOperationPending = false;
  state.addForReviewOperationGeneration += 1;
  if (state.addForReviewRecoveryRequired) applyAddForReviewInteractionLock();
  else releaseAddForReviewInteractionLock();
}

function latchAddForReviewRecovery(message, recoveryHref = null) {
  state.addForReviewRecoveryRequired = true;
  const banner = $("#state-banner");
  banner.hidden = false;
  banner.textContent = message;
  const form = document.querySelector("#action-dialog .add-for-review-form");
  if (form) {
    let recovery = form.querySelector("[data-add-recovery-status]");
    if (!recovery) {
      recovery = document.createElement("section");
      recovery.className = "state-card";
      recovery.dataset.addRecoveryStatus = "true";
      recovery.setAttribute("role", "alert");
      const recoveryMessage = text("p", message, "state-note");
      const destination = recoveryHref || "/member/triage?view=all_candidates";
      const reload = text("button", recoveryHref ? "Open saved Triage item" : "Check Triage before retrying", "button button-primary");
      reload.type = "button";
      reload.dataset.addRecovery = "true";
      reload.addEventListener("click", () => window.location.assign(destination));
      recovery.append(recoveryMessage, reload);
      form.append(recovery);
    }
  }
  applyAddForReviewInteractionLock();
  const recoveryControl = form?.querySelector("[data-add-recovery]");
  if (recoveryControl) {
    recoveryControl.disabled = false;
    delete recoveryControl.dataset.addForReviewPriorDisabled;
    recoveryControl.focus();
  }
}

function invalidateTriageLoad() {
  state.triageLoadGeneration += 1;
  const button = document.querySelector("[data-triage-more]");
  if (button?.dataset.triageLoading === "true") {
    button.disabled = false;
    button.textContent = "Show more";
    delete button.dataset.triageLoading;
  }
}

function beginTriageOperation() {
  if (triageInteractionLocked()) return null;
  invalidateTriageLoad();
  state.triageOperationPending = true;
  state.triageOperationGeneration += 1;
  applyTriageInteractionLock();
  return state.triageOperationGeneration;
}

function triageOperationIsCurrent(token) {
  return state.triageOperationPending && token === state.triageOperationGeneration;
}

function finishTriageOperation(token) {
  if (!triageOperationIsCurrent(token)) return;
  state.triageLoadGeneration += 1;
  state.triageOperationPending = false;
  state.triageOperationGeneration += 1;
  if (state.triageRefreshRequired) applyTriageInteractionLock();
  else releaseTriageInteractionLock();
}

function latchTriageRefreshFailure(message) {
  state.triageBannerKind = "refresh_failure";
  state.triageRefreshRequired = true;
  const banner = $("#state-banner");
  banner.hidden = false;
  banner.textContent = message;
  applyTriageInteractionLock();
}

function requestDefinitelyRejected(error) {
  if (error.body?.detail?.capture_state === "intent_and_features_recorded") return false;
  return Number.isInteger(error.status) && error.status >= 400 && error.status < 500 && error.status !== 408;
}

function triageDecisionHasDurableReceipt(item, action, requestId) {
  if (!item) return false;
  if (action === "dismiss") {
    return item.state === "dismissed"
      && Boolean(item.dismissal_event_id)
      && item.dismissal_request_id === requestId;
  }
  if (!item.confirmation_event_id || item.confirmation_request_id !== requestId) return false;
  if (item.state === "approved") {
    return ["approved_awaiting_openclaw", "approved_awaiting_provider"].includes(item.resolution_reason)
      && Boolean(item.domain_approval_event_id)
      && Boolean(item.materialization_event_id);
  }
  if (item.state === "failed") return Boolean(item.outcome_event_id);
  if (item.state !== "confirmed") return false;
  return Boolean(item.outcome_event_id) || (item.materialized_event_ids || []).length > 0;
}

async function reconcileTriageDecision(item, action, requestId, token, error, banner, { mayReportRejection = false } = {}) {
  try {
    const refreshed = await reloadActiveSurface({ triageOperationToken: token, triageReconcileExact: true });
    if (!refreshed || !triageOperationIsCurrent(token)) return "unresolved";
    const exact = [...(refreshed.items || []), ...(refreshed.recent_items || [])].find((candidate) => candidate.proposal_id === item.proposal_id);
    if (refreshed.focus?.requested_candidate_id === item.proposal_id && exact && triageDecisionHasDurableReceipt(exact, action, requestId)) {
      const operation = action === "approve" ? "confirmation" : "dismissal";
      state.triageBannerKind = "reconciliation";
      banner.hidden = false;
      banner.textContent = `The ${operation} response was interrupted, but Triage reconciled this exact candidate as ${memberStatusLabel(exact.state).toLowerCase()}. Review this receipt before taking another action.`;
      if (action === "approve" && exact.resolution_reason === "approved_awaiting_openclaw") appendAppleExecutionHandoff(banner, exact.route?.provider, exact.domain_approval_event_id);
      return "settled";
    }
    if (mayReportRejection && refreshed.focus?.requested_candidate_id === item.proposal_id && ["pending_interpretation", "pending_review"].includes(exact?.state)) {
      state.triageBannerKind = "error";
      banner.hidden = false;
      banner.textContent = `${error.message} Nothing was confirmed or dismissed.`;
      return "rejected";
    }
    if (action === "approve" && exact?.state === "approved" && exact.domain_approval_event_id) return "continuation_required";
    return "unresolved";
  } catch (refreshError) {
    return "unresolved";
  }
}

async function decideTriage(item, action, buttons, feedbackReason = null, { operationToken = null, contextCorrection = null } = {}) {
  const token = operationToken ?? beginTriageOperation();
  if (token == null || !triageOperationIsCurrent(token)) return;
  const banner = $("#state-banner"); banner.hidden = false;
  state.triageBannerKind = "progress";
  banner.textContent = action === "approve" ? "Confirming this candidate through the household gate…" : "Dismissing this candidate…";
  const workingView = state.triageView === "recent" ? "all_candidates" : state.triageView;
  let decisionRecorded = false;
  const decisionRequestId = crypto.randomUUID();
  const decisionRequest = {
    method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
    body: JSON.stringify({ request_id: decisionRequestId, expected_last_event_id: item.last_event_id, reason: action === "dismiss" ? "Dismissed from Triage" : null, feedback_reason: action === "dismiss" ? feedbackReason : null, context_correction: action === "dismiss" ? contextCorrection : null }),
  };
  try {
    const decisionPath = `/api/member/v1/triage/${encodeURIComponent(item.proposal_id)}/${action}`;
    let result;
    try {
      result = await api(decisionPath, decisionRequest);
    } catch (initialError) {
      const definitelyRejected = requestDefinitelyRejected(initialError);
      const reconciliation = await reconcileTriageDecision(
        item, action, decisionRequestId, token, initialError, banner, { mayReportRejection: definitelyRejected },
      );
      if (["settled", "rejected"].includes(reconciliation)) return;
      if (definitelyRejected && reconciliation !== "continuation_required") {
        latchTriageRefreshFailure(`The decision failed and Triage could not prove this exact candidate remained undecided: ${initialError.message} Reload this page before continuing.`);
        return;
      }
      try {
        result = await api(decisionPath, decisionRequest);
      } catch (retryError) {
        const retryReconciliation = await reconcileTriageDecision(item, action, decisionRequestId, token, retryError, banner);
        if (["settled", "rejected"].includes(retryReconciliation)) return;
        latchTriageRefreshFailure(`The decision response was interrupted and Triage could not prove an action-specific receipt after an idempotent retry: ${retryError.message} Reload this page before continuing.`);
        return;
      }
    }
    decisionRecorded = true;
    state.triageView = workingView;
    state.triageSelected = null;
    state.triageMatter = null;
    state.triageFocusPending = false;
    replaceQuery({ view: workingView, item: null, matter: null });
    const refreshed = await reloadActiveSurface({ triageOperationToken: token });
    if (!refreshed || !triageOperationIsCurrent(token)) return;
    const hasNextDecision = triageVisibleMatters(refreshed, workingView).length > 0;
    const continuation = hasNextDecision
      ? "The next decision in this view is ready."
      : refreshed.triage?.has_more
        ? "No other decision is loaded in this view. Load more matters or choose another view."
        : "You're caught up in this view.";
    state.triageBannerKind = "continuation";
    banner.replaceChildren();
    if (action === "approve") {
      if (result.destination) {
        const pending = result.destination.state === "awaiting_openclaw_approval";
        banner.append(document.createTextNode(pending ? `Confirmed. The governed provider action is awaiting approval. ${continuation} ` : `Confirmed and added to ${result.destination.label || titleCase(result.destination.surface)}. ${continuation} `));
        const link = text("a", `Open ${result.destination.label || titleCase(result.destination.surface)}`, "link-button"); link.href = result.destination.href; banner.append(link);
      } else banner.textContent = `Confirmed through the household gate. ${continuation}`;
      if (result.state === "approved_awaiting_openclaw") appendAppleExecutionHandoff(banner, item.route?.provider, result.pending_provider_action_event_id);
    } else {
      banner.textContent = contextCorrection ? `Dismissed. The exact context correction was recorded and can be reversed. ${continuation}` : feedbackReason ? `Dismissed. No household record was created. Your reversible feedback was recorded. ${continuation}` : `Dismissed. No household record or provider write was created. ${continuation}`;
    }
  } catch (error) {
    if (decisionRecorded) {
      latchTriageRefreshFailure(`The decision was recorded, but Triage could not refresh: ${error.message} Reload this page before deciding another item.`);
    }
    else latchTriageRefreshFailure(`Triage could not finish or reconcile this decision: ${error.message} Reload this page before continuing.`);
  } finally {
    finishTriageOperation(token);
  }
}

function field(label, control, extraClass = "") {
  const wrapper = document.createElement("label");
  wrapper.className = `field ${extraClass}`.trim();
  wrapper.append(text("span", label), control);
  return wrapper;
}

function selectControl(options, selected, emptyLabel = "Choose…") {
  const select = document.createElement("select");
  if (emptyLabel) {
    const empty = text("option", emptyLabel); empty.value = ""; select.append(empty);
  }
  for (const option of options) {
    const node = text("option", option.label); node.value = option.value;
    if (option.value === selected) node.selected = true;
    select.append(node);
  }
  return select;
}

function replaceSelectOptions(select, options, selected = "", emptyLabel = "Choose…") {
  select.replaceChildren();
  if (emptyLabel) {
    const empty = text("option", emptyLabel); empty.value = ""; select.append(empty);
  }
  for (const option of options) {
    const node = text("option", option.label); node.value = option.value;
    if (option.value === selected) node.selected = true;
    select.append(node);
  }
}

function triageDateTimeParts(value) {
  if (!value) return { date: "", time: "" };
  const raw = String(value);
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return { date: raw, time: "" };
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return { date: raw.slice(0, 10), time: raw.slice(11, 16) };
  return {
    date: `${parsed.getFullYear()}-${String(parsed.getMonth() + 1).padStart(2, "0")}-${String(parsed.getDate()).padStart(2, "0")}`,
    time: `${String(parsed.getHours()).padStart(2, "0")}:${String(parsed.getMinutes()).padStart(2, "0")}`,
  };
}

function triageFieldControl(spec, candidate, data) {
  const current = candidate?.[spec.name] ?? spec.default;
  if (spec.control === "date_time") {
    const parts = triageDateTimeParts(current);
    const wrapper = document.createElement("div"); wrapper.className = "date-time-control";
    const dateInput = document.createElement("input"); dateInput.type = "date"; dateInput.name = `${spec.name}__date`; dateInput.value = parts.date;
    dateInput.setAttribute("aria-label", `${spec.label} date`);
    const timeInput = document.createElement("input"); timeInput.type = "time"; timeInput.name = `${spec.name}__time`; timeInput.value = parts.time;
    timeInput.setAttribute("aria-label", `${spec.label} time (optional)`);
    wrapper.append(dateInput);
    if (!spec.date_only) wrapper.append(timeInput);
    return wrapper;
  }
  if (spec.control === "date") {
    const input = document.createElement("input"); input.type = "date"; input.name = spec.name; input.value = current ? String(current).slice(0, 10) : ""; return input;
  }
  if (spec.control === "textarea") {
    const input = document.createElement("textarea"); input.name = spec.name; input.rows = 3; input.maxLength = 20000; input.value = current || ""; return input;
  }
  if (spec.control === "select") {
    const options = (spec.options || []).map((value) => ({ value, label: titleCase(value) }));
    const input = selectControl(options, current || "", "Not set"); input.name = spec.name; return input;
  }
  if (spec.control === "party") {
    const options = (data.destinations?.people || []).map((person) => ({ value: person.party_id, label: `${person.label} · ${titleCase(person.kind)}` }));
    const input = selectControl(options, current || "", "Choose a person…"); input.name = spec.name; return input;
  }
  if (spec.control === "checkbox") {
    const input = document.createElement("input"); input.type = "checkbox"; input.name = spec.name; input.checked = Boolean(current); return input;
  }
  if (spec.control === "number") {
    const input = document.createElement("input"); input.type = "number"; input.name = spec.name; input.value = current ?? ""; input.min = spec.minimum ?? 0; if (spec.maximum != null) input.max = spec.maximum; return input;
  }
  if (spec.control === "priority") {
    const input = selectControl([{ value: "0", label: "None" }, { value: "1", label: "High" }, { value: "5", label: "Medium" }, { value: "9", label: "Low" }], String(current ?? 0), ""); input.name = spec.name; return input;
  }
  if (spec.control === "recurrence") {
    const existing = Array.isArray(current) ? current[0] : current;
    const known = new Map([
      ["FREQ=DAILY", "daily"],
      ["FREQ=WEEKLY", "weekly"],
      ["FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", "weekdays"],
      ["FREQ=MONTHLY", "monthly"],
      ["FREQ=YEARLY", "yearly"],
    ]);
    const selected = known.get(String(existing || "").replace(/^RRULE:/, "")) || (existing ? "existing" : "none");
    const options = [
      { value: "none", label: "Does not repeat" }, { value: "daily", label: "Daily" },
      { value: "weekdays", label: "Weekdays" }, { value: "weekly", label: "Weekly" },
      { value: "monthly", label: "Monthly" }, { value: "yearly", label: "Yearly" },
    ];
    if (selected === "existing") options.push({ value: `existing:${existing}`, label: "Keep existing custom pattern" });
    const input = selectControl(options, selected === "existing" ? `existing:${existing}` : selected, ""); input.name = `${spec.name}__repeat`; return input;
  }
  if (spec.control === "alarm_offset") {
    const seconds = Array.isArray(current) ? Math.abs(Number(current[0]?.relative_offset_seconds || 0)) : 0;
    const minutes = seconds ? String(Math.round(seconds / 60)) : "none";
    const input = selectControl([
      { value: "none", label: "None" }, { value: "0", label: "At due time" },
      { value: "5", label: "5 minutes before" }, { value: "15", label: "15 minutes before" },
      { value: "30", label: "30 minutes before" }, { value: "60", label: "1 hour before" },
      { value: "1440", label: "1 day before" },
    ], minutes, ""); input.name = `${spec.name}__offset`; return input;
  }
  const input = document.createElement("input"); input.type = spec.control === "url" ? "url" : spec.control === "color" ? "color" : "text"; input.name = spec.name; input.value = current || ""; return input;
}

function triageCandidateUpdates(form, targetKind, schema, formValues = null) {
  const values = formValues ?? new FormData(form); const updates = {};
  const activeProvider = String(values.get("provider") || "");
  const target = schema?.targets?.[targetKind] || {};
  const source = target.fields_from ? schema.targets?.[target.fields_from] : target;
  for (const spec of source?.fields || []) {
    if (spec.provider && spec.provider !== activeProvider) continue;
    if (spec.control === "date_time") {
      const dateValue = String(values.get(`${spec.name}__date`) || "");
      const timeValue = String(values.get(`${spec.name}__time`) || "");
      updates[spec.name] = dateValue
        ? activeProvider === "google_tasks" && spec.name === "due"
          ? dateValue
          : timeValue ? `${dateValue}T${timeValue}` : dateValue
        : null;
    } else if (spec.control === "checkbox") {
      updates[spec.name] = values.has(spec.name);
    } else if (spec.control === "number") {
      const value = String(values.get(spec.name) || ""); updates[spec.name] = value ? Number(value) : null;
    } else if (spec.control === "priority") {
      updates[spec.name] = Number(values.get(spec.name) || 0);
    } else if (spec.control === "recurrence") {
      const selected = String(values.get(`${spec.name}__repeat`) || "none");
      const rules = { daily: "FREQ=DAILY", weekdays: "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", weekly: "FREQ=WEEKLY", monthly: "FREQ=MONTHLY", yearly: "FREQ=YEARLY" };
      const rule = selected.startsWith("existing:") ? selected.slice(9) : rules[selected];
      updates[spec.name] = spec.name === "recurrence_rules" ? (rule ? [rule] : []) : (rule || null);
    } else if (spec.control === "alarm_offset") {
      const selected = String(values.get(`${spec.name}__offset`) || "none");
      updates[spec.name] = selected !== "none" ? [{ kind: "relative", relative_offset_seconds: -Number(selected) * 60, proximity: "none" }] : [];
    } else {
      const value = String(values.get(spec.name) || "").trim(); updates[spec.name] = value || null;
    }
  }
  return updates;
}

async function saveTriageForm(item, form, { reload = true, operationToken = null, formValues = null } = {}) {
  const values = formValues ?? new FormData(form);
  const ownsOperation = operationToken == null;
  const token = operationToken ?? beginTriageOperation();
  if (token == null || !triageOperationIsCurrent(token)) return null;
  const targetKind = String(values.get("target_kind") || item.target_kind);
  const candidateUpdates = triageCandidateUpdates(form, targetKind, state.surface?.enrichment_schema, values);
  let route = null;
  if (item.source_kind !== "commitment_proposal") {
    route = {
      member_id: String(values.get("member_id") || state.bootstrap.member.id),
      provider: String(values.get("provider") || "") || null,
      provider_account_id: String(values.get("provider_account_id") || "") || null,
      container_external_id: String(values.get("container_external_id") || "") || null,
    };
  }
  setBanner("Saving these details as a proposal—nothing official is being created yet…");
  let saveRecorded = false;
  try {
    const result = await api(`/api/member/v1/triage/${encodeURIComponent(item.proposal_id)}/enrich`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
      body: JSON.stringify({
        request_id: crypto.randomUUID(),
        expected_last_event_id: item.last_event_id,
        title: String(values.get("title") || item.title).trim(),
        target_kind: targetKind,
        candidate_updates: candidateUpdates,
        route,
      }),
    });
    if (!triageOperationIsCurrent(token)) return null;
    saveRecorded = true;
    Object.assign(item, result.triage);
    delete form.dataset.dirty;
    delete form.dataset.structuralChanged;
    const confirm = [...form.querySelectorAll("button")].find((button) => ["Save & confirm", "Save destination", "Confirm and route"].includes(button.textContent));
    if (confirm) confirm.textContent = "Confirm and route";
    setBanner("Details saved. Review the destination and preview once more before confirming.");
    if (reload) {
      const refreshed = await reloadActiveSurface({ triageOperationToken: token });
      if (!refreshed || !triageOperationIsCurrent(token)) return null;
    }
    return result.triage;
  } catch (error) {
    if (!triageOperationIsCurrent(token)) return null;
    if (saveRecorded) {
      latchTriageRefreshFailure(`The details were saved, but Triage could not refresh: ${error.message} Reload this page before continuing.`);
    } else setBanner(`${error.message} The proposal was not changed.`);
    return null;
  } finally {
    if (ownsOperation) finishTriageOperation(token);
  }
}

function triageSourceContext(item) {
  const context = item.source_context || {};
  const section = document.createElement("section"); section.className = "triage-source-context";
  section.append(text("p", "WHY THIS IS HERE", "eyebrow"));
  if (context.state !== "available") {
    section.append(text("p", context.state === "withheld" ? "Source context is outside this member view." : "The immutable source is unavailable. Do not confirm until its evidence can be reviewed.", "review-state"));
    return section;
  }
  const heading = document.createElement("div"); heading.className = "triage-source-heading";
  const channel = [context.channel && titleCase(context.channel), context.direction && titleCase(context.direction)].filter(Boolean).join(" · ");
  heading.append(text("strong", channel || titleCase(context.source_event_type || "Source")));
  section.append(heading);
  if (context.provider_category) {
    const category = document.createElement("div"); category.className = "triage-provider-category";
    category.append(text("span", context.provider_category.label, "pill pill-lavender"), text("small", `${context.provider_category.provenance} marked this source as ${context.provider_category.label}. It is context only; it does not decide importance or hide the item.`));
    section.append(category);
  }
  const facts = document.createElement("dl"); facts.className = "triage-source-facts";
  for (const [label, value] of [[context.direction === "outbound" ? "Counterparty" : "Sender", context.sender], ["Subject / source title", context.subject], ["Message date", context.occurred_at ? new Date(context.occurred_at).toLocaleString() : null], ["Imported / recorded", context.recorded_at && context.recorded_at !== context.occurred_at ? new Date(context.recorded_at).toLocaleString() : null]]) {
    if (value) facts.append(text("dt", label), text("dd", value));
  }
  section.append(facts);
  const meaning = context.meaning || {};
  const meaningBox = document.createElement("div"); meaningBox.className = "triage-meaning-box";
  meaningBox.append(text("strong", `AdministrateMe found a possible ${titleCase(meaning.target_kind || item.target_kind || "item")}`), text("p", meaning.summary || item.title));
  if (meaning.extracted_fields?.length) {
    const values = document.createElement("dl"); values.className = "triage-extracted-fields";
    for (const entry of meaning.extracted_fields) values.append(text("dt", entry.label), text("dd", Array.isArray(entry.value) ? entry.value.join(", ") : String(entry.value)));
    meaningBox.append(values);
  }
  section.append(meaningBox);
  const interpretation = context.interpretation;
  if (interpretation) {
    const guide = document.createElement("div"); guide.className = "triage-context-guide";
    guide.append(text("p", "CONTEXT SUMMARY · REVIEW AID", "eyebrow"));
    for (const [label, value] of [["What this is", interpretation.case_summary], ["Why it may need you", interpretation.why_member], ["Smallest next step", interpretation.next_step]]) {
      if (!value) continue;
      const row = document.createElement("div"); row.className = "triage-context-guide-row";
      row.append(text("strong", label), text("p", value)); guide.append(row);
    }
    const status = [interpretation.thread_state && titleCase(interpretation.thread_state), interpretation.action_owner && `Owner: ${titleCase(interpretation.action_owner)}`, interpretation.lifecycle_state && `Status: ${titleCase(interpretation.lifecycle_state)}`].filter(Boolean).join(" · ");
    if (status) guide.append(text("p", status, "meta"));
    if (interpretation.unresolved_questions?.length) {
      guide.append(text("strong", "Still unresolved"));
      const questions = document.createElement("ul"); questions.className = "triage-context-questions";
      for (const question of interpretation.unresolved_questions) questions.append(text("li", question));
      guide.append(questions);
    }
    if (interpretation.source_chain?.length > 1) {
      const chain = document.createElement("details"); chain.className = "triage-source-chain";
      chain.append(text("summary", `${interpretation.source_chain.length} messages used for context`));
      const list = document.createElement("ol");
      for (const source of interpretation.source_chain) {
        const relation = source.relation_reason_codes?.length ? `${titleCase(source.relation)}: ${source.relation_reason_codes.map(titleCase).join(", ")}` : titleCase(source.relation);
        const label = [relation, source.subject || source.sender || titleCase(source.channel), source.occurred_at ? new Date(source.occurred_at).toLocaleString() : null].filter(Boolean).join(" · ");
        list.append(text("li", label));
      }
      chain.append(list); guide.append(chain);
    }
    guide.append(text("small", "This explanation came from bounded language interpretation. Exact source words remain the evidence below; nothing is official until you confirm it.", "state-note"));
    section.append(guide);
  }
  const evidence = context.evidence || {};
  const assessment = item.interpretation?.assessment_state === "verified" ? item.interpretation?.obligation_assessment : null;
  if (assessment) {
    const assessed = document.createElement("section"); assessed.className = "triage-obligation-assessment";
    assessed.append(text("strong", "What the source supports"));
    const facts = document.createElement("dl"); facts.className = "triage-source-facts";
    for (const [label, value] of [["Purpose", sourcePurposeLabel(assessment.purpose)], ["Action", titleCase(assessment.actionability)], ["Condition", assessment.condition], ["Condition state", assessment.condition ? titleCase(assessment.condition_state) : null], ["Date means", assessment.date_role !== "none" ? titleCase(assessment.date_role) : null]]) if (value) facts.append(text("dt", label), text("dd", value));
    assessed.append(facts);
    for (const citation of assessment.evidence || []) {
      if (citation.quote === evidence.quote) continue;
      assessed.append(text("small", `${titleCase(citation.role)} · exact source text`), text("blockquote", citation.quote, "triage-evidence-quote"));
    }
    section.append(assessed);
  }
  if (evidence.quote) {
    const quote = document.createElement("blockquote"); quote.className = "triage-evidence-quote"; quote.textContent = evidence.quote;
    section.append(text("strong", "Exact source evidence"), quote);
  } else {
    section.append(text("p", "No exact text span applies to this structured provider rule. Review the source facts before confirming.", "state-note"));
  }
  const technical = document.createElement("details"); technical.className = "triage-technical-source";
  technical.append(text("summary", "Technical source details"));
  const coordinates = document.createElement("dl"); coordinates.className = "triage-source-facts";
  for (const [label, value] of [["Source", context.source_id], ["Adapter", context.adapter], ["Account", context.account], ["Event type", context.source_event_type]]) {
    if (value) coordinates.append(text("dt", label), text("dd", value));
  }
  technical.append(coordinates);
  if (context.methods?.length) {
    const methods = document.createElement("div"); methods.className = "triage-methods";
    for (const method of context.methods) { const badge = text("span", method.label, "pill pill-lavender"); badge.title = method.detail; methods.append(badge); }
    technical.append(methods);
  }
  section.append(technical);
  return section;
}

async function manageTriageMatter(item, action, button) {
  const token = beginTriageOperation();
  if (token == null) return;
  let recorded = false;
  const payload = {
    request_id: crypto.randomUUID(),
    expected_last_event_id: item.last_event_id,
    action,
    reason_codes: [`member_${action}`],
    evidence_event_ids: [item.source_event_id].filter(Boolean),
  };
  if (action === "snooze") payload.snoozed_until = new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString();
  try {
    const result = await api(`/api/member/v1/triage/${encodeURIComponent(item.proposal_id)}/manage`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
      body: JSON.stringify(payload),
    });
    if (!triageOperationIsCurrent(token)) return;
    recorded = true;
    setBanner(`${titleCase(action)} recorded for this item. Future source preferences are unchanged. ${result.provider_effects || 0} provider effects.`);
    await reloadActiveSurface({ triageOperationToken: token });
  } catch (error) {
    if (!triageOperationIsCurrent(token)) return;
    if (recorded) latchTriageRefreshFailure(`Your correction was recorded, but Triage could not refresh: ${error.message} Reload before continuing.`);
    else if (!error.status || error.status >= 500) latchTriageRefreshFailure(`The correction result is uncertain: ${error.message} Reload before retrying.`);
    else setBanner(`${error.message} The correction was rejected.`);
  } finally { finishTriageOperation(token); }
}

function triageDetail(item, data, readOnly = false) {
  const section = document.createElement("section"); section.className = "card triage-detail";
  if (!item) {
    section.append(text("h2", "Choose a proposal"), text("p", "Select one item to review its evidence, destination, and exact effect.", "empty"));
    return section;
  }
  section.dataset.triageItem = item.proposal_id;
  section.tabIndex = -1;
  const reviewHeading = item.interpretation?.compact_summary || item.title;
  section.append(text("p", readOnly ? "RECENT DECISION" : "YOUR DECISION", "eyebrow"), text("h2", reviewHeading));
  if (!readOnly) {
    const [laneLabel, laneReason] = triageLanePresentation(item.human_lane || item.queue_lane);
    const reason = document.createElement("div"); reason.className = "triage-attention-reason";
    reason.append(text("strong", laneLabel), text("span", laneReason));
    section.append(reason);
  }
  section.append(triageSourceContext(item));
  const submission = item.candidate?.submission_status;
  if (!readOnly && item.candidate?.evidence_complete === false) section.append(text("p", "Some forwarded material could not be read completely. Add the missing text or attachment details before confirming an item.", "review-state"));
  if (!readOnly && submission) {
    const statusCopy = { failed: "Extraction could not finish. Your submission is saved for review.", quarantined: "This submission needs clarification before it can become an item.", publication_held: "Your submission is saved. Review is paused until the required authorization is available." };
    const explanation = statusCopy[submission.extraction_state] || statusCopy[submission.assessment_outcome];
    if (explanation) section.append(text("p", explanation, "review-state"));
  }
  if (readOnly) {
    const eligibilityChanged = item.resolution_reason === "eligibility_rule_replaced";
    const outcomeLabels = { explicit_member_submission_required: "Retired when member forwarding became required", provider_observed_matched: "Verified on your platform", provider_observed_drifted: "Your platform now differs from the approved item", provider_observed_missing: "The item is no longer present on your platform", provider_observed_incomplete: "The platform returned too little detail to verify every approved field", provider_completed_awaiting_observation: "Sent; waiting to verify on your platform" };
    const outcome = eligibilityChanged ? "Retired after context or eligibility changed" : outcomeLabels[item.resolution_reason] || (item.resolution_reason ? titleCase(item.resolution_reason) : titleCase(item.state));
    section.append(text("p", `${outcome}${item.resolved_at ? ` on ${new Date(item.resolved_at).toLocaleString()}` : ""}.`, `review-state ${item.state === "failed" ? "" : "ready"}`.trim()));
    if (eligibilityChanged) section.append(text("p", "This untouched candidate was replaced after its source context or eligibility changed. It is historical lifecycle evidence, not a sender-wide rule created by this dismissal.", "state-note"));
    if (item.materialized_event_ids?.length) section.append(text("p", `${item.materialized_event_ids.length} audited materialization event${item.materialized_event_ids.length === 1 ? "" : "s"}.`, "meta"));
    if (item.domain_approval_event_id) section.append(text("p", item.materialized_event_ids?.length ? "Your confirmation was recorded before canonical materialization." : "Your domain confirmation is recorded before any provider action.", "meta"));
    if (item.outcome_event_id) section.append(text("p", "A terminal provider-path outcome is linked to this decision.", "meta"));
    if (item.destination) {
      const destination = text("a", `Open ${item.destination.label || titleCase(item.destination.surface)}`, "button button-quiet");
      destination.href = item.destination.href;
      section.append(destination);
    }
    const evidence = text("button", "View source evidence", "link-button"); evidence.type = "button"; evidence.addEventListener("click", () => showEvidence(item.source_event_id)); section.append(evidence);
    if (data.human_triage_management?.active && item.management?.state && item.management.state !== "open" && item.management?.event_id && ["pending_interpretation", "pending_review"].includes(item.state)) {
      const reopen = text("button", "Reopen this local decision", "button button-quiet"); reopen.type = "button";
      reopen.addEventListener("click", () => manageTriageMatter(item, "reopen", reopen));
      section.append(reopen, text("small", "Reopening appends a reversal and restores the admitted matter without contacting a provider.", "state-note"));
    }
    return section;
  }

  const form = document.createElement("form"); form.className = "triage-review-form";
  const effect = document.createElement("div"); effect.className = "triage-proposed-effect";
  const disposition = item.disposition?.disposition || "ordinary_queue";
  const consequence = disposition === "alerts"
    ? "Keep this in the separate alerts and safety lane."
    : disposition === "context"
      ? "Keep this source as searchable context without creating an ordinary obligation."
      : disposition === "handled"
        ? "Keep the audit trail without returning this to the ordinary queue."
        : `Create or enrich a ${titleCase(item.target_kind || "household item")} for ${state.bootstrap.member.label || "this household member"}.`;
  effect.append(text("p", "PROPOSED EFFECT", "eyebrow"), text("strong", consequence), text("small", "The consequence is local, explicit, reversible where no provider effect exists, and never authorizes a provider write by itself."));
  form.append(effect);
  if (data.human_triage_management?.active) {
    const management = document.createElement("details"); management.className = "triage-management";
    management.append(text("summary", "Correct or set aside this item"));
    management.append(text("p", "Already handled closes this particular obligation. Not useful corrects this suggestion. Neither teaches a future source rule. Nothing here contacts a provider.", "state-note"));
    const managementRow = document.createElement("div"); managementRow.className = "button-row";
    const actions = item.management?.state && item.management.state !== "open"
      ? [["reopen", "Reopen"]]
      : [["mark_handled", "Already handled"], ["dismiss_not_useful", "Not useful"], ["keep_context", "Keep as reference"], ["route_alert", "Route to alerts"], ["ask_clarification", "Ask clarification"], ["snooze", "Snooze 1 day"]];
    for (const [action, label] of actions) {
      const button = text("button", label, "button button-quiet"); button.type = "button";
      button.addEventListener("click", () => manageTriageMatter(item, action, button));
      managementRow.append(button);
    }
    management.append(managementRow); form.append(management);
    if (item.learning_scope?.available && item.learning_scope?.conditions) {
      const similar = document.createElement("details"); similar.className = "triage-similar-rule";
      similar.append(text("summary", "Do this for similar messages"), text("p", "Choose a future preference separately. Preview the exact scope and exceptions before saving.", "state-note"));
      similar.append(sourceRuleBuilder(item.learning_scope, { canMutate: true, candidate: item }));
      form.append(similar);
    }
  }
  const grid = document.createElement("div"); grid.className = "field-grid";
  const schema = data.enrichment_schema || {};
  const title = document.createElement("input"); title.name = "title"; title.required = true; title.maxLength = 1000; title.value = item.title || "";
  grid.append(field("Review title", title, "field-span"));
  const targetOptions = Object.entries(schema.targets || {}).map(([value, contract]) => ({ value, label: contract.label || titleCase(value) }));
  const target = selectControl(targetOptions, item.target_kind, ""); target.name = "target_kind"; target.disabled = item.source_kind === "commitment_proposal";
  grid.append(field("Creates", target));
  const members = (data.destinations?.members || []).map((value) => ({ value: value.member_id, label: `${value.label} · ${value.role || "member"}` }));
  const member = selectControl(members, item.route?.member_id || state.bootstrap.member.id, "Choose a household member…"); member.name = "member_id"; member.required = true; member.disabled = item.source_kind === "commitment_proposal";
  grid.append(field("Household member", member));

  const candidate = item.candidate || {};
  const targetSchema = schema.targets?.[item.target_kind] || {};
  const fieldSchema = targetSchema.fields_from ? schema.targets?.[targetSchema.fields_from] : targetSchema;
  const activeProvider = item.route?.provider || "";
  for (const spec of fieldSchema?.fields || []) {
    if (spec.provider && spec.provider !== activeProvider) continue;
    const currentCandidate = { ...candidate };
    if (spec.name === "due_date" && !currentCandidate.due_date) currentCandidate.due_date = candidate.due || candidate.due_at;
    if (spec.name === "notes" && currentCandidate.notes == null) currentCandidate.notes = candidate.description;
    if (spec.name === "start" && item.target_kind === "calendar_event" && !currentCandidate.start) currentCandidate.start = candidate.due;
    const effectiveSpec = activeProvider === "google_tasks"
      && ["reminder", "list_item"].includes(item.target_kind)
      && spec.name === "due"
      ? { ...spec, label: "Scheduled date", date_only: true }
      : spec;
    grid.append(field(effectiveSpec.label, triageFieldControl(effectiveSpec, currentCandidate, data), effectiveSpec.control === "textarea" ? "field-span" : ""));
  }
  if (targetSchema.capability_note) grid.append(text("p", targetSchema.capability_note, "state-note field-span"));
  if (activeProvider && schema.provider_notes?.[activeProvider]) grid.append(text("p", schema.provider_notes[activeProvider], "state-note field-span"));

  const external = ["calendar_event", "reminder", "list", "list_item"].includes(item.target_kind);
  if (external) {
    const calendarRoutes = (data.destinations?.calendars || []).map((value) => ({
      provider: value.provider, account: value.provider_account_id, container: value.calendar_external_id,
      label: value.calendar_name || titleCase(value.destination_kind),
    }));
    const reminderRoutes = (data.destinations?.reminder_lists || []).map((value) => ({
      provider: value.provider, account: value.provider_account_id, container: value.list_external_id,
      label: value.list_name || "Reminder list",
    }));
    const routes = item.target_kind === "calendar_event" ? calendarRoutes : reminderRoutes;
    const providers = [...new Set(routes.map((value) => value.provider))].map((value) => ({ value, label: titleCase(value) }));
    const provider = selectControl(providers, item.route?.provider || ""); provider.name = "provider";
    const account = document.createElement("select"); account.name = "provider_account_id";
    const container = document.createElement("select"); container.name = "container_external_id";
    const refreshContainers = (selected = "") => {
      const options = routes.filter((value) => value.provider === provider.value && value.account === account.value).map((value) => ({ value: value.container, label: value.label }));
      replaceSelectOptions(container, options, selected, item.target_kind === "list" ? "Provider default" : "Choose destination…");
    };
    const refreshAccounts = (selectedAccount = "", selectedContainer = "") => {
      const options = [...new Set(routes.filter((value) => value.provider === provider.value).map((value) => value.account))].map((value) => ({ value, label: value }));
      replaceSelectOptions(account, options, selectedAccount, "Choose account…");
      refreshContainers(selectedContainer);
    };
    provider.addEventListener("change", () => { if (triageInteractionLocked()) return; refreshAccounts(); form.dataset.structuralChanged = "true"; });
    account.addEventListener("change", () => { if (triageInteractionLocked()) return; refreshContainers(); form.dataset.structuralChanged = "true"; });
    container.addEventListener("change", () => { if (triageInteractionLocked()) return; form.dataset.structuralChanged = "true"; });
    refreshAccounts(item.route?.provider_account_id || "", item.route?.container_external_id || "");
    grid.append(field("Provider", provider), field("Account", account), field(item.target_kind === "calendar_event" ? "Calendar" : "List", container));
  }
  const editDetails = document.createElement("details"); editDetails.className = "triage-edit-details";
  editDetails.open = item.review_state?.state !== "ready";
  editDetails.append(text("summary", item.review_state?.state === "ready" ? "Edit details or destination" : "Complete the missing details"), grid);
  form.append(editDetails);
  let contextCorrection = null;
  const correctionCapabilities = item.source_context?.correction_capabilities || {};
  const correctableRelatedSourceIds = new Set(Array.isArray(correctionCapabilities.unrelated_source_event_ids) ? correctionCapabilities.unrelated_source_event_ids : []);
  const relatedContextSources = (item.source_context?.interpretation?.source_chain || []).filter((source) => source.relation === "related" && source.source_event_id && correctableRelatedSourceIds.has(source.source_event_id));
  const canCorrectQuotedHistory = correctionCapabilities.quoted_or_forwarded_history === true;
  if (item.source_context?.state === "available" && (relatedContextSources.length || canCorrectQuotedHistory)) {
    contextCorrection = document.createElement("fieldset"); contextCorrection.className = "triage-context-correction"; contextCorrection.hidden = true;
    contextCorrection.append(text("legend", "Correct this exact context"), text("p", "These corrections affect only the selected source relationship or quoted text. They do not create a sender-, account-, or keyword-wide rule.", "state-note"));
    for (const source of relatedContextSources) {
      const control = document.createElement("input"); control.type = "checkbox"; control.dataset.unrelatedSourceEventId = source.source_event_id;
      const sourceLabel = [source.subject || source.sender || "Related message", source.occurred_at ? new Date(source.occurred_at).toLocaleString() : null].filter(Boolean).join(" · ");
      const wrapper = document.createElement("label"); wrapper.className = "check-control"; wrapper.append(control, document.createTextNode(` This message is unrelated: ${sourceLabel}`)); contextCorrection.append(wrapper);
    }
    if (canCorrectQuotedHistory) {
      const quoted = document.createElement("input"); quoted.type = "checkbox"; quoted.dataset.quotedOrForwardedHistory = "true";
      const quotedWrapper = document.createElement("label"); quotedWrapper.className = "check-control"; quotedWrapper.append(quoted, document.createTextNode(" The displayed ask came only from quoted or forwarded history, not newly authored text")); contextCorrection.append(quotedWrapper);
    }
    form.append(contextCorrection);
  }
  if (item.calendar_preview) {
    const preview = item.calendar_preview;
    const box = document.createElement("section"); box.className = "state-card";
    box.append(text("h3", "Calendar impact preview"));
    if (preview.state === "needs_details") {
      box.append(text("p", `Add ${preview.missing.map(titleCase).join(", ")} to calculate conflicts, buffers, and guardrails.`, "state-note"));
    } else {
      const event = preview.event || {};
      const destination = preview.destination?.calendar_name || preview.destination?.destination_kind;
      box.append(text("p", destination ? `Destination: ${destination}.` : "This is not an approved writable destination.", destination ? "calendar-route-ready" : "calendar-route-missing"));
      box.append(text("p", `Effective load: ${titleCase(event.effect)} · buffer ${event.buffer_before_minutes || 0} min before / ${event.buffer_after_minutes || 0} min after.`, "meta"));
      box.append(text("p", preview.conflicts?.length ? `${preview.conflicts.length} conflict or pressure warning${preview.conflicts.length === 1 ? "" : "s"}: ${preview.conflicts.map((value) => value.why).join(" · ")}` : "No buffered conflict appears in this range.", preview.conflicts?.length ? "calendar-route-missing" : "calendar-route-ready"));
      box.append(text("p", preview.guardrails?.length ? `Overlaps ${preview.guardrails.map((value) => value.label || titleCase(value.kind)).join(", ")}.` : "No household guardrail overlaps this proposal.", "state-note"));
      box.append(text("p", preview.observation?.active ? "Observation mode: confirmation would be audited without changing the provider." : "Confirmation would enter the governed provider-write path.", "meta"));
    }
    form.append(box);
  }
  const missing = item.review_state?.missing || [];
  const decisionEligible = item.decision_disposition === "decision";
  const review = text(
    "p",
    !decisionEligible
      ? item.decision_disposition === "attach_existing"
        ? "This matches existing household truth. It can be attached as evidence or edited into a distinct item, but it cannot create duplicate work."
        : "This is supporting evidence, not a separate decision. It can be edited or dismissed, but it cannot create household truth."
      : missing.length
      ? `Needs: ${missing.map(titleCase).join(", ")}. Save the missing details before confirming.`
      : "Ready for your final confirmation. Every real write still passes governance and observation controls.",
    `review-state ${decisionEligible && !missing.length ? "ready" : ""}`.trim(),
  );
  form.append(review);
  const row = document.createElement("div"); row.className = "button-row";
  const save = text("button", "Save details", "button button-quiet"); save.type = "submit";
  const approve = text("button", "Confirm and route", "button button-primary"); approve.type = "button"; approve.disabled = missing.length > 0 || !decisionEligible;
  const feedback = document.createElement("select"); feedback.setAttribute("aria-label", "Why this candidate should leave Triage");
  for (const [value, label] of [["", "Choose dismissal reason…"], ["not_actionable", "Not actionable"], ["already_done", "Already done"], ["duplicate", "Duplicate"], ["wrong_kind", "Wrong kind"], ["wrong_context", "Wrong context"], ["wrong_owner", "Wrong owner"], ["not_mine", "Not mine"], ["recurring_normal", "Normal recurrence"], ["low_signal", "Low signal"], ["provider_promotion", "Promotion / marketing"], ["spam", "Spam"], ["other", "Other"]]) { const option = document.createElement("option"); option.value = value; option.textContent = label; feedback.append(option); }
  const dismiss = text("button", "Dismiss & teach", "button button-quiet"); dismiss.type = "button"; dismiss.disabled = true;
  const evidence = text("button", "View source evidence", "link-button"); evidence.type = "button";
  const buttons = [save, approve, dismiss];
  form.addEventListener("submit", (event) => { event.preventDefault(); saveTriageForm(item, form); });
  const markDirty = (event) => { if (triageInteractionLocked() || event?.target?.closest?.(".triage-context-correction")) return; form.dataset.dirty = "true"; approve.textContent = form.dataset.structuralChanged ? "Save destination" : "Save & confirm"; };
  target.addEventListener("change", () => { if (triageInteractionLocked()) return; form.dataset.structuralChanged = "true"; markDirty(); });
  member.addEventListener("change", () => { if (triageInteractionLocked()) return; form.dataset.structuralChanged = "true"; markDirty(); });
  form.addEventListener("input", markDirty);
  form.addEventListener("change", markDirty);
  approve.addEventListener("click", async () => {
    if (form.dataset.dirty) {
      const formValues = new FormData(form);
      const operationToken = beginTriageOperation();
      if (operationToken == null) return;
      const structuralChange = Boolean(form.dataset.structuralChanged);
      const saved = await saveTriageForm(item, form, { reload: structuralChange, operationToken, formValues });
      if (!saved || structuralChange) {
        finishTriageOperation(operationToken);
        return;
      }
      if (saved.review_state?.state !== "ready") {
        state.triageView = saved.review_state?.state === "needs_interpretation" ? "needs_interpretation" : "needs_details";
        replaceQuery({ view: state.triageView });
        try {
          const refreshed = await reloadActiveSurface({ triageOperationToken: operationToken, triageReconcileExact: true });
          if (refreshed && triageOperationIsCurrent(operationToken)) {
            setBanner("Details saved. This candidate needs more review before it can be confirmed.");
          }
        } catch (error) {
          if (triageOperationIsCurrent(operationToken)) {
            latchTriageRefreshFailure(`The details were saved and now need more review, but Triage could not refresh: ${error.message} Reload this page before continuing.`);
          }
        } finally {
          finishTriageOperation(operationToken);
        }
        return;
      }
      await decideTriage(saved, "approve", buttons, null, { operationToken });
      return;
    }
    await decideTriage(item, "approve", buttons);
  });
  feedback.addEventListener("change", () => { if (triageInteractionLocked()) return; dismiss.disabled = !feedback.value; if (contextCorrection) contextCorrection.hidden = feedback.value !== "wrong_context"; });
  dismiss.addEventListener("click", () => {
    const unrelatedSourceEventIds = contextCorrection ? [...contextCorrection.querySelectorAll("[data-unrelated-source-event-id]:checked")].map((control) => control.dataset.unrelatedSourceEventId) : [];
    const quotedOrForwardedHistory = Boolean(contextCorrection?.querySelector("[data-quoted-or-forwarded-history]:checked"));
    const correction = feedback.value === "wrong_context" && (unrelatedSourceEventIds.length || quotedOrForwardedHistory) ? { unrelated_source_event_ids: unrelatedSourceEventIds, quoted_or_forwarded_history: quotedOrForwardedHistory } : null;
    decideTriage(item, "dismiss", buttons, feedback.value, { contextCorrection: correction });
  });
  evidence.addEventListener("click", () => showEvidence(item.source_event_id));
  row.append(save, approve, feedback, dismiss, evidence); form.append(row); section.append(form);
  return section;
}

function triageCandidateMatches(item, view) {
  if (view === "all_candidates") return true;
  const attentionView = { ready: "needs_you", low_signal: "possibilities", alerts: "alerts", recent: "history" }[view];
  if (state.surface?.human_triage_management?.active && attentionView && item.attention_view) return item.attention_view === attentionView;
  if (state.surface?.human_triage_management?.active && item.attention_view === "history") return false;
  if (view === "recent") return false;
  if (view === "alerts") return item.disposition?.disposition === "alerts" || item.management?.state === "alert";
  if (view === "needs_interpretation") return item.review_state?.state === "needs_interpretation";
  if (view === "needs_details") return item.review_state?.state === "needs_details" && item.decision_disposition === "decision";
  if (view === "ready") return item.queue_lane !== "standard" && item.decision_disposition === "decision";
  if (view === "low_signal") return item.queue_lane === "standard" && item.decision_disposition === "decision";
  if (view === "recurring_exceptions") return item.queue_lane === "recurring_exception";
  if (view === "learned_rules") return item.policy_disposition !== "none";
  return true;
}

function triageHistoryItems(data) {
  if (!data.human_triage_management?.active) return data.recent_items || [];
  const items = new Map((data.recent_items || []).map((item) => [item.proposal_id, item]));
  for (const item of data.items || []) if (item.attention_view === "history") items.set(item.proposal_id, item);
  return [...items.values()];
}

function triageVisibleMatters(data, view) {
  const pending = data.items || [];
  if (view === "recent") {
    return triageHistoryItems(data).map((item) => ({
      context_id: `recent:${item.proposal_id}`,
      label: item.title,
      queue_lane: item.state,
      candidate_count: 1,
      source_count: 1,
      candidates: [item],
    }));
  }
  const matters = data.triage?.matters || pending.map((item) => ({
    context_id: item.context_id || item.proposal_id,
    label: item.group_label || item.title,
    queue_lane: item.queue_lane || "standard",
    candidate_count: 1,
    source_count: 1,
    candidates: [item],
  }));
  return matters
    .map((matter) => ({
      ...matter,
      candidates: (matter.candidates || []).filter((item) => triageCandidateMatches(item, view)),
    }))
    .filter((matter) => matter.candidates.length);
}

async function loadMoreTriage(data) {
  if (triageInteractionLocked()) return;
  const cursor = data.triage?.next_cursor;
  if (!cursor) return;
  const generation = state.triageLoadGeneration;
  const button = document.querySelector("[data-triage-more]"); if (button) { button.dataset.triageLoading = "true"; button.disabled = true; button.textContent = "Loading…"; }
  try {
    const params = triageRequestParams({ cursor });
    const next = await api(`/api/member/v1/surfaces/triage?${params}`);
    if (triageInteractionLocked() || generation !== state.triageLoadGeneration) return;
    const known = new Set((data.triage.matters || []).map((matter) => matter.context_id));
    for (const matter of next.triage?.matters || []) if (!known.has(matter.context_id)) data.triage.matters.push(matter);
    const knownItems = new Set((data.items || []).map((item) => item.proposal_id));
    for (const item of next.items || []) if (!knownItems.has(item.proposal_id)) data.items.push(item);
    data.triage.next_cursor = next.triage?.next_cursor || null; data.triage.has_more = Boolean(next.triage?.has_more);
    renderSurface(data);
  } catch (error) {
    if (triageInteractionLocked() || generation !== state.triageLoadGeneration) return;
    if (error.status === 422 && error.message === "stale triage cursor") {
      state.triageCursorRefreshPending = true;
      state.triageLoadGeneration += 1;
      applyTriageInteractionLock();
      const root = $("#surface");
      root.setAttribute("aria-busy", "true");
      const notice = document.createElement("section");
      notice.className = "state-card";
      notice.setAttribute("role", "status");
      notice.append(text("h2", "Triage changed while you were reviewing it."), text("p", "Refreshing the complete scoped queue before showing another matter."));
      root.replaceChildren(notice);
      try {
        const refreshed = await reloadActiveSurface({ triageReconcileExact: Boolean(state.triageSelected) });
        if (!refreshed) throw new Error("The refreshed queue was not available.");
        state.triageCursorRefreshPending = false;
        releaseTriageInteractionLock();
        state.triageBannerKind = "status";
        const banner = $("#state-banner");
        banner.hidden = false;
        banner.textContent = "Triage changed in another view, so the queue was refreshed before pagination continued.";
      } catch (refreshError) {
        state.triageCursorRefreshPending = false;
        root.setAttribute("aria-busy", "false");
        const failure = document.createElement("section");
        failure.className = "state-card";
        failure.setAttribute("role", "alert");
        failure.append(text("h2", "Triage needs an authoritative reload."), text("p", "The stale page was removed. Reload this page before reviewing another candidate."));
        root.replaceChildren(failure);
        latchTriageRefreshFailure(`Triage changed and the authoritative refresh failed: ${refreshError.message} Reload this page before continuing.`);
      }
      return;
    }
    state.triageBannerKind = "error";
    const banner = $("#state-banner");
    banner.hidden = false;
    banner.textContent = error.message;
    if (button) { delete button.dataset.triageLoading; button.disabled = false; button.textContent = "Show more"; }
  }
}

async function approveTriageSuggestion(suggestion, button) {
  const token = beginTriageOperation();
  if (token == null) return;
  const values = { scope_kind: suggestion.scope_kind, scope_value: suggestion.scope_value, disposition: suggestion.suggested_disposition };
  const banner = $("#state-banner"); banner.hidden = false;
  state.triageBannerKind = "status";
  let ruleRecorded = false;
  try {
    if (!button.dataset.previewAffectedCount) {
      const preview = await api("/api/member/v1/triage-policy/preview", { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify(values) });
      if (!triageOperationIsCurrent(token)) return;
      button.dataset.previewAffectedCount = String(preview.affected_feedback_count);
      button.textContent = "Approve shadow rule";
      banner.textContent = `${preview.affected_feedback_count} matching feedback decisions. Select “Approve shadow rule” to confirm. It will not suppress, confirm, route, or write candidates.`;
      return;
    }
    const expectedAffectedCount = Number(button.dataset.previewAffectedCount);
    await api("/api/member/v1/triage-policy/approve", { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify({ ...values, request_id: crypto.randomUUID(), expected_affected_count: expectedAffectedCount }) });
    if (!triageOperationIsCurrent(token)) return;
    ruleRecorded = true;
    banner.textContent = "Shadow rule recorded. It is reversible and has no automatic candidate or provider effect in this rollout.";
    const refreshed = await reloadActiveSurface({ triageOperationToken: token });
    if (!refreshed || !triageOperationIsCurrent(token)) return;
  } catch (error) {
    if (!triageOperationIsCurrent(token)) return;
    if (ruleRecorded) latchTriageRefreshFailure(`The shadow rule was recorded, but Triage could not refresh: ${error.message} Reload this page before continuing.`);
    else banner.textContent = `${error.message} No rule was approved.`;
  } finally { finishTriageOperation(token); }
}

async function revokeTriageRule(rule, button) {
  const token = beginTriageOperation();
  if (token == null) return;
  const banner = $("#state-banner"); banner.hidden = false;
  state.triageBannerKind = "status";
  let revokeRecorded = false;
  try {
    await api(`/api/member/v1/triage-policy/${encodeURIComponent(rule.rule_id)}/revoke`, { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify({ request_id: crypto.randomUUID(), reason: "Revoked from Triage learning controls" }) });
    if (!triageOperationIsCurrent(token)) return;
    revokeRecorded = true;
    banner.textContent = "Shadow rule revoked.";
    const refreshed = await reloadActiveSurface({ triageOperationToken: token });
    if (!refreshed || !triageOperationIsCurrent(token)) return;
  } catch (error) {
    if (!triageOperationIsCurrent(token)) return;
    if (revokeRecorded) latchTriageRefreshFailure(`The shadow rule was revoked, but Triage could not refresh: ${error.message} Reload this page before continuing.`);
    else banner.textContent = `${error.message} The rule remains unchanged.`;
  } finally { finishTriageOperation(token); }
}

async function decideTriageLearningProposal(proposal, decision, button) {
  // Legacy proposals remain inspectable and rejectable; new activation uses V2.
  if (decision !== "reject" && decision !== "suppress") return;
  await runSourcePreferenceMutation(() => api(`/api/member/v1/triage-learning/${encodeURIComponent(proposal.policy_proposal_id)}/decide`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
    body: JSON.stringify({ request_id: crypto.randomUUID(), decision, instruction: proposal.instruction, preview_digest: proposal.preview_digest, trial_proposal_ids: [] }),
  }), "Earlier suggestion dismissed. No future rule was activated.");
}

async function reviewTriageLearningRule(rule, decision, button) {
  const nextInstruction = { always: "usually", usually: "ask", ask: "never" }[rule.instruction] || null;
  await runSourcePreferenceMutation(() => api(`/api/member/v1/triage-learning/rules/${encodeURIComponent(rule.rule_id)}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
    body: JSON.stringify({ request_id: crypto.randomUUID(), decision, negative_outcome: decision === "suspend" ? "member_correction" : null, replacement_instruction: decision === "narrow" ? nextInstruction : null, reason: `${titleCase(decision)} from Triage learning controls` }),
  }), `${titleCase(decision)} recorded for the earlier rule. Its audit history is retained.`);
}

function triageSurface(data, { preserveView = false } = {}) {
  const fragment = document.createDocumentFragment();
  const pending = data.items || [];
  const recent = triageHistoryItems(data);
  const inventory = data.triage?.inventory || {};
  const candidateCount = inventory.total_candidates || 0; const matterCount = inventory.total_matters || 0;
  const suggestions = data.triage?.feedback_suggestions || [];
  const policyRules = data.triage?.policy_rules || [];
  const sourcePolicyProposals = data.triage?.source_policy_proposals || [];
  const sourcePolicyRules = data.triage?.source_policy_rules || [];
  const activePolicyRules = policyRules.filter((rule) => rule.status === "active");
  const views = new Set(["needs_interpretation", "ready", "needs_details", "low_signal", "recurring_exceptions", "learned_rules", "alerts", "all_candidates", "recent"]);
  if (!views.has(state.triageView)) state.triageView = "ready";
  if (!preserveView && state.triageSelected && data.focus?.state === "pending") state.triageView = "all_candidates";
  if (!preserveView && state.triageSelected && data.focus?.state === "resolved") state.triageView = "recent";
  const viewCounts = inventory.views || {};
  const attentionCount = viewCounts.ready?.candidates ?? pending.filter((item) => triageCandidateMatches(item, "ready")).length;
  const overview = document.createElement("section"); overview.className = "card triage-overview";
  overview.append(text("p", "TRIAGE", "eyebrow"));
  if (data.communication_intake?.member_selection_required) overview.append(text("p", "Forward selected obligations to your CoS or use Add for review. Your CoS extracts proposed items here for your decision. Personal inbox messages do not automatically become obligations.", "state-note"));
  if (data.human_triage_management?.active) overview.append(text("p", "Focus contains admitted obligations only. Alerts and searchable context remain separate, and promotion, spam, routine updates, receipts, and context-only material do not become ordinary work.", "state-note"));
  if (attentionCount) {
    overview.append(text("h2", `${attentionCount} decision${attentionCount === 1 ? "" : "s"} need${attentionCount === 1 ? "s" : ""} your attention.`), text("p", "Start with the clearest security, date, money, promise, priority, and recurring-issue signals. Other suggestions stay available without crowding this queue."));
  } else {
    overview.append(text("h2", "You’re caught up on decisions that need attention."), text("p", candidateCount ? "Other suggestions and complete system accounting are still available when you want them." : "New eligible proposals will appear here when they need a human decision."));
  }
  const assurance = data.processing_health?.details?.source_assurance;
  if (assurance?.counts && (assurance.counts.missing || assurance.counts.failed)) {
    const sourceWarning = text("p", `${assurance.counts.failed || 0} source assessments failed and ${assurance.counts.missing || 0} are missing. Review system accounting before relying on this queue as complete.`, "review-state");
    sourceWarning.setAttribute("data-source-assurance", assurance.state || "unknown"); overview.append(sourceWarning);
  }
  const accounting = document.createElement("details"); accounting.className = "triage-system-accounting";
  accounting.append(text("summary", `System accounting · ${candidateCount} ${data.human_triage_management?.active ? "retained" : "unresolved"} candidate${candidateCount === 1 ? "" : "s"} in ${matterCount} decision group${matterCount === 1 ? "" : "s"}`));
  accounting.append(text("p", `${inventory.decision_candidates || 0} are independent decisions · ${inventory.duplicate_evidence || 0} are exact duplicate evidence · ${inventory.existing_object_evidence || 0} link to existing work.`, "state-note"));
  if (assurance?.counts) {
    const counts = assurance.counts;
    const status = text("p", `${counts.accounted} of ${counts.obligations} source observations accounted for · ${counts.eligible_candidates} produced candidates · ${counts.no_candidate} ${data.communication_intake?.member_selection_required ? "were not selected or produced no candidate" : "were explicitly assessed with none"} · ${counts.quarantined} safely quarantined${counts.pending ? ` · ${counts.pending} processing` : ""}.`, "state-note");
    status.setAttribute("data-source-assurance", assurance.state || "unknown"); accounting.append(status);
  }
  overview.append(accounting); fragment.append(overview);

  const controls = document.createElement("section"); controls.className = "triage-view-controls";
  const tabs = document.createElement("div"); tabs.className = "triage-lens-tabs"; tabs.setAttribute("role", "group"); tabs.setAttribute("aria-label", "Triage views");
  const humanViews = Boolean(data.human_triage_management?.active);
  const moreViews = document.createElement("details"); moreViews.className = "triage-more-views";
  moreViews.open = !["ready", "alerts", "recent", "all_candidates"].includes(state.triageView);
  moreViews.append(text("summary", "More views"));
  const additionalTabs = document.createElement("div"); additionalTabs.className = "triage-lens-tabs";
  const lenses = humanViews
    ? [["ready", "Needs you"], ["alerts", "Alerts"], ["recent", "History"], ["all_candidates", "All candidates"], ["needs_details", "Needs details"], ["needs_interpretation", "Needs interpretation"], ["low_signal", "Other possibilities"], ["recurring_exceptions", "Recurring issues"], ["learned_rules", "Earlier shadow rules"]]
    : [["ready", "Needs attention"], ["alerts", "Alerts"], ["needs_details", "Needs details"], ["needs_interpretation", "Needs interpretation"], ["low_signal", "Low signal"], ["recurring_exceptions", "Recurring issues"], ["learned_rules", "Learned rules"], ["all_candidates", "All candidates"], ["recent", "Recent"]];
  for (const [key, label] of lenses) {
    const count = key === "recent" ? (humanViews ? viewCounts.history?.candidates ?? recent.length : recent.length) : key === "learned_rules" ? activePolicyRules.length : viewCounts[key]?.candidates ?? pending.filter((item) => triageCandidateMatches(item, key)).length;
    const button = document.createElement("button"); button.className = "comms-filter"; button.type = "button"; button.setAttribute("aria-pressed", String(state.triageView === key)); button.setAttribute("aria-label", `${label}, ${count}`);
    button.append(text("span", label), text("span", count, "triage-tab-count"));
    button.addEventListener("click", async () => {
      if (state.triageView === key || triageInteractionLocked()) return;
      if (state.triageBannerKind === "continuation") clearBanner();
      state.triageLoadGeneration += 1;
      state.triageView = key; state.triageSelected = null; state.triageMatter = null; state.triageFocusPending = false;
      data.focus = { requested_candidate_id: null, state: "not_requested", candidate_state: null, matter_id: null };
      const mustReload = key !== "all_candidates" && (state.triageSort !== "attention" || state.triageSource !== "all" || state.triageKind !== "all");
      if (mustReload) { state.triageSort = "attention"; state.triageSource = "all"; state.triageKind = "all"; }
      replaceQuery({ view: key, item: null, matter: null, sort: state.triageSort === "attention" ? null : state.triageSort, source: state.triageSource === "all" ? null : state.triageSource, kind: state.triageKind === "all" ? null : state.triageKind });
      if (!mustReload) { renderSurface(data); return; }
      try { await reloadActiveSurface({ triageFocus: false }); }
      catch (error) { setBanner(`The complete queue could not be reloaded: ${error.message}`); }
    });
    (humanViews && !["ready", "alerts", "recent", "all_candidates"].includes(key) ? additionalTabs : tabs).append(button);
  }
  controls.append(tabs);
  if (humanViews) {
    moreViews.append(additionalTabs); controls.append(moreViews);
    const destinations = document.createElement("div"); destinations.className = "button-row";
    for (const [label, href] of [["Updates and source history", "/member/comms?view=all"], ["Tune sources", "/member/settings?section=source-context"], ["Learning and Undo", "/member/settings?section=learning-trust"]]) { const link = text("a", label, "link-button"); link.href = href; destinations.append(link); }
    controls.append(destinations);
  }
  if (state.triageView === "all_candidates") {
    const facets = inventory.facets || {};
    const advanced = document.createElement("div"); advanced.className = "triage-inventory-filters";
    const sort = selectControl([{ value: "attention", label: "Attention order" }, { value: "oldest", label: "Oldest first" }, { value: "newest", label: "Newest first" }], state.triageSort, ""); sort.setAttribute("aria-label", "Sort complete inventory");
    const source = selectControl([{ value: "all", label: "All sources" }, ...(facets.sources || []).map((value) => ({ value: value.id, label: `${value.label} (${value.candidates})` }))], state.triageSource, ""); source.setAttribute("aria-label", "Filter complete inventory by source");
    const kind = selectControl([{ value: "all", label: "All types" }, ...(facets.types || []).map((value) => ({ value: value.id, label: `${value.label} (${value.candidates})` }))], state.triageKind, ""); kind.setAttribute("aria-label", "Filter complete inventory by type");
    const refresh = async () => {
      if (triageInteractionLocked()) return;
      state.triageSort = sort.value; state.triageSource = source.value; state.triageKind = kind.value;
      state.triageSelected = null; state.triageMatter = null; state.triageLoadGeneration += 1;
      replaceQuery({ item: null, matter: null, sort: state.triageSort === "attention" ? null : state.triageSort, source: state.triageSource === "all" ? null : state.triageSource, kind: state.triageKind === "all" ? null : state.triageKind });
      for (const control of [sort, source, kind]) control.disabled = true;
      try { await reloadActiveSurface({ triageFocus: false }); }
      catch (error) { setBanner(`The inventory filter could not be applied: ${error.message}`); for (const control of [sort, source, kind]) control.disabled = false; }
    };
    for (const control of [sort, source, kind]) control.addEventListener("change", refresh);
    advanced.append(field("Sort", sort), field("Source", source), field("Type", kind));
    const selectedCount = data.triage?.selection?.candidates ?? candidateCount;
    advanced.append(text("p", `${selectedCount} matching candidate${selectedCount === 1 ? "" : "s"}. Filters change only this recovery view, never importance or candidate state.`, "state-note field-span"));
    controls.append(advanced);
  }
  if (data.human_triage_management?.backlog_preview_available) {
    const preview = document.createElement("section"); preview.className = "card triage-backlog-preview";
    preview.append(text("p", "BACKLOG REASSESSMENT", "eyebrow"), text("h2", "Preview old and proposed handling"), text("p", "The preview is bounded and read-only. It preserves every source and audit event, applies no mass dismissal, and cannot contact a provider.", "state-note"));
    const previewButton = text("button", "Preview first batch", "button button-quiet"); previewButton.type = "button";
    const output = document.createElement("div"); output.setAttribute("aria-live", "polite");
    previewButton.addEventListener("click", async () => {
      if (sourcePreferenceInteractionLocked()) return;
      const generation = state.triageOperationGeneration;
      previewButton.disabled = true;
      try {
        const result = await api("/api/member/v1/triage/backlog-reassessment-preview?limit=25");
        if (!preview.isConnected || sourcePreferenceInteractionLocked() || generation !== state.triageOperationGeneration) return;
        output.replaceChildren(text("p", `${result.batch.count} of ${result.batch.total} candidates previewed · ${result.changes.filter((change) => change.changed).length} proposed changes · 0 provider effects.`, "state-note"));
        const list = document.createElement("ul");
        for (const change of result.changes.slice(0, 10)) list.append(text("li", `${titleCase(change.old.disposition)} → ${titleCase(change.proposed.disposition)} · ${titleCase(change.proposed.lane)} · ${(change.proposed.reason_codes || []).join(", ") || "deterministic admission rule"}`));
        output.append(list);
      } catch (error) { output.replaceChildren(text("p", error.message, "review-state")); }
      finally { sourcePreferenceControlState(previewButton, false); }
    });
    preview.append(previewButton, output); fragment.append(preview);
  }
  fragment.append(controls);
  const visibleMatters = triageVisibleMatters(data, state.triageView);
  const missingRequestedCandidate = Boolean(state.triageSelected && data.focus?.state === "not_found");
  if (state.triageSelected) state.triageMatter = visibleMatters.find((matter) => matter.candidates.some((item) => item.proposal_id === state.triageSelected))?.context_id || state.triageMatter;
  if (missingRequestedCandidate) state.triageMatter = null;
  else if (!state.triageMatter || !visibleMatters.some((matter) => matter.context_id === state.triageMatter)) state.triageMatter = visibleMatters[0]?.context_id || null;
  const selectedMatter = visibleMatters.find((matter) => matter.context_id === state.triageMatter) || null;
  const visibleCandidateCount = visibleMatters.reduce((count, matter) => count + matter.candidates.length, 0);
  const completeViewCount = state.triageView === "all_candidates" ? data.triage?.selection?.candidates ?? candidateCount : state.triageView === "recent" && humanViews ? viewCounts.history?.candidates ?? visibleCandidateCount : viewCounts[state.triageView]?.candidates ?? visibleCandidateCount;
  const matchesBeyondPage = humanViews && !visibleMatters.length && data.triage?.has_more && completeViewCount > 0;
  const workspace = document.createElement("div"); workspace.className = "triage-workspace";
  const workspaceLabels = { ready: "Decisions that need you", needs_details: "Decisions missing details", needs_interpretation: "Items to classify", low_signal: "Other suggestions", recurring_exceptions: "Recurring issues", learned_rules: "Items matching shadow rules", alerts: "Alerts and safety", all_candidates: "Complete candidate inventory", recent: "Recent decisions" };
  const master = document.createElement("section"); master.className = "card triage-master"; master.append(text("h2", workspaceLabels[state.triageView] || "Decisions to review"));
  if (state.triageView === "recent") master.append(text("p", humanViews ? "Retained decisions and reference candidates. Open candidates remain reviewable; completed decisions keep their audit trail." : "Read-only audit history. System-retired candidates are historical lifecycle receipts, not new sender rules.", "state-note"));
  if (!visibleMatters.length) master.append(text("p", matchesBeyondPage ? `${completeViewCount} candidate${completeViewCount === 1 ? " matches" : "s match"} this view beyond the loaded page. Choose Show more to review ${completeViewCount === 1 ? "it" : "them"}.` : state.triageView === "ready" ? "Nothing needs your attention right now." : state.triageView === "needs_details" ? "No decisions are waiting for missing details." : state.triageView === "needs_interpretation" ? "No items are waiting for a type." : state.triageView === "low_signal" ? "No lower-signal suggestions are waiting." : state.triageView === "recent" ? "No recent decisions yet." : "Nothing matches this view.", "empty"));
  const list = document.createElement("ul"); list.className = "triage-master-list";
  let priorLane = null;
  const groupByAttention = state.triageView === "ready" || (state.triageView === "all_candidates" && state.triageSort === "attention");
  for (const matter of visibleMatters) {
    if (groupByAttention && matter.queue_lane !== priorLane) {
      priorLane = matter.queue_lane;
      const [laneLabel] = triageLanePresentation(matter.queue_lane);
      const heading = document.createElement("li"); heading.className = "triage-lane-heading"; heading.append(text("span", laneLabel), text("small", "Explainable attention group")); list.append(heading);
    }
    const li = document.createElement("li"); const button = document.createElement("button"); button.type = "button"; button.className = "triage-master-button"; button.setAttribute("aria-current", String(matter.context_id === state.triageMatter));
    const first = matter.candidates[0]; const source = first?.source_context?.state === "available" ? [first.source_context.channel && titleCase(first.source_context.channel), first.source_context.sender].filter(Boolean).join(" · ") : null;
    const [laneLabel] = triageLanePresentation(matter.queue_lane); const due = triageDateLabel(triageCandidateDate(first)); const category = first?.source_context?.provider_category?.label;
    const cues = [laneLabel, due ? `Date: ${due}` : null, source, category ? `Provider category: ${category}` : null, matter.candidates.length > 1 ? `${matter.candidates.length} related decisions` : null, matter.source_count > 1 ? `${matter.source_count} sources` : null].filter(Boolean).join(" · ");
    button.append(text("strong", matter.label || first?.title || "Review decision"), text("small", cues));
    button.addEventListener("click", () => { if (triageInteractionLocked()) return; state.triageMatter = matter.context_id; state.triageSelected = matter.candidates[0]?.proposal_id || null; state.triageFocusPending = true; replaceQuery({ view: state.triageView, item: state.triageSelected, matter: matter.context_id }); renderSurface(data); }); li.append(button); list.append(li);
  }
  master.append(list);
  const viewCountIsComplete = state.triageView !== "learned_rules";
  if (data.triage?.has_more && (state.triageView !== "recent" || humanViews) && (!viewCountIsComplete || visibleCandidateCount < completeViewCount)) { const more = text("button", "Show more", "button button-quiet"); more.type = "button"; more.dataset.triageMore = "true"; more.addEventListener("click", () => loadMoreTriage(data)); master.append(more); }
  const detail = document.createElement("div"); detail.className = "triage-matter-detail";
  if (missingRequestedCandidate) {
    const unavailable = document.createElement("section"); unavailable.className = "card triage-detail"; unavailable.setAttribute("role", "status");
    unavailable.append(text("p", "EXACT REVIEW LINK", "eyebrow"), text("h2", "This candidate is not available in your member scope."), text("p", "It may have been removed by source correction, or the link may belong to a different private member. AdministrateMe did not substitute another decision.", "state-note"));
    detail.append(unavailable);
  } else if (selectedMatter) {
    if (selectedMatter.candidates.length > 1) detail.append(text("p", `${selectedMatter.candidates.length} related decisions are shown together. Confirming one never confirms the others.`, "state-note"));
    for (const item of selectedMatter.candidates) {
      const closed = !["pending_interpretation", "pending_review"].includes(item.state) || !["open", "clarification", "suppression_proposed", "alert"].includes(item.management?.state || "open");
      detail.append(triageDetail(item, data, humanViews ? closed : state.triageView === "recent"));
    }
  }
  workspace.append(master, detail); fragment.append(workspace);
  if (suggestions.length || activePolicyRules.length || sourcePolicyProposals.length || sourcePolicyRules.length) {
    const learning = document.createElement("section"); learning.className = "card triage-learning"; learning.append(text("p", "REVERSIBLE LEARNING", "eyebrow"), text("h2", `${suggestions.length} suggested pattern${suggestions.length === 1 ? "" : "s"} · ${activePolicyRules.length} shadow rule${activePolicyRules.length === 1 ? "" : "s"}`), text("p", "Rules begin in shadow mode. They stay visible and reversible, and cannot silently confirm or suppress household truth.", "state-note"));
    for (const suggestion of suggestions.slice(0, 5)) {
      const row = document.createElement("div"); row.className = "button-row";
      row.append(text("span", `${suggestion.occurrences} earlier corrections · ${titleCase(suggestion.reason)}`));
      if (data.human_triage_management?.active) {
        const review = text("a", "Choose an exact future preference", "button button-quiet"); review.href = "/member/settings?section=source-context"; row.append(review);
      } else {
        const approve = text("button", "Preview & approve shadow rule", "button button-quiet"); approve.type = "button"; approve.addEventListener("click", () => approveTriageSuggestion(suggestion, approve)); row.append(approve);
      }
      learning.append(row);
    }
    for (const rule of activePolicyRules) { const row = document.createElement("div"); row.className = "button-row"; row.append(text("span", `${titleCase(rule.disposition)} · ${titleCase(rule.scope_kind)} · shadow only`)); const revoke = text("button", "Revoke", "button button-quiet"); revoke.type = "button"; revoke.addEventListener("click", () => revokeTriageRule(rule, revoke)); row.append(revoke); learning.append(row); }
    for (const proposal of sourcePolicyProposals.filter((value) => value.status === "proposed")) {
      const row = document.createElement("div"); row.className = "button-row";
      row.append(text("span", `Earlier ${titleCase(proposal.scope_kind)} suggestion · not activated`));
      const review = text("a", "Review exact source and purpose", "button button-quiet"); review.href = "/member/settings?section=source-context"; row.append(review);
      const reject = text("button", "Dismiss this suggestion", "button button-quiet"); reject.type = "button";
      reject.addEventListener("click", () => decideTriageLearningProposal(proposal, "reject", reject)); row.append(reject);
      learning.append(row);
    }
    for (const rule of sourcePolicyRules.filter((value) => ["active", "trial"].includes(value.status))) {
      const row = document.createElement("div"); row.className = "button-row";
      row.append(text("span", `${titleCase(rule.instruction)} · ${titleCase(rule.scope_kind)} · ${titleCase(rule.status)}`));
      const reviews = [["retain", "Retain"], ...(rule.instruction === "never" ? [] : [["narrow", "Narrow one step"]]), ["suspend", "Suspend"], ["revoke", "Revoke"]];
      for (const [decision, label] of reviews) {
        const action = text("button", label, "button button-quiet"); action.type = "button";
        action.addEventListener("click", () => reviewTriageLearningRule(rule, decision, action)); row.append(action);
      }
      learning.append(row);
    }
    fragment.append(learning);
  }
  if (data.human_triage_management?.active) fragment.append(sourceLearningRulesCard(true));
  if (state.triageSelected && state.triageFocusPending && !missingRequestedCandidate) {
    queueMicrotask(() => {
      const target = [...document.querySelectorAll("[data-triage-item]")].find((node) => node.dataset.triageItem === state.triageSelected);
      if (!target) return;
      target.focus({ preventScroll: true });
      target.scrollIntoView({ block: "start", behavior: "smooth" });
      state.triageFocusPending = false;
    });
  }
  return fragment;
}

function rankedItems(value) {
  if (!value) return [];
  if (Array.isArray(value.items)) return value.items;
  if (Array.isArray(value.ranked)) return value.ranked;
  if (value.action) return [value.action];
  if (value.horizons) {
    return ["right_now", "next", "today", "this_week", "this_month", "someday"]
      .map((key) => value.horizons[key])
      .filter(Boolean);
  }
  return [];
}

function whatNowCard(value, mode) {
  const ranked = rankedItems(value);
  if (mode === "compressed") return card("Decision queue", ranked, "Your queue is clear. Leave it that way for a minute.");
  const item = ranked[0];
  const section = document.createElement("section");
  section.className = "hero-card";
  section.append(text("p", "ONE SMALL MOVE", "eyebrow"));
  if (!item) {
    section.append(text("h2", "Nothing urgent is asking for you.", "action-title"));
    section.append(text("p", "You can pause, or choose something gently on purpose."));
    return section;
  }
  section.append(text("h2", item.title || item.description || "Take the next small step", "action-title"));
  section.append(text("p", item.micro_step || "Open it, look once, and decide only the first move.", "microstep"));
  section.append(text("p", [item.energy, item.effort, item.context].filter(Boolean).join(" · ") || "Low decision load", "meta"));
  const progress = document.createElement("div");
  progress.className = "progress-track";
  progress.setAttribute("role", "progressbar");
  progress.setAttribute("aria-label", "Starting progress");
  progress.setAttribute("aria-valuenow", "34"); progress.setAttribute("aria-valuemin", "0"); progress.setAttribute("aria-valuemax", "100");
  progress.append(document.createElement("span"));
  section.append(progress);
  if (item.last_event_id && item.id && ["task", "commitment"].includes(item.kind)) {
    const row = document.createElement("div"); row.className = "button-row";
    for (const action of ["complete", "defer", "block"].filter((value) => (item.actions || []).includes(value) && (item.kind === "task" || value === "complete"))) {
      const button = text("button", action[0].toUpperCase() + action.slice(1), action === "complete" ? "button button-primary" : "button button-quiet");
      button.type = "button";
      button.addEventListener("click", () => advanceAction(item, action, section));
      row.append(button);
    }
    if (item.kind === "commitment" && (item.actions || []).some((action) => ["defer", "block", "unblock", "cancel", "delegate"].includes(action))) {
      const manage = text("button", "Manage promise…", "button button-quiet"); manage.type = "button"; manage.addEventListener("click", () => commitmentManageDialog(item)); row.append(manage);
    }
    section.append(row);
  }
  return section;
}

async function advanceAction(item, action, section) {
  const buttons = section.querySelectorAll("button"); buttons.forEach((button) => { button.disabled = true; });
  const banner = $("#state-banner"); banner.hidden = false; banner.textContent = "Nice—recording that outcome and finding the next small step…";
  const tomorrow = new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString();
  try {
    const result = await api("/api/member/v1/lifecycle", {
      method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
      body: JSON.stringify({
        item_kind: item.kind, item_id: item.id, action,
        expected_last_event_id: item.last_event_id, request_id: crypto.randomUUID(),
        until: ["defer", "snooze"].includes(action) ? tomorrow : null,
        reason: action === "block" ? "Paused from Today; needs one unblock condition." : null,
        note: action === "complete" ? "Completed from the member portal" : null,
      }),
    });
    const next = result.next_action || result.what_now;
    banner.textContent = action === "complete" ? "Recorded. Here’s what fits now." : "Updated. Here’s the recalculated next move.";
    section.replaceWith(whatNowCard(next, state.surface?.mode || state.bootstrap.mode));
  } catch (error) {
    banner.textContent = `${error.message} Nothing was implied as complete.`;
    buttons.forEach((button) => { button.disabled = false; });
  }
}

function titleCase(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function memberStatusLabel(value) {
  return value === "inbox" ? "Open" : titleCase(value);
}

function todayItemMeta(item) {
  return [item.time_label, item.context, item.status ? memberStatusLabel(item.status) : null]
    .filter(Boolean)
    .join(" · ");
}

function todayDecisionExplanation(item) {
  const value = item.decision_context;
  if (!value) return null;
  const section = document.createElement("div"); section.className = "today-decision-context";
  const happened = document.createElement("p"); happened.append(text("strong", "What happened: "), document.createTextNode(value.what_happened || "A source produced a candidate for review."));
  const why = document.createElement("p"); why.append(text("strong", "Why it is here: "), document.createTextNode(value.why_now || "It still needs your confirmation."));
  section.append(happened, why);
  if (value.source?.label) {
    const source = text("p", value.source.label, "today-decision-source");
    if (value.source.occurred_at) source.append(document.createTextNode(` · ${new Date(value.source.occurred_at).toLocaleString()}`));
    section.append(source);
  }
  const details = document.createElement("details"); details.className = "today-decision-details";
  details.append(text("summary", "Context, uncertainties, and consequences"));
  details.append(text("p", value.smallest_next_step || item.micro_step, "state-note"));
  if (value.uncertainties?.length) {
    details.append(text("strong", "Still unknown"));
    const list = document.createElement("ul"); for (const uncertainty of value.uncertainties) list.append(text("li", uncertainty)); details.append(list);
  }
  if (value.choices?.length) {
    details.append(text("strong", "Your choices"));
    const choices = document.createElement("dl"); choices.className = "today-decision-choices";
    for (const choice of value.choices) choices.append(text("dt", choice.label), text("dd", choice.consequence));
    details.append(choices);
  }
  details.append(text("p", value.interpretation_disclosure === "bounded_language_review_aid" ? "The explanation is a bounded language review aid. Exact source words remain evidence; only your decision can make anything official." : "This explanation uses typed source facts and fixed rules. Only your decision can make anything official.", "state-note"));
  section.append(details);
  return section;
}

function setBanner(message) {
  if (currentSurface() === "triage") state.triageBannerKind = "status";
  const banner = $("#state-banner");
  banner.hidden = false;
  banner.textContent = message;
}

function appendAppleExecutionHandoff(container, provider, approvalEventId) {
  if (!["apple_calendar", "apple_reminders"].includes(provider) || typeof approvalEventId !== "string" || !approvalEventId) return;
  const member = state.bootstrap?.member;
  if (!member || member.authenticated_role !== "principal" || member.viewing_as || member.authenticated_id !== member.id) return;
  const key = `administrateme:apple-action-handoff:${member.authenticated_id}:${approvalEventId}`;
  let started = true;
  try { started = sessionStorage.getItem(key) !== null; } catch { /* Fail closed without durable browser handoff tracking. */ }
  const button = text("button", "Continue to execution approval", "button button-primary"); button.type = "button";
  const note = text("span", started ? "Execution was already requested from this browser. Check CoS’s result before taking another action." : "Your decision is recorded. Apple still needs a separate, one-time execution approval.", "state-note apple-execution-handoff-note");
  note.setAttribute("role", "status"); note.setAttribute("aria-live", "polite");
  button.disabled = started || !state.bootstrap.chat.enabled || state.bootstrap.chat.state !== "healthy";
  button.addEventListener("click", async (event) => {
    if (started) return;
    if (state.controller) { note.textContent = "Finish or stop the current conversation before continuing."; return; }
    try { sessionStorage.setItem(key, JSON.stringify({ domain_approval_event_id: approvalEventId, provider })); }
    catch { button.disabled = true; note.textContent = "This browser cannot retain the handoff reference. No execution approval was requested."; return; }
    started = true; button.disabled = true;
    $("#chat-panel").classList.add("open"); $("#chat-open").setAttribute("aria-expanded", "true");
    await sendChat(event, {
      message: `Please use administrateme_execute_pending_provider_action with domain_approval_event_id ${approvalEventId} to request the separate one-time execution approval for the exact ${titleCase(provider)} action I approved.`,
      displayMessage: `Please request execution approval for the exact ${titleCase(provider)} action I just approved.`,
    });
    note.textContent = "Check CoS’s result. This action will not be requested again from this control.";
  });
  container.append(document.createTextNode(" "), button, note);
}

function clearBanner() {
  const banner = $("#state-banner"); banner.hidden = true; banner.textContent = "";
  state.triageBannerKind = null;
}

async function executeTodayAction(item, action, container) {
  if (action === "evidence") {
    if (item.evidence_event_id || item.last_event_id) await showEvidence(item.evidence_event_id || item.last_event_id);
    return;
  }
  if (["open_triage", "open_comms", "open_reminder", "open_calendar"].includes(action)) {
    location.href = item.surface_link?.href || ({
      open_triage: "/member/triage",
      open_comms: "/member/comms",
      open_reminder: "/member/reminders",
      open_calendar: "/member/calendar",
    })[action];
    return;
  }
  if (action === "open_chat") {
    $("#chat-open").click();
    return;
  }
  if (item.kind === "commitment" && action === "manage_commitment") {
    commitmentManageDialog(item);
    return;
  }
  const buttons = container.querySelectorAll("button");
  buttons.forEach((button) => { button.disabled = true; });
  let appleApprovalEventId = null;
  try {
    if (["task", "commitment", "recurrence"].includes(item.kind) && action === "complete") {
      setBanner("Recording that finish and rebuilding the rest of today…");
      await api("/api/member/v1/lifecycle", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
        body: JSON.stringify({
          item_kind: item.kind,
          item_id: item.id,
          action: "complete",
          expected_last_event_id: item.last_event_id,
          request_id: crypto.randomUUID(),
          note: "Completed from Today",
        }),
      });
      setBanner("Recorded. Today has been recalculated from household truth.");
    } else if (item.kind === "calendar_event" && ["attended", "missed"].includes(action)) {
      setBanner("Recording this calendar outcome without changing the event itself…");
      await api(`/api/member/v1/calendar/${encodeURIComponent(item.id)}/outcome`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
        body: JSON.stringify({
          request_id: crypto.randomUUID(),
          expected_last_event_id: item.last_event_id,
          outcome: action,
        }),
      });
      setBanner(action === "attended" ? "Attendance recorded." : "Missed event recorded. Nothing else was inferred.");
    } else if (item.kind === "routine" && ["complete_step", "skip_step"].includes(action)) {
      const step = item.current_step;
      setBanner(action === "complete_step" ? "Recording this routine step…" : "Skipping only this step and keeping the routine moving…");
      await api(`/api/member/v1/routines/${encodeURIComponent(item.id)}/occurrences/${encodeURIComponent(item.occurrence_date)}/steps/${encodeURIComponent(step.step_id)}/outcome`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
        body: JSON.stringify({
          request_id: crypto.randomUUID(),
          expected_last_event_id: item.last_event_id,
          outcome: action === "complete_step" ? "completed" : "skipped",
        }),
      });
      setBanner(action === "complete_step" ? "Step recorded. Here is the next one." : "Step skipped without changing any linked task.");
    } else if (["recurrence", "routine"].includes(item.kind) && action === "pause") {
      await runRhythmLifecycle({ ...item, object_kind: item.kind }, "pause");
      return;
    } else if (item.kind === "list_item" && action === "complete_external") {
      setBanner("Requesting completion through the reminder provider…");
      const result = await api("/api/member/v1/reminders/commands", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
        body: JSON.stringify({
          provider: item.provider,
          provider_account_id: item.provider_account_id,
          operation: item.provider === "google_tasks" ? "complete_task" : "complete_reminder",
          list_external_id: item.list_external_id,
          item_external_id: item.item_external_id,
          expected_last_event_id: item.last_event_id,
          expected_provider_revision: item.provider_revision,
          payload: {},
          request_id: crypto.randomUUID(),
        }),
      });
      if (item.provider === "apple_reminders" && result.state === "awaiting_openclaw_approval") appleApprovalEventId = result.domain_approval_event_id;
      setBanner("Completion saved for approval. The provider is unchanged until OpenClaw confirms this exact action.");
    }
    await reloadActiveSurface();
    if (appleApprovalEventId) appendAppleExecutionHandoff($("#state-banner"), item.provider, appleApprovalEventId);
  } catch (error) {
    setBanner(`${error.message} Nothing was implied as complete.`);
    buttons.forEach((button) => { button.disabled = false; });
  }
}

function actionButton(item, action, container) {
  const labels = {
    complete: "Done",
    complete_external: "Done",
    complete_step: "Step done",
    skip_step: "Skip step",
    pause: "Pause",
    attended: "Attended",
    missed: "Missed",
    open_triage: "Review",
    open_comms: "Open conversation",
    open_reminder: "Review copies",
    open_calendar: "Open calendar",
    open_chat: "Talk it through",
    evidence: "Why this is here",
    manage_commitment: "Manage promise…",
  };
  const primary = ["complete", "complete_external", "complete_step", "attended", "open_triage"].includes(action);
  const button = text("button", labels[action] || titleCase(action), action === "evidence" ? "link-button" : `button ${primary ? "button-primary" : "button-quiet"}`);
  button.type = "button";
  button.addEventListener("click", () => executeTodayAction(item, action, container));
  return button;
}

function possibleCopySources(item, compact = false) {
  const copies = item.possible_copy?.items || [];
  if (!copies.length) return null;
  const box = document.createElement(compact ? "small" : "section");
  box.className = "possible-copy possible-copy-sources";
  box.append(text("strong", `${copies.length} possible provider copies are grouped only in this view. `));
  const links = document.createElement("span");
  copies.forEach((copy, index) => {
    if (index) links.append(document.createTextNode(" · "));
    const anchor = text("a", copy.list_name || `Source ${index + 1}`);
    anchor.href = copy.surface_link?.href || "/member/reminders";
    links.append(anchor);
  });
  box.append(links);
  return box;
}

function todayItem(item, { interactive = true } = {}) {
  const li = document.createElement("li");
  li.className = `day-item day-item-${String(item.kind).replaceAll("_", "-")}`;
  const body = document.createElement("div");
  body.className = "day-item-body";
  body.append(text("strong", item.title || "Household item"));
  const meta = todayItemMeta(item);
  if (meta) body.append(text("span", meta, "meta"));
  const decisionExplanation = todayDecisionExplanation(item);
  if (decisionExplanation) body.append(decisionExplanation);
  else if (item.micro_step) body.append(text("small", item.micro_step));
  const copies = possibleCopySources(item, true); if (copies) body.append(copies);
  li.append(body);
  if (interactive) {
    const row = document.createElement("div");
    row.className = "day-item-actions";
    const actions = (item.actions || []).filter((action) => ["complete", "complete_external", "complete_step", "skip_step", "pause", "attended", "missed", "open_triage", "open_comms", "open_reminder", "open_calendar", "open_chat", "evidence"].includes(action));
    if (item.kind === "commitment" && (item.actions || []).some((action) => ["defer", "block", "unblock", "cancel", "delegate"].includes(action))) actions.splice(Math.max(actions.length - Number(actions.includes("evidence")), 0), 0, "manage_commitment");
    for (const action of actions) row.append(actionButton(item, action, li));
    if (actions.length) li.append(row);
  }
  return li;
}

function daySection(title, items, emptyCopy) {
  const section = document.createElement("section");
  section.className = "card day-section";
  const heading = document.createElement("div");
  heading.className = "section-heading";
  heading.append(text("h2", title), text("span", `${items.length}`, "count-badge"));
  section.append(heading);
  if (!items.length) {
    section.append(text("p", emptyCopy, "empty compact-empty"));
    return section;
  }
  const list = document.createElement("ol");
  list.className = "day-list";
  for (const item of items) list.append(todayItem(item));
  section.append(list);
  return section;
}

function todayViewControls(data) {
  const controls = document.createElement("div");
  controls.className = "today-controls";
  const switcher = document.createElement("div");
  switcher.className = "view-switcher";
  switcher.setAttribute("role", "group");
  switcher.setAttribute("aria-label", "Today view");
  for (const [view, label] of [["brief", "Day Brief"], ["carousel", "Carousel"]]) {
    const button = text("button", label, "view-button");
    button.type = "button";
    button.setAttribute("aria-pressed", String(state.todayView === view));
    button.addEventListener("click", () => {
      state.todayView = view;
      replaceQuery({ view });
      if (view === "carousel") state.carouselIndex = 0;
      renderToday(data);
    });
    switcher.append(button);
  }
  const whatNow = text("button", "What Now?", "button button-primary what-now-button");
  whatNow.type = "button";
  whatNow.addEventListener("click", () => {
    const first = data.brief?.carousel?.recommended_item || data.brief?.what_now?.horizons?.right_now;
    const match = state.carouselItems.findIndex((item) => item.kind === first?.kind && item.id === first?.id);
    state.carouselIndex = match >= 0 ? match : 0;
    state.todayView = "carousel";
    renderToday(data);
    $(".carousel-card")?.focus();
  });
  controls.append(switcher, whatNow);
  return controls;
}

function briefHero(brief) {
  const section = document.createElement("section");
  section.className = "hero-card day-hero";
  section.append(
    text("p", `${brief.daypart} briefing`, "eyebrow"),
    text("h2", "Here is the shape of your day.", "action-title"),
    text("p", brief.summary_text, "brief-summary"),
  );
  if (brief.next_event) {
    const next = document.createElement("div");
    next.className = "next-event";
    next.append(text("span", "NEXT ON THE CLOCK", "meta"), text("strong", brief.next_event.title), text("span", brief.next_event.time_label, "meta"));
    section.append(next);
  }
  const stats = document.createElement("dl");
  stats.className = "brief-stats";
  for (const [label, value] of [["Timed", brief.stats.timed_events ?? brief.stats.events], ["Day context", brief.stats.day_context || 0], ["Moves", brief.stats.moves], ["Decisions", brief.stats.questions]]) {
    stats.append(text("div", "", "stat"));
    stats.lastElementChild.append(text("dt", label), text("dd", value));
  }
  section.append(stats);
  return section;
}

function todayCalendarGuidance(brief) {
  const guidance = brief.calendar_guidance || {};
  const section = document.createElement("section");
  section.className = "card day-calendar-guidance";
  section.append(text("h2", "Time guardrails"));
  const current = guidance.current_guardrail;
  if (current) {
    const currentRow = document.createElement("p");
    currentRow.append(text("strong", current.label || titleCase(current.kind)));
    const effect = current.effect === "hard_block" ? "Protected" : titleCase(current.effect);
    currentRow.append(text("span", ` · ${effect} until ${new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date(current.end_at))}`, "meta"));
    section.append(currentRow);
    if (current.effect === "context") section.append(text("p", "Context only — it does not block your time.", "state-note"));
  }
  const now = new Date();
  const opening = (guidance.open_windows || []).find((window) => new Date(window.end_at) > now);
  if (opening) {
    const start = new Date(opening.start_at);
    const end = new Date(opening.end_at);
    const isOpen = start <= now;
    section.append(text("p", isOpen ? `Open now until ${new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(end)}.` : `Next opening: ${calendarTime(start)}–${new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(end)}.`, "calendar-open-now"));
  }
  const softLoad = Number(guidance.counts?.soft_hold || 0);
  if (softLoad) section.append(text("p", `${softLoad} soft-load ${softLoad === 1 ? "item is" : "items are"} visible. It warns without silently blocking your day.`, "state-note"));
  if (!current && !opening && !softLoad) section.append(text("p", "No calendar guardrail needs attention right now.", "empty compact-empty"));
  return section;
}

function renderDayBrief(brief) {
  const fragment = document.createDocumentFragment();
  fragment.append(briefHero(brief), todayCalendarGuidance(brief));
  const grid = document.createElement("div");
  grid.className = "brief-grid";
  grid.append(
    daySection("On the clock", brief.timed_schedule || [], "No timed events are on the calendar today."),
    daySection("Moves for today", brief.plan || [], "No planned moves are due today."),
  );
  fragment.append(grid);
  if ((brief.day_context || []).length) fragment.append(daySection("Day context", brief.day_context, "No all-day context."));
  if ((brief.questions || []).length || (brief.messages || []).length) {
    const attention = document.createElement("div");
    attention.className = "brief-grid";
    attention.append(
      daySection("Needs a decision", brief.questions || [], "No clarifying decisions are waiting."),
      daySection("Messages to notice", brief.messages || [], "No message needs a response right now."),
    );
    fragment.append(attention);
  }
  return fragment;
}

function renderCarousel(brief) {
  const items = state.carouselItems;
  const section = document.createElement("section");
  section.className = "hero-card carousel-card";
  section.tabIndex = -1;
  if (!items.length) {
    section.append(text("p", "WHAT NOW", "eyebrow"), text("h2", "You are caught up for now.", "action-title"), text("p", "Pause, or choose something gently on purpose."));
    return section;
  }
  state.carouselIndex = Math.min(Math.max(state.carouselIndex, 0), items.length - 1);
  const item = items[state.carouselIndex];
  section.append(
    text("p", `${titleCase(item.kind)} · ${state.carouselIndex + 1} of ${items.length}`, "eyebrow"),
    text("h2", item.title || "Take the next small step", "action-title"),
  );
  const decisionExplanation = todayDecisionExplanation(item);
  if (decisionExplanation) section.append(decisionExplanation);
  else section.append(text("p", item.micro_step || "Open it and decide only the first move.", "microstep"));
  const meta = todayItemMeta(item);
  if (meta) section.append(text("p", meta, "meta carousel-meta"));
  const copies = possibleCopySources(item); if (copies) section.append(copies);
  const progress = document.createElement("div");
  progress.className = "progress-track carousel-progress";
  progress.setAttribute("role", "progressbar");
  progress.setAttribute("aria-label", "Carousel position");
  progress.setAttribute("aria-valuenow", String(state.carouselIndex + 1));
  progress.setAttribute("aria-valuemin", "1");
  progress.setAttribute("aria-valuemax", String(items.length));
  const fill = document.createElement("span");
  fill.style.width = `${((state.carouselIndex + 1) / items.length) * 100}%`;
  progress.append(fill);
  section.append(progress);
  const row = document.createElement("div");
  row.className = "button-row carousel-actions";
  const carouselActions = (item.actions || []).filter((value) => ["complete", "complete_external", "complete_step", "skip_step", "pause", "attended", "missed", "open_triage", "open_comms", "open_reminder", "open_calendar", "open_chat"].includes(value));
  if (item.kind === "commitment" && (item.actions || []).some((action) => ["defer", "block", "unblock", "cancel", "delegate"].includes(action))) carouselActions.push("manage_commitment");
  for (const action of carouselActions) {
    row.append(actionButton(item, action, section));
  }
  if (items.length > 1) {
    const later = text("button", "Later", "button button-quiet");
    later.type = "button";
    later.title = "Move to the next card without changing this item's due date or status.";
    later.addEventListener("click", () => {
      state.carouselIndex = (state.carouselIndex + 1) % items.length;
      renderToday(state.surface);
    });
    row.append(later);
  }
  if (item.evidence_event_id || item.last_event_id) row.append(actionButton(item, "evidence", section));
  section.append(row);
  if (items.length > 1) {
    const navigation = document.createElement("div");
    navigation.className = "carousel-navigation";
    const back = text("button", "← Previous", "link-button");
    back.type = "button";
    back.addEventListener("click", () => {
      state.carouselIndex = (state.carouselIndex - 1 + items.length) % items.length;
      renderToday(state.surface);
    });
    navigation.append(back, text("small", "Later only changes this view. Defer changes household truth and is always explicit."));
    section.append(navigation);
  }
  return section;
}

function renderToday(data) {
  const root = $("#surface");
  const brief = data.brief;
  root.replaceChildren();
  root.append(freshnessStrip(data));
  $("#mode-label").textContent = brief?.date_label || "Your daily briefing";
  root.append(todayViewControls(data));
  root.append(state.todayView === "carousel" ? renderCarousel(brief) : renderDayBrief(brief));
}

function commsMatches(thread) {
  const view = state.commsView;
  const viewMatch = view === "all"
    || (view === "focus" && ["focus", "needs_reply"].includes(thread.bucket))
    || (view === "needs_reply" && ["focus", "needs_reply"].includes(thread.bucket))
    || thread.bucket === view;
  const accountMatch = state.commsAccount === "all" || thread.provider_account_id === state.commsAccount;
  return viewMatch && accountMatch;
}

function commsControls(cockpit, data) {
  const controls = document.createElement("section");
  controls.className = "comms-controls card";
  const views = document.createElement("div");
  views.className = "comms-filter-row";
  views.setAttribute("role", "group");
  views.setAttribute("aria-label", "Communication focus view");
  const choices = [
    ["alerts", "Alerts", cockpit.counts.alerts || 0],
    ["focus", "Focus", cockpit.counts.focus],
    ["waiting", "Waiting", cockpit.counts.waiting],
    ["snoozed", "Snoozed", cockpit.counts.snoozed],
    ["quieted", "Quieted", cockpit.counts.quieted],
    ["all", "All", cockpit.counts.all],
  ];
  for (const [value, label, count] of choices) {
    const button = text("button", `${label} ${count}`, "comms-filter");
    button.type = "button";
    button.setAttribute("aria-pressed", String(state.commsView === value));
    button.addEventListener("click", () => { state.commsView = value; replaceQuery({ view: value }); renderComms(data); });
    views.append(button);
  }
  const accounts = document.createElement("div");
  accounts.className = "comms-filter-row account-filters";
  accounts.setAttribute("role", "group");
  accounts.setAttribute("aria-label", "Communication account");
  for (const account of [{ provider_account_id: "all", label: "All accounts", workspace: "" }, ...(cockpit.accounts || [])]) {
    const button = text("button", `${account.label}${account.workspace ? ` · ${titleCase(account.workspace)}` : ""}`, "account-filter");
    button.type = "button";
    button.setAttribute("aria-pressed", String(state.commsAccount === account.provider_account_id));
    button.addEventListener("click", () => { state.commsAccount = account.provider_account_id; replaceQuery({ account: state.commsAccount }); renderComms(data); });
    accounts.append(button);
  }
  controls.append(views, accounts);
  return controls;
}

function commsHero(cockpit) {
  const hero = document.createElement("section");
  hero.className = "hero-card comms-hero";
  hero.append(
    text("p", "COMMUNICATION COCKPIT", "eyebrow"),
    text("h2", cockpit.counts.focus === 1 ? "1 conversation deserves a look." : cockpit.counts.focus ? `${cockpit.counts.focus} conversations deserve a look.` : "No conversation is pulling on your working memory.", "action-title"),
    text("p", cockpit.attention_policy?.focus_requires_admitted_matter ? "Focus contains only conversations with an admitted obligation. Other messages remain searchable context, while alerts stay in their own lane." : "Focus uses typed action signals—not unread status—to protect your attention. Messages stay in their original member, workspace, provider, and privacy boundary.", "brief-summary"),
  );
  const stats = document.createElement("dl");
  stats.className = "brief-stats comms-stats";
  for (const [label, value] of [["Alerts", cockpit.counts.alerts || 0], ["Needs reply", cockpit.counts.needs_reply], ["Waiting", cockpit.counts.waiting], ["To Triage", cockpit.counts.triage], ["Quieted", cockpit.counts.quieted]]) {
    const stat = text("div", "", "stat"); stat.append(text("dt", label), text("dd", value)); stats.append(stat);
  }
  hero.append(stats);
  return hero;
}

async function runCommsCommand(thread, operation, extras = {}) {
  setBanner(operation === "reply" ? "Saving this exact reply for independent approval…" : "Saving this change for independent approval…");
  try {
    const result = await api(`/api/member/v1/comms/${encodeURIComponent(thread.last_interaction_id)}/commands`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
      body: JSON.stringify({
        operation,
        expected_last_event_id: thread.last_event_id,
        request_id: crypto.randomUUID(),
        ...extras,
      }),
    });
    setBanner(result.local ? "Updated. The focus view has been recalculated." : "Your decision is saved. OpenClaw must separately approve this exact provider action before anything changes outside AdministrateMe.");
    await reloadActiveSurface();
    return result;
  } catch (error) {
    setBanner(`${error.message} Nothing was changed.`);
    return null;
  }
}

function messageBubble(message) {
  const item = document.createElement("li");
  item.className = `comms-message ${message.direction === "outbound" ? "outbound" : "inbound"}`;
  const people = message.direction === "outbound" ? `To ${message.to.join(", ") || "recipient"}` : `From ${message.from.join(", ") || "sender"}`;
  const timestamp = message.occurred_at ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(message.occurred_at)) : "";
  item.append(text("span", `${people}${timestamp ? ` · ${timestamp}` : ""}`, "meta"));
  if (message.subject) item.append(text("strong", message.subject));
  item.append(text("p", message.body || message.preview));
  if (message.attachments?.length) {
    const attachments = document.createElement("ul"); attachments.className = "attachment-list";
    for (const attachment of message.attachments) {
      const size = attachment.size ? ` · ${Math.max(1, Math.round(attachment.size / 1024))} KB` : "";
      attachments.append(text("li", `${attachment.filename || "Attachment"}${size}`));
    }
    item.append(attachments);
  }
  if (message.evidence_event_ids?.[0]) {
    const evidence = text("button", "Why this is here", "link-button"); evidence.type = "button";
    evidence.addEventListener("click", () => showEvidence(message.evidence_event_ids[0])); item.append(evidence);
    const triage = text("button", "Send to Triage", "link-button"); triage.type = "button";
    triage.addEventListener("click", () => openAddForReview({
      exactText: [message.subject, message.body || message.preview].filter(Boolean).join("\n\n"),
      sourceEventId: message.evidence_event_ids[0], sourceSurface: "comms",
      dialogTitle: "Extract work from this message",
    }));
    item.append(triage);
  }
  return item;
}

function commsReadingPane(thread) {
  const pane = document.createElement("section");
  pane.className = "card comms-reading";
  if (!thread) {
    pane.append(text("h2", "Choose one conversation"), text("p", "The combined view carries the remembering. You only need to look at one thread at a time.", "empty"));
    return pane;
  }
  const heading = document.createElement("div"); heading.className = "comms-reading-heading";
  const title = document.createElement("div");
  title.append(text("p", `${thread.account_label} · ${titleCase(thread.channel)}`, "eyebrow"), text("h2", thread.title));
  const badge = text("span", titleCase(thread.bucket), `pill ${thread.bucket === "focus" ? "pill-blush" : "pill-lavender"}`);
  heading.append(title, badge); pane.append(heading);
  const why = document.createElement("p"); why.className = "focus-reason"; why.append(text("strong", "Why it is here: "), document.createTextNode(thread.why.join(" · "))); pane.append(why);
  if (thread.command_state) {
    const labels = {
      pending: "Pending exact member approval.",
      awaiting_openclaw_approval: "Member approval recorded; awaiting independent OpenClaw approval.",
      dispatched: "Dispatched to the protected member-bridge queue; this is not a delivery claim.",
      suppressed: "Observation mode recorded the approved change without contacting the provider.",
      result_accepted: "Transport accepted the reply; awaiting native outgoing observation.",
      result_sent: "Transport reported sent; awaiting native outgoing observation.",
      observed: "The outgoing message was observed through the read path; reconciliation is pending.",
      reconciled: "The exact outgoing observation reconciled to this approved reply.",
      ambiguous: "The transport result is ambiguous. AdministrateMe will not resend automatically; review the conversation.",
      canceled: "The pending reply was canceled before dispatch.",
      completed: "Provider reported the requested change complete.",
      failed: "Provider change failed; the original message state remains authoritative.",
    };
    pane.append(text("p", labels[thread.command_state] || `Command state: ${titleCase(thread.command_state)}.`, `state-label state-${String(thread.command_state).replaceAll("_", "-")}`));
  }
  if (thread.triage_count) {
    const triage = document.createElement("section"); triage.className = "comms-triage-links";
    triage.append(text("strong", `${thread.triage_count} extracted candidate${thread.triage_count === 1 ? "" : "s"} across ${thread.triage_matter_count || thread.triage_count} matter${(thread.triage_matter_count || thread.triage_count) === 1 ? "" : "s"}`));
    const links = document.createElement("ul");
    for (const candidate of thread.triage_candidates || []) {
      const item = document.createElement("li"); const link = text("a", candidate.title || "Review extracted candidate", "triage-link");
      link.href = candidate.surface_link?.href || "/member/triage"; item.append(link); links.append(item);
    }
    triage.append(links); pane.append(triage);
  }
  const messages = document.createElement("ol"); messages.className = "comms-transcript";
  for (const message of thread.messages) messages.append(messageBubble(message)); pane.append(messages);
  const actions = document.createElement("div"); actions.className = "button-row comms-actions";
  const latest = thread.messages[thread.messages.length - 1];
  if (thread.capabilities.mark_read) {
    const operation = latest.unread ? "mark_read" : "mark_unread";
    const button = text("button", latest.unread ? "Mark read" : "Mark unread", "button button-quiet"); button.type = "button";
    button.addEventListener("click", () => runCommsCommand(thread, operation)); actions.append(button);
  }
  if (thread.capabilities.archive) {
    const operation = thread.archived ? "restore" : "archive";
    const button = text("button", thread.archived ? "Restore" : "Archive", "button button-quiet"); button.type = "button";
    button.addEventListener("click", () => runCommsCommand(thread, operation)); actions.append(button);
  }
  const snooze = text("button", thread.bucket === "snoozed" ? "Unsnooze" : "Snooze 2h", "button button-quiet"); snooze.type = "button";
  snooze.addEventListener("click", () => runCommsCommand(thread, thread.bucket === "snoozed" ? "unsnooze" : "snooze", thread.bucket === "snoozed" ? {} : { snoozed_until: new Date(Date.now() + 2 * 60 * 60 * 1000).toISOString() })); actions.append(snooze);
  const quiet = text("button", thread.quieted ? "Restore to focus" : "Quiet this", "button button-quiet"); quiet.type = "button";
  quiet.addEventListener("click", () => runCommsCommand(thread, thread.quieted ? "unquiet" : "quiet")); actions.append(quiet);
  pane.append(actions);
  const compose = document.createElement("form"); compose.className = "comms-compose";
  const replyLabel = text("label", "Reply in this thread"); replyLabel.htmlFor = "comms-reply";
  const input = document.createElement("textarea"); input.id = "comms-reply"; input.rows = 4; input.maxLength = 20000; input.placeholder = "Write the reply you intend to send…"; compose.append(replyLabel, input);
  const destination = document.createElement("dl"); destination.className = "settings-facts";
  const participantLabels = [...new Set([...(latest.from || []), ...(latest.to || [])])];
  for (const [label, value] of [["Your Messages account", thread.account_label], ["Source", `${thread.provider} · ${thread.transport || thread.channel}`], ["Native chat", thread.native_chat_id || "Unavailable"], ["Participants", participantLabels.join(", ") || thread.counterparties.join(", ") || "Unavailable"]]) destination.append(text("dt", label), text("dd", value));
  compose.append(destination);
  if (thread.is_group) compose.append(text("p", "Group conversation: this reply goes to the exact participant set shown above. Any stale or ambiguous participant state stops the command before provider contact.", "review-state"));
  compose.append(text("p", "Sending is an external provider effect. The account, existing chat, and participant set are fixed and cannot be edited here. Plain text only; no attachment, signature, or quoted history is added.", "state-note"));
  const preview = document.createElement("section"); preview.className = "state-card"; preview.hidden = true;
  const previewBody = document.createElement("pre"); previewBody.className = "comms-reply-preview";
  preview.append(text("strong", "Final exact preview"), text("p", `From ${thread.account_label} to the exact existing ${thread.is_group ? "group" : "conversation"}.`), previewBody, text("p", "Approving records this exact destination and Unicode body for one use. OpenClaw must still approve independently.", "review-state"));
  compose.append(preview);
  let approvedPreview = null;
  input.addEventListener("input", () => {
    input.removeAttribute("aria-invalid");
    input.removeAttribute("aria-describedby");
    approvedPreview = null;
    preview.hidden = true;
    const submit = compose.querySelector('button[type="submit"]');
    if (submit) submit.textContent = "Preview exact reply";
  });
  if (thread.capabilities.reply) {
    compose.append(text("p", "Replies are enabled ask-every-time. Previewing does nothing; the final explicit action records one pending command, never a delivery claim.", "state-note"));
    const send = text("button", "Preview exact reply", "button button-primary"); send.type = "submit";
    compose.append(send);
    compose.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!input.value.trim()) {
        input.setAttribute("aria-invalid", "true");
        input.setAttribute("aria-describedby", "state-banner");
        setBanner("Write the reply you want approved. Nothing was sent.");
        input.focus();
        return;
      }
      if (approvedPreview === null) {
        approvedPreview = input.value;
        previewBody.textContent = approvedPreview;
        preview.hidden = false;
        send.textContent = "Approve this exact reply";
        setBanner("Review the fixed account, chat, participants, and exact body. Nothing has been sent or queued.");
        return;
      }
      if (input.value !== approvedPreview) {
        approvedPreview = null;
        preview.hidden = true;
        send.textContent = "Preview exact reply";
        setBanner("The body changed. Review a new exact preview; nothing was queued.");
        return;
      }
      send.disabled = true;
      input.disabled = true;
      const result = await runCommsCommand(thread, "reply", { body: approvedPreview });
      if (!result) { send.disabled = false; input.disabled = false; }
    });
  } else {
    input.disabled = true;
    compose.append(text("p", thread.reply_eligibility?.reason || "This exact conversation is not eligible for replies. Enable the member-owned capability in Settings after read observation settles.", "state-note"));
  }
  if (thread.command_state === "awaiting_openclaw_approval" && thread.last_command_id) {
    const cancel = text("button", "Cancel pending reply", "button button-quiet"); cancel.type = "button";
    cancel.addEventListener("click", async () => { cancel.disabled = true; try { await api(`/api/member/v1/comms/replies/${encodeURIComponent(thread.last_command_id)}/cancel`, { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify({ request_id: crypto.randomUUID() }) }); setBanner("Pending reply canceled. No provider call occurred."); await reloadActiveSurface(); } catch (error) { setBanner(`${error.message} The pending reply was not canceled.`); cancel.disabled = false; } }); compose.append(cancel);
  }
  pane.append(compose);
  return pane;
}

function commsThreadList(threads) {
  const list = document.createElement("section"); list.className = "card comms-list";
  const heading = document.createElement("div"); heading.className = "section-heading"; heading.append(text("h2", "Conversations"), text("span", threads.length, "count-badge")); list.append(heading);
  if (!threads.length) { list.append(text("p", "Nothing in this lens. Try All, another account, or enjoy the quiet.", "empty")); return list; }
  const items = document.createElement("ol"); items.className = "comms-thread-items";
  for (const thread of threads) {
    const li = document.createElement("li");
    const button = document.createElement("button"); button.type = "button"; button.className = "comms-thread";
    if (thread.thread_key === state.commsThread) button.setAttribute("aria-current", "true");
    const first = document.createElement("span"); first.className = "comms-thread-title"; first.append(text("strong", thread.title), text("span", thread.account_label, "meta"));
    const count = thread.unread_count ? text("span", thread.unread_count, "unread-badge") : text("span", titleCase(thread.bucket), "meta");
    button.append(first, count, text("span", thread.preview, "thread-preview"), text("span", thread.why[0], "focus-explanation"));
    button.addEventListener("click", () => { state.commsThread = thread.thread_key; replaceQuery({ thread: thread.thread_key }); renderComms(state.surface); });
    li.append(button); items.append(li);
  }
  list.append(items); return list;
}

function renderComms(data) {
  const root = $("#surface"); root.replaceChildren();
  const cockpit = data.cockpit;
  $("#mode-label").textContent = "Private · deterministic focus";
  root.append(freshnessStrip(data), commsHero(cockpit), commsControls(cockpit, data));
  const visible = cockpit.threads.filter(commsMatches);
  if (!state.commsThread || !visible.some((thread) => thread.thread_key === state.commsThread)) state.commsThread = visible[0]?.thread_key || null;
  const selected = visible.find((thread) => thread.thread_key === state.commsThread);
  const workspace = document.createElement("div"); workspace.className = "comms-workspace";
  workspace.append(commsThreadList(visible), commsReadingPane(selected)); root.append(workspace);
  if (!(cockpit.accounts || []).length) {
    const setup = document.createElement("section"); setup.className = "state-card";
    setup.append(text("strong", "No member-bound communication account is connected."), text("p", "Connect Gmail, Outlook, a trusted Messages bridge, Twilio SMS, or WhatsApp in Settings. Unbound accounts are deliberately excluded to prevent cross-member leakage.")); root.append(setup);
  }
}

function reminderOperation(item, stem) {
  const suffix = item.provider === "google_tasks" ? "task" : "reminder";
  return `${stem}_${suffix}`;
}

async function runReminderCommand(item, operation, payload = {}) {
  setBanner("Saving one explicit provider change for independent approval…");
  try {
    const result = await api("/api/member/v1/reminders/commands", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
      body: JSON.stringify({
        provider: item.provider,
        provider_account_id: item.provider_account_id,
        operation,
        list_external_id: item.list_external_id || null,
        item_external_id: item.item_external_id || null,
        expected_last_event_id: item.last_event_id || null,
        expected_provider_revision: item.provider_revision || null,
        payload,
        request_id: crypto.randomUUID(),
      }),
    });
    setBanner("Your decision is saved. OpenClaw must approve this exact provider action before the provider can change.");
    await reloadActiveSurface();
    if (item.provider === "apple_reminders" && result.state === "awaiting_openclaw_approval") appendAppleExecutionHandoff($("#state-banner"), item.provider, result.domain_approval_event_id);
  } catch (error) {
    setBanner(`${error.message} Nothing was changed or implied.`);
  }
}

async function runTaskLifecycle(item, action, extras = {}) {
  setBanner(`Recording task ${action}…`);
  try {
    await api("/api/member/v1/lifecycle", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
      body: JSON.stringify({
        item_kind: "task", item_id: item.task_id, action,
        expected_last_event_id: item.last_event_id, request_id: crypto.randomUUID(),
        until: extras.until || null, reason: extras.reason || null, note: extras.note || null,
      }),
    });
    const confirmations = {
      complete: "Done. The canonical task completion is recorded.",
      cancel: "Abandoned. The task remains in history with your reason and evidence.",
      reopen: "Reopened. The task is active again without erasing its prior history.",
      snooze: "Snoozed. It will become actionable again at the time you chose.",
      defer: "Deferred. The reason and return time are recorded separately from a snooze.",
    };
    setBanner(confirmations[action] || `Task ${action} recorded.`);
    await reloadActiveSurface();
  } catch (error) {
    setBanner(`${error.message} The task was not changed.`);
  }
}

async function runCommitmentLifecycle(item, action, extras = {}) {
  setBanner(`Recording ${action} for this promise…`);
  try {
    await api("/api/member/v1/lifecycle", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
      body: JSON.stringify({
        item_kind: "commitment", item_id: item.id || item.commitment_id, action,
        expected_last_event_id: item.last_event_id, request_id: crypto.randomUUID(),
        until: extras.until || null, reason: extras.reason || null, note: extras.note || null,
        delegate_to_party_id: extras.delegate_to_party_id || null,
      }),
    });
    const confirmations = {
      complete: "Done. The promise is recorded as completed.",
      unblock: "Unblocked. The promise is eligible for Today again.",
      defer: "Deferred. Its return time remains distinct from its due date.",
      block: "Waiting condition recorded. The promise remains visible in history.",
      cancel: "Released. The promise is closed with your reason and evidence.",
      delegate: "Hand-off recorded. The original promise remains yours in history; no obligation was silently assigned to the other person.",
    };
    setBanner(confirmations[action] || `Promise ${action} recorded.`);
    await reloadActiveSurface();
  } catch (error) {
    setBanner(`${error.message} The promise was not changed.`);
  }
}

function commitmentReasonDialog(item, action) {
  const form = document.createElement("form"); form.className = "dialog-form";
  const reason = document.createElement("textarea"); reason.rows = 3; reason.maxLength = 500; reason.required = action === "block";
  const copy = {
    block: ["What are you waiting on?", "Record waiting condition"],
    cancel: ["Why is this promise being released? (optional)", "Release promise"],
  }[action];
  const buttons = document.createElement("div"); buttons.className = "button-row";
  const keep = text("button", "Keep it", "button button-quiet"); keep.type = "button"; keep.addEventListener("click", closeActionDialog);
  const submit = text("button", copy[1], "button button-primary"); submit.type = "submit"; buttons.append(keep, submit);
  form.append(field(copy[0], reason), buttons);
  form.addEventListener("submit", async (event) => { event.preventDefault(); closeActionDialog(); await runCommitmentLifecycle(item, action, { reason: reason.value.trim() || null }); });
  openActionDialog(action === "block" ? "Put this promise in Waiting" : "Release this promise?", form);
}

function commitmentDelegateDialog(item) {
  const options = state.surface?.lifecycle_options?.commitment_delegates || [];
  const form = document.createElement("form"); form.className = "dialog-form";
  form.append(text("p", "This records that you handed off your original promise. It does not create or assign a new obligation to the other person without their consent.", "state-note"));
  const delegate = selectControl(options.map((person) => ({ value: person.party_id, label: `${person.label} · ${titleCase(person.kind)}` })), "", "Choose a confirmed person…"); delegate.required = true;
  const buttons = document.createElement("div"); buttons.className = "button-row";
  const keep = text("button", "Keep it", "button button-quiet"); keep.type = "button"; keep.addEventListener("click", closeActionDialog);
  const submit = text("button", "Record hand-off", "button button-primary"); submit.type = "submit"; submit.disabled = !options.length; buttons.append(keep, submit);
  form.append(field("Handed off to", delegate), buttons);
  if (!options.length) form.prepend(text("p", "No other confirmed Party is visible. Confirm the person in People before recording a hand-off.", "state-note"));
  form.addEventListener("submit", async (event) => { event.preventDefault(); closeActionDialog(); await runCommitmentLifecycle(item, "delegate", { delegate_to_party_id: delegate.value }); });
  openActionDialog("Hand off this promise?", form);
}

function commitmentManageDialog(item) {
  const content = document.createElement("div"); content.className = "dialog-form";
  content.append(text("p", "Choose the exact state change. AdministrateMe will preserve the original promise and its evidence.", "state-note"));
  const actions = document.createElement("div"); actions.className = "button-row";
  const available = new Set(item.actions || []);
  if (available.has("unblock")) {
    const button = text("button", "No longer waiting", "button button-primary"); button.type = "button"; button.addEventListener("click", async () => { closeActionDialog(); await runCommitmentLifecycle(item, "unblock"); }); actions.append(button);
  }
  if (available.has("defer")) {
    const button = text("button", "Return tomorrow", "button button-quiet"); button.type = "button"; button.addEventListener("click", async () => { closeActionDialog(); await runCommitmentLifecycle(item, "defer", { until: new Date(Date.now() + 86400000).toISOString() }); }); actions.append(button);
  }
  if (available.has("block")) {
    const button = text("button", "Waiting on…", "button button-quiet"); button.type = "button"; button.addEventListener("click", () => { closeActionDialog(); commitmentReasonDialog(item, "block"); }); actions.append(button);
  }
  if (available.has("delegate")) {
    const button = text("button", "Hand off…", "button button-quiet"); button.type = "button"; button.addEventListener("click", () => { closeActionDialog(); commitmentDelegateDialog(item); }); actions.append(button);
  }
  if (available.has("cancel")) {
    const button = text("button", "Release promise…", "button button-quiet"); button.type = "button"; button.addEventListener("click", () => { closeActionDialog(); commitmentReasonDialog(item, "cancel"); }); actions.append(button);
  }
  const close = text("button", "Close", "button button-quiet"); close.type = "button"; close.addEventListener("click", closeActionDialog); actions.append(close);
  content.append(actions);
  openActionDialog("Manage this promise", content);
}

async function runRhythmLifecycle(item, action, extras = {}) {
  setBanner(`Recording ${action} for this ${item.object_kind || item.kind}…`);
  try {
    await api("/api/member/v1/lifecycle", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
      body: JSON.stringify({
        item_kind: item.object_kind || item.kind, item_id: item.id, action,
        expected_last_event_id: item.last_event_id, request_id: crypto.randomUUID(),
        reason: extras.reason || null,
      }),
    });
    setBanner(action === "end" ? "Ended and retained in history." : action === "pause" ? "Paused. It will not enter Today until resumed." : "Resumed from the existing definition.");
    await reloadActiveSurface();
  } catch (error) {
    setBanner(`${error.message} The recurring structure was not changed.`);
  }
}

function endRhythmDialog(item) {
  const form = document.createElement("form"); form.className = "dialog-form";
  form.append(text("p", "Ending stops future occurrences but retains the definition and completed history.", "state-note"));
  const reason = document.createElement("textarea"); reason.rows = 3; reason.maxLength = 500;
  const buttons = document.createElement("div"); buttons.className = "button-row";
  const keep = text("button", "Keep it", "button button-quiet"); keep.type = "button"; keep.addEventListener("click", closeActionDialog);
  const end = text("button", "End recurring item", "button button-primary"); end.type = "submit"; buttons.append(keep, end);
  form.append(field("Reason (optional)", reason), buttons);
  form.addEventListener("submit", async (event) => { event.preventDefault(); closeActionDialog(); await runRhythmLifecycle(item, "end", { reason: reason.value.trim() || null }); });
  openActionDialog("End this recurring structure?", form);
}

function rhythmRuleEditor(initial = "FREQ=DAILY") {
  const presets = [
    { value: "FREQ=DAILY", label: "Every day" },
    { value: "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", label: "Weekdays" },
    { value: "FREQ=WEEKLY", label: "Every week" },
    { value: "FREQ=MONTHLY", label: "Every month" },
    { value: "FREQ=YEARLY", label: "Every year" },
  ];
  const exact = presets.some((item) => item.value === initial);
  const select = selectControl([...presets, { value: "custom", label: "Custom rule" }], exact ? initial : "custom", "");
  const custom = document.createElement("input"); custom.value = exact ? "" : initial; custom.maxLength = 1000; custom.placeholder = "FREQ=MONTHLY;INTERVAL=3"; custom.hidden = exact;
  select.addEventListener("change", () => { custom.hidden = select.value !== "custom"; });
  const node = document.createElement("div"); node.className = "date-time-control"; node.append(select, custom);
  return { node, value: () => select.value === "custom" ? custom.value.trim() : select.value };
}

function recurrenceDefinitionDialog(item = null) {
  const form = document.createElement("form"); form.className = "dialog-form";
  form.append(text("p", "A recurrence is a repeating fact or trackable rhythm. It does not silently create a task until a reviewed rule says it should.", "state-note"));
  const name = document.createElement("input"); name.required = true; name.maxLength = 160; name.value = item?.kind || "";
  const rule = rhythmRuleEditor(item?.rrule || "FREQ=WEEKLY");
  const parts = triageDateTimeParts(item?.next_occurrence || new Date().toISOString());
  const due = document.createElement("div"); due.className = "date-time-control";
  const dueDate = document.createElement("input"); dueDate.type = "date"; dueDate.required = true; dueDate.value = parts.date;
  const dueTime = document.createElement("input"); dueTime.type = "time"; dueTime.required = true; dueTime.value = parts.time || "09:00"; due.append(dueDate, dueTime);
  const scope = selectControl([{ value: "member", label: "Only me" }, { value: "household", label: "Household" }], item?.linked_kind === "household" ? "household" : "member", ""); scope.disabled = Boolean(item);
  const lead = document.createElement("input"); lead.type = "number"; lead.min = "0"; lead.max = "365"; lead.value = String(item?.lead_time_days || 0);
  const trackable = document.createElement("input"); trackable.type = "checkbox"; trackable.checked = Boolean(item?.trackable);
  const trackWrap = document.createElement("label"); trackWrap.className = "check-control"; trackWrap.append(trackable, document.createTextNode(" Track each occurrence"));
  const notes = document.createElement("textarea"); notes.rows = 3; notes.maxLength = 5000; notes.value = item?.notes || "";
  const buttons = document.createElement("div"); buttons.className = "button-row";
  const cancel = text("button", "Cancel", "button button-quiet"); cancel.type = "button"; cancel.addEventListener("click", closeActionDialog);
  const submit = text("button", item ? "Save recurrence" : "Add recurrence", "button button-primary"); submit.type = "submit"; buttons.append(cancel, submit);
  form.append(field("Name", name), field("Repeats", rule.node), field("Next occurrence", due), field("Visible to", scope), field("Lead time in days", lead), trackWrap, field("Notes (optional)", notes), buttons);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const rrule = rule.value();
    if (!rrule) { setBanner("Choose a repeat pattern or enter a valid custom rule."); return; }
    const recurrenceId = item?.id || `recurrence_${crypto.randomUUID().replaceAll("-", "")}`;
    closeActionDialog(); setBanner(item ? "Saving recurrence…" : "Adding recurrence…");
    try {
      await api(`/api/member/v1/recurrences/${encodeURIComponent(recurrenceId)}`, {
        method: "PUT", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
        body: JSON.stringify({ request_id: crypto.randomUUID(), expected_last_event_id: item?.last_event_id || null, scope: scope.value, kind: name.value.trim(), rrule, next_occurrence: new Date(`${dueDate.value}T${dueTime.value}`).toISOString(), lead_time_days: Number(lead.value || 0), trackable: trackable.checked, notes: notes.value.trim() || null }),
      });
      setBanner(item ? "Recurrence updated with its prior history intact." : "Recurrence added."); await reloadActiveSurface();
    } catch (error) { setBanner(`${error.message} The recurrence was not saved.`); }
  });
  openActionDialog(item ? "Edit recurrence" : "Add a recurrence", form);
}

function routineDefinitionDialog(item = null) {
  const form = document.createElement("form"); form.className = "dialog-form";
  form.append(text("p", "A routine is an ordered sequence inside a repeat window. Put one step per line; add a tiny-start cue after a vertical bar.", "state-note"));
  const title = document.createElement("input"); title.required = true; title.maxLength = 500; title.value = item?.title || "";
  const rule = rhythmRuleEditor(item?.rrule || "FREQ=DAILY");
  const starts = document.createElement("input"); starts.type = "date"; starts.required = true; starts.value = item?.starts_on || new Date().toISOString().slice(0, 10);
  const window = document.createElement("div"); window.className = "date-time-control";
  const startTime = document.createElement("input"); startTime.type = "time"; startTime.required = true; startTime.value = item?.window_start_local || "08:00";
  const endTime = document.createElement("input"); endTime.type = "time"; endTime.required = true; endTime.value = item?.window_end_local || "09:00"; window.append(startTime, endTime);
  const steps = document.createElement("textarea"); steps.rows = 7; steps.required = true; steps.maxLength = 10000; steps.placeholder = "Put medication by water | Start by filling the glass\nReview Today | Open the first card"; steps.value = (item?.steps || []).map((step) => `${step.title}${step.micro_step ? ` | ${step.micro_step}` : ""}`).join("\n");
  const buttons = document.createElement("div"); buttons.className = "button-row";
  const cancel = text("button", "Cancel", "button button-quiet"); cancel.type = "button"; cancel.addEventListener("click", closeActionDialog);
  const submit = text("button", item ? "Save routine" : "Add routine", "button button-primary"); submit.type = "submit"; buttons.append(cancel, submit);
  form.append(field("Routine name", title), field("Repeats", rule.node), field("Starts", starts), field("Daily window", window), field("Ordered steps", steps), buttons);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const lines = steps.value.split("\n").map((line) => line.trim()).filter(Boolean);
    const rrule = rule.value();
    if (!lines.length || !rrule) { setBanner("Add at least one routine step and a repeat pattern."); return; }
    const routineId = item?.id || `routine_${crypto.randomUUID().replaceAll("-", "")}`;
    const priorSteps = item?.steps || [];
    const definitions = lines.map((line, position) => { const parts = line.split("|"); return { step_id: priorSteps[position]?.step_id || `step_${position + 1}`, title: parts.shift().trim(), position, micro_step: parts.join("|").trim() || null, linked_task_id: priorSteps[position]?.linked_task_id || null }; });
    closeActionDialog(); setBanner(item ? "Saving routine…" : "Adding routine…");
    try {
      await api(`/api/member/v1/routines/${encodeURIComponent(routineId)}`, {
        method: "PUT", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
        body: JSON.stringify({ request_id: crypto.randomUUID(), expected_last_event_id: item?.last_event_id || null, title: title.value.trim(), timezone: item?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone, starts_on: starts.value, rrule, window_start_local: startTime.value, window_end_local: endTime.value, steps: definitions }),
      });
      setBanner(item ? "Routine updated with its outcome history intact." : "Routine added."); await reloadActiveSurface();
    } catch (error) { setBanner(`${error.message} The routine was not saved.`); }
  });
  openActionDialog(item ? "Edit routine" : "Add a routine", form);
}

function rhythmDefinitionsCard(items) {
  const section = document.createElement("section"); section.className = "card"; section.append(text("h2", "Recurring structures"));
  const createActions = document.createElement("div"); createActions.className = "button-row";
  const addRecurrence = text("button", "Add recurrence", "button button-quiet"); addRecurrence.type = "button"; addRecurrence.addEventListener("click", () => recurrenceDefinitionDialog());
  const addRoutine = text("button", "Add routine", "button button-primary"); addRoutine.type = "button"; addRoutine.addEventListener("click", () => routineDefinitionDialog()); createActions.append(addRecurrence, addRoutine); section.append(createActions);
  if (!items?.length) { section.append(text("p", "No recurring structures are recorded.", "empty")); return section; }
  const list = document.createElement("ul"); list.className = "item-list";
  for (const item of items) {
    const li = document.createElement("li"); li.className = "item";
    li.append(text("strong", item.title), text("span", `${titleCase(item.object_kind)} · ${titleCase(item.status || "active")}`, `meta state-label state-${item.status || "active"}`));
    if (item.next_occurrence) li.append(text("small", `Next: ${reminderDate(item.next_occurrence)}`));
    const actions = document.createElement("div"); actions.className = "button-row";
    if (item.status !== "ended") {
      const edit = text("button", "Edit", "button button-quiet"); edit.type = "button"; edit.addEventListener("click", () => item.object_kind === "routine" ? routineDefinitionDialog(item) : recurrenceDefinitionDialog(item)); actions.append(edit);
    }
    if ((item.status || "active") === "active") {
      const pause = text("button", "Pause", "button button-quiet"); pause.type = "button"; pause.addEventListener("click", () => runRhythmLifecycle(item, "pause")); actions.append(pause);
    } else if (item.status === "paused") {
      const resume = text("button", "Resume", "button button-primary"); resume.type = "button"; resume.addEventListener("click", () => runRhythmLifecycle(item, "resume")); actions.append(resume);
    }
    if (item.status !== "ended") {
      const end = text("button", "End…", "button button-quiet"); end.type = "button"; end.addEventListener("click", () => endRhythmDialog(item)); actions.append(end);
    }
    if (item.last_event_id) { const evidence = text("button", "View evidence", "link-button"); evidence.type = "button"; evidence.addEventListener("click", () => showEvidence(item.last_event_id)); actions.append(evidence); }
    li.append(actions); list.append(li);
  }
  section.append(list); return section;
}

function taskPropertiesForm(initial, { create = false } = {}) {
  const form = document.createElement("form"); form.className = "dialog-form task-properties-form";
  const title = document.createElement("input"); title.name = "title"; title.required = true; title.maxLength = 1000; title.value = initial.title || "";
  const dueParts = triageDateTimeParts(initial.due_date);
  const due = document.createElement("div"); due.className = "date-time-control";
  const dueDate = document.createElement("input"); dueDate.type = "date"; dueDate.name = "due_date"; dueDate.value = dueParts.date; dueDate.setAttribute("aria-label", "Due date");
  const dueTime = document.createElement("input"); dueTime.type = "time"; dueTime.name = "due_time"; dueTime.value = dueParts.time; dueTime.setAttribute("aria-label", "Due time (optional)"); due.append(dueDate, dueTime);
  const availableParts = triageDateTimeParts(initial.available_from);
  const available = document.createElement("div"); available.className = "date-time-control";
  const availableDate = document.createElement("input"); availableDate.type = "date"; availableDate.name = "available_date"; availableDate.value = availableParts.date; availableDate.setAttribute("aria-label", "Available from date");
  const availableTime = document.createElement("input"); availableTime.type = "time"; availableTime.name = "available_time"; availableTime.value = availableParts.time; availableTime.setAttribute("aria-label", "Available from time (optional)"); available.append(availableDate, availableTime);
  const energy = selectControl(["low", "medium", "high"].map((value) => ({ value, label: titleCase(value) })), initial.energy || "", "Not set"); energy.name = "energy";
  const effort = selectControl(["tiny", "small", "medium", "large"].map((value) => ({ value, label: titleCase(value) })), initial.effort || "", "Not set"); effort.name = "effort";
  const itemType = selectControl(["task", "purchase", "appointment", "research", "decision", "chore", "maintenance"].map((value) => ({ value, label: titleCase(value) })), initial.item_type || "task", ""); itemType.name = "item_type";
  const domain = document.createElement("input"); domain.name = "domain"; domain.maxLength = 120; domain.value = initial.domain || "tasks";
  const micro = document.createElement("textarea"); micro.name = "micro_script"; micro.rows = 2; micro.maxLength = 2000; micro.value = initial.micro_script || "";
  const notes = document.createElement("textarea"); notes.name = "notes"; notes.rows = 4; notes.maxLength = 20000; notes.value = initial.notes || "";
  form.append(
    field("Task", title), field("Due", due), field("Available from", available), field("Energy needed", energy),
    field("Effort", effort), field("Work type", itemType), field("Area", domain),
    field("First tiny step", micro), field("Notes", notes),
  );
  const submit = text("button", create ? "Add task" : "Save task", "button button-primary"); submit.type = "submit"; form.append(submit);
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); submit.disabled = true;
    const values = new FormData(form); const dateValue = String(values.get("due_date") || ""); const timeValue = String(values.get("due_time") || "");
    const availableDateValue = String(values.get("available_date") || ""); const availableTimeValue = String(values.get("available_time") || "");
    const payload = {
      request_id: crypto.randomUUID(), title: String(values.get("title") || "").trim(),
      due_date: dateValue ? (timeValue ? `${dateValue}T${timeValue}` : dateValue) : null,
      available_from: availableDateValue ? (availableTimeValue ? `${availableDateValue}T${availableTimeValue}` : availableDateValue) : null,
      energy: String(values.get("energy") || "") || null, effort: String(values.get("effort") || "") || null,
      item_type: String(values.get("item_type") || "task"), domain: String(values.get("domain") || "tasks").trim(),
      micro_script: String(values.get("micro_script") || "").trim() || null,
      notes: String(values.get("notes") || "").trim() || null,
    };
    if (!create) payload.expected_last_event_id = initial.last_event_id;
    try {
      const result = await api(create ? "/api/member/v1/tasks" : `/api/member/v1/tasks/${encodeURIComponent(initial.task_id)}`, {
        method: create ? "POST" : "PATCH", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify(payload),
      });
      closeActionDialog(); state.reminderItem = `task:${result.task.task_id}`; setBanner(create ? "Task added to Reminders." : "Task properties saved."); await reloadActiveSurface();
    } catch (error) { setBanner(`${error.message} The task was not saved.`); submit.disabled = false; }
  });
  return form;
}

function deferTaskDialog(item, action = "defer") {
  const form = document.createElement("form"); form.className = "dialog-form";
  const until = document.createElement("input"); until.type = "datetime-local"; until.required = true;
  const reason = document.createElement("input"); reason.maxLength = 500; reason.placeholder = "Why is this being deferred?";
  const isSnooze = action === "snooze";
  const submit = text("button", isSnooze ? "Snooze task" : "Defer task", "button button-primary"); submit.type = "submit";
  form.append(field(isSnooze ? "Bring back at" : "Defer until", until));
  if (!isSnooze) form.append(field("Reason (optional)", reason));
  form.append(submit);
  form.addEventListener("submit", async (event) => { event.preventDefault(); closeActionDialog(); await runTaskLifecycle(item, action, { until: until.value, reason: reason.value.trim() || null }); });
  openActionDialog(isSnooze ? "Snooze this task" : "Defer this task", form);
}

function blockTaskDialog(item) {
  const form = document.createElement("form"); form.className = "dialog-form";
  const reason = document.createElement("input"); reason.required = true; reason.maxLength = 500; reason.placeholder = "What are you waiting for?";
  const submit = text("button", "Mark waiting", "button button-primary"); submit.type = "submit"; form.append(field("Waiting on", reason), submit);
  form.addEventListener("submit", async (event) => { event.preventDefault(); closeActionDialog(); await runTaskLifecycle(item, "block", { reason: reason.value.trim() }); });
  openActionDialog("Mark task as waiting", form);
}

function abandonTaskDialog(item) {
  const form = document.createElement("form"); form.className = "dialog-form";
  form.append(text("p", "Abandoning removes this from active work but keeps an auditable history. You can reopen it later.", "state-note"));
  const reason = document.createElement("textarea"); reason.rows = 3; reason.maxLength = 500; reason.placeholder = "Optional: why is this no longer worth doing?";
  const buttons = document.createElement("div"); buttons.className = "button-row";
  const keep = text("button", "Keep task", "button button-quiet"); keep.type = "button"; keep.addEventListener("click", closeActionDialog);
  const abandon = text("button", "Abandon task", "button button-primary"); abandon.type = "submit";
  buttons.append(keep, abandon); form.append(field("Reason", reason), buttons);
  form.addEventListener("submit", async (event) => { event.preventDefault(); closeActionDialog(); await runTaskLifecycle(item, "cancel", { reason: reason.value.trim() || null }); });
  openActionDialog("Abandon this task?", form);
}

function openActionDialog(title, content) {
  const dialog = $("#action-dialog");
  if (document.activeElement instanceof HTMLElement) dialogReturnFocus.set(dialog, document.activeElement);
  $("#action-title").textContent = title;
  $("#action-content").replaceChildren(content);
  dialog.showModal();
  content.querySelector("input, select, textarea, button")?.focus();
}

function closeActionDialog({ force = false } = {}) {
  const dialog = $("#action-dialog");
  if (!force && addForReviewInteractionLocked() && dialog.querySelector(".add-for-review-form")) return;
  dialog.close();
}

function openAddForReview({ exactText = "", sourceEventId = null, sourceSurface = null, dialogTitle = "Add for review" } = {}) {
  if (addForReviewInteractionLocked() || (currentSurface() === "triage" && triageInteractionLocked())) return;
  const form = document.createElement("form");
  form.className = "triage-review-form add-for-review-form";
  form.append(text("p", "This saves your thought in Triage. Nothing becomes a task, event, reminder, or provider change until you confirm it.", "state-note"));
  const input = document.createElement("textarea");
  input.name = "text"; input.rows = 5; input.required = true;
  input.placeholder = "What just came up?";
  input.value = exactText;
  form.append(field("Thought to hold", input));
  const textLimit = text("small", "Up to 32,000 characters. Longer material stays in this form so you can split it without losing the original.", "state-note");
  form.append(textLimit);
  function validateSubmissionLength() { input.setCustomValidity(input.value.length > 32000 ? "Split this material into submissions of at most 32,000 characters. Your full text is still here." : ""); }
  input.addEventListener("input", validateSubmissionLength); validateSubmissionLength();
  const hints = [
    { value: "", label: "Let Triage identify possible types" },
    { value: "task", label: "Do — a task" },
    { value: "commitment", label: "Promise — something I owe" },
    { value: "calendar_event", label: "Time — a calendar event" },
    { value: "reminder", label: "Reminder" },
    { value: "list", label: "List" },
    { value: "list_item", label: "List item" },
    { value: "recurrence", label: "Repeat — a recurring pattern" },
    { value: "party_identifier", label: "Link a person’s email or phone" },
    { value: "party_candidate", label: "Add a person or organization" },
    { value: "party_relationship", label: "Relationship between people" },
    { value: "relationship_fact", label: "Fact about a person" },
  ];
  const hint = selectControl(hints, "", ""); hint.name = "kind_hint";
  form.append(field("Type hint (optional)", hint));
  const note = text("p", "If the wording is unclear, it stays under Needs interpretation—never silently discarded.", "meta");
  const submit = text("button", "Add for review", "button button-primary"); submit.type = "submit";
  const cancel = text("button", "Cancel", "button button-quiet"); cancel.type = "button"; cancel.addEventListener("click", closeActionDialog);
  const actions = document.createElement("div"); actions.className = "button-row"; actions.append(submit, cancel);
  form.append(note, actions);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submittedText = input.value.trim();
    if (!submittedText) return;
    validateSubmissionLength(); if (!input.reportValidity()) return;
    const submittedHint = hint.value || null;
    const triageActive = currentSurface() === "triage";
    const addOperationToken = beginAddForReviewOperation();
    if (addOperationToken == null) return;
    const operationToken = triageActive ? beginTriageOperation() : null;
    if (triageActive && operationToken == null) {
      finishAddForReviewOperation(addOperationToken);
      return;
    }
    setBanner("Saving your exact words before interpretation…");
    const allowedSurfaces = new Set(["today", "triage", "comms", "reminders", "calendar", "people", "notes", "finance", "scoreboard", "settings"]);
    const capturedSurface = allowedSurfaces.has(sourceSurface) ? sourceSurface : allowedSurfaces.has(currentSurface()) ? currentSurface() : "today";
    const clientRequestId = form.dataset.clientRequestId || crypto.randomUUID();
    form.dataset.clientRequestId = clientRequestId;
    const intentRequest = {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
      body: JSON.stringify({
        text: submittedText,
        source_surface: capturedSurface,
        kind_hint: submittedHint,
        source_event_id: sourceEventId,
        client_request_id: clientRequestId,
      }),
    };
    let intentRecorded = false;
    let refocus = false;
    try {
      let result;
      try {
        result = await api("/api/member/v1/triage/intents", intentRequest);
      } catch (initialError) {
        if (requestDefinitelyRejected(initialError)) throw initialError;
        try {
          result = await api("/api/member/v1/triage/intents", intentRequest);
        } catch (retryError) {
          retryError.ambiguousAfterRetry = true;
          throw retryError;
        }
      }
      if (!addForReviewOperationIsCurrent(addOperationToken) || (triageActive && !triageOperationIsCurrent(operationToken))) return;
      intentRecorded = true;
      delete form.dataset.clientRequestId;
      closeActionDialog({ force: true });
      const savedMessage = result.state === "pending_interpretation" ? "Saved. Triage needs you to choose what this is; nothing was lost or created." : `${result.proposal_ids.length} review item${result.proposal_ids.length === 1 ? "" : "s"} saved in Triage. Nothing is committed yet.`;
      if (triageActive) {
        state.triageView = result.state === "pending_interpretation" ? "needs_interpretation" : "all_candidates";
        state.triageSelected = result.proposal_ids[0] || result.intent_id;
        replaceQuery({ view: state.triageView, item: state.triageSelected });
        const refreshed = await reloadActiveSurface({ triageOperationToken: operationToken });
        if (!refreshed || !triageOperationIsCurrent(operationToken)) return;
      }
      setBanner(savedMessage);
    } catch (error) {
      if (triageActive && intentRecorded) {
        latchTriageRefreshFailure(`Your thought was saved, but Triage could not refresh: ${error.message} Reload this page before continuing.`);
      } else if (error.ambiguousAfterRetry) {
        const recoveryHref = error.body?.detail?.recovery?.href || null;
        const recoveryInstruction = recoveryHref
          ? "Open the saved Triage item to reconcile the durable intent before trying again."
          : "Open Triage and check for the retained thought before trying again; the durable outcome is unknown.";
        const recoveryMessage = `The Add-for-review response remained interrupted after an idempotent retry: ${error.message} Your exact words are still visible. ${recoveryInstruction}`;
        latchAddForReviewRecovery(recoveryMessage, recoveryHref);
        if (triageActive) latchTriageRefreshFailure(recoveryMessage);
      } else {
        delete form.dataset.clientRequestId;
        setBanner(`${error.message} Your thought was not saved; the text is still here.`);
        refocus = true;
      }
    } finally {
      finishAddForReviewOperation(addOperationToken);
      if (triageActive) finishTriageOperation(operationToken);
      if (refocus && !addForReviewInteractionLocked()) input.focus();
    }
  });
  openActionDialog(dialogTitle, form);
}

function configureReminderCue(item) {
  const form = document.createElement("form"); form.className = "dialog-form";
  const signalKey = document.createElement("input"); signalKey.required = true; signalKey.placeholder = "presence:home or weather:rain";
  const source = selectControl(["home_assistant", "member_bridge", "weather", "device", "network", "calendar", "member_profile", "manual"].map((value) => ({ value, label: titleCase(value) })), "home_assistant", "");
  const kind = selectControl(["location", "presence", "weather", "device_state", "network_presence", "calendar_transition", "energy", "capacity"].map((value) => ({ value, label: titleCase(value) })), "presence", "");
  const operator = selectControl(["equals", "not_equals", "above", "below", "entered", "left", "present"].map((value) => ({ value, label: titleCase(value) })), "equals", "");
  const expected = document.createElement("input"); expected.value = "present"; expected.placeholder = "State or numeric threshold";
  const note = text("p", "This compares one fresh, scoped signal with one explicit operator. It does not change provider reminder truth.", "state-note");
  const submit = text("button", "Save cue", "button button-primary"); submit.type = "submit";
  form.append(field("Signal key", signalKey), field("Source", source), field("Signal kind", kind), field("Match rule", operator), field("Expected value", expected), note, submit);
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); submit.disabled = true; setBanner("Saving a deterministic cue. This will not change the source reminder…");
    try {
      await api("/api/member/v1/reminders/cues", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
        body: JSON.stringify({
          cue_id: crypto.randomUUID(), list_external_id: item.list_external_id,
          item_external_id: item.item_external_id, expected_last_event_id: item.last_event_id,
          signal_key: signalKey.value.trim(), source: source.value, kind: kind.value, operator: operator.value,
          expected_state: ["equals", "not_equals"].includes(operator.value) ? expected.value : null,
          expected_number: ["above", "below"].includes(operator.value) ? Number(expected.value) : null,
          enabled: true,
        }),
      });
      closeActionDialog(); setBanner("Cue saved. It activates only when a fresh authorized signal matches."); await reloadActiveSurface();
    } catch (error) { setBanner(`${error.message} No cue was created.`); submit.disabled = false; }
  });
  openActionDialog("Add a context cue", form);
}

function editReminderDialog(item) {
  const form = document.createElement("form"); form.className = "dialog-form";
  const title = document.createElement("input"); title.required = true; title.maxLength = 1000; title.value = item.title;
  const submit = text("button", "Save title", "button button-primary"); submit.type = "submit";
  form.append(field("Reminder title", title), submit);
  form.addEventListener("submit", async (event) => { event.preventDefault(); closeActionDialog(); await runReminderCommand(item, reminderOperation(item, "update"), { title: title.value.trim() }); });
  openActionDialog("Edit provider reminder", form);
}

function moveReminderDialog(item, cockpit) {
  const candidates = (cockpit.lists || []).filter((list) => list.provider === item.provider && list.provider_account_id === item.provider_account_id && list.list_external_id !== item.list_external_id);
  const form = document.createElement("form"); form.className = "dialog-form";
  const destination = selectControl(candidates.map((list) => ({ value: list.list_external_id, label: list.list_name })), candidates[0]?.list_external_id || "", candidates.length ? "" : "No other list available"); destination.required = true;
  const submit = text("button", "Move reminder", "button button-primary"); submit.type = "submit"; submit.disabled = !candidates.length;
  form.append(field("Destination list", destination), submit);
  form.addEventListener("submit", async (event) => { event.preventDefault(); closeActionDialog(); await runReminderCommand(item, reminderOperation(item, "move"), { destination_list_external_id: destination.value }); });
  openActionDialog("Move provider reminder", form);
}

function deleteReminderDialog(item) {
  const wrapper = document.createElement("div"); wrapper.className = "dialog-form";
  wrapper.append(text("p", `Delete “${item.title}” from ${item.account_label}? This is a real provider action after approval; it is suppressed while observation mode is active.`));
  const row = document.createElement("div"); row.className = "button-row";
  const cancel = text("button", "Cancel", "button button-quiet"); cancel.type = "button"; cancel.addEventListener("click", closeActionDialog);
  const confirm = text("button", "Delete from provider", "button button-primary"); confirm.type = "button"; confirm.addEventListener("click", async () => { closeActionDialog(); await runReminderCommand(item, reminderOperation(item, "delete")); });
  row.append(cancel, confirm); wrapper.append(row); openActionDialog("Confirm deletion", wrapper);
}

function reminderMatches(item) {
  return item.bucket === state.remindersView
    && (state.remindersAccount === "all" || item.provider_account_id === state.remindersAccount)
    && (state.remindersList === "all" || item.list_external_id === state.remindersList);
}

function reminderDate(value, dateStyle = "medium") {
  if (!value) return "No date";
  const raw = String(value);
  return new Intl.DateTimeFormat(undefined, {
    dateStyle,
    ...(raw.includes("T") ? { timeStyle: "short" } : {}),
  }).format(raw.includes("T") ? new Date(raw) : calendarDate(raw));
}

function taskStatusLabel(value) {
  return ({ inbox: "Open", next: "Next", in_progress: "In progress", waiting_on: "Waiting", deferred: "Deferred", done: "Completed", cancelled: "Abandoned", dismissed: "Removed" })[value] || memberStatusLabel(value);
}

function remindersHero(cockpit) {
  const hero = document.createElement("section"); hero.className = "hero-card reminders-hero";
  const nowCount = cockpit.counts.now || 0;
  hero.append(
    text("p", "TASKS & REMINDERS", "eyebrow"),
    text("h2", nowCount ? `${nowCount} item${nowCount === 1 ? " needs" : "s need"} attention now.` : "Nothing is asking for attention right now.", "action-title"),
    text("p", "Canonical household tasks and provider-owned reminders share one calm workspace while retaining separate truth, provenance, and write paths.", "brief-summary"),
  );
  const stats = document.createElement("dl"); stats.className = "brief-stats reminder-stats";
  for (const [label, value] of [["Now", cockpit.counts.now], ["Scheduled", cockpit.counts.scheduled], ["Places", cockpit.counts.places], ["Anytime", cockpit.counts.anytime]]) {
    const stat = text("div", "", "stat"); stat.append(text("dt", label), text("dd", value || 0)); stats.append(stat);
  }
  hero.append(stats); return hero;
}

function remindersControls(cockpit, data) {
  const controls = document.createElement("section"); controls.className = "card reminder-controls";
  const views = document.createElement("div"); views.className = "comms-filter-row"; views.setAttribute("role", "group"); views.setAttribute("aria-label", "Reminder activation lens");
  for (const [value, label] of [["now", "Now"], ["scheduled", "Scheduled"], ["places", "Places"], ["anytime", "Anytime"], ["waiting", "Waiting"], ["completed", "Completed"]]) {
    const button = text("button", `${label} ${cockpit.counts[value] || 0}`, "comms-filter"); button.type = "button";
    button.setAttribute("aria-pressed", String(state.remindersView === value));
    button.addEventListener("click", () => { state.remindersView = value; state.reminderItem = null; replaceQuery({ view: value, item: null }); renderReminders(data); }); views.append(button);
  }
  const accounts = document.createElement("div"); accounts.className = "comms-filter-row account-filters";
  for (const account of [{ provider_account_id: "all", label: "All accounts" }, ...(cockpit.accounts || [])]) {
    const button = text("button", account.label, "account-filter"); button.type = "button";
    button.setAttribute("aria-pressed", String(state.remindersAccount === account.provider_account_id));
    button.addEventListener("click", () => { state.remindersAccount = account.provider_account_id; state.remindersList = "all"; state.reminderItem = null; replaceQuery({ account: state.remindersAccount, list: null, item: null }); renderReminders(data); }); accounts.append(button);
  }
  const lists = document.createElement("div"); lists.className = "comms-filter-row account-filters";
  for (const list of [{ list_external_id: "all", list_name: "All lists" }, ...(cockpit.lists || []).filter((row) => state.remindersAccount === "all" || row.provider_account_id === state.remindersAccount)]) {
    const button = text("button", list.list_name, "account-filter"); button.type = "button";
    button.setAttribute("aria-pressed", String(state.remindersList === list.list_external_id));
    button.addEventListener("click", () => { state.remindersList = list.list_external_id; state.reminderItem = null; replaceQuery({ list: state.remindersList, item: null }); renderReminders(data); }); lists.append(button);
  }
  controls.append(views, accounts, lists); return controls;
}

function reminderItemButton(item) {
  const button = document.createElement("button"); button.type = "button"; button.className = "reminder-item";
  if (state.reminderItem === item.id) button.setAttribute("aria-current", "true");
  const heading = document.createElement("span"); heading.className = "reminder-item-heading";
  heading.append(text("strong", item.title), text("span", `${item.account_label} · ${item.list_name}`, "meta"));
  button.append(heading, text("span", item.why?.[0] || "Available", "focus-explanation"));
  if (item.due_at) button.append(text("span", reminderDate(item.due_at), "meta"));
  button.addEventListener("click", () => { state.reminderItem = item.id; replaceQuery({ item: item.id }); renderReminders(state.surface); });
  return button;
}

function taskDetail(item) {
  const pane = document.createElement("section"); pane.className = "card reminder-detail task-detail";
  pane.append(text("p", "ADMINISTRATEME TASK · CANONICAL HOUSEHOLD WORK", "eyebrow"), text("h2", item.title));
  if (item.notes) pane.append(text("p", item.notes));
  const why = document.createElement("p"); why.className = "focus-reason"; why.append(text("strong", "Why it is here: "), document.createTextNode((item.why || []).join(" · "))); pane.append(why);
  const facts = document.createElement("dl"); facts.className = "reminder-facts";
  for (const [label, value] of [
    ["Status", taskStatusLabel(item.status)], ["Due", reminderDate(item.due_date, "full")],
    ["Available from", item.available_from ? reminderDate(item.available_from, "full") : "Now"],
    ["Energy", item.energy ? titleCase(item.energy) : "Not set"], ["Effort", item.effort ? titleCase(item.effort) : "Not set"],
    ["Work type", titleCase(item.item_type || "task")], ["Area", titleCase(item.domain || "tasks")],
  ]) facts.append(text("dt", label), text("dd", value));
  pane.append(facts);
  if (item.micro_script) pane.append(text("p", `First tiny step: ${item.micro_script}`, "state-note"));
  pane.append(text("p", "This is an AdministrateMe task, not a provider reminder. Provider copies require an explicit, separately governed route.", "meta"));
  const actions = document.createElement("div"); actions.className = "button-row";
  const terminal = ["done", "cancelled", "dismissed"].includes(item.status);
  if (!terminal) {
    const done = text("button", "Done", "button button-primary"); done.type = "button"; done.addEventListener("click", () => runTaskLifecycle(item, "complete")); actions.append(done);
    const edit = text("button", "Edit properties", "button button-quiet"); edit.type = "button"; edit.addEventListener("click", () => openActionDialog("Edit household task", taskPropertiesForm(item))); actions.append(edit);
    const defer = text("button", "Defer", "button button-quiet"); defer.type = "button"; defer.addEventListener("click", () => deferTaskDialog(item)); actions.append(defer);
    const snooze = text("button", "Snooze", "button button-quiet"); snooze.type = "button"; snooze.addEventListener("click", () => deferTaskDialog(item, "snooze")); actions.append(snooze);
    if (item.status === "waiting_on") {
      const unblock = text("button", "No longer waiting", "button button-quiet"); unblock.type = "button"; unblock.addEventListener("click", () => runTaskLifecycle(item, "unblock")); actions.append(unblock);
    } else {
      const block = text("button", "Waiting on…", "button button-quiet"); block.type = "button"; block.addEventListener("click", () => blockTaskDialog(item)); actions.append(block);
    }
    const abandon = text("button", "Abandon…", "button button-quiet"); abandon.type = "button"; abandon.addEventListener("click", () => abandonTaskDialog(item)); actions.append(abandon);
  } else {
    const reopen = text("button", "Reopen", "button button-primary"); reopen.type = "button"; reopen.addEventListener("click", () => runTaskLifecycle(item, "reopen")); actions.append(reopen);
  }
  pane.append(actions); return pane;
}

function reminderDetail(item, cockpit) {
  if (item?.record_kind === "task") return taskDetail(item);
  const pane = document.createElement("section"); pane.className = "card reminder-detail";
  if (!item) { pane.append(text("h2", "Choose one reminder"), text("p", "The cockpit carries the remembering. You only need to decide about one item at a time.", "empty")); return pane; }
  pane.append(text("p", `${item.account_label} · ${item.list_name}`, "eyebrow"), text("h2", item.title));
  if (item.notes) pane.append(text("p", item.notes));
  const why = document.createElement("p"); why.className = "focus-reason"; why.append(text("strong", "Why it is here: "), document.createTextNode((item.why || []).join(" · "))); pane.append(why);
  const facts = document.createElement("dl"); facts.className = "reminder-facts";
  const fields = [["Provider", item.account_label], ["Scheduled", reminderDate(item.due_at, "full")], ["Priority", item.priority === 1 ? "High" : item.priority === 5 ? "Medium" : item.priority === 9 ? "Low" : "None"], ["Repeats", item.recurrence_rules?.length ? item.recurrence_rules.join(", ") : "No"], ["Context cues", item.cues?.length || 0]];
  for (const [label, value] of fields) facts.append(text("dt", label), text("dd", value)); pane.append(facts);
  if (item.read_only_reason) pane.append(text("p", item.read_only_reason, "state-note"));
  if (item.possible_copy) pane.append(text("p", `${item.possible_copy.count} possible copies are preserved across provider lists. Review each source before changing or completing anything.`, "possible-copy"));
  const actions = document.createElement("div"); actions.className = "button-row";
  if (item.can_complete || item.completed) {
    const stem = item.completed ? "uncomplete" : "complete"; const button = text("button", item.completed ? "Restore" : "Done", "button button-primary"); button.type = "button";
    button.addEventListener("click", () => runReminderCommand(item, reminderOperation(item, stem))); actions.append(button);
  }
  if (item.can_edit) {
    const edit = text("button", "Edit title", "button button-quiet"); edit.type = "button";
    edit.addEventListener("click", () => editReminderDialog(item)); actions.append(edit);
    const move = text("button", "Move", "button button-quiet"); move.type = "button";
    move.addEventListener("click", () => moveReminderDialog(item, cockpit)); actions.append(move);
    const remove = text("button", "Delete", "button button-quiet"); remove.type = "button";
    remove.addEventListener("click", () => deleteReminderDialog(item)); actions.append(remove);
  }
  const cue = text("button", "Add context cue", "button button-quiet"); cue.type = "button";
  cue.addEventListener("click", () => configureReminderCue(item)); actions.append(cue);
  if (item.last_event_id) {
    const triage = text("button", "Extract related work", "button button-quiet"); triage.type = "button";
    triage.addEventListener("click", () => openAddForReview({
      exactText: [item.title, item.notes].filter(Boolean).join("\n\n"), sourceEventId: item.last_event_id,
      sourceSurface: "reminders", dialogTitle: "Extract related work from this reminder",
    })); actions.append(triage);
  }
  pane.append(actions);
  if (item.source_links?.length) { const links = document.createElement("div"); links.className = "source-links"; for (const link of item.source_links) { const anchor = text("a", link.description || "Open source", "link-button"); anchor.href = link.url; anchor.target = "_blank"; anchor.rel = "noreferrer"; links.append(anchor); } pane.append(links); }
  return pane;
}

function reminderQuickAdd(cockpit) {
  const cardNode = document.createElement("section"); cardNode.className = "card reminder-quick-add"; cardNode.append(text("h2", "Add to a connected provider"), text("p", "Write directly only to the selected Apple Reminders or Google Tasks list.", "state-note"));
  const writable = (cockpit.accounts || []).filter((account) => account.state !== "read_only" && account.operations?.some((operation) => ["create_task", "create_reminder"].includes(operation)));
  if (!writable.length) { cardNode.append(text("p", "Connect a writable Apple Reminders or Google Tasks account in Settings.", "empty")); return cardNode; }
  const form = document.createElement("form"); form.className = "reminder-add-form";
  const title = document.createElement("input"); title.required = true; title.maxLength = 1000; title.placeholder = "What should not be forgotten?"; title.setAttribute("aria-label", "Reminder title");
  const accountSelect = document.createElement("select"); accountSelect.setAttribute("aria-label", "Reminder account");
  for (const account of writable) { const option = document.createElement("option"); option.value = `${account.provider}|${account.provider_account_id}`; option.textContent = account.label; accountSelect.append(option); }
  const listSelect = document.createElement("select"); listSelect.setAttribute("aria-label", "Reminder list");
  const refreshLists = () => { listSelect.replaceChildren(); const [provider, accountId] = accountSelect.value.split("|"); for (const list of (cockpit.lists || []).filter((row) => row.provider === provider && row.provider_account_id === accountId)) { const option = document.createElement("option"); option.value = list.list_external_id; option.textContent = list.list_name; listSelect.append(option); } };
  accountSelect.addEventListener("change", refreshLists); refreshLists();
  const due = document.createElement("input"); due.type = "date"; due.setAttribute("aria-label", "Scheduled date (optional)");
  const submit = text("button", "Add reminder", "button button-primary"); submit.type = "submit"; form.append(title, accountSelect, listSelect, due, submit);
  form.addEventListener("submit", (event) => { event.preventDefault(); const [provider, providerAccountId] = accountSelect.value.split("|"); runReminderCommand({ provider, provider_account_id: providerAccountId, list_external_id: listSelect.value }, provider === "google_tasks" ? "create_task" : "create_reminder", { title: title.value.trim(), ...(due.value ? { due: due.value } : {}) }); });
  cardNode.append(form); return cardNode;
}

function taskQuickAdd() {
  const cardNode = document.createElement("section"); cardNode.className = "card task-quick-add";
  cardNode.append(text("h2", "Add household work"), text("p", "Create an explicit canonical task. It appears here immediately and remains separate from provider truth.", "state-note"));
  const button = text("button", "New task", "button button-primary"); button.type = "button"; button.addEventListener("click", () => openActionDialog("New household task", taskPropertiesForm({}, { create: true }))); cardNode.append(button); return cardNode;
}

function reminderProviderTruth(cockpit) {
  const section = document.createElement("section"); section.className = "card provider-truth"; section.append(text("h2", "What each provider can really do"));
  for (const account of cockpit.accounts || []) { const details = document.createElement("details"); const summary = text("summary", `${account.label} · ${titleCase(account.state)}`); details.append(summary, text("p", `Native: ${(account.native || []).map(titleCase).join(", ")}.`)); for (const limit of account.limitations || []) details.append(text("p", limit, "state-note")); section.append(details); }
  if (!(cockpit.accounts || []).length) section.append(text("p", "No reminder account is connected yet.", "empty")); return section;
}

function calendarTime(value, options = {}) {
  if (!value) return "";
  const timeZone = options.timeZone || state.surface?.cockpit?.timezone;
  return new Intl.DateTimeFormat(undefined, { weekday: "short", hour: "numeric", minute: "2-digit", ...(timeZone ? { timeZone } : {}), ...options }).format(new Date(value));
}

function calendarLocalDate(date = new Date()) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function calendarDate(value) {
  if (value instanceof Date) return value;
  const [year, month, day] = String(value).slice(0, 10).split("-").map(Number);
  return new Date(year, month - 1, day, 12, 0, 0);
}

function calendarZonedParts(value, timeZone = state.surface?.cockpit?.timezone) {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(value instanceof Date ? value : new Date(value));
  return Object.fromEntries(parts.filter((part) => part.type !== "literal").map((part) => [part.type, Number(part.value)]));
}

function calendarDateKey(value, timeZone = state.surface?.cockpit?.timezone) {
  const parts = calendarZonedParts(value, timeZone);
  return `${parts.year}-${String(parts.month).padStart(2, "0")}-${String(parts.day).padStart(2, "0")}`;
}

function calendarHour(value, timeZone = state.surface?.cockpit?.timezone) {
  const parts = calendarZonedParts(value, timeZone);
  return parts.hour + (parts.minute / 60);
}

function calendarSourceKey(source) {
  return `${source.provider || ""}|${source.provider_account_id || ""}|${source.calendar_external_id || ""}`;
}

function calendarEffectLabel(effect) {
  return ({ hard_block: "Hard block", soft_hold: "Soft load", context: "Context", ignore: "Ignored" })[effect] || titleCase(effect);
}

function calendarSources(cockpit) {
  const rules = new Map((cockpit.rules || []).map((rule) => [calendarSourceKey(rule), rule]));
  return (cockpit.sources || []).map((source) => ({ ...source, rule: rules.get(calendarSourceKey(source)) || {} }));
}

function calendarSourceColor(source, cockpit) {
  const palette = ["#2d736e", "#8d5b53", "#7b659f", "#a5762e", "#4f6f8b", "#8b5e79", "#55774d", "#846a54"];
  const keys = calendarSources(cockpit).map(calendarSourceKey).sort();
  return palette[Math.max(0, keys.indexOf(calendarSourceKey(source))) % palette.length];
}

function calendarEventIsVisible(event) {
  const routes = event.source_routes?.length ? event.source_routes : [event];
  return routes.some((route) => !state.calendarHiddenSources.includes(calendarSourceKey(route)));
}

function calendarEventOccursOn(event, dateKey, timeZone = state.surface?.cockpit?.timezone) {
  const startKey = calendarDateKey(event.start_at, timeZone);
  const endKey = calendarDateKey(event.end_at, timeZone);
  const endsAtMidnight = calendarHour(event.end_at, timeZone) === 0;
  return startKey <= dateKey && (event.all_day || endsAtMidnight ? dateKey < endKey : dateKey <= endKey);
}

function calendarRangeDates(cockpit) {
  const dates = [];
  const cursor = calendarDate(cockpit.range?.start_at);
  const end = calendarDate(cockpit.range?.end_at);
  while (cursor < end) {
    dates.push(new Date(cursor));
    cursor.setDate(cursor.getDate() + 1);
  }
  return dates;
}

async function setCalendarView(view, anchor = state.calendarAnchor) {
  state.calendarView = view;
  state.calendarAnchor = anchor || state.calendarToday || calendarLocalDate();
  state.calendarSelectedEvent = null;
  replaceQuery({ view, date: state.calendarAnchor, event: null });
  await reloadActiveSurface();
}

function shiftCalendar(direction) {
  const anchor = calendarDate(state.calendarAnchor || state.calendarToday || calendarLocalDate());
  if (state.calendarView === "month") anchor.setMonth(anchor.getMonth() + direction, 1);
  else {
    const amount = state.calendarView === "day" ? 1 : state.calendarView === "week" ? 7 : 14;
    anchor.setDate(anchor.getDate() + (amount * direction));
  }
  setCalendarView(state.calendarView, calendarLocalDate(anchor));
}

function calendarRangeTitle(cockpit) {
  const anchor = calendarDate(state.calendarAnchor || state.calendarToday || calendarLocalDate());
  if (state.calendarView === "rules") return "Calendar policy";
  if (state.calendarView === "day") return new Intl.DateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" }).format(anchor);
  if (state.calendarView === "month") return new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric" }).format(anchor);
  const dates = calendarRangeDates(cockpit);
  if (!dates.length) return "Calendar";
  const first = dates[0]; const last = dates.at(-1);
  const firstMonth = new Intl.DateTimeFormat(undefined, { month: "short" }).format(first);
  const lastMonth = new Intl.DateTimeFormat(undefined, { month: "short" }).format(last);
  const year = last.getFullYear();
  return first.getMonth() === last.getMonth()
    ? `${firstMonth} ${first.getDate()}–${last.getDate()}, ${year}`
    : `${firstMonth} ${first.getDate()} – ${lastMonth} ${last.getDate()}, ${year}`;
}

function calendarHero(cockpit) {
  const section = document.createElement("section"); section.className = "card calendar-hero";
  const counts = cockpit.counts || {}; const onboarding = cockpit.onboarding || {};
  const copy = document.createElement("div"); copy.className = "calendar-hero-copy";
  copy.append(text("p", "CALENDAR REASONING", "eyebrow"), text("h2", "One schedule, explicit rules.", "action-title"), text("p", `${onboarding.source_count || 0} connected calendars are composed without merging provider truth. View filters never change availability or write routes.`, "state-note"));
  const stats = document.createElement("dl"); stats.className = "calendar-stats";
  for (const [label, value, kind] of [["Hard", counts.hard_block, "hard"], ["Soft load", counts.soft_hold, "soft"], ["Context", counts.context, "context"], ["Conflicts", counts.conflicts, "conflict"]]) { const node = document.createElement("div"); node.className = `calendar-stat calendar-stat-${kind}`; node.append(text("dt", label), text("dd", value || 0)); stats.append(node); }
  section.append(copy, stats); return section;
}

function calendarControls(cockpit) {
  const controls = document.createElement("section"); controls.className = "card calendar-commandbar";
  const dateControls = document.createElement("div"); dateControls.className = "calendar-date-controls";
  const today = text("button", "Today", "button button-quiet calendar-today"); today.type = "button"; today.addEventListener("click", () => setCalendarView(state.calendarView === "rules" ? "week" : state.calendarView, state.calendarToday || calendarLocalDate()));
  const previous = text("button", "‹", "calendar-step"); previous.type = "button"; previous.setAttribute("aria-label", "Previous date range"); previous.addEventListener("click", () => shiftCalendar(-1));
  const next = text("button", "›", "calendar-step"); next.type = "button"; next.setAttribute("aria-label", "Next date range"); next.addEventListener("click", () => shiftCalendar(1));
  const title = text("h2", calendarRangeTitle(cockpit), "calendar-range-title");
  dateControls.append(today, previous, next, title);
  const switcher = document.createElement("div"); switcher.className = "view-switcher calendar-view-switcher";
  for (const [id, label] of [["day", "Day"], ["week", "Week"], ["month", "Month"], ["agenda", "Agenda"], ["find", "Find time"]]) { const button = text("button", label, "view-button"); button.type = "button"; button.setAttribute("aria-pressed", String(state.calendarView === id)); button.addEventListener("click", () => setCalendarView(id)); switcher.append(button); }
  const settings = text("button", "Calendar settings", "button calendar-settings-button"); settings.type = "button"; settings.setAttribute("aria-pressed", String(state.calendarView === "rules")); settings.addEventListener("click", () => setCalendarView(state.calendarView === "rules" ? "week" : "rules"));
  const right = document.createElement("div"); right.className = "calendar-command-actions"; right.append(switcher, settings);
  controls.append(dateControls, right); return controls;
}

function calendarSourcePanel(cockpit, data) {
  const section = document.createElement("section"); section.className = "card calendar-source-panel";
  const heading = document.createElement("div"); heading.className = "calendar-source-heading";
  const copy = document.createElement("div"); copy.append(text("h2", "Calendars in view"), text("p", "Show only what is useful right now. This is a private display filter; household rules remain active.", "state-note"));
  const lens = document.createElement("div"); lens.className = "calendar-lens-switcher";
  for (const [id, label] of [["capacity", "Capacity colors"], ["source", "Calendar colors"]]) { const button = text("button", label, "account-filter"); button.type = "button"; button.setAttribute("aria-pressed", String(state.calendarLens === id)); button.addEventListener("click", () => { state.calendarLens = id; renderCalendar(data); }); lens.append(button); }
  heading.append(copy, lens); section.append(heading);
  const filters = document.createElement("div"); filters.className = "calendar-source-filters";
  for (const source of calendarSources(cockpit)) { const key = calendarSourceKey(source); const label = document.createElement("label"); label.className = "calendar-source-filter"; const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.checked = !state.calendarHiddenSources.includes(key); checkbox.addEventListener("change", () => { state.calendarHiddenSources = checkbox.checked ? state.calendarHiddenSources.filter((value) => value !== key) : [...new Set([...state.calendarHiddenSources, key])]; state.calendarSelectedEvent = null; renderCalendar(data); }); const dot = document.createElement("span"); dot.className = "calendar-source-dot"; dot.style.background = calendarSourceColor(source, cockpit); const body = document.createElement("span"); body.append(text("strong", source.calendar_name || "Calendar"), text("small", `${titleCase(source.rule.purpose || "unclassified")} · ${calendarEffectLabel(source.rule.effect || "context")}`)); label.append(checkbox, dot, body); filters.append(label); }
  section.append(filters);
  const actions = document.createElement("div"); actions.className = "calendar-filter-actions";
  const all = text("button", "Show all", "link-button"); all.type = "button"; all.addEventListener("click", () => { state.calendarHiddenSources = []; renderCalendar(data); });
  const capacity = text("button", "Capacity only", "link-button"); capacity.type = "button"; capacity.addEventListener("click", () => { state.calendarHiddenSources = calendarSources(cockpit).filter((source) => !["hard_block", "soft_hold"].includes(source.rule.effect)).map(calendarSourceKey); renderCalendar(data); });
  actions.append(all, capacity, text("span", `${calendarSources(cockpit).length - state.calendarHiddenSources.length} of ${calendarSources(cockpit).length} visible`, "meta")); section.append(actions);
  return section;
}

function calendarEventButton(event, cockpit, compact = false) {
  const source = event.source_routes?.find((route) => !state.calendarHiddenSources.includes(calendarSourceKey(route))) || event.source_routes?.[0] || event;
  const button = document.createElement("button"); button.type = "button";
  button.className = `calendar-event-block calendar-effect-${event.effect} calendar-lens-${state.calendarLens}${compact ? " calendar-event-compact" : ""}`;
  button.style.setProperty("--source-color", calendarSourceColor(source, cockpit));
  const time = event.all_day ? "All day" : new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit", timeZone: cockpit.timezone }).format(new Date(event.start_at));
  button.setAttribute("aria-label", `${event.title}, ${time}, ${calendarEffectLabel(event.effect)}`);
  if (compact) button.append(text("span", event.all_day ? "" : time, "calendar-event-time"), text("strong", event.title));
  else button.append(text("strong", event.title), text("span", `${time} · ${calendarEffectLabel(event.effect)}`, "calendar-event-meta"));
  button.addEventListener("click", () => { state.calendarSelectedEvent = event.cluster_id || event.id; replaceQuery({ event: state.calendarSelectedEvent }); renderCalendar(state.surface); });
  return button;
}

function calendarEventDetail(cockpit) {
  const pane = document.createElement("aside"); pane.className = "card calendar-event-detail"; pane.setAttribute("aria-label", "Calendar event details");
  const events = (cockpit.events || []).filter(calendarEventIsVisible);
  const event = events.find((item) => (item.cluster_id || item.id) === state.calendarSelectedEvent);
  if (!event) { pane.append(text("p", "EVENT DETAIL", "eyebrow"), text("h2", "Select a calendar item"), text("p", "Open an item to see its source, effect, buffers, duplicate copies, and evidence without leaving the calendar.", "state-note")); return pane; }
  pane.append(text("p", "EVENT DETAIL", "eyebrow"), text("h2", event.title));
  const effect = text("span", calendarEffectLabel(event.effect), `pill calendar-effect-pill calendar-effect-${event.effect}`); pane.append(effect);
  const facts = document.createElement("dl"); facts.className = "calendar-event-facts";
  const rows = [
    ["When", event.all_day ? `${new Intl.DateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric", timeZone: cockpit.timezone }).format(new Date(event.start_at))} · all day` : `${calendarTime(event.start_at)}–${calendarTime(event.end_at, { weekday: undefined })}`],
    ["Calendar", (event.source_calendars || [event.calendar_name]).filter(Boolean).join(" · ")],
    ["Purpose", titleCase(event.purpose)],
    ["Buffer", event.buffer_before_minutes || event.buffer_after_minutes ? `${event.buffer_before_minutes || 0} min before · ${event.buffer_after_minutes || 0} min after` : "None"],
    ["Location", event.location],
    ["Provider", titleCase(event.provider)],
  ];
  for (const [label, value] of rows) if (value) facts.append(text("dt", label), text("dd", value));
  pane.append(facts);
  if (event.why?.length) pane.append(text("h3", "Why it behaves this way"), text("p", event.why.join(" "), "state-note"));
  if (event.duplicate_collapsed) pane.append(text("p", `${event.copy_count} provider copies are grouped here; none were deleted.`, "calendar-duplicate"));
  const actions = document.createElement("div"); actions.className = "button-row";
  if (event.last_event_id) { const evidence = text("button", "View evidence", "button button-quiet"); evidence.type = "button"; evidence.addEventListener("click", () => showEvidence(event.last_event_id)); actions.append(evidence); }
  if (event.last_event_id) { const triage = text("button", "Extract related work", "button button-quiet"); triage.type = "button"; triage.addEventListener("click", () => openAddForReview({ exactText: [event.title, event.description, event.location].filter(Boolean).join("\n\n"), sourceEventId: event.last_event_id, sourceSurface: "calendar", dialogTitle: "Extract related work from this event" })); actions.append(triage); }
  if (event.html_link) { const provider = text("a", "Open in provider", "button button-quiet"); provider.href = event.html_link; provider.target = "_blank"; provider.rel = "noreferrer"; actions.append(provider); }
  pane.append(actions); return pane;
}

function calendarEventLayout(events) {
  const ends = []; const layouts = new Map();
  for (const event of [...events].sort((left, right) => new Date(left.start_at) - new Date(right.start_at))) { const start = new Date(event.start_at).getTime(); let column = ends.findIndex((end) => end <= start); if (column < 0) column = ends.length; ends[column] = new Date(event.end_at).getTime(); layouts.set(event.cluster_id || event.id, column); }
  return { columns: Math.max(1, ends.length), layouts };
}

function calendarTimeGrid(cockpit) {
  const dates = calendarRangeDates(cockpit).slice(0, state.calendarView === "day" ? 1 : 7);
  const visible = (cockpit.events || []).filter(calendarEventIsVisible);
  const timed = visible.filter((event) => !event.all_day); const allDay = visible.filter((event) => event.all_day);
  const minHour = Math.max(0, Math.min(6, ...timed.map((event) => calendarHour(event.start_at, cockpit.timezone))));
  const maxHour = Math.min(24, Math.max(22, ...timed.map((event) => Math.ceil(calendarHour(event.end_at, cockpit.timezone)))));
  const hourHeight = 56; const height = Math.max(8, maxHour - minHour) * hourHeight;
  const board = document.createElement("section"); board.className = "card calendar-board";
  const boardHeading = document.createElement("div"); boardHeading.className = "calendar-board-heading"; boardHeading.append(text("h2", state.calendarView === "day" ? "Day plan" : "Week at a glance"));
  const legend = document.createElement("div"); legend.className = "calendar-legend"; for (const [effect, label] of [["hard_block", "Hard"], ["soft_hold", "Soft"], ["context", "Context"], ["policy", "Guardrail"], ["buffer", "Buffer"]]) { const item = document.createElement("span"); item.className = `calendar-legend-${effect}`; item.append(document.createElement("i"), document.createTextNode(label)); legend.append(item); } boardHeading.append(legend); board.append(boardHeading);
  const scroller = document.createElement("div"); scroller.className = "calendar-grid-scroll";
  const grid = document.createElement("div"); grid.className = "calendar-time-grid"; grid.style.setProperty("--calendar-days", dates.length);
  grid.append(text("span", cockpit.timezone || "", "calendar-timezone"));
  for (const date of dates) { const head = document.createElement("div"); head.className = `calendar-day-head${calendarLocalDate(date) === state.calendarToday ? " is-today" : ""}`; head.append(text("span", new Intl.DateTimeFormat(undefined, { weekday: "short" }).format(date)), text("strong", date.getDate())); grid.append(head); }
  grid.append(text("span", "all-day", "calendar-all-day-label"));
  for (const date of dates) { const cell = document.createElement("div"); cell.className = "calendar-all-day-cell"; for (const event of allDay.filter((item) => calendarEventOccursOn(item, calendarLocalDate(date), cockpit.timezone)).slice(0, 4)) cell.append(calendarEventButton(event, cockpit, true)); grid.append(cell); }
  const axis = document.createElement("div"); axis.className = "calendar-time-axis"; axis.style.height = `${height}px`; for (let hour = minHour; hour <= maxHour; hour += 1) { const label = text("span", new Intl.DateTimeFormat(undefined, { hour: "numeric" }).format(new Date(2020, 0, 1, hour)), "calendar-hour-label"); label.style.top = `${(hour - minHour) * hourHeight}px`; axis.append(label); } grid.append(axis);
  for (const date of dates) {
    const key = calendarLocalDate(date); const lane = document.createElement("div"); lane.className = "calendar-day-lane"; lane.style.height = `${height}px`; lane.style.setProperty("--hour-height", `${hourHeight}px`);
    for (const window of (cockpit.schedule_windows || []).filter((item) => calendarDateKey(item.start_at, cockpit.timezone) === key)) { const startHour = calendarHour(window.start_at, cockpit.timezone); const endHour = calendarDateKey(window.end_at, cockpit.timezone) !== calendarDateKey(window.start_at, cockpit.timezone) ? 24 : calendarHour(window.end_at, cockpit.timezone); const top = Math.max(0, startHour - minHour) * hourHeight; const blockHeight = Math.max(0, Math.min(maxHour, endHour) - Math.max(minHour, startHour)) * hourHeight; if (blockHeight <= 0) continue; const policy = text("div", blockHeight >= 34 ? window.label : "", `calendar-policy-window calendar-policy-${window.effect}`); policy.style.top = `${top}px`; policy.style.height = `${blockHeight}px`; policy.title = `${window.label} · ${calendarEffectLabel(window.effect)}`; lane.append(policy); }
    const dayEvents = timed.filter((event) => calendarEventOccursOn(event, key, cockpit.timezone)); const layout = calendarEventLayout(dayEvents);
    for (const event of dayEvents) { const startHour = calendarDateKey(event.start_at, cockpit.timezone) === key ? calendarHour(event.start_at, cockpit.timezone) : 0; const endHour = calendarDateKey(event.end_at, cockpit.timezone) === key ? calendarHour(event.end_at, cockpit.timezone) : 24; const top = Math.max(0, startHour - minHour) * hourHeight; const eventHeight = Math.max(28, (Math.min(maxHour, endHour) - Math.max(minHour, startHour)) * hourHeight); const column = layout.layouts.get(event.cluster_id || event.id) || 0; const left = `calc(${(column / layout.columns) * 100}% + 2px)`; const width = `calc(${100 / layout.columns}% - 4px)`; if (event.buffer_before_minutes || event.buffer_after_minutes) { const bufferedStartHour = calendarDateKey(event.buffered_start_at, cockpit.timezone) === key ? calendarHour(event.buffered_start_at, cockpit.timezone) : 0; const bufferedEndHour = calendarDateKey(event.buffered_end_at, cockpit.timezone) === key ? calendarHour(event.buffered_end_at, cockpit.timezone) : 24; const halo = document.createElement("span"); halo.className = `calendar-buffer-halo calendar-buffer-${event.effect}`; halo.style.top = `${Math.max(0, bufferedStartHour - minHour) * hourHeight}px`; halo.style.height = `${Math.max(0, Math.min(maxHour, bufferedEndHour) - Math.max(minHour, bufferedStartHour)) * hourHeight}px`; halo.style.left = left; halo.style.width = width; halo.title = `${event.buffer_before_minutes || 0} minute preparation buffer and ${event.buffer_after_minutes || 0} minute recovery buffer`; lane.append(halo); } const block = calendarEventButton(event, cockpit); block.style.top = `${top}px`; block.style.height = `${eventHeight}px`; block.style.left = left; block.style.width = width; lane.append(block); }
    if (key === state.calendarToday) { const hour = calendarHour(new Date(), cockpit.timezone); if (hour >= minHour && hour <= maxHour) { const line = document.createElement("span"); line.className = "calendar-now-line"; line.style.top = `${(hour - minHour) * hourHeight}px`; lane.append(line); } }
    grid.append(lane);
  }
  scroller.append(grid); board.append(scroller); const layout = document.createElement("div"); layout.className = "calendar-board-layout"; layout.append(board, calendarEventDetail(cockpit)); return layout;
}

function calendarAgenda(cockpit) {
  const section = document.createElement("section"); section.className = "card calendar-agenda"; section.append(text("h2", "Agenda"));
  const events = (cockpit.events || []).filter(calendarEventIsVisible); const dates = calendarRangeDates(cockpit);
  let found = false;
  for (const date of dates) { const dayEvents = events.filter((event) => calendarEventOccursOn(event, calendarLocalDate(date), cockpit.timezone)); if (!dayEvents.length) continue; found = true; const group = document.createElement("section"); group.className = "calendar-agenda-day"; group.append(text("h3", new Intl.DateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric" }).format(date))); const list = document.createElement("div"); list.className = "calendar-agenda-items"; for (const event of dayEvents) list.append(calendarEventButton(event, cockpit)); group.append(list); section.append(group); }
  if (!found) section.append(text("p", "No visible calendar items in this range. Hidden calendars and provider records are unchanged.", "empty")); return section;
}

function calendarMonth(cockpit) {
  const section = document.createElement("section"); section.className = "card calendar-month"; section.append(text("h2", "Month at a glance")); const grid = document.createElement("div"); grid.className = "calendar-month-grid";
  for (const label of ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]) grid.append(text("span", label, "calendar-month-weekday"));
  const anchor = calendarDate(state.calendarAnchor || state.calendarToday || calendarLocalDate()); const events = (cockpit.events || []).filter(calendarEventIsVisible);
  for (const date of calendarRangeDates(cockpit)) { const key = calendarLocalDate(date); const cell = document.createElement("div"); cell.className = `calendar-month-day${date.getMonth() !== anchor.getMonth() ? " outside-month" : ""}${key === state.calendarToday ? " is-today" : ""}`; const head = text("button", String(date.getDate()), "calendar-month-date"); head.type = "button"; head.setAttribute("aria-label", `Open ${new Intl.DateTimeFormat(undefined, { month: "long", day: "numeric" }).format(date)} in Day view`); head.addEventListener("click", () => setCalendarView("day", key)); cell.append(head); const dayEvents = events.filter((event) => calendarEventOccursOn(event, key, cockpit.timezone)); for (const event of dayEvents.slice(0, 3)) cell.append(calendarEventButton(event, cockpit, true)); if (dayEvents.length > 3) { const more = text("button", `+${dayEvents.length - 3} more`, "calendar-month-more"); more.type = "button"; more.addEventListener("click", () => setCalendarView("day", key)); cell.append(more); } grid.append(cell); }
  section.append(grid); return section;
}

function calendarFindTime(cockpit) {
  const fragment = document.createDocumentFragment(); const section = document.createElement("section"); section.className = "card calendar-find";
  const heading = document.createElement("div"); heading.className = "calendar-find-heading"; const copy = document.createElement("div"); copy.append(text("h2", "Find a workable window"), text("p", "Hard blocks, buffers, and protected time are removed. Soft load is flagged, never silently overruled.", "state-note"));
  const durations = document.createElement("div"); durations.className = "calendar-duration-switcher"; for (const minutes of [30, 60, 90, 120]) { const button = text("button", `${minutes} min`, "account-filter"); button.type = "button"; button.setAttribute("aria-pressed", String(state.calendarDuration === minutes)); button.addEventListener("click", () => { state.calendarDuration = minutes; renderCalendar(state.surface); }); durations.append(button); } heading.append(copy, durations); section.append(heading);
  const now = Date.now(); const soft = (cockpit.events || []).filter((event) => event.effect === "soft_hold"); const candidates = (cockpit.open_windows || []).filter((window) => new Date(window.end_at) - new Date(window.start_at) >= state.calendarDuration * 60000 && new Date(window.start_at).getTime() >= now - 60000).map((window) => { const suggestedEnd = new Date(new Date(window.start_at).getTime() + state.calendarDuration * 60000); return { ...window, suggested_end_at: suggestedEnd.toISOString(), soft_load: soft.some((event) => new Date(event.start_at) < suggestedEnd && new Date(event.end_at) > new Date(window.start_at)) }; }).sort((left, right) => Number(left.soft_load) - Number(right.soft_load) || new Date(left.start_at) - new Date(right.start_at));
  if (!candidates.length) section.append(text("p", `No ${state.calendarDuration}-minute unprotected window appears in this range.`, "empty")); else { const list = document.createElement("ol"); list.className = "calendar-window-list"; for (const [index, window] of candidates.slice(0, 18).entries()) { const li = document.createElement("li"); const body = document.createElement("div"); body.append(text("strong", `${index === 0 ? "Earliest fit · " : ""}${new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric", timeZone: cockpit.timezone }).format(new Date(window.start_at))}`), text("span", `${calendarTime(window.start_at, { weekday: undefined })}–${calendarTime(window.suggested_end_at, { weekday: undefined })}`, "meta")); li.append(body, text("span", window.soft_load ? "Soft load nearby" : "Clear", `pill ${window.soft_load ? "calendar-window-soft" : "pill-teal"}`)); list.append(li); } section.append(list); }
  const conditional = document.createElement("section"); conditional.className = "card calendar-conditional-card"; conditional.append(text("h2", "Available only if you accept")); for (const window of cockpit.conditional_windows || []) conditional.append(text("p", `${window.label}: ${calendarTime(window.start_at)}–${calendarTime(window.end_at, { weekday: undefined })}`, "calendar-conditional")); if (!(cockpit.conditional_windows || []).length) conditional.append(text("p", "No conditional household windows in this range.", "empty")); fragment.append(section, conditional, calendarConflictPanel(cockpit)); return fragment;
}

function calendarConflictPanel(cockpit) {
  const section = document.createElement("section"); section.className = "card calendar-conflicts"; section.append(text("h2", "Conflicts and pressure"));
  if (!(cockpit.conflicts || []).length) section.append(text("p", "No buffered hard conflict or soft-load pressure is visible.", "empty compact-empty"));
  for (const conflict of cockpit.conflicts || []) { const row = document.createElement("div"); row.className = `calendar-conflict calendar-${conflict.kind}`; row.append(text("strong", `${titleCase(conflict.kind)} · ${conflict.minutes} minutes`), text("span", conflict.why, "state-note")); section.append(row); } return section;
}

function calendarPolicyOverview(cockpit) {
  const fragment = document.createDocumentFragment(); const policy = cockpit.schedule_policy || {};
  const section = document.createElement("section"); section.className = "card calendar-policy-overview"; section.append(text("h2", "Household time guardrails"), text("p", "These recurring windows shape availability before any suggested opening is shown.", "state-note"));
  const dayNames = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]; const list = document.createElement("div"); list.className = "calendar-policy-list";
  for (const window of policy.windows || []) { const row = document.createElement("div"); row.className = `calendar-policy-row calendar-effect-${window.effect}`; row.append(text("strong", window.label), text("span", `${window.days.map((day) => dayNames[day]).join(", ")} · ${window.start_local}–${window.end_local}`, "meta"), text("span", calendarEffectLabel(window.effect), "calendar-policy-effect")); list.append(row); } section.append(list);
  const buffers = document.createElement("section"); buffers.className = "card calendar-buffer-card"; buffers.append(text("h2", "Preparation and recovery buffers")); const bufferList = document.createElement("dl"); bufferList.className = "calendar-buffer-list"; const values = policy.buffer_minutes || {}; for (const kind of ["remote", "appointment", "medical", "handoff", "deep_work"]) { const before = values[`${kind}_before`] || 0; const after = values[`${kind}_after`] || 0; const row = document.createElement("div"); row.append(text("dt", titleCase(kind)), text("dd", `${before} min before · ${after} min after`)); bufferList.append(row); } buffers.append(bufferList); fragment.append(section, buffers); return fragment;
}

function calendarRuleEditor(cockpit, data) {
  const fragment = document.createDocumentFragment(); fragment.append(calendarPolicyOverview(cockpit));
  const section = document.createElement("section"); section.className = "card calendar-rules"; section.append(text("h2", "Calendar routing and capacity"), text("p", "Provider access is factual. Purpose, capacity effect, and approved write destinations are explicit household policy.", "state-note"));
  const disclosure = document.createElement("details"); disclosure.className = "calendar-rule-disclosure"; disclosure.append(text("summary", "Review or change calendar rules"));
  const form = document.createElement("form"); form.className = "calendar-rule-form";
  const existing = new Map((cockpit.rules || []).map((rule) => [`${rule.provider}|${rule.provider_account_id}|${rule.calendar_external_id}`, rule]));
  for (const source of cockpit.sources || []) { const key = `${source.provider}|${source.provider_account_id}|${source.calendar_external_id}`; const rule = existing.get(key) || {}; const row = document.createElement("fieldset"); row.dataset.source = JSON.stringify(source); row.append(text("legend", source.calendar_name || "Calendar"), text("small", `${titleCase(source.access_role)} · ${source.write_events ? "can write" : "read only"}`, "meta")); const purposeLabel = text("label", "Purpose", "calendar-rule-label"); const purpose = document.createElement("select"); purpose.dataset.field = "purpose"; purpose.setAttribute("aria-label", `${source.calendar_name} purpose`); for (const value of ["personal", "work", "household", "coparenting", "property", "family_context", "holidays", "test", "other"]) { const option = new Option(titleCase(value), value); option.selected = (rule.purpose || "other") === value; purpose.append(option); } purposeLabel.append(purpose); const effectLabel = text("label", "Capacity effect", "calendar-rule-label"); const effect = document.createElement("select"); effect.dataset.field = "effect"; effect.setAttribute("aria-label", `${source.calendar_name} capacity effect`); for (const value of ["hard_block", "soft_hold", "context", "ignore"]) { const option = new Option(calendarEffectLabel(value), value); option.selected = (rule.effect || "context") === value; effect.append(option); } effectLabel.append(effect); const write = document.createElement("input"); write.type = "checkbox"; write.dataset.field = "write"; write.checked = Boolean(rule.write_target); write.disabled = !source.write_events; const writeLabel = document.createElement("label"); writeLabel.className = "calendar-write-label"; writeLabel.append(write, document.createTextNode(source.write_events ? " Approved Triage write destination" : " Read-only provider calendar")); row.append(purposeLabel, effectLabel, writeLabel); form.append(row); }
  const save = text("button", "Save reviewed rules", "button button-primary"); save.type = "submit"; form.append(save);
  form.addEventListener("submit", async (event) => { event.preventDefault(); save.disabled = true; const rules = [...form.querySelectorAll("fieldset")].map((row) => { const source = JSON.parse(row.dataset.source); const old = existing.get(`${source.provider}|${source.provider_account_id}|${source.calendar_external_id}`) || {}; const writeTarget = row.querySelector('[data-field="write"]').checked; const purpose = row.querySelector('[data-field="purpose"]').value; const writableKinds = ["personal", "work", "household", "coparenting", "property"]; return { source_member_id: source.member_id, provider: source.provider, provider_account_id: source.provider_account_id, calendar_external_id: source.calendar_external_id, purpose, effect: row.querySelector('[data-field="effect"]').value, tentative_effect: old.tentative_effect || "soft_hold", caregiving_load: old.caregiving_load || "ignore", caregiving_summary_values: old.caregiving_summary_values || [], buffer_kind: old.buffer_kind || "auto", write_target: writeTarget, destination_kind: writeTarget ? (old.destination_kind || (writableKinds.includes(purpose) ? purpose : "personal")) : null, enabled: true }; }); try { await api("/api/member/v1/calendar/onboarding", { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify({ request_id: crypto.randomUUID(), member_id: data.member_id, timezone: cockpit.timezone, rules, windows: cockpit.schedule_policy?.windows || [], buffer_minutes: cockpit.schedule_policy?.buffer_minutes || {}, accepted_chores_required: cockpit.schedule_policy?.accepted_chores_required ?? true, one_confirm_write: cockpit.schedule_policy?.one_confirm_write ?? true }) }); setBanner("Calendar rules saved. Availability was recalculated from provider facts."); await reloadActiveSurface(); } catch (error) { setBanner(`${error.message} Calendar rules were not changed.`); save.disabled = false; } });
  disclosure.append(form); section.append(disclosure); fragment.append(section); return fragment;
}

function renderCalendar(data) {
  const root = $("#surface"); root.replaceChildren(); const cockpit = data.cockpit || {};
  state.calendarAnchor ||= data.calendar_anchor || calendarLocalDate();
  state.calendarToday = data.calendar_today || state.calendarToday || calendarLocalDate();
  $("#mode-label").textContent = "Private · deterministic calendar policy";
  root.append(freshnessStrip(data), calendarHero(cockpit), calendarControls(cockpit));
  if (state.calendarView !== "rules") root.append(calendarSourcePanel(cockpit, data));
  if (state.calendarView === "find") root.append(calendarFindTime(cockpit));
  else if (state.calendarView === "rules") root.append(calendarRuleEditor(cockpit, data));
  else if (state.calendarView === "month") root.append(calendarMonth(cockpit), calendarConflictPanel(cockpit));
  else if (state.calendarView === "agenda") root.append(calendarAgenda(cockpit), calendarConflictPanel(cockpit));
  else root.append(calendarTimeGrid(cockpit), calendarConflictPanel(cockpit));
}

function renderReminders(data) {
  const root = $("#surface"); root.replaceChildren(); const cockpit = data.cockpit;
  if (state.reminderDeepLinkPending) {
    const linkedItem = cockpit.items.find((item) => item.id === state.reminderItem);
    if (linkedItem) {
      state.remindersView = linkedItem.bucket;
      state.remindersAccount = "all";
      state.remindersList = "all";
      replaceQuery({ view: linkedItem.bucket, account: null, list: null, item: linkedItem.id });
    }
    state.reminderDeepLinkPending = false;
  }
  $("#mode-label").textContent = "Private · canonical tasks + provider truth · deterministic";
  root.append(freshnessStrip(data), remindersHero(cockpit), remindersControls(cockpit, data));
  const visible = cockpit.items.filter(reminderMatches);
  if (!state.reminderItem || !visible.some((item) => item.id === state.reminderItem)) state.reminderItem = visible[0]?.id || null;
  const selected = visible.find((item) => item.id === state.reminderItem);
  const workspace = document.createElement("div"); workspace.className = "reminder-workspace";
  const list = document.createElement("section"); list.className = "card reminder-list"; list.append(text("h2", `${titleCase(state.remindersView)} tasks & reminders`));
  if (!visible.length) list.append(text("p", "Nothing in this lens. Your provider truth is unchanged.", "empty")); else { const items = document.createElement("ol"); items.className = "reminder-items"; for (const item of visible) { const li = document.createElement("li"); li.append(reminderItemButton(item)); items.append(li); } list.append(items); }
  workspace.append(list, reminderDetail(selected, cockpit)); root.append(workspace, taskQuickAdd(), reminderQuickAdd(cockpit), rhythmDefinitionsCard(cockpit.rhythms), reminderProviderTruth(cockpit));
}

function peopleMatchesLens(person) {
  if (state.peopleLens === "favorites") return Boolean(person.favorite);
  if (state.peopleLens === "organizations") return person.kind === "organization";
  if (state.peopleLens === "suppressed") return Boolean(person.suppressed);
  if (state.peopleLens === "household") return !["Contact", "Organization"].includes(person.relationship_label);
  return !person.suppressed;
}

function peopleHero(cockpit) {
  const section = document.createElement("section"); section.className = "hero-card people-hero";
  section.append(
    text("p", "PRIVATE RELATIONSHIP MEMORY", "eyebrow"),
    text("h2", "Remember the person, not another inbox.", "action-title"),
    text("p", "People joins only confirmed identity, relationship, contact, commitment, and interaction facts. Observed senders wait outside the directory until you confirm them."),
  );
  const stats = document.createElement("dl"); stats.className = "brief-stats people-stats";
  for (const [label, value] of [["People", cockpit.lens_counts?.all || 0], ["Household", cockpit.lens_counts?.household || 0], ["Gentle cues", cockpit.cues?.length || 0], ["Unresolved senders", cockpit.unresolved_endpoints?.count || 0]]) {
    const item = document.createElement("div"); item.className = "stat"; item.append(text("dt", label), text("dd", value)); stats.append(item);
  }
  section.append(stats); return section;
}

function peopleControls(data) {
  const cockpit = data.cockpit || {}; const section = document.createElement("section"); section.className = "card people-controls";
  const search = document.createElement("form"); search.className = "people-search";
  const input = document.createElement("input"); input.type = "search"; input.value = state.peopleQuery; input.placeholder = "Search names, organizations, circles, and confirmed facts"; input.setAttribute("aria-label", "Search people you are authorized to see");
  const submit = text("button", "Search", "button button-primary"); submit.type = "submit";
  const clear = text("button", "Clear", "button button-quiet"); clear.type = "button"; clear.hidden = !state.peopleQuery;
  search.append(input, submit, clear);
  search.addEventListener("submit", async (event) => {
    event.preventDefault(); const query = input.value.trim(); if (query.length < 2) return;
    submit.disabled = true; setBanner("Searching only relationship memory you are authorized to see…");
    try {
      const result = await api(`/api/member/v1/people/search?q=${encodeURIComponent(query)}`);
      const originals = new Map((cockpit.directory || []).map((person) => [person.party_id, person]));
      state.peopleQuery = query; state.peopleResults = result.results.map((person) => ({ ...(originals.get(person.party_id) || {}), ...person }));
      state.peopleSelected = state.peopleResults[0]?.party_id || null; state.peopleDetail = null;
      replaceQuery({ q: query, item: state.peopleSelected }); renderPeople(data);
      setBanner(`${state.peopleResults.length} authorized result${state.peopleResults.length === 1 ? "" : "s"}.`);
    } catch (error) { setBanner(`${error.message} No relationship data was changed.`); submit.disabled = false; }
  });
  clear.addEventListener("click", () => { state.peopleQuery = ""; state.peopleResults = null; state.peopleSelected = null; state.peopleDetail = null; clearBanner(); replaceQuery({ q: null, item: null }); renderPeople(data); });
  const lenses = document.createElement("div"); lenses.className = "comms-filter-row"; lenses.setAttribute("role", "group"); lenses.setAttribute("aria-label", "People lenses");
  for (const [key, label] of [["all", "All"], ["household", "Household"], ["favorites", "Favorites"], ["organizations", "Organizations"], ["suppressed", "Suppressed"]]) {
    const button = text("button", `${label} ${cockpit.lens_counts?.[key] || 0}`, "comms-filter"); button.type = "button"; button.setAttribute("aria-pressed", String(state.peopleLens === key));
    button.addEventListener("click", () => { state.peopleLens = key; state.peopleSelected = null; state.peopleDetail = null; clearBanner(); replaceQuery({ view: key, item: null }); renderPeople(data); }); lenses.append(button);
  }
  section.append(search, lenses); return section;
}

function peopleSources(cockpit) {
  const section = document.createElement("section"); section.className = "card people-sources";
  section.append(text("h2", "Sources"), text("p", "Each member's contacts stay private unless a Party or field group is explicitly shared.", "state-note"));
  const list = document.createElement("div"); list.className = "people-source-list";
  if (!(cockpit.sources || []).length) list.append(text("p", "No contact adapter is configured. Confirmed household Parties still remain available.", "empty"));
  for (const source of cockpit.sources || []) {
    const row = document.createElement("div"); row.className = "people-source-row";
    row.append(text("strong", source.label || titleCase(source.provider)), text("span", titleCase(source.state || "projected"), `pill state-label state-${String(source.state || "projected").replaceAll("_", "-")}`), text("small", `${source.record_count || 0} projected contact${Number(source.record_count || 0) === 1 ? "" : "s"} · ${titleCase(source.runtime)}`));
    if ((source.limitations || []).length) row.title = source.limitations.join(" "); list.append(row);
  }
  section.append(list);
  const settings = text("a", "Connect or review sources", "button button-quiet"); settings.href = "/member/settings"; section.append(settings); return section;
}

function peopleCuePanel(cockpit) {
  const section = document.createElement("section"); section.className = "card people-cues"; section.append(text("h2", "Relationship cues"), text("p", "Only cadences you explicitly enable can appear here.", "state-note"));
  if (!(cockpit.cues || []).length) { section.append(text("p", "No one is overdue by an enabled cadence. Silence is a valid result.", "empty")); return section; }
  const list = document.createElement("ul"); list.className = "item-list";
  for (const cue of cockpit.cues) { const item = document.createElement("li"); item.className = "item"; item.append(text("strong", cue.title), text("small", cue.reason)); const open = text("button", "Open person", "link-button"); open.type = "button"; open.addEventListener("click", () => document.querySelector(`[data-party-id="${CSS.escape(cue.party_id)}"]`)?.click()); item.append(open); list.append(item); }
  section.append(list); return section;
}

function peopleDirectory(people) {
  const section = document.createElement("section"); section.className = "card people-master";
  section.append(text("h2", state.peopleQuery ? `Results for “${state.peopleQuery}”` : titleCase(state.peopleLens)));
  if (!people.length) { section.append(text("p", state.peopleQuery ? "No authorized person matches this search." : "No confirmed people appear in this lens.", "empty")); return section; }
  const list = document.createElement("ol"); list.className = "people-master-list";
  for (const person of people) {
    const item = document.createElement("li"); const button = document.createElement("button"); button.type = "button"; button.className = "people-master-button"; button.dataset.partyId = person.party_id; button.setAttribute("aria-current", String(person.party_id === state.peopleSelected));
    const heading = document.createElement("span"); heading.className = "people-person-heading"; heading.append(text("strong", person.display_name || "Unnamed person")); if (person.favorite) heading.append(text("span", "Favorite", "pill pill-lavender"));
    const context = [person.relationship_label, person.organization, person.job_title].filter(Boolean).join(" · ");
    const contact = person.last_contact_at ? `Last visible contact ${new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(person.last_contact_at))}` : "No visible interaction yet";
    button.append(heading, text("span", context || "Confirmed Party", "people-context"), text("small", contact));
    button.addEventListener("click", () => { state.peopleSelected = person.party_id; state.peopleDetail = null; replaceQuery({ item: person.party_id }); renderPeople(state.surface); });
    item.append(button); list.append(item);
  }
  section.append(list); return section;
}

function peopleDetail(person, detail) {
  const section = document.createElement("section"); section.className = "card people-detail";
  if (!person) { section.append(text("h2", "Choose a person"), text("p", "Select one confirmed Party to see authorized relationship context, source records, commitments, and interactions.", "empty")); return section; }
  section.append(text("p", person.relationship_label || titleCase(person.kind), "eyebrow"), text("h2", person.display_name || "Unnamed person"), text("p", [person.organization, person.job_title].filter(Boolean).join(" · ") || "Confirmed identity", "meta"));
  if (!detail) { section.append(text("p", "Loading authorized relationship memory…", "empty")); return section; }
  const facts = document.createElement("dl"); facts.className = "people-facts";
  const identifiers = (detail.identifiers || []).map((value) => `${titleCase(value.kind)} · ${value.value}`);
  for (const [label, value] of [["Access", titleCase(detail.party?.access_mode || "authorized")], ["Contact", identifiers.join("\n") || "No authorized endpoint"], ["Sources", `${(detail.contact_records || []).length} member-owned record${(detail.contact_records || []).length === 1 ? "" : "s"}`], ["Last contact", detail.contact_gap?.last_contact_iso ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(detail.contact_gap.last_contact_iso)) : "No visible interaction"]]) facts.append(text("dt", label), text("dd", value));
  section.append(facts);
  if (["owner", "household_shared"].includes(detail.party?.access_mode)) {
    const profile = detail.member_profile || {}; const settings = document.createElement("details"); settings.className = "people-settings"; settings.append(text("summary", "Relationship settings"));
    const form = document.createElement("form"); form.className = "people-settings-form";
    const circle = document.createElement("input"); circle.value = profile.circle || ""; circle.placeholder = "Family, friend, school, professional…";
    const cadence = document.createElement("input"); cadence.type = "number"; cadence.min = "1"; cadence.max = "3650"; cadence.value = profile.desired_cadence_days || ""; cadence.placeholder = "No cadence";
    const tags = document.createElement("input"); tags.value = (profile.tags || []).join(", "); tags.placeholder = "Comma-separated private tags";
    const favorite = document.createElement("input"); favorite.type = "checkbox"; favorite.checked = Boolean(profile.favorite);
    const cues = document.createElement("input"); cues.type = "checkbox"; cues.checked = Boolean(profile.cues_enabled);
    const suppressed = document.createElement("input"); suppressed.type = "checkbox"; suppressed.checked = Boolean(profile.suppressed);
    const reason = document.createElement("input"); reason.value = profile.suppression_reason || ""; reason.placeholder = "Required when suppressed";
    const check = (label, control) => { const wrapper = document.createElement("label"); wrapper.className = "people-check"; wrapper.append(control, document.createTextNode(label)); return wrapper; };
    const toggles = document.createElement("div"); toggles.className = "people-toggle-row"; toggles.append(check("Favorite", favorite), check("Enable cadence cue", cues), check("Suppress cues", suppressed));
    const save = text("button", "Save relationship settings", "button button-primary"); save.type = "submit";
    form.append(field("Circle", circle), field("Cadence in days", cadence), field("Private tags", tags), toggles, field("Suppression reason", reason), save);
    form.addEventListener("submit", async (event) => { event.preventDefault(); save.disabled = true; setBanner("Saving your explicit relationship policy…"); try { await api(`/api/member/v1/people/${encodeURIComponent(person.party_id)}/profile`, { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify({ request_id: crypto.randomUUID(), expected_last_event_id: profile.last_event_id || null, circle: circle.value.trim() || null, desired_cadence_days: cadence.value ? Number(cadence.value) : null, cues_enabled: cues.checked, favorite: favorite.checked, suppressed: suppressed.checked, suppression_reason: reason.value.trim() || null, tags: tags.value.split(",").map((value) => value.trim()).filter(Boolean) }) }); setBanner("Relationship settings saved. Cues remain deterministic and opt-in."); await reloadActiveSurface(); } catch (error) { setBanner(`${error.message} Relationship settings were not changed.`); save.disabled = false; } });
    settings.append(form); section.append(settings);
  }
  const relationshipFacts = (detail.relationship_facts || []).filter((value) => !value.revoked_at);
  const factSection = document.createElement("section"); factSection.className = "people-detail-section"; factSection.append(text("h3", "Confirmed relationship facts"));
  if (!relationshipFacts.length) factSection.append(text("p", "No evidence-backed relationship facts have been confirmed.", "empty"));
  else { const list = document.createElement("ul"); list.className = "item-list"; for (const fact of relationshipFacts) { const item = document.createElement("li"); item.className = "item"; item.append(text("strong", fact.label || titleCase(fact.kind)), text("span", fact.value), text("small", `Confirmed from event ${fact.source_event_id}`)); list.append(item); } factSection.append(list); }
  const activity = document.createElement("section"); activity.className = "people-detail-section"; activity.append(text("h3", "Visible activity"));
  const combined = [...(detail.commitments || []).map((value) => ({ ...value, _kind: "Commitment" })), ...(detail.interactions || []).slice(0, 8).map((value) => ({ ...value, _kind: titleCase(value.channel || value.kind || "Interaction") }))];
  if (!combined.length) activity.append(text("p", "No visible commitments or interactions. AdministrateMe does not invent relationship history.", "empty"));
  else { const list = document.createElement("ul"); list.className = "item-list"; for (const value of combined.slice(0, 12)) { const item = document.createElement("li"); item.className = "item"; item.append(text("strong", value.title || value.subject || value.description || value._kind), text("small", value.occurred_at || value.due_at || (value.status ? memberStatusLabel(value.status) : value._kind))); list.append(item); } activity.append(list); }
  section.append(factSection, activity); return section;
}

function renderPeople(data) {
  const root = $("#surface"); root.replaceChildren(); const cockpit = data.cockpit || { directory: [], sources: [], cues: [], lens_counts: {} };
  $("#mode-label").textContent = "Private · confirmed identity · deterministic cues";
  const sourcePeople = state.peopleResults || cockpit.directory || []; const visible = sourcePeople.filter(peopleMatchesLens);
  if (!state.peopleSelected || !visible.some((person) => person.party_id === state.peopleSelected)) { state.peopleSelected = visible[0]?.party_id || null; state.peopleDetail = null; }
  const selected = visible.find((person) => person.party_id === state.peopleSelected) || null;
  const workspace = document.createElement("div"); workspace.className = "people-workspace";
  const rail = document.createElement("div"); rail.className = "people-rail-stack"; rail.append(peopleSources(cockpit), peopleCuePanel(cockpit));
  workspace.append(rail, peopleDirectory(visible), peopleDetail(selected, state.peopleDetail));
  root.append(freshnessStrip(data), peopleHero(cockpit), peopleControls(data), workspace);
  if (selected && !state.peopleDetail) api(`/api/member/v1/people/${encodeURIComponent(selected.party_id)}`).then((detail) => { if (state.peopleSelected === selected.party_id) { state.peopleDetail = detail; renderPeople(state.surface); } }).catch((error) => setBanner(`${error.message} Nothing was changed.`));
}

function parseJsonList(value) {
  if (Array.isArray(value)) return value;
  if (typeof value !== "string" || !value) return [];
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function noteSourceLabel(note) {
  return note.account_name || titleCase(note.source_kind || "note source");
}

function noteTimestamp(note) {
  const value = note.provider_modified_at || note.captured_at;
  if (!value) return "Time unavailable";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "Time unavailable";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(parsed);
}

function notePlainBody(note) {
  const body = String(note.body || "");
  if (note.body_format !== "html") return body;
  return new DOMParser().parseFromString(body, "text/html").body.textContent || "";
}

function noteSourceKey(note) {
  return `${note.source_kind || "unknown"}|${note.account_external_id || note.source_account_id || ""}`;
}

function noteMatchesLens(note) {
  if (state.notesSource !== "all" && noteSourceKey(note) !== state.notesSource) return false;
  if (state.notesLens === "recent") {
    const value = new Date(note.provider_modified_at || note.captured_at || 0).getTime();
    return value >= Date.now() - (30 * 24 * 60 * 60 * 1000);
  }
  if (state.notesLens === "shared") {
    return note.access_mode === "shared_reference" || (note.grants || []).some((grant) => !grant.revoked_at);
  }
  if (state.notesLens === "meetings") return note.source_type === "meeting_note";
  if (state.notesLens === "triage") return Number(note.triage_count || 0) > 0;
  return true;
}

function notesHero(data) {
  const counts = data.counts || {};
  const section = document.createElement("section"); section.className = "hero-card notes-hero";
  section.append(
    text("p", "PRIVATE RECALL", "eyebrow"),
    text("h2", "Write it down once. Find it when it matters.", "action-title"),
    text("p", "Notes keeps your member-owned sources searchable and turns only evidence-backed candidates into Triage proposals. Nothing becomes an official event, reminder, or task here."),
  );
  const stats = document.createElement("dl"); stats.className = "brief-stats notes-stats";
  for (const [label, value] of [["Visible notes", counts.notes || 0], ["Shared with me", counts.shared_with_me || 0], ["Meeting notes", counts.meeting_notes || 0], ["Needs Triage", counts.triage || 0]]) {
    const item = document.createElement("div"); item.className = "stat";
    item.append(text("dt", label), text("dd", value)); stats.append(item);
  }
  section.append(stats);
  return section;
}

function notesControls(data) {
  const section = document.createElement("section"); section.className = "card notes-controls";
  const search = document.createElement("form"); search.className = "notes-search";
  const input = document.createElement("input"); input.type = "search"; input.value = state.notesQuery; input.placeholder = "Search titles, notes, folders, and tags"; input.setAttribute("aria-label", "Search your authorized notes");
  const submit = text("button", "Search", "button button-primary"); submit.type = "submit";
  const clear = text("button", "Clear", "button button-quiet"); clear.type = "button"; clear.hidden = !state.notesQuery;
  search.append(input, submit, clear);
  search.addEventListener("submit", async (event) => {
    event.preventDefault();
    const query = input.value.trim();
    if (!query) return;
    submit.disabled = true;
    setBanner("Searching only notes you are authorized to search…");
    try {
      const params = new URLSearchParams({ q: query });
      if (state.notesSource !== "all") params.set("source", state.notesSource.split("|")[0]);
      const result = await api(`/api/member/v1/notes/search?${params}`);
      const originals = new Map((data.notes || []).map((note) => [note.knowledge_id, note]));
      state.notesQuery = query;
      state.notesResults = result.items.map((note) => ({ ...(originals.get(note.knowledge_id) || {}), ...note }));
      state.notesSelected = state.notesResults[0]?.knowledge_id || null;
      replaceQuery({ q: query, note: state.notesSelected });
      renderNotes(data);
      setBanner(`${state.notesResults.length} authorized search result${state.notesResults.length === 1 ? "" : "s"}.`);
    } catch (error) {
      setBanner(`${error.message} No note content was changed.`);
      submit.disabled = false;
    }
  });
  clear.addEventListener("click", () => {
    state.notesQuery = ""; state.notesResults = null; state.notesSelected = null;
    clearBanner(); replaceQuery({ q: null, note: null }); renderNotes(data);
  });
  const lenses = document.createElement("div"); lenses.className = "comms-filter-row"; lenses.setAttribute("role", "group"); lenses.setAttribute("aria-label", "Note lenses");
  for (const [key, label] of [["all", "All"], ["recent", "Recent"], ["shared", "Shared"], ["meetings", "Meetings"], ["triage", "Needs Triage"]]) {
    const button = text("button", label, "comms-filter"); button.type = "button"; button.setAttribute("aria-pressed", String(state.notesLens === key));
    button.addEventListener("click", () => { state.notesLens = key; state.notesSelected = null; clearBanner(); replaceQuery({ view: key, note: null }); renderNotes(data); }); lenses.append(button);
  }
  section.append(search, lenses);
  return section;
}

function notesSources(data) {
  const section = document.createElement("section"); section.className = "card notes-sources";
  section.append(text("h2", "Sources"), text("p", "Provider access and AdministrateMe sharing stay separate.", "state-note"));
  const list = document.createElement("div"); list.className = "notes-source-list";
  const all = text("button", `All sources · ${(data.notes || []).length}`, "notes-source-button"); all.type = "button"; all.setAttribute("aria-current", String(state.notesSource === "all"));
  all.addEventListener("click", () => { state.notesSource = "all"; state.notesSelected = null; state.notesResults = null; state.notesQuery = ""; clearBanner(); replaceQuery({ source: null, note: null, q: null }); renderNotes(data); }); list.append(all);
  for (const source of data.sources || []) {
    const key = `${source.source_kind || "unknown"}|${source.account_external_id || ""}`;
    const button = document.createElement("button"); button.type = "button"; button.className = "notes-source-button"; button.setAttribute("aria-current", String(state.notesSource === key));
    const name = source.account_name || titleCase(source.source_kind);
    button.append(text("strong", name), text("small", `${source.count} note${source.count === 1 ? "" : "s"} · ${source.state === "limited" ? "Limited" : "Ready"}`));
    if ((source.warnings || []).length) button.title = source.warnings.map(titleCase).join(" · ");
    button.addEventListener("click", () => { state.notesSource = key; state.notesSelected = null; state.notesResults = null; state.notesQuery = ""; clearBanner(); replaceQuery({ source: key, note: null, q: null }); renderNotes(data); }); list.append(button);
  }
  section.append(list);
  const settings = text("a", "Connect or review sources", "button button-quiet"); settings.href = "/member/settings"; section.append(settings);
  return section;
}

function noteMasterList(notes, data) {
  const section = document.createElement("section"); section.className = "card notes-master";
  section.append(text("h2", state.notesQuery ? `Results for “${state.notesQuery}”` : titleCase(state.notesLens)));
  if (!notes.length) {
    section.append(text("p", state.notesQuery ? "No authorized notes match this search." : "No notes appear in this lens. Connect a member-owned source in Settings, or choose another lens.", "empty"));
    return section;
  }
  const list = document.createElement("ol"); list.className = "notes-master-list";
  for (const note of notes) {
    const item = document.createElement("li"); const button = document.createElement("button"); button.type = "button"; button.className = "notes-master-button"; button.setAttribute("aria-current", String(note.knowledge_id === state.notesSelected));
    const title = note.title || "Untitled note";
    const preview = notePlainBody(note).replace(/\s+/g, " ").trim().slice(0, 130);
    button.append(text("strong", title), text("span", preview || "No note text", "notes-preview"), text("small", `${noteSourceLabel(note)} · ${noteTimestamp(note)}`));
    if (note.access_mode === "shared_reference") button.append(text("span", "Shared with me", "pill pill-lavender"));
    if (note.triage_count) button.append(text("span", `${note.triage_count} in Triage`, "pill pill-blush"));
    button.addEventListener("click", () => { state.notesSelected = note.knowledge_id; replaceQuery({ note: note.knowledge_id }); renderNotes(data); });
    item.append(button); list.append(item);
  }
  section.append(list);
  return section;
}

function noteSharing(note, data) {
  const section = document.createElement("section"); section.className = "notes-sharing";
  section.append(text("h3", "AdministrateMe access"), text("p", "A provider share never grants access here. Share one note explicitly and revoke it independently.", "state-note"));
  const activeGrants = (note.grants || []).filter((grant) => !grant.revoked_at);
  for (const grant of activeGrants) {
    const member = (data.sharing_members || []).find((value) => value.member_id === grant.grantee_member_id);
    const row = document.createElement("div"); row.className = "notes-grant-row";
    const body = document.createElement("div"); body.append(text("strong", member?.label || "Enrolled household adult"), text("small", (grant.permissions || []).map(titleCase).join(" · ")));
    const revoke = text("button", "Revoke", "link-button"); revoke.type = "button";
    revoke.addEventListener("click", async () => {
      revoke.disabled = true; setBanner("Revoking this AdministrateMe note grant…");
      try {
        await api(`/api/member/v1/notes/${encodeURIComponent(note.knowledge_id)}/grants/${encodeURIComponent(grant.grant_id)}/revoke`, {
          method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify({ request_id: crypto.randomUUID() }),
        });
        setBanner("Access revoked. Provider sharing, if any, was not changed."); await reloadActiveSurface();
      } catch (error) { setBanner(`${error.message} Access was not changed.`); revoke.disabled = false; }
    });
    row.append(body, revoke); section.append(row);
  }
  if (!(data.sharing_members || []).length) {
    section.append(text("p", "No other enrolled adult member is eligible for an AdministrateMe grant.", "empty"));
    return section;
  }
  const form = document.createElement("form"); form.className = "notes-share-form";
  const member = selectControl((data.sharing_members || []).map((value) => ({ value: value.member_id, label: value.label })), "", "Choose household adult…"); member.name = "member"; member.required = true;
  const submit = text("button", "Share note", "button button-quiet"); submit.type = "submit";
  const permission = document.createElement("input"); permission.value = "Read and search"; permission.disabled = true;
  form.append(field("Member", member), field("Permission", permission), submit);
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); submit.disabled = true; setBanner("Recording one explicit note grant…");
    try {
      await api(`/api/member/v1/notes/${encodeURIComponent(note.knowledge_id)}/grants`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
        body: JSON.stringify({ request_id: crypto.randomUUID(), grantee_member_id: member.value, permissions: ["read", "search"] }),
      });
      setBanner("Note access recorded. No provider permission was changed."); await reloadActiveSurface();
    } catch (error) { setBanner(`${error.message} Access was not changed.`); submit.disabled = false; }
  });
  section.append(form);
  return section;
}

function noteDetail(note, data) {
  const section = document.createElement("section"); section.className = "card notes-detail";
  if (!note) {
    section.append(text("h2", "Choose a note"), text("p", "Select one note to read its authorized copy, provenance, sharing state, and evidence-backed Triage candidates.", "empty"));
    return section;
  }
  section.append(text("p", titleCase(note.source_type || "note"), "eyebrow"), text("h2", note.title || "Untitled note"), text("p", `${noteSourceLabel(note)} · ${noteTimestamp(note)}`, "meta"));
  const tags = parseJsonList(note.tags_json);
  if (tags.length) { const row = document.createElement("div"); row.className = "notes-tag-row"; for (const tag of tags) row.append(text("span", tag, "pill pill-lavender")); section.append(row); }
  const body = text("pre", notePlainBody(note) || "This provider record has no readable note text.", "notes-body"); section.append(body);
  const actions = document.createElement("div"); actions.className = "button-row";
  if (note.source_url && /^https?:\/\//i.test(note.source_url)) { const open = text("a", "Open in source", "button button-quiet"); open.href = note.source_url; open.target = "_blank"; open.rel = "noopener noreferrer"; actions.append(open); }
  if (note.triage_href) { const triage = text("a", `Review ${note.triage_count} in Triage`, "button button-primary"); triage.href = note.triage_href; actions.append(triage); }
  if (note.last_event_id) { const extract = text("button", "Send to Triage", "button button-quiet"); extract.type = "button"; extract.addEventListener("click", () => openAddForReview({ exactText: [note.title, notePlainBody(note)].filter(Boolean).join("\n\n"), sourceEventId: note.last_event_id, sourceSurface: "notes", dialogTitle: "Extract work from this note" })); actions.append(extract); }
  if (note.last_event_id) { const evidence = text("button", "View source evidence", "link-button"); evidence.type = "button"; evidence.addEventListener("click", () => showEvidence(note.last_event_id)); actions.append(evidence); }
  section.append(actions);
  const facts = document.createElement("dl"); facts.className = "notes-facts";
  for (const [label, value] of [["Folder", note.folder_path || "Provider root"], ["Access", note.access_mode === "shared_reference" ? "Explicit shared reference" : "Owner"], ["Format", titleCase(note.body_format || "plain")], ["Revision", note.provider_revision || "Unreported"]]) facts.append(text("dt", label), text("dd", value));
  section.append(facts);
  const warnings = parseJsonList(note.capability_warnings_json);
  if (warnings.length) section.append(text("p", `Provider limits: ${warnings.map(titleCase).join(" · ")}.`, "possible-copy"));
  if (note.access_mode === "owner" && note.sensitivity !== "privileged") section.append(noteSharing(note, data));
  else if (note.sensitivity === "privileged") section.append(text("p", "Privileged notes stay owner-only and are excluded from model processing and search indexes.", "review-state"));
  return section;
}

function newAppleNoteCard() {
  const card = document.createElement("section"); card.className = "card new-apple-note";
  card.append(text("h2", "New Apple note"));
  const identity = state.bootstrap?.member; const trust = state.bootstrap?.trust;
  if (!identity || identity.authenticated_role !== "principal" || identity.viewing_as || !identity.authenticated_id || identity.authenticated_id !== identity.id || trust?.actions_as !== trust?.reads_as) {
    card.append(text("p", "Return to your own private view to prepare an Apple note.", "state-note")); return card;
  }
  const pendingKey = `administrateme:apple-note-pending:${identity.authenticated_id}`;
  let pending = null; let busy = false; let loaded = false; let canCreate = false; let targets = []; let retryAllowed = false; let receipt = null;
  try { pending = JSON.parse(sessionStorage.getItem(pendingKey) || "null"); } catch { pending = { unknown: true }; }
  const status = text("p", "Checking your Apple Notes writing permission…", "state-note"); status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
  const settings = text("a", "Review Apple Notes permissions", "button button-quiet"); settings.href = "/member/settings?section=apple-services";
  const form = document.createElement("form"); form.className = "new-apple-note-form";
  const folder = document.createElement("select"); folder.required = true;
  const title = document.createElement("input"); title.type = "text"; title.maxLength = 1000;
  const body = document.createElement("textarea"); body.rows = 5; body.required = true; body.maxLength = 100000;
  const review = text("button", "Review new note", "button button-primary"); review.type = "submit";
  form.append(field("Apple Notes folder", folder), field("Note title (optional)", title), field("Note text", body), text("p", "Plain text only. Review the exact note and destination before approval. Apple will change only after a separate execution approval.", "state-note"), review);
  const check = text("button", "Check note status", "button button-secondary"); check.type = "button";
  const retry = text("button", "Retry saved note approval", "button button-secondary"); retry.type = "button"; retry.hidden = true;
  const continueAction = text("button", "Continue to execution approval", "button button-primary"); continueAction.type = "button"; continueAction.hidden = true;
  const another = text("button", "Prepare another note", "button button-quiet"); another.type = "button"; another.hidden = true;
  const actions = document.createElement("div"); actions.className = "button-row"; actions.append(check, retry, continueAction, another, settings); card.append(form, status, actions);
  function lock() {
    for (const control of form.querySelectorAll("input,select,textarea,button")) control.disabled = busy || !loaded || !canCreate || Boolean(pending);
    check.disabled = busy; check.hidden = !pending;
    retry.hidden = !retryAllowed; retry.disabled = busy || !canCreate || !retryAllowed;
    another.hidden = !receipt; another.disabled = busy;
    continueAction.hidden = receipt?.state !== "awaiting_openclaw_approval";
    continueAction.disabled = busy || Boolean(pending?.handoff_started) || !state.bootstrap.chat.enabled || state.bootstrap.chat.state !== "healthy";
  }
  function savePending(value) { if (value) sessionStorage.setItem(pendingKey, JSON.stringify(value)); else sessionStorage.removeItem(pendingKey); pending = value; }
  function matchesRequest(result) {
    return Boolean(pending?.body && result.request_id === pending.body.request_id && result.command_id === pending.body.request_id && result.request && Object.keys(pending.body).every((key) => result.request[key] === pending.body[key]) && Object.keys(result.request).length === Object.keys(pending.body).length);
  }
  async function reconcile() {
    busy = true; retryAllowed = false; receipt = null; lock();
    try {
      const permissions = await api("/api/member/v1/settings/apple-services");
      const service = (permissions.items || []).find((item) => item.provider === "apple_notes");
      targets = service?.write_access ? (service.scopes || []).filter((scope) => (service.selected_scope_ids || []).includes(scope.id) && scope.provider_account_id && scope.account_id && scope.container_id) : [];
      canCreate = permissions.can_mutate === true && Boolean(service?.write_access) && (service?.operations || []).includes("create_note") && targets.length > 0; loaded = true;
      const previous = folder.value; folder.replaceChildren(); for (const target of targets) { const option = text("option", target.label); option.value = target.id; folder.append(option); } if (targets.some((target) => target.id === previous)) folder.value = previous;
      if (pending?.body) {
        title.value = pending.body.title || ""; body.value = pending.body.body || "";
        const selected = targets.find((target) => target.provider_account_id === pending.body.provider_account_id && target.account_id === pending.body.account_external_id && target.container_id === pending.body.folder_external_id); if (selected) folder.value = selected.id;
        const result = await api(`/api/member/v1/notes/commands/status?request_id=${encodeURIComponent(pending.body.request_id)}`);
        if (matchesRequest(result) && result.domain_approval_event_id && result.state !== "not_recorded") {
          receipt = result;
          const messages = { awaiting_openclaw_approval: "Your exact note is approved. A separate execution approval is still required before Apple can change.", pending: "Your approved note is pending. It has not been confirmed in Apple.", completed: "The bridge reported this note change complete. Check the synced Notes list for the latest observed copy.", failed: "This note change failed. Review the result before preparing another action.", unknown: "The provider result is unknown. Do not repeat this note change; check its status again." };
          status.textContent = messages[result.state] || `Note approval recorded. Current state: ${titleCase(result.state)}.`;
          if (result.state === "unknown") receipt = null;
        } else if (result.request_id === pending.body.request_id && result.state === "not_recorded") { retryAllowed = true; status.textContent = "This exact note approval was not recorded. You may retry the saved approval; no new text will be submitted."; }
        else status.textContent = "Your note approval is not confirmed. Check its status before preparing another note.";
      } else if (pending) status.textContent = "A previous note request cannot be reconciled here. Have its exact request reviewed before preparing another note.";
      else status.textContent = canCreate ? "Choose an allowed folder and write the note you want CoS to create." : "Apple Notes writing is off or no allowed folder is ready. Review Apple Notes permissions in Settings.";
    } catch (error) { loaded = false; status.textContent = `${error.message} Note status is unconfirmed. No new approval is allowed until it can be checked.`; }
    finally { busy = false; lock(); }
  }
  async function submit(change, isRetry = false) {
    if (busy || !loaded || !canCreate || (pending && !isRetry) || (isRetry && (!retryAllowed || pending !== change))) return;
    try { savePending(change); } catch { status.textContent = "This browser cannot retain the request reference. No note approval was requested."; return; }
    busy = true; lock(); closeActionDialog(); status.textContent = "Recording your exact note approval…";
    try { await api("/api/member/v1/notes/commands", { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify(change.body) }); }
    catch { /* Reconcile every response by its original request, never auto-retry. */ }
    finally { busy = false; await reconcile(); }
  }
  form.addEventListener("submit", (event) => {
    event.preventDefault(); if (busy || pending || !loaded || !canCreate) return;
    const target = targets.find((value) => value.id === folder.value); if (!target || !body.value.trim()) return;
    const request = { request_id: crypto.randomUUID(), operation: "create_note", provider_account_id: target.provider_account_id, account_external_id: target.account_id, folder_external_id: target.container_id, knowledge_id: null, expected_last_event_id: null, expected_provider_revision: null, body: body.value, title: title.value || null };
    const confirmation = document.createElement("form"); confirmation.className = "dialog-form apple-note-confirmation";
    confirmation.append(text("p", `${identity.authenticated_display_name} · ${target.label}`), text("h3", request.title || "Untitled note"), text("pre", request.body, "notes-body"), text("p", "Approve this exact text and folder. A separate execution approval is required before the note can be created in Apple.", "state-note"));
    const back = text("button", "Go back", "button button-quiet"); back.type = "button"; back.addEventListener("click", closeActionDialog);
    const approve = text("button", "Approve this note", "button button-primary"); approve.type = "submit"; confirmation.append(back, approve);
    confirmation.addEventListener("submit", (submitEvent) => { submitEvent.preventDefault(); approve.disabled = true; void submit({ body: request }); }); openActionDialog("Create this Apple note?", confirmation);
  });
  check.addEventListener("click", () => { void reconcile(); }); retry.addEventListener("click", () => { if (pending) void submit(pending, true); });
  continueAction.addEventListener("click", async (event) => {
    if (busy || pending?.handoff_started || receipt?.state !== "awaiting_openclaw_approval") return;
    if (state.controller) { status.textContent = "Finish or stop the current conversation before continuing."; return; }
    try { savePending({ ...pending, handoff_started: true }); } catch { status.textContent = "This browser cannot retain the handoff reference. No execution approval was requested."; return; }
    continueAction.disabled = true;
    $("#chat-panel").classList.add("open"); $("#chat-open").setAttribute("aria-expanded", "true");
    await sendChat(event, {
      message: `Please use administrateme_execute_pending_provider_action with domain_approval_event_id ${receipt.domain_approval_event_id} to request the separate one-time execution approval for the exact Apple note I approved.`,
      displayMessage: "Please request execution approval for the exact Apple note I just approved.",
    });
    await reconcile();
  });
  another.addEventListener("click", () => { if (!busy && receipt) { savePending(null); receipt = null; title.value = ""; body.value = ""; void reconcile(); } });
  void reconcile(); return card;
}

function renderNotes(data) {
  const root = $("#surface"); root.replaceChildren();
  $("#mode-label").textContent = "Private · authorized recall · deterministic Triage";
  const notes = (state.notesResults || data.notes || []).filter(noteMatchesLens);
  if (!state.notesSelected || !notes.some((note) => note.knowledge_id === state.notesSelected)) state.notesSelected = notes[0]?.knowledge_id || null;
  const selected = notes.find((note) => note.knowledge_id === state.notesSelected) || null;
  const workspace = document.createElement("div"); workspace.className = "notes-workspace";
  workspace.append(notesSources(data), noteMasterList(notes, data), noteDetail(selected, data));
  root.append(freshnessStrip(data), notesHero(data), notesControls(data), newAppleNoteCard(), workspace);
}

const FINANCE_VIEWS = [
  ["overview", "Overview"],
  ["cashflow", "Cash flow"],
  ["spending", "Spending"],
  ["scenarios", "Scenarios"],
  ["networth", "Net worth"],
  ["health", "Data health"],
];

function financeDifference(left, right) {
  return left == null || right == null ? null : left - right;
}

function financeControls() {
  const controls = document.createElement("nav"); controls.className = "finance-controls"; controls.setAttribute("aria-label", "Finance views");
  for (const [value, label] of FINANCE_VIEWS) {
    const button = text("button", label, "finance-view-button"); button.type = "button";
    button.setAttribute("aria-pressed", String(state.financeView === value));
    button.addEventListener("click", () => { state.financeView = value; replaceQuery({ view: value }); renderFinance(state.surface); });
    controls.append(button);
  }
  return controls;
}

function financeHero(cockpit) {
  const focus = cockpit.focus || {};
  const hero = document.createElement("section"); hero.className = `hero-card finance-hero finance-${focus.state || "steady"}`;
  const copy = document.createElement("div");
  const hasBooks = Boolean(cockpit.accepted_books?.publication_id);
  copy.append(text("p", hasBooks ? "OTHER FINANCIAL OBSERVATIONS" : "HOUSEHOLD CASH-FLOW COCKPIT", "eyebrow"), text("h2", focus.title || "Finance plan", "action-title"), text("p", focus.why || "Deterministic workbook and transaction state.", "brief-summary"), text("p", hasBooks ? "Accepted Books reports are shown separately above." : focus.action || "", "microstep"));
  const baseline = (cockpit.scenarios || []).find((item) => item.is_baseline) || cockpit.scenarios?.[0];
  const quick = document.createElement("dl"); quick.className = "finance-hero-stats";
  for (const [label, value] of [
    ["Cash", moneyMinor(cockpit.summary.cash_minor, { compact: true })],
    ["Month-to-date", moneyMinor(cockpit.summary.mtd_net_minor, { compact: true })],
    ["Monthly plan", moneyMinor((baseline || {}).monthly_net_minor, { compact: true })],
    ["Cash safety", "Not established"],
  ]) { const item = document.createElement("div"); item.append(text("dt", label), text("dd", value)); quick.append(item); }
  hero.append(copy, quick); return hero;
}

function financeAcceptedBooks(cockpit) {
  const books = cockpit.accepted_books || { state: "missing_evidence", reports: [] };
  const section = document.createElement("section"); section.className = "card finance-accepted-books";
  section.append(text("h2", "Accepted Books reports"));
  if (!books.publication_id) {
    const messages = {
      disabled: "Accepted Books integration is not enabled.",
      permission_required: "Books reports require your authorized private session.",
      selection_required: "Select an authorized book through Finance query before reviewing its reports.",
      missing_evidence: "No accepted publication is available in this scope. Missing values are unavailable, not zero.",
    };
    section.append(text("p", messages[books.state] || "Accepted Books reports are temporarily unavailable.", "empty"));
    return section;
  }
  section.append(text("p", `Qualified historical snapshot · accepted ${books.as_of} · publication ${books.publication_id}`, "state-note"));
  for (const limitation of books.limitations || []) section.append(text("p", limitation, "state-note"));
  const provenance = document.createElement("details"); provenance.append(text("summary", "Publication provenance"));
  provenance.append(text("p", `Book: ${books.book_id} · contract: ${books.schema_version} · content hash: ${books.content_hash}`));
  const clocks = books.source_clock_summary || {};
  provenance.append(text("p", `Oldest source observation: ${clocks.oldest_observed_at || "Unavailable"}. Current source freshness is unverified.`));
  section.append(provenance);
  for (const report of books.reports || []) {
    const details = document.createElement("details"); details.open = true;
    details.append(text("summary", `${titleCase(report.report_id)} · ${titleCase(report.release_state)}`));
    details.append(text("p", `${titleCase(report.basis)} · ${report.period_start} to ${report.period_end} · model ${report.model_id} ${report.model_version}`, "meta"));
    details.append(text("p", `Quality: ${Object.entries(report.quality || {}).map(([dimension, value]) => `${titleCase(dimension)} ${value}`).join("; ")}.`, "state-note"));
    if (report.required_evidence?.length) details.append(text("p", `Required evidence: ${report.required_evidence.join(", ")}.`, "state-note"));
    if (!(report.metrics || []).length) details.append(text("p", report.release_state === "unavailable" ? "This report has not been released." : "No metrics in this bounded view.", "empty"));
    else {
      const table = document.createElement("table"); table.className = "finance-table";
      const head = document.createElement("thead"); const heading = document.createElement("tr");
      for (const label of ["Published metric", "Exact value", "Scenario / evidence"]) heading.append(text("th", label));
      head.append(heading); table.append(head); const body = document.createElement("tbody");
      for (const metric of report.metrics) {
        const row = document.createElement("tr");
        const value = metric.value_minor == null ? "Unavailable" : publishedMoneyMinor(metric.value_minor, metric.currency);
        row.append(text("th", metric.metric_id), text("td", value), text("td", `${metric.scenario_id || "No scenario"} · ${(metric.evidence_ids || []).join(", ") || "No evidence reference"}`));
        body.append(row);
      }
      table.append(body); details.append(table);
    }
    if (report.more_metrics) details.append(text("p", "Additional metrics require a bounded Finance report query.", "state-note"));
    section.append(details);
  }
  if (books.details_state === "report_query_required") section.append(text("p", "Report details exceed this display budget. Request one bounded report before relying on its values.", "state-note"));
  return section;
}

function financeKpis(cockpit) {
  const grid = document.createElement("section"); grid.className = "finance-kpi-grid"; grid.setAttribute("aria-label", "Finance summary");
  const values = [
    ["Observed month-end pace", financeDifference(cockpit.summary.projected_month_income_minor, cockpit.summary.projected_month_expenses_minor), "Partial observations; coverage unverified"],
    ["Monthly plan", financeDifference(cockpit.summary.base_monthly_income_minor, cockpit.summary.base_monthly_expenses_minor), "Chosen assumptions; not actuals"],
    ["Listed net value", cockpit.summary.observed_listed_net_minor, "Observed inputs; not a reconciled balance sheet"],
    ["Spending vs prior", financeDifference(cockpit.summary.mtd_expenses_minor, cockpit.summary.prior_month_expenses_minor), "Same elapsed time; coverage unverified"],
  ];
  for (const [label, value, note] of values) { const item = document.createElement("article"); item.className = "card finance-kpi"; item.append(text("span", label, "eyebrow"), text("strong", moneyMinor(value, { compact: true })), text("small", note)); grid.append(item); }
  return grid;
}

function financeCashFlow(cockpit) {
  const section = document.createElement("section"); section.className = "card finance-cashflow"; section.append(text("h2", "Cash flow plan"), text("p", "Observed activity is compared with the workbook baseline. The dashboard never rewrites your bank.", "state-note"));
  const rows = [
    ["Income", cockpit.summary.mtd_income_minor, cockpit.summary.base_monthly_income_minor],
    ["Expenses", cockpit.summary.mtd_expenses_minor, cockpit.summary.base_monthly_expenses_minor],
    ["Net", cockpit.summary.mtd_net_minor, financeDifference(cockpit.summary.base_monthly_income_minor, cockpit.summary.base_monthly_expenses_minor)],
  ];
  const table = document.createElement("table"); table.className = "finance-table"; const head = document.createElement("thead"); const headRow = document.createElement("tr"); for (const value of ["Measure", "Month to date", "Monthly plan"]) headRow.append(text("th", value)); head.append(headRow); const body = document.createElement("tbody");
  for (const [label, actual, plan] of rows) { const row = document.createElement("tr"); row.append(text("th", label), text("td", moneyMinor(actual)), text("td", moneyMinor(plan))); body.append(row); }
  table.append(head, body); section.append(table); return section;
}

function financeBudgets(cockpit) {
  const section = document.createElement("section"); section.className = "card finance-budget"; section.append(text("h2", "Spending plan"));
  if (!(cockpit.budgets || []).length) { section.append(text("p", "No category budgets are published yet. Add them in the workbook; the dashboard will not invent targets.", "empty")); return section; }
  const list = document.createElement("ul"); list.className = "finance-budget-list";
  for (const item of cockpit.budgets) { const row = document.createElement("li"); const heading = document.createElement("div"); heading.append(text("strong", item.category), text("span", item.state === "watch" ? "Review" : item.state === "unavailable" ? "Unavailable" : "Partial observations", `pill state-label state-${item.state === "watch" ? "degraded" : "healthy"}`)); const progress = document.createElement("div"); progress.className = "finance-progress"; const fill = document.createElement("span"); const ratio = item.planned_minor && item.actual_minor != null ? Math.min(1.2, item.actual_minor / item.planned_minor) : 0; fill.style.width = `${Math.min(100, ratio * 100)}%`; progress.append(fill); row.append(heading, progress, text("small", `${moneyMinor(item.actual_minor)} of ${moneyMinor(item.planned_minor)} · ${moneyMinor(item.remaining_minor)} remaining`)); list.append(row); }
  section.append(list); return section;
}

function financeScenarios(cockpit) {
  const section = document.createElement("section"); section.className = "card finance-scenarios"; section.append(text("h2", "Scenario lab"), text("p", "Scenarios are workbook-authored deltas. Switching here compares projections; it does not change the baseline.", "state-note"));
  const scenarios = cockpit.scenarios || [];
  if (!scenarios.length) { section.append(text("p", "A scenario needs explicit opening cash, income, expenses and reserve inputs. Missing inputs do not establish zero spending or safe runway.", "empty")); return section; }
  if (!state.financeScenario || !scenarios.some((item) => item.scenario_id === state.financeScenario)) state.financeScenario = (scenarios.find((item) => item.is_baseline) || scenarios[0]).scenario_id;
  const switcher = document.createElement("div"); switcher.className = "finance-scenario-switcher";
  for (const scenario of scenarios) { const button = text("button", scenario.name, "finance-scenario-button"); button.type = "button"; button.setAttribute("aria-pressed", String(scenario.scenario_id === state.financeScenario)); button.addEventListener("click", () => { state.financeScenario = scenario.scenario_id; renderFinance(state.surface); }); switcher.append(button); }
  const selected = scenarios.find((item) => item.scenario_id === state.financeScenario) || scenarios[0]; const stats = document.createElement("dl"); stats.className = "finance-scenario-stats";
  for (const [label, value] of [["Monthly net", moneyMinor(selected.monthly_net_minor)], ["Ending cash", moneyMinor(selected.ending_cash_minor)], ["Below reserve", selected.below_floor_month ? `Month ${selected.below_floor_month}` : "Not in horizon"], ["Cash out", selected.cash_out_month ? `Month ${selected.cash_out_month}` : "Not in horizon"]]) { const item = document.createElement("div"); item.append(text("dt", label), text("dd", value)); stats.append(item); }
  const chart = document.createElement("div"); chart.className = "finance-spark"; chart.setAttribute("aria-label", `${selected.name} cash forecast`); const points = selected.points || []; const max = Math.max(...points.map((item) => Math.abs(item.cash_minor)), 1); for (const point of points.filter((_item, index) => index % Math.max(1, Math.ceil(points.length / 24)) === 0)) { const bar = document.createElement("span"); bar.style.height = `${Math.max(4, Math.abs(point.cash_minor) / max * 100)}%`; bar.classList.toggle("negative", point.cash_minor < 0); bar.title = `Month ${point.month}: ${moneyMinor(point.cash_minor)}`; chart.append(bar); }
  section.append(switcher, stats, chart); for (const limitation of selected.limitations || []) section.append(text("p", limitation, "state-note")); if (selected.notes) section.append(text("p", selected.notes, "meta")); return section;
}

function financeGoals(cockpit) {
  const section = document.createElement("section"); section.className = "card finance-goals";
  section.append(text("h2", "Workbook goals and capital plans"), text("p", "Goals organize future uses of money. They never move funds or silently change a scenario.", "state-note"));
  const goals = cockpit.goals || [];
  if (!goals.length) { section.append(text("p", "No goals are published yet. Add a target in the workbook when it is useful.", "empty")); return section; }
  const list = document.createElement("ul"); list.className = "finance-goal-list";
  for (const goal of goals) {
    const row = document.createElement("li");
    const heading = document.createElement("div"); heading.append(text("strong", goal.name), text("span", titleCase(goal.status), `pill state-label state-${goal.status === "complete" ? "healthy" : "ready"}`));
    const target = Number(goal.target_minor || 0); const current = Number(goal.current_minor || 0); const ratio = target > 0 ? Math.min(1, current / target) : 0;
    const progress = document.createElement("div"); progress.className = "finance-progress"; const fill = document.createElement("span"); fill.style.width = `${ratio * 100}%`; progress.append(fill);
    const due = goal.target_date ? ` · target ${new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(`${goal.target_date}T00:00:00`))}` : "";
    row.append(heading, progress, text("small", `${moneyMinor(current)} of ${moneyMinor(target)}${due}`)); if (goal.notes) row.append(text("small", goal.notes));
    const actions = document.createElement("div"); actions.className = "button-row";
    if (goal.status === "active") {
      const pause = text("button", "Pause", "button button-quiet"); pause.type = "button"; pause.addEventListener("click", () => financeGoalLifecycleDialog(goal, "pause")); actions.append(pause);
      const complete = text("button", "Mark complete", "button button-primary"); complete.type = "button"; complete.addEventListener("click", () => financeGoalLifecycleDialog(goal, "complete")); actions.append(complete);
    } else if (["paused", "complete"].includes(goal.status)) {
      const reopen = text("button", "Reopen", "button button-primary"); reopen.type = "button"; reopen.addEventListener("click", () => runFinanceGoalLifecycle(goal, "reopen")); actions.append(reopen);
    }
    if (goal.last_event_id) { const evidence = text("button", "View evidence", "link-button"); evidence.type = "button"; evidence.addEventListener("click", () => showEvidence(goal.last_event_id)); actions.append(evidence); }
    row.append(actions); list.append(row);
  }
  section.append(list); return section;
}

async function runFinanceGoalLifecycle(goal, action, reason = null) {
  setBanner(`Recording ${action} for this finance goal…`);
  try {
    await api(`/api/member/v1/finance/goals/${encodeURIComponent(goal.goal_id)}/lifecycle`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
      body: JSON.stringify({
        action, reason, expected_last_event_id: goal.last_event_id,
        request_id: crypto.randomUUID(),
      }),
    });
    setBanner(action === "complete" ? "Goal completed and retained in history." : action === "pause" ? "Goal paused without changing the plan or moving money." : "Goal reopened from its existing definition.");
    await reloadActiveSurface();
  } catch (error) {
    setBanner(`${error.message} The finance goal was not changed.`);
  }
}

function financeGoalLifecycleDialog(goal, action) {
  const form = document.createElement("form"); form.className = "dialog-form";
  form.append(text("p", action === "complete" ? "This records the planning outcome. It does not move funds." : "Pausing removes the goal from active attention without deleting its plan or evidence.", "state-note"));
  const reason = document.createElement("textarea"); reason.rows = 3; reason.maxLength = 500;
  const buttons = document.createElement("div"); buttons.className = "button-row";
  const keep = text("button", "Keep active", "button button-quiet"); keep.type = "button"; keep.addEventListener("click", closeActionDialog);
  const submit = text("button", action === "complete" ? "Mark complete" : "Pause goal", "button button-primary"); submit.type = "submit"; buttons.append(keep, submit);
  form.append(field(action === "complete" ? "Outcome note (optional)" : "Reason (optional)", reason), buttons);
  form.addEventListener("submit", async (event) => { event.preventDefault(); closeActionDialog(); await runFinanceGoalLifecycle(goal, action, reason.value.trim() || null); });
  openActionDialog(action === "complete" ? "Complete this goal?" : "Pause this goal?", form);
}

function financePlanningCard(books) {
  const section = document.createElement("section"); section.className = "card finance-planning";
  section.append(text("h2", "Dated Books projections"));
  if (!books?.publication_id) { section.append(text("p", "An accepted accounting publication is required.", "empty")); return section; }
  const status = text("p", "Loading verified projection contexts…", "state-note"); section.append(status);
  const controls = document.createElement("div"); controls.className = "button-row";
  const contextSelect = document.createElement("select"); contextSelect.setAttribute("aria-label", "Projection context"); contextSelect.style.maxWidth = "100%";
  const horizonSelect = document.createElement("select"); horizonSelect.setAttribute("aria-label", "Projection horizon");
  for (const days of [7, 30, 90, 180, 365]) { const option = text("option", `${days} days`); option.value = String(days); horizonSelect.append(option); }
  horizonSelect.value = "30";
  controls.append(contextSelect, horizonSelect);
  const content = document.createElement("div"); const detail = document.createElement("div");
  section.append(controls, content, detail); controls.hidden = true;
  let projectionId = null; let requestNumber = 0;
  const request = (report, extra = {}) => api(`/api/member/v1/finance/planning?${new URLSearchParams({ book_id: books.book_id, report_id: report, ...(projectionId ? { projection_id: projectionId } : {}), ...extra })}`);
  const exact = publishedMoneyMinor;
  const range = (values, currency) => !values ? "Unknown" : values[0] === values[1] ? exact(values[0], currency) : `${exact(values[0], currency)} to ${exact(values[1], currency)}`;
  const incomeLabel = (basis) => basis === "OBSERVED_BASE_ONLY" ? "Observed income only" : "Modeled net funding (not payroll)";
  const openingLabel = (basis) => basis === "verified_unrestricted" ? "Verified unrestricted opening" : "Hypothetical observed opening";
  async function events(offset, generation) {
    const answer = await request("planning_events", { context_id: contextSelect.value, horizon_days: horizonSelect.value, offset: String(offset) });
    if (generation !== requestNumber || !section.isConnected) return;
    detail.replaceChildren(text("h3", "Dated events"), text("p", `Full context manifest: ${answer.manifest.row_count} events. This page lists original date windows overlapping the ${answer.horizon_days}-day horizon, plus undated items that remain unplaced. Bounds are not clipped. Overlap and the source included flag do not prove occurrence within this horizon; producer totals are unchanged.`, "state-note"));
    const table = document.createElement("table"); table.className = "finance-table";
    const head = document.createElement("tr"); for (const label of ["Dates / route", "Type", "Amount range", "Source included"]) head.append(text("th", label)); table.append(head);
    for (const item of answer.events || []) {
      const row = document.createElement("tr"); row.append(text("td", `${item.earliest_date ? `${item.earliest_date} – ${item.latest_date}` : `Undated (${titleCase(item.date_unknown_reason)})`} · ${item.account_route_id}`), text("td", titleCase(item.kind)), text("td", range([item.amount.lower_minor, item.amount.upper_minor], answer.context.currency)), text("td", item.included ? "Yes" : "No")); table.append(row);
    }
    detail.append(table);
    for (const [label, next] of [["Previous events", offset > 0 ? Math.max(0, offset - 50) : null], ["Next events", answer.next_offset]]) {
      if (next == null) continue;
      const button = text("button", label, "button button-quiet"); button.type = "button";
      button.addEventListener("click", () => { button.disabled = true; void events(next, generation).catch(() => { status.textContent = "Event details could not be loaded. Retry the selected context."; }); }); detail.append(button);
    }
  }
  async function load() {
    const generation = ++requestNumber;
    content.replaceChildren(); detail.replaceChildren(); status.textContent = "Loading selected projection…";
    try {
      const answer = await request("planning_projection", { context_id: contextSelect.value, horizon_days: horizonSelect.value });
      if (generation !== requestNumber || !section.isConnected) return;
      const c = answer.context; const h = answer.summary;
      status.textContent = `${c.measure === "BANK_CASH_SUBSET" ? "Bank cash subset" : "Cash-equivalent funding — not bank cash"} · ${h.completeness_state} · ${h.estimate_state}`;
      content.append(text("p", `${titleCase(c.scenario_id)} · Plan case ${c.plan_case_id} · ${incomeLabel(c.income_basis)} · ${openingLabel(c.opening_basis)}`, "state-note"));
      content.append(text("p", `${h.start_inclusive} inclusive to ${h.end_exclusive} exclusive · ${c.timezone}. Cash safety: ${h.safety_state === "SOURCE_VERIFIED" ? "historically verified by source; current safety unverified" : "not established"}.`, "state-note"));
      const stats = document.createElement("dl"); stats.className = "finance-scenario-stats";
      for (const [label, value] of [["Opening range", h.opening], ["Closing range", h.closing], [Array.isArray(h.minimum) ? "Minimum range" : "Conservative minimum", h.minimum], ["Encumbered range", h.encumbered], ["Cash-in range", h.cash_in], ["Cash-out range", h.cash_out]]) { const item = document.createElement("div"); item.append(text("dt", label), text("dd", Array.isArray(value) ? range(value, c.currency) : `${exact(value.value_minor, c.currency)}${value.unknown_reason ? ` (${titleCase(value.unknown_reason)})` : ""}`)); stats.append(item); }
      content.append(stats, text("p", "These measures cannot be added. Positive remainder is not available surplus. Modeled net funding is not payroll or guaranteed bank income.", "state-note"));
      content.append(text("p", `Reserve threshold: ${exact(h.reserve_threshold, c.currency)}. Checking threshold: ${exact(h.checking_threshold, c.currency)}.`, "state-note"));
      for (const [label, value] of [["Reserve breach", h.reserve_breach], ["Negative bank cash", h.negative_bank_cash], ["Checking shortage", h.checking_shortage]]) content.append(text("p", `${label}: ${value == null ? "Unknown" : value ? "Yes" : "No"}.`, "state-note"));
      for (const [label, key] of [["Minimum date", "minimum_date"], ["First negative date", "first_negative_date"], ["First zero-crossing date", "first_zero_crossing_date"], ["First reserve-breach date", "first_reserve_breach_date"]]) { const dated = h[key]; content.append(text("p", `${label}: ${dated.date || `Unknown (${titleCase(dated.unknown_reason)})`}.`, "state-note")); }
      const references = document.createElement("div"); references.className = "finance-gap-details";
      async function reference(report, referenceId, target, offset = 0, gapGroupId = null) {
        const info = await request(report, { context_id:c.context_id, horizon_days:String(h.days), offset:String(offset), ...(gapGroupId ? {gap_group_id:gapGroupId} : {}), ...(referenceId ? {reference_id:referenceId} : {}) });
        if (generation !== requestNumber || !section.isConnected) return;
        target.replaceChildren();
        for (const record of info.records || []) {
          if (report === "planning_gaps" || report === "planning_issues") {
            if (record.issue_id) target.append(text("p", `${record.required ? "Required" : "Optional"} source issue: ${record.issue_id}`, "state-note"));
            if (record.issue_group) {
              target.append(text("p", `${record.issue_counts.required} required of ${record.issue_counts.total} original issues in this context and horizon.`, "state-note"));
              const issues = text("button", "Original source issues", "button button-quiet"); issues.type = "button"; const issueBody = document.createElement("div");
              issues.addEventListener("click", () => { void reference("planning_issues", null, issueBody, 0, record.gap_id).catch(() => { issueBody.textContent = "Source issues are unavailable in this private context."; }); }); target.append(issues, issueBody);
            }
            target.append(text("p", record.explanation), text("p", `Required input: ${record.required_input}`), text("p", `When: ${titleCase(record.timing)}`, "state-note"));
            for (const evidenceId of record.evidence_ids) { const evidence = text("button", "Evidence details", "button button-quiet"); evidence.type = "button"; const evidenceBody = document.createElement("div"); evidence.addEventListener("click", () => { void reference("planning_evidence", evidenceId, evidenceBody).catch(() => { evidenceBody.textContent = "Evidence details are unavailable in this private context."; }); }); target.append(evidence, evidenceBody); }
          } else {
            if (record.description) target.append(text("p", record.description));
            if (record.source_content_state === "not_included") target.append(text("p", "Source content is not included; this is its protected reference and declared hash.", "state-note"));
            target.append(text("pre", JSON.stringify(record, null, 2), "notes-body"));
          }
        }
        for (const [label, next] of [["Previous references", offset > 0 ? Math.max(0, offset - 50) : null], ["Next references", info.next_offset]]) {
          if (next == null) continue;
          const button = text("button", label, "button button-quiet"); button.type = "button";
          button.addEventListener("click", () => { button.disabled = true; void reference(report, referenceId, target, next, gapGroupId).catch(() => { button.disabled = false; status.textContent = "Reference details could not be loaded."; }); }); target.append(button);
        }
      }
      for (const condition of h.conditions) {
        const item = document.createElement("details"); item.append(text("summary", `Resolve: ${titleCase(condition.replaceAll("-", " "))}`)); const target = text("div", "Open to view the required input and evidence."); item.append(target); let loaded = false;
        item.addEventListener("toggle", () => { if (item.open && !loaded) { loaded = true; void reference("planning_gaps", condition, target).catch(() => { loaded = false; target.textContent = "This gap is unavailable in the current private context."; }); } }); references.append(item);
      }
      content.append(references);
      content.append(text("p", `Unsupported collections: ${(answer.unsupported_collections || []).map(titleCase).join(", ")}.`, "state-note"));
      const sources = document.createElement("details"); sources.append(text("summary", "Per-source clocks and references")); const sourceBody = document.createElement("div"); sources.append(sourceBody); let sourcesLoaded = false;
      sources.addEventListener("toggle", () => { if (sources.open && !sourcesLoaded) { sourcesLoaded = true; void reference("planning_sources", null, sourceBody).catch(() => { sourcesLoaded = false; sourceBody.textContent = "Source provenance is unavailable."; }); } }); content.append(sources);
      for (const [field, reason] of Object.entries(h.unknowns)) content.append(text("p", `${titleCase(field)}: ${titleCase(reason)}`, "state-note"));
      content.append(text("p", `Source refresh: ${answer.refresh_state}.`, "state-note"));
      for (const [name, clock] of Object.entries(answer.source_clocks)) content.append(text("p", `${titleCase(name)}: ${clock.value || `Unknown (${titleCase(clock.unknown_reason)})`}`, "meta"));
      for (const limit of answer.limits) content.append(text("p", titleCase(limit), "meta"));
      const show = text("button", "Show dated events", "button button-quiet"); show.type = "button"; show.addEventListener("click", () => { show.disabled = true; void events(0, generation).catch(() => { status.textContent = "Event details could not be loaded. Retry the selected context."; show.disabled = false; }); }); content.append(show);
    } catch { if (generation === requestNumber) { content.replaceChildren(); detail.replaceChildren(); status.textContent = "This projection is unavailable in the current private session."; } }
  }
  contextSelect.addEventListener("change", () => { void load(); }); horizonSelect.addEventListener("change", () => { void load(); });
  void (async () => {
    try {
      const index = await request("planning_contexts");
      if (!section.isConnected) return;
      if (!index.contexts?.length) { status.textContent = "No verified dated projection is available. Accounting acceptance alone does not establish a forecast."; return; }
      projectionId = index.projection.id;
      for (const c of index.contexts) { const option = text("option", `${c.measure === "BANK_CASH_SUBSET" ? "Bank cash" : "Funding, not bank cash"} · ${titleCase(c.scenario_id)} · ${c.income_basis === "OBSERVED_BASE_ONLY" ? "Observed income" : "Modeled funding"} · ${c.opening_basis === "verified_unrestricted" ? "Verified opening" : "Hypothetical opening"} · ${c.plan_case_id}`); option.value = c.context_id; contextSelect.append(option); }
      controls.hidden = false; await load();
    } catch { status.textContent = "Dated projections are unavailable in the current private session."; }
  })();
  return section;
}

function financeChoicesCard(books) {
  const section = document.createElement("section"); section.className = "card finance-choices";
  section.append(text("h2", "Your finance choices"), text("p", "Save a goal or an explicit preference against this accepted snapshot. These choices do not change Books, schedule reviews, move money, or give anyone else access.", "state-note"));
  const identity = state.bootstrap?.member; const trust = state.bootstrap?.trust;
  if (!books?.publication_id || !identity || identity.authenticated_role !== "principal" || identity.viewing_as || identity.authenticated_id !== identity.id || trust?.actions_as !== trust?.reads_as) {
    section.append(text("p", "An accepted publication and your own private view are required.", "empty")); return section;
  }
  const endpoint = "/api/member/v1/finance/choices";
  const status = text("p", "Loading your saved choices…", "state-note"); status.setAttribute("role", "status");
  const list = document.createElement("ul"); list.className = "finance-goal-list";
  const actions = document.createElement("div"); actions.className = "button-row";
  const add = text("button", "Add a goal", "button button-primary"); add.type = "button"; add.disabled = true;
  const prefer = text("button", "Choose a preference", "button button-secondary"); prefer.type = "button"; prefer.disabled = true;
  const refresh = text("button", "Refresh choices", "button button-quiet"); refresh.type = "button";
  actions.append(add, prefer, refresh); section.append(status, list, actions);
  let current = null; let busy = false;
  const preferenceOptions = {
    grocery_substitution: ["ask_before_substitution", "keep_menu", "compare_unit_prices"],
    service_time_tradeoff: ["ask_about_time_value", "minimize_cost", "prioritize_time"],
    reserve_policy: ["ask_before_using_reserve", "review_chosen_floor", "preserve_chosen_floor"],
    support_intent: ["discuss_before_committing", "prefer_gift", "prefer_documented_loan", "optional_reserve"],
    review_cadence: ["on_request", "monthly", "annually"],
    decision_priority: ["compare_cost_and_risk", "prioritize_flexibility", "ask_each_time"],
  };
  function select(values, prompt) {
    const control = document.createElement("select"); control.required = true;
    const empty = text("option", prompt); empty.value = ""; control.append(empty);
    for (const value of values) { const option = text("option", titleCase(value)); option.value = value; control.append(option); }
    return control;
  }
  async function load() {
    add.disabled = true; prefer.disabled = true;
    try {
      current = await api(`${endpoint}?book_id=${encodeURIComponent(books.book_id)}`);
      list.replaceChildren();
      for (const item of current.items || []) {
        const record = item.record; const row = document.createElement("li");
        if (item.kind === "goal") {
          row.append(text("strong", record.label || titleCase(record.kind)), text("p", `${titleCase(record.status)} · ${record.target_minor == null ? "Target not selected" : new Intl.NumberFormat(undefined, { style: "currency", currency: record.currency }).format(record.target_minor / (10 ** new Intl.NumberFormat(undefined, { style: "currency", currency: record.currency }).resolvedOptions().maximumFractionDigits))}${record.target_date ? ` · ${record.target_date}` : ""}`));
          const controls = document.createElement("div"); controls.className = "button-row";
          for (const [value, label] of (record.status === "selected" ? [["paused", "Pause"], ["completed", "Mark complete"]] : [["selected", "Resume"]])) {
            const button = text("button", label, "button button-quiet"); button.type = "button";
            button.addEventListener("click", () => { if (busy) return; const body = { request_id: crypto.randomUUID(), book_id: books.book_id, publication_id: current.publication.publication_id, expected_event_id: item.event_id, label: record.label || titleCase(record.kind), kind: record.kind, status: value, target_minor: record.target_minor, currency: record.currency, target_date: record.target_date, priority: record.priority }; void save(`/goals/${encodeURIComponent(record.goal_id)}`, body); }); controls.append(button);
          }
          row.append(controls);
        } else row.append(text("strong", titleCase(record.dimension)), text("p", titleCase(record.selected_option_id)));
        const provenance = document.createElement("details"); provenance.append(text("summary", "Saved choice evidence"), text("p", `Publication ${record.scope.baseline_publication_id} · recorded ${record.recorded_at}`));
        const evidence = text("button", "View recorded choice", "link-button"); evidence.type = "button"; evidence.addEventListener("click", () => showEvidence(item.event_id)); provenance.append(evidence); row.append(provenance); list.append(row);
      }
      status.textContent = current.can_record ? ((current.items || []).length ? "Your explicit choices are saved privately." : "No explicit choices have been saved yet.") : "An accepted publication is required before saving a choice.";
      if (current.next_offset != null) status.textContent += " More choices exist beyond this page.";
      add.disabled = !current.can_record; prefer.disabled = !current.can_record;
    } catch (error) { status.textContent = `${error.message} Saved choices could not be checked.`; }
  }
  async function save(path, body, submit = null) {
    if (busy) return; busy = true; if (submit) submit.disabled = true; status.textContent = "Saving your explicit choice…";
    const notice = submit?.closest("form")?.querySelector(".choice-save-status"); if (notice) notice.textContent = status.textContent;
    try {
      await api(endpoint + path, { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify(body) });
      closeActionDialog(); await load();
    } catch (error) { status.textContent = `${error.message} Save is unconfirmed. Retry this same choice or refresh to inspect saved choices.`; if (notice) notice.textContent = status.textContent; }
    finally { busy = false; if (submit) submit.disabled = false; }
  }
  add.addEventListener("click", () => {
    if (!current?.can_record || busy) return;
    const form = document.createElement("form"); form.className = "dialog-form";
    const label = document.createElement("input"); label.required = true; label.maxLength = 120; label.addEventListener("input", () => label.setCustomValidity(""));
    const kind = select(["reserve", "purchase", "education", "retirement", "support", "debt"], "Choose a goal type");
    const amount = document.createElement("input"); amount.inputMode = "decimal"; amount.placeholder = "Leave blank if undecided"; amount.pattern = "[0-9]{1,10}(\\.[0-9]{1,2})?";
    const due = document.createElement("input"); due.type = "date";
    const priority = select(["unranked", "essential", "preferred", "optional"], "Choose a priority");
    const submit = text("button", "Save my goal", "button button-primary"); submit.type = "submit";
    const requestId = crypto.randomUUID(); const goalId = crypto.randomUUID(); let pending = null;
    const notice = text("p", "", "choice-save-status"); notice.setAttribute("role", "status");
    form.append(field("Goal name", label), field("Goal type", kind), field("Target in USD (optional)", amount), field("Target date (optional)", due), field("Priority", priority), submit, notice);
    form.addEventListener("submit", (event) => {
      event.preventDefault(); if (busy) return;
      if (!pending) {
        if (!label.value.trim()) { label.setCustomValidity("Enter a goal name."); label.reportValidity(); return; }
        label.setCustomValidity("");
        const parts = amount.value.trim().split("."); const minor = amount.value.trim() ? Number(parts[0]) * 100 + Number((parts[1] || "").padEnd(2, "0")) : null;
        pending = { request_id: requestId, book_id: books.book_id, publication_id: current.publication.publication_id, label: label.value.trim(), kind: kind.value, status: "selected", target_minor: minor, currency: "USD", target_date: due.value || null, priority: priority.value };
        for (const control of form.querySelectorAll("input,select")) control.disabled = true;
      }
      void save(`/goals/${goalId}`, pending, submit);
    }); openActionDialog("Save a finance goal", form);
  });
  prefer.addEventListener("click", () => {
    if (!current?.can_record || busy) return;
    const form = document.createElement("form"); form.className = "dialog-form";
    const dimension = select(Object.keys(preferenceOptions), "Choose a topic"); const option = select([], "Choose your preference");
    dimension.addEventListener("change", () => { const replacement = select(preferenceOptions[dimension.value] || [], "Choose your preference"); option.replaceChildren(...replacement.childNodes); });
    const submit = text("button", "Save my preference", "button button-primary"); submit.type = "submit";
    const requestId = crypto.randomUUID(); let pending = null; let selectedDimension = null;
    const notice = text("p", "", "choice-save-status"); notice.setAttribute("role", "status");
    form.append(field("Topic", dimension), field("Your explicit choice", option), submit, notice);
    form.addEventListener("submit", (event) => {
      event.preventDefault(); if (busy) return;
      if (!pending) { selectedDimension = dimension.value; const prior = current.items.find((item) => item.kind === "preference" && item.record.dimension === selectedDimension); pending = { request_id: requestId, book_id: books.book_id, publication_id: current.publication.publication_id, expected_event_id: prior?.event_id || null, selected_option_id: option.value }; dimension.disabled = true; option.disabled = true; }
      void save(`/preferences/${encodeURIComponent(selectedDimension)}`, pending, submit);
    }); openActionDialog("Save an explicit preference", form);
  });
  refresh.addEventListener("click", () => { if (!busy) void load(); }); void load(); return section;
}

function financeTransactions(cockpit) {
  const section = document.createElement("section"); section.className = "card finance-transactions"; section.append(text("h2", "Recent money movement"));
  const flows = cockpit.recent_flows || [];
  if (!flows.length) { section.append(text("p", "No visible transactions in this scope.", "empty")); return section; }
  const list = document.createElement("ul"); list.className = "finance-transaction-list";
  for (const flow of flows) {
    const row = document.createElement("li"); const body = document.createElement("div");
    body.append(text("strong", flow.merchant_name || flow.memo || flow.notes || titleCase(flow.kind || "Money movement")), text("small", `${flow.occurred_at || "Date unavailable"} · ${flow.assigned_category || flow.category || flow.provider_category || "Uncategorized"}`));
    const signed = flow.kind === "received" ? Number(flow.amount_minor || 0) : -Math.abs(Number(flow.amount_minor || 0));
    row.append(body, text("strong", moneyMinor(signed), signed < 0 ? "finance-outflow" : "finance-inflow")); list.append(row);
  }
  section.append(list); return section;
}

function financeAccounts(cockpit) {
  const section = document.createElement("section"); section.className = "card"; section.append(text("h2", "Observed asset and liability inputs"), text("p", `Listed net value: ${moneyMinor(cockpit.summary.observed_listed_net_minor)}. Coverage is unverified; a reconciled balance sheet is unavailable.`, "state-note"));
  if (!(cockpit.accounts || []).length) { section.append(text("p", "No visible provider accounts. Workbook asset and liability assumptions still participate in the total.", "empty")); return section; }
  const list = document.createElement("ul"); list.className = "finance-account-list"; for (const account of cockpit.accounts) { const row = document.createElement("li"); const body = document.createElement("div"); const liability = ["credit", "loan"].includes(account.account_type); body.append(text("strong", account.display_name), text("small", `${account.institution_name} · ${liability ? "Liability" : "Asset"} · ${titleCase(account.account_type)} · •••• ${account.last4 || "—"}`)); const signed = account.current_balance_minor == null ? null : liability ? -Math.abs(Number(account.current_balance_minor)) : Number(account.current_balance_minor); row.append(body, text("strong", moneyMinor(signed), liability ? "finance-outflow" : "")); list.append(row); } section.append(list); return section;
}

function financeHealth(cockpit) {
  const health = cockpit.data_health || {}; const workbook = cockpit.workbook;
  const section = document.createElement("section"); section.className = "card finance-health"; section.append(text("h2", "Source and model health"));
  const facts = document.createElement("dl"); facts.className = "finance-health-grid"; for (const [label, value] of [["State", titleCase(health.state)], ["Transactions", health.transaction_count || 0], ["Accounts", health.account_count || 0], ["Accounts needing attention", health.unhealthy_account_count || 0], ["Pending", health.pending_count || 0], ["Uncategorized", health.uncategorized_count || 0], ["Non-USD rows excluded", health.unsupported_currency_count || 0], ["Latest source", health.latest_source_at ? new Date(health.latest_source_at).toLocaleString() : "No source watermark"], ["Workbook", workbook ? `${titleCase(workbook.validation_state)} · schema ${workbook.workbook_schema_version}` : "Not published"]]) facts.append(text("dt", label), text("dd", value)); section.append(facts);
  if (workbook?.validation_failures?.length) { const list = document.createElement("ul"); list.className = "settings-rule-list"; for (const failure of workbook.validation_failures) list.append(text("li", failure)); section.append(text("h3", "Workbook checks"), list); }
  for (const [report, status] of Object.entries(cockpit.reports || {})) section.append(text("h3", titleCase(report)), text("p", `${titleCase(status.state)} · ${status.reason}`, "state-note"));
  section.append(text("p", "Accepted Books publications own accounting and planning. Provider observations alone do not release reports; the event log preserves accepted provenance and replay.", "review-state")); return section;
}

function financeBooksOverview(books) {
  const hero = document.createElement("section"); hero.className = "hero-card finance-hero";
  const copy = document.createElement("div");
  copy.append(text("p", "YOUR HOUSEHOLD BOOKS", "eyebrow"), text("h2", "Your financial picture", "action-title"), text("p", "Review the published plan, explore dated cash and funding estimates, and keep your choices alongside their evidence.", "brief-summary"));
  copy.append(text("p", "Books owns these outputs. A planning estimate is not a verified bank balance or permission to spend.", "state-note"));
  const facts = document.createElement("dl"); facts.className = "finance-hero-stats";
  for (const [label, value] of [["Publication accepted", books.as_of || "Unknown"], ["Source observed", books.source_clock_summary?.oldest_observed_at || "Unknown"], ["Current freshness", "Not established"], ["Cash safety", "Not established"]]) {
    const fact = document.createElement("div"); fact.append(text("dt", label), text("dd", value)); facts.append(fact);
  }
  hero.append(copy, facts); return hero;
}

function renderFinance(data) {
  const root = $("#surface"); root.replaceChildren(); $("#mode-label").textContent = "Private · deterministic planning · no bank writes";
  const cockpit = data.cockpit || { summary: {}, budgets: [], scenarios: [], accounts: [], data_health: {} };
  const requested = new URLSearchParams(location.search).get("view"); if (FINANCE_VIEWS.some(([value]) => value === requested)) state.financeView = requested;
  if (cockpit.accepted_books?.publication_id) {
    // Accepted Books is the finance authority for this owner. Legacy workbook
    // scenarios and provider observations must not appear as a second plan.
    $("#mode-label").textContent = "Private · Books outputs · source assumptions preserved";
    root.append(freshnessStrip(data), financeBooksOverview(cockpit.accepted_books), financePlanningCard(cockpit.accepted_books), financeChoicesCard(cockpit.accepted_books), financeAcceptedBooks(cockpit));
    return;
  }
  root.append(freshnessStrip(data), financeAcceptedBooks(cockpit), financePlanningCard(cockpit.accepted_books), financeHero(cockpit), financeControls());
  if (state.financeView === "overview") root.append(financeKpis(cockpit), financeCashFlow(cockpit), financeBudgets(cockpit), financeScenarios(cockpit), financeGoals(cockpit), financeChoicesCard(cockpit.accepted_books));
  else if (state.financeView === "cashflow") root.append(financeCashFlow(cockpit), financeScenarios(cockpit));
  else if (state.financeView === "spending") root.append(financeBudgets(cockpit), financeTransactions(cockpit));
  else if (state.financeView === "scenarios") root.append(financeScenarios(cockpit), financeGoals(cockpit), financeChoicesCard(cockpit.accepted_books));
  else if (state.financeView === "networth") root.append(financeAccounts(cockpit));
  else root.append(financeHealth(cockpit));
}

const SETTINGS_SECTIONS = [
  ["overview", "Overview"],
  ["identity", "My identity"],
  ["behavior", "CoS behavior"],
  ["source-context", "Source intake & context"],
  ["cos-work", "How my CoS works"],
  ["cos-replies", "CoS replies"],
  ["capabilities", "Capabilities"],
  ["connections", "Connections"],
  ["apple-services", "Apple permissions"],
  ["bridges", "Devices & bridges"],
  ["learning-trust", "Learning & trust"],
  ["privacy", "Household & privacy"],
  ["system", "Safety & system"],
  ["admin-requests", "Admin requests"],
];

function settingsNav() {
  const nav = document.createElement("nav"); nav.className = "settings-nav"; nav.setAttribute("aria-label", "Settings sections");
  for (const [value, label] of SETTINGS_SECTIONS) {
    const button = text("button", label, "settings-nav-button"); button.type = "button";
    button.setAttribute("aria-pressed", String(state.settingsSection === value));
    button.addEventListener("click", () => { if (state.sourceSettingsPending || state.sourceSettingsRefreshRequired) return; state.settingsSection = value; replaceQuery({ section: value }); renderSettings(state.surface); });
    nav.append(button);
  }
  return nav;
}

function identitySummary(identity, compact = false) {
  const section = document.createElement("section"); section.className = compact ? "settings-identity compact" : "card settings-identity";
  const heading = document.createElement("div"); heading.append(text("p", "SIGNED-IN IDENTITY", "eyebrow"), text("h2", identity.authenticated_display_name));
  const status = text("span", identity.identity_bound ? "Identity bound" : "Binding incomplete", `pill state-label state-${identity.identity_bound ? "healthy" : "blocked"}`);
  section.append(heading, status);
  const facts = document.createElement("dl"); facts.className = "settings-facts";
  for (const [label, value] of [["Role", titleCase(identity.authenticated_role)], ["Authenticated through", identity.authentication_method], ["Reading as", identity.view_display_name], ["Actions execute as", identity.authenticated_display_name]]) facts.append(text("dt", label), text("dd", value));
  section.append(facts);
  if (identity.viewing_as) section.append(text("p", `You are viewing ${identity.view_display_name}. Settings changes are disabled; actions still belong to ${identity.authenticated_display_name}.`, "review-state"));
  return section;
}

function settingsHero(data) {
  const control = data.control; const identity = control.identity;
  const hero = document.createElement("section"); hero.className = "hero-card settings-hero";
  const copy = document.createElement("div"); copy.append(text("p", "MEMBER CONTROL PLANE", "eyebrow"), text("h2", `You’re signed in as ${identity.authenticated_display_name}.`, "action-title"), text("p", "See what AdministrateMe can access, what is actually running, and what it is allowed to change. Every control below follows explicit rules and leaves an audit receipt.", "brief-summary"));
  const stats = document.createElement("dl"); stats.className = "settings-hero-stats";
  const healthy = control.connections.filter((item) => item.runtime === "running").length;
  for (const [label, value] of [["Needs attention", control.needs_attention.length], ["Your connected accounts", control.connections.filter((item) => item.setup === "connected").length], ["Your syncs running", healthy], ["Writes execute", data.observation.active ? "No" : "Gated"]]) { const item = document.createElement("div"); item.append(text("dt", label), text("dd", value)); stats.append(item); }
  hero.append(copy, stats); return hero;
}

function attentionCard(items) {
  const section = document.createElement("section"); section.className = "card settings-attention"; section.append(text("h2", "Needs attention"));
  if (!items.length) { section.append(text("p", "Nothing needs intervention. Unknown state will still remain visible in Connections.", "empty compact-empty")); return section; }
  const list = document.createElement("ul"); list.className = "settings-attention-list";
  for (const item of items) { const row = document.createElement("li"); row.append(text("span", titleCase(item.severity), `pill state-label state-${item.severity}`)); const body = document.createElement("div"); body.append(text("strong", item.title), text("small", item.detail)); const open = text("button", "Review", "link-button"); open.type = "button"; open.addEventListener("click", () => { state.settingsSection = item.section; replaceQuery({ section: item.section }); renderSettings(state.surface); }); row.append(body, open); list.append(row); }
  section.append(list); return section;
}

function connectionAxis(label, value, stateClass = value) {
  const axis = document.createElement("div"); axis.className = "connection-axis"; axis.append(text("span", label), text("strong", titleCase(value), `state-label state-${String(stateClass).replaceAll("_", "-")}`)); return axis;
}

function permissionList(title, values) {
  const box = document.createElement("div"); box.className = "permission-box"; box.append(text("span", title));
  if (!values.length) box.append(text("strong", "None"));
  else { const list = document.createElement("ul"); for (const value of values) list.append(text("li", titleCase(value))); box.append(list); }
  return box;
}

function connectionCard(connection, data) {
  const section = document.createElement("article"); section.className = "card connection-card";
  const head = document.createElement("div"); head.className = "connection-heading";
  const copy = document.createElement("div"); copy.append(text("p", connection.product, "eyebrow"), text("h2", connection.label), text("small", `${connection.provider_label} · ${connection.account_hint} · ${titleCase(connection.workspace)}`)); head.append(copy, text("span", titleCase(connection.deployment), "pill pill-lavender")); section.append(head);
  const axes = document.createElement("div"); axes.className = "connection-axes"; axes.append(connectionAxis("Setup", connection.setup), connectionAxis("Runtime", connection.runtime));
  const credentialState = connection.permissions.credential_reference_present ? "stored" : "missing"; axes.append(connectionAxis("Credential ref", credentialState, credentialState === "stored" ? "healthy" : "blocked"), connectionAxis("Safety", connection.safety.observation_only ? "observation only" : connection.safety.writes_execute ? "writes gated" : "read only", connection.safety.observation_only ? "observation-only" : "healthy")); section.append(axes);
  const permissions = document.createElement("div"); permissions.className = "permission-grid"; permissions.append(permissionList("Reviewed read operations", connection.permissions.read), permissionList("Reviewed write operations", connection.permissions.write)); section.append(permissions);
  const note = !connection.safety.human_gate_required ? "This connection has no reviewed provider write capability." : connection.safety.observation_only ? "Observation mode suppresses provider changes even when a reviewed provider write capability exists." : "Writes still require member confirmation, governance, rate limits, privacy checks, and independent approval."; section.append(text("p", note, "state-note"));
  const inactiveCopy = { cos_forwarded_intake: "Personal source syncing is off. Forward selected messages to your CoS or use Add for review. CoS email and messages are shown separately.", acquisition_restricted: "Only selected sources are enabled right now. Sync controls are unavailable for this connection.", configuration_disabled: "This connection is disabled in the current configuration. Sync controls are unavailable." };
  if (inactiveCopy[connection.inactive_reason]) section.append(text("p", inactiveCopy[connection.inactive_reason], "state-note"));
  if (connection.error) section.append(text("p", `${titleCase(connection.error.severity)} ${titleCase(connection.error.kind)} error${connection.error.occurred_at ? ` · ${new Date(connection.error.occurred_at).toLocaleString()}` : ""}.`, "review-state"));
  const actions = document.createElement("div"); actions.className = "button-row";
  for (const action of connection.actions || []) { const button = text("button", action === "pause" ? "Pause sync" : "Resume sync", action === "pause" ? "button button-quiet" : "button button-primary"); button.type = "button"; button.disabled = !data.control.identity.can_mutate; button.addEventListener("click", async () => { button.disabled = true; setBanner(`${action === "pause" ? "Pausing" : "Resuming"} ${connection.label}…`); try { const result = await api(`/api/member/v1/settings/connections/${encodeURIComponent(connection.id)}`, { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify({ action, expected_revision: data.control.revision, request_id: crypto.randomUUID() }) }); setBanner(`${connection.label} is ${result.state}. Runtime reconciliation was audited.`); await reloadActiveSurface(); } catch (error) { setBanner(`${error.message} The connection was not changed.`); button.disabled = false; } }); actions.append(button); }
  const technical = document.createElement("details"); technical.className = "settings-technical"; technical.append(text("summary", "Technical status")); const facts = document.createElement("dl"); facts.className = "settings-facts"; for (const [label, value] of [["Connection ID", connection.id], ["Provider", connection.provider], ["Last cursor evidence", connection.last_sync_at ? new Date(connection.last_sync_at).toLocaleString() : "No cursor evidence yet"]]) facts.append(text("dt", label), text("dd", value)); technical.append(facts); section.append(actions, technical); return section;
}

function serviceChannelsCard(data) {
  const section = document.createElement("section"); section.className = "card settings-service-channels";
  section.append(text("h2", "CoS email and messages"), text("p", "Send or forward selected items to these accounts for review. They are separate from your personal connections."));
  const channels = data.control.service_channels?.channels || [];
  if (!channels.length) { section.append(text("p", "No dedicated CoS channel is configured.", "empty compact-empty")); return section; }
  const grid = document.createElement("div"); grid.className = "settings-card-grid";
  for (const channel of channels) {
    const card = document.createElement("article"); card.className = "card settings-service-channel";
    card.append(text("h3", channel.provider === "gmail" ? "CoS email" : ["apple_imessage", "imessage"].includes(channel.provider) ? "CoS iMessage" : "CoS channel"));
    const facts = document.createElement("dl"); facts.className = "settings-facts";
    const sending = data.observation.active ? "Paused for observation" : channel.sending_enabled ? "Enabled in setup · approval required" : "Off";
    for (const [label, value] of [["Account setup", channel.enrolled ? "Enrolled" : "Not enrolled"], ["Saved status", titleCase(channel.state)], ["Connection status", "Not available here"], ["Replies", sending]]) facts.append(text("dt", label), text("dd", value));
    card.append(facts);
    card.append(text("p", "Account setup does not confirm a current connection or recent message receipt.", "state-note"));
    card.append(text("p", "Replies require an allowed recipient and approval before sending.", "state-note"));
    grid.append(card);
  }
  section.append(grid); return section;
}

const GOOGLE_SERVICE_IDS = ["gmail", "calendar", "tasks"];

function googleConnectionsSettings(data) {
  const section = document.createElement("section"); section.className = "google-connections";
  const intro = document.createElement("div"); intro.className = "card";
  intro.append(text("p", "YOUR GOOGLE ACCOUNTS", "eyebrow"), text("h2", "Connect your Google services"), text("p", "Bring your Gmail, calendars, and tasks into your private AdministrateMe console. Choose reading and writing separately for each service."), text("p", "Personal inbox obligation extraction stays off. Your CoS permissions and what you share with another member remain separate. Each external change still needs the required approvals.", "state-note"));
  const cosLink = text("a", "Review my CoS permissions", "link-button"); cosLink.href = "/member/settings?section=cos-work"; intro.append(cosLink); section.append(intro);
  const identity = data.control.identity;
  const own = identity.can_mutate && identity.authenticated_role === "principal" && Boolean(identity.authenticated_member_id) && !identity.viewing_as && identity.authenticated_member_id === identity.view_member_id && identity.actions_as === identity.authenticated_member_id && identity.reads_as === identity.authenticated_member_id;
  if (!own) { intro.append(text("p", "Return to your own private view to connect Google or change its permissions.", "review-state")); return section; }
  const endpoint = "/api/member/v1/settings/google-connections";
  const pendingKey = `administrateme:google-connection-pending:${identity.authenticated_member_id}`;
  const requestPattern = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
  let pending = null; let busy = false; let loaded = false; let model = null; let authorization = null; let requestState = null; let polls = 0; let pollTimer = null;
  const callbackRequest = new URLSearchParams(location.search).get("google_request");
  try {
    pending = JSON.parse(sessionStorage.getItem(pendingKey) || "null");
    if (callbackRequest && requestPattern.test(callbackRequest) && !pending) {
      pending = { request_id: callbackRequest }; sessionStorage.setItem(pendingKey, JSON.stringify(pending));
    }
  } catch { pending = { unknown: true }; }
  const list = document.createElement("div"); list.className = "google-account-list";
  const add = document.createElement("div"); add.className = "google-add-account";
  const status = text("p", "Checking your Google connections…", "state-note"); status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite"); status.tabIndex = -1;
  const refresh = text("button", "Check Google connection status", "button button-secondary"); refresh.type = "button";
  const continueLink = text("a", "Continue to Google", "button button-primary"); continueLink.hidden = true; continueLink.referrerPolicy = "no-referrer";
  const actions = document.createElement("div"); actions.className = "button-row"; actions.append(continueLink, refresh);
  section.append(list, add, status, actions);
  function savePending(value) { if (value) sessionStorage.setItem(pendingKey, JSON.stringify(value)); else sessionStorage.removeItem(pendingKey); pending = value; }
  function serviceSelections(values) { return Object.fromEntries(GOOGLE_SERVICE_IDS.map((id) => [id, { read: values?.[id]?.read === true, write: values?.[id]?.write === true }])); }
  function safeAuthorization(value) {
    if (!value?.authorization_url || !value.expires_at || Date.parse(value.expires_at) <= Date.now() || !Number.isFinite(Date.parse(value.expires_at))) return null;
    try { const url = new URL(value.authorization_url); return url.protocol === "https:" && url.hostname === "accounts.google.com" && !url.port && !url.username && !url.password && url.pathname === "/o/oauth2/v2/auth" ? { href: url.href, expires_at: value.expires_at } : null; } catch { return null; }
  }
  function serviceLabel(id) { return model?.services?.find((service) => service.id === id)?.label || { gmail: "Gmail", calendar: "Google Calendar", tasks: "Google Tasks" }[id]; }
  function permissionChoices(values, prefix, locked) {
    const group = document.createElement("fieldset"); group.className = "google-service-choices"; group.append(text("legend", "Choose service access"));
    const controls = new Map();
    for (const id of GOOGLE_SERVICE_IDS) {
      const service = model.services.find((item) => item.id === id); if (!service) continue;
      const row = document.createElement("div"); row.className = "google-service-choice"; row.dataset.service = id;
      const copy = document.createElement("div"); copy.append(text("strong", service.label), text("small", service.read_description));
      const choices = document.createElement("div"); choices.className = "google-access-toggles";
      const read = document.createElement("input"); read.type = "checkbox"; read.name = `${prefix}-${id}-read`; read.checked = values[id]?.read === true;
      const write = document.createElement("input"); write.type = "checkbox"; write.name = `${prefix}-${id}-write`; write.checked = values[id]?.write === true;
      const readLabel = document.createElement("label"); readLabel.append(read, text("span", "Read")); read.setAttribute("aria-label", `${service.label} Read`);
      const writeLabel = document.createElement("label"); writeLabel.append(write, text("span", "Write")); write.setAttribute("aria-label", `${service.label} Write`);
      const note = text("small", service.write_description || "Changes still require approval.", "google-write-description");
      function update() { read.disabled = locked; write.disabled = locked || !read.checked; if (!read.checked) write.checked = false; }
      read.addEventListener("change", update); update(); choices.append(readLabel, writeLabel); row.append(copy, choices, note); group.append(row); controls.set(id, { read, write });
    }
    return { node: group, selected: () => Object.fromEntries(GOOGLE_SERVICE_IDS.map((id) => [id, { read: controls.get(id)?.read.checked === true, write: controls.get(id)?.write.checked === true }])) };
  }
  function render() {
    list.replaceChildren(); add.replaceChildren();
    const locked = busy || !loaded || Boolean(pending);
    refresh.disabled = busy;
    continueLink.hidden = busy || !authorization || !pending || Date.parse(authorization.expires_at) <= Date.now();
    if (!continueLink.hidden) continueLink.href = authorization.href; else continueLink.removeAttribute("href");
    if (!loaded || !model) return;
    if (!model.available) add.append(text("p", model.reason || "Google sign-in is not available yet. Check again after setup is completed.", "review-state"));
    for (const account of model.accounts || []) {
      const cardNode = document.createElement("article"); cardNode.className = "card google-account"; cardNode.dataset.accountId = account.id;
      cardNode.append(text("h3", account.email), text("p", `${titleCase(account.workspace)} account`, "state-note"));
      const states = document.createElement("dl"); states.className = "settings-facts google-service-status";
      for (const id of GOOGLE_SERVICE_IDS) {
        const service = account.services[id]; if (!service) continue;
        states.append(text("dt", serviceLabel(id)), text("dd", `Read ${service.read ? "on" : "off"} · Write ${service.write ? "on · approval required" : "off"} · Setup: ${titleCase(service.setup || "unknown")} · Sync: ${titleCase(service.runtime || "unknown")}`));
      }
      cardNode.append(states, text("p", account.cos_context_access ? "CoS has permission to use your private AdministrateMe context. Sharing with another member is still separate." : "To talk with CoS about this account, review your private CoS permissions.", "state-note"));
      const form = document.createElement("form"); form.className = "google-account-permissions";
      const choices = permissionChoices(serviceSelections(account.services), account.id, locked);
      const save = text("button", "Review Google permissions", "button button-primary"); save.type = "submit"; save.disabled = locked;
      form.append(choices.node, save); form.addEventListener("submit", (event) => { event.preventDefault(); if (!locked && !busy && !pending) review({ account, services: choices.selected() }); }); cardNode.append(form); list.append(cardNode);
    }
    const details = document.createElement("details"); details.className = "card google-add-form"; details.open = !(model.accounts || []).length;
    details.append(text("summary", "Add a Google account"));
    const form = document.createElement("form"); form.className = "google-connection-form";
    const email = document.createElement("input"); email.type = "email"; email.name = "google-account-email"; email.required = true; email.autocomplete = "email"; email.disabled = locked || !model.available;
    const workspace = selectControl([{ value: "personal", label: "Personal" }, { value: "work", label: "Work" }], "personal", ""); workspace.disabled = locked || !model.available;
    const fields = document.createElement("div"); fields.className = "google-account-fields"; fields.append(field("Google account email", email), field("Account use", workspace));
    const choices = permissionChoices({ gmail: { read: true }, calendar: { read: true }, tasks: { read: false } }, "new", locked || !model.available);
    const submit = text("button", "Review Google access", "button button-primary"); submit.type = "submit"; submit.disabled = locked || !model.available;
    form.append(fields, text("p", "Sign in to this exact Google account on the next screen. Google will ask you to approve only your selected services.", "state-note"), choices.node, submit);
    form.addEventListener("submit", (event) => { event.preventDefault(); if (busy || pending || !model.available || !email.checkValidity()) return; if (model.accounts.some((account) => account.email.toLowerCase() === email.value.trim().toLowerCase())) { status.textContent = "This Google account is already connected. Use its permission controls above."; status.focus(); return; } const services = choices.selected(); if (!Object.values(services).some((service) => service.read)) { status.textContent = "Choose at least one service to read before connecting Google."; return; } review({ expected_account_id: email.value.trim().toLowerCase(), workspace: workspace.value, services }); }); details.append(form); add.append(details);
  }
  async function reconcile() {
    if (busy) return;
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
    busy = true; authorization = null; requestState = null; render();
    let message = "Your Google permissions and each service’s sync status are shown above."; let completion = null;
    try {
      if (pending?.request_id) {
        const result = await api(`${endpoint}/requests/${encodeURIComponent(pending.request_id)}`);
        if (result.request_id !== pending.request_id) throw new Error("This response did not match your saved Google request.");
        requestState = result.state;
        if (result.state === "configured" && result.account_id) { completion = result; }
        else if (["failed", "cancelled", "expired"].includes(result.state)) { savePending(null); replaceQuery({ google_request: null }); message = result.message || `Google setup ${result.state}. Review your choices before trying again.`; }
        else { authorization = safeAuthorization(result); message = result.message || (authorization ? "Your choices are ready. Continue to Google to sign in and approve this account." : "Your Google request is still pending. Check its status before starting another request."); }
      } else if (pending) message = "Your previous Google request cannot be confirmed in this browser. Have the saved request reviewed before making another change.";
      model = await api(endpoint); loaded = true;
      if (!Array.isArray(model.services) || !Array.isArray(model.accounts)) throw new Error("Google connection details are incomplete.");
      if (completion) {
        const account = model.accounts.find((item) => item.id === completion.account_id);
        const sameAccount = account && (!pending.account_id || pending.account_id === account.id) && (!pending.body?.expected_account_id || pending.body.expected_account_id === account.email.toLowerCase());
        if (!sameAccount) throw new Error("The confirmed Google account did not match your saved request.");
        savePending(null); replaceQuery({ google_request: null }); message = "Your Google permission choice was saved. Check each service’s sync status below; account setup alone does not confirm a successful sync.";
      }
      status.textContent = message;
    } catch (error) { loaded = false; authorization = null; status.textContent = `${error.message} Google connection status is unconfirmed. Changes remain locked; check status again.`; }
    finally {
      busy = false; render();
      if (pending && requestState === "authorizing" && loaded && polls < 6) {
        polls += 1; pollTimer = setTimeout(() => { if (section.isConnected && !busy) void reconcile(); }, 2000);
      }
    }
  }
  async function submitChange(change) {
    if (busy || pending || !loaded) return;
    try { savePending(change); } catch { status.textContent = "This browser cannot retain your request reference. No Google permission change was requested."; return; }
    busy = true; authorization = null; render(); closeActionDialog(); status.textContent = "Saving your selected Google access…";
    try {
      const result = await api(change.path, { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify(change.body) });
      if (result.request_id === change.request_id) authorization = safeAuthorization(result);
    } catch (error) {
      if (error.status === 409 && error.body?.detail?.code === "stale_revision") { savePending(null); setBanner("Google permissions changed in another window. Review the current choices before saving again."); }
      else if (error.status === 409 && error.body?.detail?.code === "account_already_connected") { savePending(null); setBanner("This Google account is already connected. Review its current permission controls."); }
      // Never replay an uncertain POST. The own-member, immutable request GET
      // restores the original consent link or confirms its terminal result.
    } finally { busy = false; await reconcile(); if (section.isConnected) (continueLink.hidden ? status : continueLink).focus(); }
  }
  function review(choice) {
    if (busy || pending || !loaded) return;
    const content = document.createElement("form"); content.className = "dialog-form google-permission-confirmation";
    content.append(text("p", `${identity.authenticated_display_name} · ${choice.account?.email || choice.expected_account_id} · ${titleCase(choice.account?.workspace || choice.workspace)}`));
    const summary = document.createElement("ul"); for (const id of GOOGLE_SERVICE_IDS) { const access = choice.services[id]; summary.append(text("li", `${serviceLabel(id)}: ${access.read ? access.write ? "Read and write" : "Read only" : "Off"}`)); } content.append(summary, text("p", "Google may ask for consent if more access is needed. Reading enables private sync; writing permits approved changes only. This does not share your information with another member or enable automatic obligation extraction.", "state-note"));
    const cancel = text("button", "Go back", "button button-quiet"); cancel.type = "button"; cancel.addEventListener("click", closeActionDialog);
    const confirm = text("button", choice.account ? "Save Google permissions" : "Prepare Google sign-in", "button button-primary"); confirm.type = "submit"; content.append(cancel, confirm);
    content.addEventListener("submit", (event) => { event.preventDefault(); confirm.disabled = true; const requestId = crypto.randomUUID(); const body = choice.account ? { request_id: requestId, expected_revision: choice.account.revision, services: choice.services } : { request_id: requestId, expected_account_id: choice.expected_account_id, workspace: choice.workspace, services: choice.services }; void submitChange({ request_id: requestId, ...(choice.account ? { account_id: choice.account.id } : {}), path: choice.account ? `${endpoint}/${encodeURIComponent(choice.account.id)}/permissions` : `${endpoint}/authorize`, body }); });
    openActionDialog(choice.account ? "Review Google permission changes" : "Review Google sign-in", content);
  }
  continueLink.addEventListener("click", (event) => { if (busy || !authorization || Date.parse(authorization.expires_at) <= Date.now()) { event.preventDefault(); authorization = null; status.textContent = "This Google sign-in link expired. Check your request status before continuing."; render(); } });
  refresh.addEventListener("click", () => { void reconcile(); }); void reconcile(); return section;
}

function renderConnections(data) {
  const fragment = document.createDocumentFragment(); fragment.append(googleConnectionsSettings(data), serviceChannelsCard(data), appleServicePermissionLink()); const intro = document.createElement("section"); intro.className = "state-card"; intro.append(text("h2", "Your personal connections"), text("strong", "Connection truth has four separate axes."), text("p", "These counts and controls cover only your accounts. Configured, authenticated, running, and allowed to write are different facts. Unknown is never promoted to healthy.")); fragment.append(intro);
  if (!data.control.connections.length) fragment.append(text("p", "No reviewed provider connection is configured for this member.", "empty card"));
  else { const grid = document.createElement("div"); grid.className = "settings-card-grid"; for (const connection of data.control.connections) grid.append(connectionCard(connection, data)); fragment.append(grid); }
  const add = document.createElement("section"); add.className = "card settings-add-provider"; add.append(text("h2", "Reviewed provider paths"), text("p", "Only adapters proven by the checked-in capability catalog appear here. Secure account enrollment and member consent stay separate from software availability.")); const list = document.createElement("div"); list.className = "provider-roadmap"; const reviewed = (data.control.capabilities?.items || []).filter((item) => item.source_kind === "adapter_manifest"); for (const item of reviewed) list.append(text("span", `${item.label} · ${titleCase(item.installation)}`, "pill pill-lavender")); if (!reviewed.length) list.append(text("span", "No reviewed central provider path", "pill pill-lavender")); add.append(list); fragment.append(add); return fragment;
}

function appleServicePermissionLink() {
  const card = document.createElement("section"); card.className = "card apple-permissions-link";
  const link = text("a", "Review Apple service permissions", "button button-secondary"); link.href = "/member/settings?section=apple-services";
  card.append(text("h2", "Apple service permissions"), text("p", "See what CoS can read and change in your Calendar, Reminders, and Notes."), link); return card;
}

function appleServicePermissions(data) {
  const section = document.createElement("section"); section.className = "apple-service-permissions";
  const intro = document.createElement("div"); intro.className = "card";
  intro.append(text("h2", "Apple service permissions"), text("p", "Choose where CoS may make changes. Each action still needs your approval and a separate execution approval. Enabling a service does not change anything in Apple."), text("p", "Reading, permission to write, and macOS permissions are separate. These controls do not grant macOS access or enable personal inbox monitoring.", "state-note")); section.append(intro);
  const identity = data.control.identity;
  const own = identity.can_mutate && identity.authenticated_role === "principal" && Boolean(identity.authenticated_member_id) && !identity.viewing_as && identity.authenticated_member_id === identity.view_member_id && identity.actions_as === identity.reads_as;
  if (!own) { intro.append(text("p", "Return to your own private view to see or change your Apple service permissions.", "review-state")); return section; }
  const endpoint = "/api/member/v1/settings/apple-services";
  const pendingKey = `administrateme:apple-permission-pending:${identity.authenticated_member_id}`;
  let pending = null; let busy = false; let loaded = false; let canMutate = false; let retryAllowed = false; let items = [];
  try { pending = JSON.parse(sessionStorage.getItem(pendingKey) || "null"); } catch { pending = { unknown: true }; }
  const list = document.createElement("div"); list.className = "settings-card-grid apple-service-permission-list";
  const status = text("p", "Checking your Apple service permissions…", "state-note"); status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
  const refresh = text("button", "Check Apple permission status", "button button-secondary"); refresh.type = "button";
  const retry = text("button", "Retry saved permission change", "button button-secondary"); retry.type = "button"; retry.hidden = true;
  const actions = document.createElement("div"); actions.className = "button-row"; actions.append(refresh, retry); section.append(list, status, actions);
  const sorted = (values) => [...(values || [])].sort();
  function savePending(value) { if (value) sessionStorage.setItem(pendingKey, JSON.stringify(value)); else sessionStorage.removeItem(pendingKey); pending = value; }
  function immutableMatch(confirmation) {
    const body = pending?.body;
    return Boolean(body && confirmation && confirmation.request_id === body.request_id && confirmation.provider === pending.provider && confirmation.expected_revision === body.expected_revision && confirmation.enabled === body.enabled && JSON.stringify(sorted(confirmation.scope_ids)) === JSON.stringify(sorted(body.scope_ids)));
  }
  function renderItems() {
    list.replaceChildren();
    const locked = busy || !loaded || !canMutate || Boolean(pending);
    for (const item of items) {
      const card = document.createElement("article"); card.className = "card apple-service-permission"; card.dataset.provider = item.provider;
      const heading = document.createElement("div"); heading.className = "connection-heading";
      heading.append(text("h3", item.label), text("span", titleCase(item.state), `pill state-label state-${item.state === "enabled" ? "healthy" : item.state === "unavailable" ? "unknown" : "paused"}`)); card.append(heading);
      const facts = document.createElement("dl"); facts.className = "settings-facts";
      facts.append(text("dt", "Reading"), text("dd", item.read_access ? "Connected" : "Not available"), text("dt", "Writing"), text("dd", item.write_access ? "Allowed in selected locations · approval required" : "Off"), text("dt", "macOS permissions"), text("dd", "Managed on your bridge Mac")); card.append(facts);
      if (item.reason) card.append(text("p", item.reason, "state-note"));
      if (item.observation_mode) card.append(text("p", "Provider changes are paused while observation mode is on.", "review-state"));
      if ((item.operations || []).length) card.append(text("p", `Available changes: ${item.operations.map(titleCase).join(", ")}.`, "state-note"));
      const form = document.createElement("form"); form.className = "apple-service-permission-form";
      const scopes = document.createElement("fieldset"); scopes.className = "apple-service-scopes"; scopes.append(text("legend", "Where CoS may write"));
      const selected = new Set(item.selected_scope_ids || []);
      for (const scope of item.scopes || []) {
        const label = document.createElement("label"); label.className = "bridge-capability-choice";
        const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.name = "scope"; checkbox.value = scope.id; checkbox.checked = selected.has(scope.id); checkbox.dataset.accountId = scope.account_id;
        const copy = document.createElement("span"); copy.append(text("strong", scope.label)); if (scope.account_label) copy.append(text("small", scope.account_label)); label.append(checkbox, copy); scopes.append(label);
      }
      if (!(item.scopes || []).length) scopes.append(text("p", "No writable location is available. Check this service on your bridge Mac.", "empty compact-empty"));
      const enable = text("button", item.write_access ? "Review selected locations" : "Review enabling writes", "button button-primary"); enable.type = "submit";
      const revoke = text("button", "Turn off writes", "button button-quiet"); revoke.type = "button"; revoke.hidden = item.can_revoke !== true && !item.write_access;
      const note = text("p", "Select only the calendars, lists, or folders you want CoS to change.", "state-note");
      function lockForm() {
        const unavailable = item.state === "unavailable";
        for (const input of form.querySelectorAll("input")) input.disabled = locked || unavailable;
        const chosen = [...form.querySelectorAll('input[name="scope"]:checked')];
        const accounts = new Set(chosen.map((input) => input.dataset.accountId));
        enable.disabled = locked || unavailable || !chosen.length || accounts.size > 1;
        revoke.disabled = locked;
        note.textContent = accounts.size > 1 ? "Choose locations from one Apple account at a time." : "Select only the calendars, lists, or folders you want CoS to change.";
      }
      const buttons = document.createElement("div"); buttons.className = "button-row"; buttons.append(enable, revoke); form.append(scopes, note, buttons);
      form.addEventListener("change", lockForm);
      form.addEventListener("submit", (event) => { event.preventDefault(); if (!enable.disabled) reviewChange(item, true, [...form.querySelectorAll('input[name="scope"]:checked')].map((input) => input.value)); });
      revoke.addEventListener("click", () => { if (!revoke.disabled) reviewChange(item, false, []); });
      lockForm(); card.append(form); list.append(card);
    }
    refresh.disabled = busy; retry.hidden = !pending || !retryAllowed; retry.disabled = busy || !loaded || !canMutate || !retryAllowed;
  }
  async function reconcile() {
    busy = true; retryAllowed = false; renderItems();
    try {
      const query = pending?.body?.request_id && pending.provider ? `?request_id=${encodeURIComponent(pending.body.request_id)}&provider=${encodeURIComponent(pending.provider)}` : "";
      const result = await api(`${endpoint}${query}`);
      items = result.items || []; canMutate = result.can_mutate === true; loaded = true;
      const confirmation = result.request_confirmation;
      if (pending && immutableMatch(confirmation) && confirmation.state === "recorded") savePending(null);
      else if (pending?.body && confirmation?.request_id === pending.body.request_id && confirmation.provider === pending.provider && confirmation.state === "not_recorded") retryAllowed = true;
      status.textContent = pending ? retryAllowed ? "This saved request was not recorded. You may retry the same permission change; its selections cannot be changed while it is pending." : "Your last permission change is not confirmed. Check its status before making another change." : "Your current Apple permissions are shown above. Turning writing off preserves your history and existing Apple items.";
    } catch (error) { loaded = false; status.textContent = `${error.message} Current Apple permissions could not be checked. Changes remain locked.`; }
    finally { busy = false; renderItems(); }
  }
  async function submitChange(change, isRetry = false) {
    if (busy || !loaded || !canMutate || (pending && !isRetry) || (isRetry && (!retryAllowed || change !== pending))) return;
    try { savePending(change); } catch { status.textContent = "This browser cannot retain the request reference. No permission change was requested."; return; }
    busy = true; retryAllowed = false; renderItems(); closeActionDialog(); status.textContent = "Saving your Apple permission decision…";
    try { await api(`${endpoint}/${encodeURIComponent(change.provider)}`, { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify(change.body) }); }
    catch (error) {
      if (error.status === 409 && error.body?.detail?.code === "stale_revision") { savePending(null); setBanner("These permissions changed in another window. Review the refreshed settings before saving again."); }
      // Every other uncertain result is reconciled by its exact request ID;
      // a failed transport never causes an automatic second POST.
    } finally { busy = false; await reconcile(); }
  }
  function reviewChange(item, enabled, scopeIds) {
    if (busy || pending || !loaded || !canMutate) return;
    const content = document.createElement("form"); content.className = "dialog-form apple-permission-confirmation";
    content.append(text("p", `${identity.authenticated_display_name} · ${item.label}`), text("p", enabled ? "Allow CoS to write in these selected locations:" : "Turn off CoS writing for this service?"));
    if (enabled) { const locations = document.createElement("ul"); for (const scope of item.scopes || []) if (scopeIds.includes(scope.id)) locations.append(text("li", scope.label)); content.append(locations, text("p", "Each action still needs your approval and a separate execution approval. No Apple item is changed by saving this setting.", "state-note")); }
    else content.append(text("p", "Existing Apple items and read access are unchanged. New writes will be blocked.", "state-note"));
    const cancel = text("button", "Go back", "button button-quiet"); cancel.type = "button"; cancel.addEventListener("click", closeActionDialog);
    const confirm = text("button", enabled ? "Allow selected writes" : "Turn off writes", "button button-primary"); confirm.type = "submit"; content.append(cancel, confirm);
    content.addEventListener("submit", (event) => { event.preventDefault(); confirm.disabled = true; void submitChange({ provider: item.provider, body: { request_id: crypto.randomUUID(), expected_revision: item.revision, enabled, scope_ids: sorted(scopeIds) } }); });
    openActionDialog(enabled ? `Allow ${item.label} changes?` : `Turn off ${item.label} changes?`, content);
  }
  refresh.addEventListener("click", () => { void reconcile(); }); retry.addEventListener("click", () => { if (pending) void submitChange(pending, true); });
  void reconcile(); return section;
}

const CANDIDATE_MODE_COPY = {
  review: "Identify supported candidates and place only eligible proposals in Triage.",
  manual: "Retain and account for source facts, but extract candidates only when you request it.",
  quiet: "Retain and account for source facts without proposing candidates.",
};

function candidatePolicyCard(source, data) {
  const policy = source.policy || {};
  const locked = Boolean(source.locked) || !data.control.identity.can_mutate;
  const form = document.createElement("form"); form.className = "card candidate-policy-card";
  const head = document.createElement("div"); head.className = "candidate-policy-heading";
  const copy = document.createElement("div"); copy.append(text("p", `${source.projection} · ${source.channel_type}`, "eyebrow"), text("h3", source.label));
  const status = source.availability === "planned" ? "Planned" : source.availability === "configured" || source.availability === "available" ? "Available" : "Not configured";
  head.append(copy, text("span", status, `pill state-label state-${source.availability === "planned" ? "unknown" : source.availability === "available_not_configured" ? "degraded" : "healthy"}`)); form.append(head);
  if (source.explicit_submission_required) { form.append(text("p", "Forward selected material to your CoS or use Add for review. Automatic obligation extraction from this personal source is off. Existing source history remains available.", "state-note")); return form; }
  const signals = document.createElement("div"); signals.className = "candidate-policy-signals";
  for (const value of source.signals || []) signals.append(text("span", value, "pill pill-lavender"));
  form.append(signals);

  const mode = document.createElement("select"); mode.name = "mode"; mode.disabled = locked;
  for (const [value, label] of [["review", "Review automatically"], ["manual", "Only when I ask"], ["quiet", "Do not propose"]]) { const option = text("option", label); option.value = value; option.selected = policy.mode === value; mode.append(option); }
  const modeNote = text("small", CANDIDATE_MODE_COPY[policy.mode] || CANDIDATE_MODE_COPY.review, "candidate-mode-copy");
  mode.addEventListener("change", () => { modeNote.textContent = CANDIDATE_MODE_COPY[mode.value]; });
  form.append(field("Candidate admission", mode), modeNote);

  const languageRow = document.createElement("label"); languageRow.className = "candidate-language-toggle";
  const language = document.createElement("input"); language.type = "checkbox"; language.name = "language_enabled"; language.checked = Boolean(policy.language_enabled); language.disabled = locked || !source.language_available;
  const languageCopy = document.createElement("span"); languageCopy.append(text("strong", "Use grounded language extraction"), text("small", "May contribute typed fields only when backed by exact source evidence. It cannot admit, route, confirm, or write an item.")); languageRow.append(language, languageCopy); form.append(languageRow);

  const kinds = document.createElement("fieldset"); kinds.className = "candidate-kind-grid"; kinds.disabled = locked; kinds.append(text("legend", "Candidate types"));
  for (const value of source.candidate_kinds || []) { const label = document.createElement("label"); const input = document.createElement("input"); input.type = "checkbox"; input.value = value; input.checked = (policy.candidate_kinds || []).includes(value); label.append(input, document.createTextNode(` ${titleCase(value)}`)); kinds.append(label); }
  form.append(kinds);

  const layers = document.createElement("details"); layers.className = "settings-technical"; layers.append(text("summary", "Signals and extraction layers")); const facts = document.createElement("dl"); facts.className = "settings-facts"; facts.append(text("dt", "Layers"), text("dd", (source.extraction_layers || []).join(" → ")), text("dt", "Source state"), text("dd", `${titleCase(source.implementation)} · ${titleCase(source.availability)}`), text("dt", "Candidate engine"), text("dd", source.policy_execution === "current" ? "Implemented" : "Planned · preference saved ahead of activation")); layers.append(facts); form.append(layers);
  if (source.locked) form.append(text("p", "Explicit member requests always remain eligible for Triage review. This safety path cannot be disabled.", "state-note"));
  else {
    const save = text("button", source.implementation === "planned" || source.policy_execution === "planned" ? "Save future preference" : "Save source settings", "button button-primary"); save.type = "submit"; save.disabled = !data.control.identity.can_mutate; form.append(save);
    form.addEventListener("submit", async (event) => {
      event.preventDefault(); const selected = [...kinds.querySelectorAll("input:checked")].map((input) => input.value);
      if (!selected.length) { setBanner("Select at least one candidate type. Nothing was changed."); return; }
      save.disabled = true; setBanner(`Saving candidate identification settings for ${source.label}…`);
      try {
        await api(`/api/member/v1/settings/candidate-policies/${encodeURIComponent(source.id)}`, { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify({ expected_revision: data.control.revision, request_id: crypto.randomUUID(), mode: mode.value, language_enabled: language.checked, candidate_kinds: selected }) });
        setBanner(`${source.label} settings saved. Source retention, privacy, routing, confirmation, and provider-write gates were unchanged.`); await reloadActiveSurface();
      } catch (error) { setBanner(`${error.message} Candidate identification settings were not changed.`); save.disabled = false; }
    });
  }
  return form;
}

const SOURCE_PURPOSE_OPTIONS = [
  ["personal_request", "Personal requests"], ["member_promise", "My promises"],
  ["appointment", "Appointments and bookings"], ["preparation", "Forms and preparation"],
  ["payment", "Payment obligations"], ["account_alert", "Account alerts"],
  ["optional_opportunity", "Optional opportunities"], ["status_update", "Status updates"],
  ["receipt", "Receipts"], ["survey", "Surveys"], ["reference", "Reference material"],
  ["recurrence", "Recurring obligations"], ["unknown", "Unclear purpose"],
].map(([value, label]) => ({ value, label }));

function sourcePurposeLabel(value) {
  return SOURCE_PURPOSE_OPTIONS.find((item) => item.value === value)?.label || titleCase(value);
}

function sourceRuleRoute(value) {
  if (value === "reference" || value === "context_only" || value === "context") return "Reference";
  if (value === "ask" || value === "clarification_required" || value === "clarification") return "Ask me";
  if (value === "review" || value === "proposal" || value === "admit" || value === "ordinary_queue") return "Keep for review";
  if (value === "alert" || value === "alert_only") return "Alerts";
  return titleCase(value || "unchanged");
}

function sourcePreferenceInteractionLocked() {
  return state.sourceSettingsPending || state.sourceSettingsRefreshRequired || triageInteractionLocked();
}

function sourcePreferenceControlState(control, disabled) {
  // Async reads must preserve the semantic state restored by the shared lock.
  if (control.hasAttribute("data-source-lock-disabled")) control.dataset.sourceLockDisabled = String(disabled);
  if (control.hasAttribute("data-triage-prior-disabled")) control.dataset.triagePriorDisabled = String(disabled);
  control.disabled = disabled || sourcePreferenceInteractionLocked();
}

function setSourceSettingsLock() {
  if (state.surface?.surface !== "settings") return;
  const surface = $("#surface");
  if (surface && !surface.dataset.sourceSettingsGuard) {
    surface.dataset.sourceSettingsGuard = "true";
    for (const type of ["click", "change", "input", "submit"]) surface.addEventListener(type, (event) => {
      if (!state.sourceSettingsPending && !state.sourceSettingsRefreshRequired) return;
      event.preventDefault(); event.stopImmediatePropagation();
    }, true);
  }
  for (const control of document.querySelectorAll("#surface button, #surface input, #surface select, #surface textarea")) {
    if (state.sourceSettingsPending || state.sourceSettingsRefreshRequired) {
      if (!control.hasAttribute("data-source-lock-disabled")) control.dataset.sourceLockDisabled = String(control.disabled);
      control.disabled = true;
    } else if (control.hasAttribute("data-source-lock-disabled")) {
      control.disabled = control.dataset.sourceLockDisabled === "true";
      delete control.dataset.sourceLockDisabled;
    }
  }
}

async function runSourcePreferenceMutation(request, success) {
  const inTriage = state.surface?.surface === "triage";
  if (state.sourceSettingsPending || state.sourceSettingsRefreshRequired || (inTriage && triageInteractionLocked())) return;
  const token = inTriage ? beginTriageOperation() : null;
  if (inTriage && token == null) return;
  state.sourceSettingsPending = true; setSourceSettingsLock();
  let recorded = false;
  try {
    const result = await request();
    if (inTriage && !triageOperationIsCurrent(token)) return;
    recorded = true;
    setBanner(typeof success === "function" ? success(result) : success);
    await reloadActiveSurface(inTriage ? { triageOperationToken: token } : {});
  } catch (error) {
    if (inTriage && !triageOperationIsCurrent(token)) return;
    if (recorded || !error.status || error.status >= 500) {
      const message = recorded ? `Your change was recorded, but the page could not refresh: ${error.message} Reload before continuing.` : `The change result is uncertain: ${error.message} Reload before retrying.`;
      if (inTriage) latchTriageRefreshFailure(message);
      else { state.sourceSettingsRefreshRequired = true; setBanner(message); }
    } else setBanner(`${error.message} The change was rejected.`);
  } finally {
    state.sourceSettingsPending = false;
    if (inTriage) finishTriageOperation(token);
    setSourceSettingsLock();
  }
}

function sourceRuleScopeLabel(label) {
  return String(label || "").split(" · ").filter((value) => !/(?:provider-account|protected-source):[a-f0-9]{32,64}/i.test(value)).join(" · ");
}

function sourceRuleSummary(rule, label = null) {
  const facts = document.createElement("dl"); facts.className = "settings-facts source-rule-summary";
  const conditions = rule.conditions || {};
  facts.dataset.ruleConditions = JSON.stringify(conditions);
  const readableLabel = sourceRuleScopeLabel(label);
  const scope = [readableLabel, conditions.provider_account_id && "This account", conditions.adapter_instance_id && "This connection", conditions.protected_source_id && "This source", conditions.native_container_id && "This exact conversation or container", conditions.sender_digest && "This exact sender"].filter(Boolean).join(" · ");
  const when = [conditions.message_purpose && sourcePurposeLabel(conditions.message_purpose), conditions.candidate_kind && titleCase(conditions.candidate_kind)].filter(Boolean).join(" · ") || "The exact sender or conversation in scope";
  const exceptions = (rule.exceptions || []).map((item) => Object.entries(item).map(([key, value]) => key === "message_purpose" ? sourcePurposeLabel(value) : key === "sender_digest" ? "Exact excluded sender" : `${titleCase(key)}: ${value}`).join(" and "));
  const duration = rule.expires_at ? `Until ${new Date(rule.expires_at).toLocaleString()} or ${rule.max_observations} future matches (${rule.observed_count || 0} observed)` : rule.status === "active" ? "Until you change or undo it" : "Choose after preview";
  for (const [name, value] of [["Applies to", scope || "Your exact private source"], ["When", when], ["Except", [...exceptions, "Independent obligations and direct requests remain protected"].join("; ")], ["Then", sourceRuleRoute(rule.action)], ["Duration", duration]]) facts.append(text("dt", name), text("dd", value));
  return facts;
}

function sourceRuleRecordScope(rule, matrix) {
  const section = document.createElement("section"); section.className = "source-rule-record-scope";
  const conditions = rule.conditions || {};
  const sources = matrix?.owner_visible ? (matrix.sources || []).filter((source) => source.coordinate && !source.direct_authenticated_intent) : [];
  const boundary = ["provider", "provider_account_id", "adapter_instance_id", "protected_source_id", "native_container_id"];
  let matches = sources.filter((source) => boundary.every((key) => !conditions[key] || source.coordinate[key] === conditions[key]));
  if (!conditions.native_container_id && matches.some((source) => source.source_scope === "account")) matches = matches.filter((source) => source.source_scope === "account");
  const ordinal = (rows, value, key) => [...new Set(rows.map((source) => key(source.coordinate)))].sort().indexOf(value) + 1;
  const accountKey = (coordinate) => JSON.stringify([coordinate.provider, coordinate.provider_account_id]);
  const names = (source) => {
    const coordinate = source.coordinate;
    const title = sourceRuleScopeLabel(source.label) || source.provider_label || "Unnamed source";
    const account = source.account_hint !== coordinate.provider_account_id || String(source.account_hint || "").includes("@") ? sourceRuleScopeLabel(source.account_hint) : "";
    const container = conditions.native_container_id && source.native_container_label !== coordinate.native_container_id ? sourceRuleScopeLabel(source.native_container_label) : "";
    return { label: [...new Set([title, account, container].filter(Boolean))].join(" · "), complete: Boolean((account || title !== source.provider_label) && (!conditions.native_container_id || container)) };
  };
  let numbered = false;
  const labels = matches.map((source) => {
    const coordinate = source.coordinate;
    const accountSources = sources.filter((value) => accountKey(value.coordinate) === accountKey(coordinate));
    const connectionSources = accountSources.filter((value) => value.coordinate.adapter_instance_id === coordinate.adapter_instance_id);
    const protectedSources = connectionSources.filter((value) => value.coordinate.protected_source_id === coordinate.protected_source_id);
    const readable = names(source);
    const ambiguous = sources.some((value) => value.source_scope === source.source_scope && value.policy_key !== source.policy_key && names(value).label === readable.label);
    const label = [readable.label];
    if (!readable.complete || ambiguous) {
      numbered = true;
      label.push(`Account ${ordinal(sources, accountKey(coordinate), accountKey)}`, `Connection ${ordinal(accountSources, coordinate.adapter_instance_id, (value) => value.adapter_instance_id)}`);
      if (coordinate.protected_source_id) label.push(`Source ${ordinal(connectionSources, coordinate.protected_source_id, (value) => value.protected_source_id || "")}`);
      if (conditions.native_container_id) {
        const containerRows = protectedSources.filter((value) => value.coordinate.native_container_kind === coordinate.native_container_kind);
        label.push(`${titleCase(coordinate.native_container_kind || "container")} ${ordinal(containerRows, coordinate.native_container_id, (value) => value.native_container_id)}`);
      }
    }
    if (source.connection_state === "disconnected") label.push("Disconnected · history retained");
    return label.filter(Boolean).join(" · ");
  });
  const label = [...new Set(labels)].join("; ") || "Source label unavailable";
  section.dataset.scopePolicyKeys = JSON.stringify(matches.map((source) => source.policy_key));
  section.append(text("p", label, "source-rule-scope-label"));
  if (!matches.length || matches.length > 1 || numbered) section.append(text("p", !matches.length ? "The available private inventory cannot identify a readable source label. Inspect the saved identifiers below before changing this rule." : matches.length > 1 ? "This rule covers each listed source. It has no narrower source boundary." : "A friendly source name is missing or shared. Numbers distinguish entries in your current private inventory; exact saved identifiers are available below.", "state-note"));
  section.append(sourceRuleSummary(rule));
  const audit = document.createElement("details"); audit.className = "settings-technical";
  audit.append(text("summary", "Exact saved source identifiers"));
  const identifiers = document.createElement("dl"); identifiers.className = "settings-facts";
  for (const key of [...boundary, "sender_digest"]) if (conditions[key]) identifiers.append(text("dt", titleCase(key)), text("dd", conditions[key]));
  audit.append(identifiers); section.append(audit); return section;
}

function sourceRulePreview(result) {
  const output = document.createElement("section"); output.className = "source-rule-preview";
  output.setAttribute("aria-label", "Rule preview results");
  const counts = result.recent_impact || {};
  output.append(text("strong", `${counts.total || 0} recent items examined · ${counts.matched || 0} matches · ${counts.exceptions || 0} exceptions · ${counts.nonmatches || 0} nonmatches · ${counts.protected || 0} protected`));
  output.append(text("p", "This preview changes nothing. Matching content is evaluated separately from other obligations in the same message.", "state-note"));
  if (result.limitations?.length) output.append(text("p", result.limitations.join(" "), "state-note"));
  const examples = document.createElement("ul"); examples.className = "source-preview-examples";
  for (const example of result.examples || []) {
    const row = document.createElement("li"); row.dataset.ruleMatch = example.match;
    row.append(text("strong", example.title || "Authorized source example"), text("span", `${titleCase(example.match)} · ${sourceRuleRoute(example.before)} → ${sourceRuleRoute(example.after)}`));
    row.append(text("small", (example.reasons || []).map(titleCase).join(" · ") || "No handling change"));
    if (example.source_event_id) { const evidence = text("button", "View source evidence", "link-button"); evidence.type = "button"; evidence.addEventListener("click", () => { if (!sourcePreferenceInteractionLocked()) showEvidence(example.source_event_id); }); row.append(evidence); }
    examples.append(row);
  }
  if (!examples.children.length) examples.append(text("li", "No authorized recent examples are available. A shadow trial can observe future matches before changing attention.", "empty"));
  output.append(examples); return output;
}

function sourceRuleBuilder(scope, { canMutate = false, candidate = null } = {}) {
  const panel = document.createElement("section"); panel.className = "source-rule-builder";
  const initial = scope.conditions || {};
  panel.append(text("h3", "A rule for similar messages"), text("p", sourceRuleScopeLabel(scope.label) || "Your exact private source", "state-note"));
  const fields = document.createElement("div"); fields.className = "preference-grid";
  const purpose = selectControl([{ value: "", label: "Choose a message purpose…" }, ...SOURCE_PURPOSE_OPTIONS], initial.message_purpose || scope.message_purpose || "", "");
  const action = selectControl([{ value: "reference", label: "Keep as reference" }, { value: "ask", label: "Ask me" }, { value: "review", label: "Keep for review" }], "reference", "");
  const exception = selectControl([{ value: "", label: "No additional exception" }, { value: "calendar_event", label: "Calendar events" }, { value: "task", label: "Tasks" }, { value: "commitment", label: "Commitments" }, { value: "reminder", label: "Reminders" }, { value: "recurrence", label: "Recurring obligations" }], "", "");
  fields.append(field("When the message purpose is", purpose), field("Then", action), field("Except this type of candidate", exception)); panel.append(fields);
  panel.append(text("p", "Only this account, connection, and any specified conversation or sender. Other people's sources are never included. Independent obligations remain visible.", "state-note"));
  const output = document.createElement("div"); output.setAttribute("aria-live", "polite");
  const draft = document.createElement("div"); draft.setAttribute("aria-label", "Rule draft scope");
  const buttons = document.createElement("div"); buttons.className = "button-row";
  const previewButton = text("button", "Preview matches and exceptions", "button button-quiet"); previewButton.type = "button";
  const activate = text("button", "Save this rule", "button button-primary"); activate.type = "button";
  const trial = text("button", "Try for 7 days / 50 messages", "button button-quiet"); trial.type = "button";
  const shadow = text("button", "Observe only for 7 days / 50 messages", "button button-quiet"); shadow.type = "button";
  const remember = text("button", "Remember for future suggestions", "button button-quiet"); remember.type = "button";
  let preview = null; let generation = 0; let reading = false;
  const enabled = canMutate && Boolean(initial.provider_account_id || initial.adapter_instance_id);
  function values() { return { conditions: { ...initial, message_purpose: purpose.value }, exceptions: exception.value ? [{ candidate_kind: exception.value }] : [], action: action.value, origin: "explicit", feedback_event_ids: [] }; }
  function invalidate() { if (sourcePreferenceInteractionLocked()) return; generation += 1; preview = null; reading = false; output.replaceChildren(); draft.replaceChildren(sourceRuleSummary(values(), scope.label)); sourcePreferenceControlState(previewButton, !enabled); for (const button of [activate, trial, shadow, remember]) button.hidden = true; }
  for (const control of [purpose, action, exception]) { control.disabled = !enabled; control.addEventListener("change", invalidate); }
  previewButton.disabled = !enabled;
  for (const button of [activate, trial, shadow, remember]) button.hidden = true;
  previewButton.addEventListener("click", async () => {
    if (!enabled || reading || state.sourceSettingsPending || state.sourceSettingsRefreshRequired || triageInteractionLocked()) return;
    if (!purpose.value) { output.replaceChildren(text("p", "Choose a message purpose to keep this rule specific.", "review-state")); purpose.focus(); return; }
    const current = ++generation; reading = true; previewButton.disabled = true;
    try {
      const result = await api("/api/member/v1/triage-learning/v2/preview", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(values()) });
      if (generation !== current || !panel.isConnected || sourcePreferenceInteractionLocked()) return;
      preview = result;
      output.replaceChildren(sourceRuleSummary(result.rule || values(), scope.label), sourceRulePreview(result));
      for (const button of [activate, trial, shadow]) button.hidden = result.activation_available === false;
      remember.hidden = !candidate || result.activation_available === false;
      if (result.activation_available === false) output.append(text("p", "Preview is available. Saving or trying rules waits for learning activation.", "state-note"));
    } catch (error) { if (generation === current) output.replaceChildren(text("p", `${error.message} Nothing changed.`, "review-state")); }
    finally { if (generation === current) { reading = false; sourcePreferenceControlState(previewButton, !enabled); } }
  });
  for (const [button, mode] of [[activate, "active"], [trial, "trial"], [shadow, "shadow"]]) button.addEventListener("click", () => {
    if (!preview || !enabled) return;
    const payload = { ...values(), request_id: crypto.randomUUID(), preview_digest: preview.preview_digest, mode, ...(mode === "active" ? {} : { trial_days: 7, trial_message_limit: 50 }) };
    runSourcePreferenceMutation(() => api("/api/member/v1/triage-learning/v2/rules", { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify(payload) }), mode === "shadow" ? "Observation trial saved for 7 days or 50 future matches. It changes no attention preferences. Undo is available in Learning." : mode === "trial" ? "Rule trial saved for 7 days or 50 future matches. It expires automatically; Undo is available in Learning." : "Your scoped rule was saved. You can undo it in Learning.");
  });
  remember.addEventListener("click", () => {
    if (!preview || !candidate || !enabled) return;
    const payload = { request_id: crypto.randomUUID(), expected_last_event_id: candidate.last_event_id, proposal_id: candidate.proposal_id, kind: "future_attention_preference", rule: values() };
    runSourcePreferenceMutation(() => api("/api/member/v1/triage-learning/v2/feedback", { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify(payload) }), "Future preference recorded. Nothing activated. Consistent preferences from independent observations may produce a rule suggestion for you to review.");
  });
  draft.append(sourceRuleSummary(values(), scope.label));
  buttons.append(previewButton, activate, trial, shadow, remember); panel.append(draft, buttons, output); return panel;
}

function sourceLearningSuggestion(suggestion, canMutate, matrix) {
  const section = document.createElement("article"); section.className = "source-rule-record";
  const rule = suggestion.rule;
  section.append(text("h3", "Suggested from your explicit preferences"), text("p", `${suggestion.independent_observations} independent observations. This suggestion has not activated.`, "state-note"), sourceRuleRecordScope(rule, matrix));
  const output = document.createElement("div"); output.setAttribute("aria-live", "polite");
  const previewButton = text("button", "Preview suggested rule", "button button-quiet"); previewButton.type = "button"; previewButton.disabled = !canMutate;
  const trial = text("button", "Try suggested rule for 7 days / 50 messages", "button button-primary"); trial.type = "button"; trial.hidden = true;
  let preview = null;
  previewButton.addEventListener("click", async () => {
    if (!canMutate || sourcePreferenceInteractionLocked()) return;
    previewButton.disabled = true;
    try { const result = await api("/api/member/v1/triage-learning/v2/preview", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(rule) }); if (!section.isConnected || sourcePreferenceInteractionLocked()) return; preview = result; output.replaceChildren(sourceRulePreview(result)); trial.hidden = result.activation_available === false; }
    catch (error) { if (section.isConnected) output.replaceChildren(text("p", `${error.message} Nothing activated.`, "review-state")); }
    finally { if (section.isConnected) sourcePreferenceControlState(previewButton, !canMutate); }
  });
  trial.addEventListener("click", () => {
    if (!preview || !canMutate) return;
    const payload = { ...rule, request_id: crypto.randomUUID(), preview_digest: preview.preview_digest, mode: "trial", trial_days: 7, trial_message_limit: 50 };
    runSourcePreferenceMutation(() => api("/api/member/v1/triage-learning/v2/rules", { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify(payload) }), "Suggested rule trial saved. It expires after 7 days or 50 future matches. You can undo it in Learning.");
  });
  const actions = document.createElement("div"); actions.className = "button-row"; actions.append(previewButton, trial); section.append(actions, output); return section;
}

function sourceLearningRulesCard(canMutate, sourceMatrix = null) {
  const section = document.createElement("section"); section.className = "card source-learning-rules";
  section.append(text("p", "YOUR LEARNING RULES", "eyebrow"), text("h2", "Review, pause, or undo a rule"), text("p", "Completed tasks, duplicate corrections, and ignored messages do not create preferences. Every rule below comes from an explicit preference or an approved learning proposal.", "state-note"));
  const output = document.createElement("div"); output.setAttribute("aria-live", "polite");
  const load = text("button", "Load my rules", "button button-quiet"); load.type = "button";
  load.disabled = !canMutate;
  if (!canMutate) section.append(text("p", "Return to your own adult member context to inspect private learning rules.", "state-note"));
  load.addEventListener("click", async () => {
    if (!canMutate || state.sourceSettingsPending || state.sourceSettingsRefreshRequired || triageInteractionLocked()) return;
    load.disabled = true;
    try {
      const response = await api("/api/member/v1/triage-learning/v2/rules");
      if (!section.isConnected || sourcePreferenceInteractionLocked()) return;
      let matrix = sourceMatrix;
      if (!matrix && ((response.rules || []).length || (response.suggestions || []).length)) {
        try { matrix = (await api("/api/member/v1/surfaces/settings")).control?.source_policy_v2; }
        catch { matrix = null; }
        if (!section.isConnected || sourcePreferenceInteractionLocked()) return;
      }
      output.replaceChildren();
      for (const suggestion of response.suggestions || []) output.append(sourceLearningSuggestion(suggestion, canMutate && response.active !== false, matrix));
      for (const rule of response.rules || []) {
        const card = document.createElement("article"); card.className = "source-rule-record";
        card.dataset.attentionRuleId = rule.rule_id;
        card.append(text("h3", `${sourcePurposeLabel(rule.conditions?.message_purpose || "Scoped messages")} → ${sourceRuleRoute(rule.action)}`), text("span", titleCase(rule.status), "pill pill-lavender"), sourceRuleRecordScope(rule, matrix));
        if (rule.status === "shadow") card.append(text("p", "Observation only. This rule does not change visibility.", "state-note"));
        if (["expired", "revoked", "suspended"].includes(rule.status)) card.append(text("p", "This rule no longer changes attention. Its history is retained.", "state-note"));
        if (canMutate && ["active", "trial", "shadow", "suspended"].includes(rule.status)) {
          const actions = document.createElement("div"); actions.className = "button-row";
          const choices = [...(rule.status === "trial" ? [["retain", "Keep this rule"]] : []), ...(rule.status === "active" || rule.status === "trial" ? [["suspend", "Pause"], ["missed", "This hid an obligation"]] : []), ["revoke", "Undo this rule"]];
          for (const [choice, label] of choices) {
            const button = text("button", label, "button button-quiet"); button.type = "button";
            button.addEventListener("click", () => {
              const decision = choice === "missed" ? "suspend" : choice;
              const payload = { request_id: crypto.randomUUID(), expected_last_event_id: rule.last_event_id, decision, ...(choice === "missed" ? { negative_outcome: "missed_obligation" } : {}), reason: choice === "missed" ? "Member reported a missed obligation" : `${label} from member rule controls` };
              runSourcePreferenceMutation(() => api(`/api/member/v1/triage-learning/v2/rules/${encodeURIComponent(rule.rule_id)}/review`, { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify(payload) }), choice === "revoke" ? "Rule undone. Its history is retained and it no longer changes attention." : decision === "suspend" ? "Rule paused. Its matches return to ordinary assessment." : "Rule retained. You can pause or undo it at any time.");
            }); actions.append(button);
          }
          card.append(actions);
        }
        output.append(card);
      }
      if (!(response.rules || []).length && !(response.suggestions || []).length) output.append(text("p", "No rules have been saved. Use a source's narrower-rule controls or Do this for similar messages on a candidate to create one.", "empty"));
      load.textContent = "Refresh rules";
    } catch (error) { if (section.isConnected) output.replaceChildren(text("p", `${error.message} Your rule state could not be read.`, "review-state")); }
    finally { if (section.isConnected) sourcePreferenceControlState(load, !canMutate); }
  });
  section.append(load, output); return section;
}

function sourcePolicyV2Card(source, matrix) {
  const policy = source.policy || {};
  const explicitOnly = Boolean(source.explicit_submission_required);
  const editable = Boolean(source.configurable && source.coordinate && !source.direct_authenticated_intent);
  const active = Boolean(matrix.admission_active || matrix.active);
  const form = document.createElement("form"); form.className = "card candidate-policy-card source-attention-card";
  form.dataset.sourcePolicyKey = source.policy_key || source.id || "unattributed";
  const head = document.createElement("div"); head.className = "candidate-policy-heading";
  const copy = document.createElement("div");
  copy.append(text("p", [source.provider_label, source.account_hint, source.workspace && titleCase(source.workspace)].filter(Boolean).join(" · "), "eyebrow"), text("h3", source.label), text("small", source.native_container_label || (source.coordinate?.native_container_id ? `This exact ${titleCase(source.coordinate.native_container_kind || "conversation")}` : "Private to you")));
  const lifecycle = source.connection_state || (source.coordinate_state === "exact" ? "attributed" : "missing_attribution");
  const status = { connected: "Connected", disconnected: "Disconnected · history retained", not_connected: "Not connected", missing_attribution: "Needs source attribution", attributed: "Exact source" }[lifecycle] || titleCase(lifecycle);
  head.append(copy, text("span", status, `pill state-label state-${lifecycle === "connected" ? "healthy" : "unknown"}`)); form.append(head);
  if (source.acquisition?.coverage === "bounded_test_sample") {
    form.append(text("p", `Bounded test sample: ${source.acquisition.selected_message_count} selected messages, with a lifetime limit of ${source.acquisition.distinct_message_cap}, including Sent and thread context. Only explicitly selected messages can be imported. This sample does not cover the whole mailbox.`, "review-state"));
  } else if (source.acquisition?.coverage === "excluded_from_test_sample") {
    form.append(text("p", "Local import is paused while the bounded test sample is active. Your provider connection is retained.", "state-note"));
  }
  if (source.retained_candidate_count) {
    form.append(text("p", `${source.retained_candidate_count} retained candidate${source.retained_candidate_count === 1 ? "" : "s"}. Source disconnection does not erase evidence or decisions.`, "state-note"));
    const history = text("a", "Review retained candidates", "link-button"); history.href = "/member/triage?view=all_candidates"; form.append(history);
  }
  if (source.direct_authenticated_intent) { form.append(text("p", "Requests you explicitly add for review always remain eligible. Passive-source settings cannot hide them.", "state-note")); return form; }
  if (explicitOnly) form.append(text("p", "You select what becomes work by forwarding it to your CoS or using Add for review. Automatic obligation extraction is off for this personal source. Source access, context, and alerts keep their separate settings.", "state-note"));
  if (!editable) {
    form.append(text("p", explicitOnly ? "Connecting this source may make authorized history available. It does not turn on obligation extraction; submit selected material to your CoS for review." : lifecycle === "not_connected" ? "This source can use the same obligation, purpose, and learning controls after a reviewed connection supplies its exact account. A preference does not connect or authorize it." : "Retained evidence stays available. An exact authorized account and connection are required before a future rule can be saved.", "state-note"));
    return form;
  }
  if (source.migration_state === "migration_needed") form.append(text("p", "An older source-family preference is retained. This selection applies only to the account and conversation shown here.", "review-state"));
  const attention = selectControl([
    { value: "obligations", label: "Focus on obligations" },
    { value: "possibilities", label: "Include possible opportunities" },
    { value: "manual", label: "Only when I ask" },
  ], policy.attention_mode || (policy.triage_mode === "manual_only" ? "manual" : policy.preset === "review_everything" ? "possibilities" : "obligations"), "");
  attention.name = "attention_mode";
  if (explicitOnly) { attention.value = "manual"; attention.disabled = true; }
  form.append(field("What should reach me?", attention), text("small", "This attention choice preserves your source access, permitted context, alerts, and candidate types.", "state-note"));
  const purposes = document.createElement("details"); purposes.className = "source-purpose-settings";
  purposes.append(text("summary", "Fine-tune by message purpose"), text("p", "A message can contain several purposes. Keeping its receipt or survey as reference still preserves an independent booking, required form, or unpaid bill.", "state-note"));
  const routes = new Map((policy.purpose_routes || []).map((item) => [item.purpose, item.route]));
  const purposeControls = new Map();
  for (const item of matrix.purpose_options || SOURCE_PURPOSE_OPTIONS) {
    const route = selectControl([{ value: "", label: "Use attention default" }, { value: "review", label: "Keep for review" }, { value: "reference", label: "Keep as reference" }, { value: "alert", label: "Alerts" }], routes.get(item.value) || "", "");
    route.dataset.messagePurpose = item.value; purposeControls.set(item.value, route);
    const row = field(item.label, route); row.classList.add("source-purpose-row"); if (item.description) row.append(text("small", item.description)); purposes.append(row);
  }
  if (!explicitOnly) form.append(purposes);
  const advanced = document.createElement("details"); advanced.className = "settings-technical"; advanced.append(text("summary", "Source access, context, and alerts"));
  const acquisition = selectControl([{ value: "active", label: "Active source assessment" }, { value: "paused", label: "Pause source assessment" }], policy.acquisition_state || "active", "");
  const alert = selectControl([{ value: "none", label: "No alerts" }, { value: "security_only", label: "Security alerts" }, { value: "financial_and_security", label: "Financial and security alerts" }, { value: "actionable_and_critical", label: "Actionable and critical alerts" }], policy.alert_mode || "financial_and_security", "");
  const context = selectControl([{ value: "none", label: "No context" }, { value: "metadata_only", label: "Metadata only" }, { value: "typed_facts", label: "Typed facts" }, { value: "typed_facts_safe_snippet", label: "Typed facts and permitted excerpts" }, { value: "content_on_explicit_authorized_demand", label: "Content only when I explicitly ask" }], policy.context_mode || "typed_facts", "");
  const advancedTriage = selectControl([{ value: "review", label: "Use attention and purpose settings" }, { value: "manual_only", label: "Only when I ask" }, { value: "alert_only", label: "Alerts only" }, { value: "context_only", label: "Reference only" }, { value: "ignore", label: "Provenance only" }], policy.triage_mode || "review", "");
  if (explicitOnly) { advancedTriage.value = "manual_only"; advancedTriage.disabled = true; }
  const planes = document.createElement("div"); planes.className = "preference-grid"; planes.append(field("Source assessment", acquisition), field("Permitted context", context), field("Alerts", alert), field("Advanced attention override", advancedTriage)); advanced.append(planes);
  const kinds = document.createElement("fieldset"); kinds.className = "candidate-kind-grid"; kinds.append(text("legend", "Candidate types"));
  for (const value of matrix.candidate_kinds || []) { const label = document.createElement("label"); const input = document.createElement("input"); input.type = "checkbox"; input.value = value; input.checked = (policy.candidate_kinds || []).includes(value); label.append(input, document.createTextNode(` ${titleCase(value)}`)); kinds.append(label); }
  if (!explicitOnly) advanced.append(kinds);
  advanced.append(text("p", "Provider permissions and notification timing are managed separately in Connections and CoS fine-tuning. These controls never grant calendar, message, or other provider writes.", "state-note")); form.append(advanced);
  const preview = document.createElement("section"); preview.className = "source-policy-preview"; preview.setAttribute("aria-live", "polite");
  let generation = 0;
  function invalidatePreview() { if (sourcePreferenceInteractionLocked()) return; generation += 1; preview.replaceChildren(); }
  attention.addEventListener("change", () => { advancedTriage.value = attention.value === "manual" ? "manual_only" : "review"; invalidatePreview(); });
  advancedTriage.addEventListener("change", () => { if (advancedTriage.value === "manual_only") attention.value = "manual"; else if (attention.value === "manual") attention.value = "obligations"; });
  form.addEventListener("change", invalidatePreview);
  function selectedPolicy() {
    if (explicitOnly) return { ...policy, preset: "custom", acquisition_state: acquisition.value, alert_mode: alert.value, context_mode: context.value, scope_widening: false, credential_creation: false, provider_write_authority: false, admission_active: false };
    return { ...policy, preset: "custom", attention_mode: attention.value, purpose_routes: [...purposeControls].filter(([, control]) => control.value).map(([purpose, control]) => ({ purpose, route: control.value, preserve_independent_obligations: true })), acquisition_state: acquisition.value, triage_mode: advancedTriage.value, alert_mode: alert.value, context_mode: context.value, candidate_kinds: [...kinds.querySelectorAll("input:checked")].map((input) => input.value), scope_widening: false, credential_creation: false, provider_write_authority: false, admission_active: false };
  }
  const actions = document.createElement("div"); actions.className = "button-row";
  const previewButton = text("button", "Preview recent effect", "button button-quiet"); previewButton.type = "button";
  previewButton.addEventListener("click", async () => {
    if (state.sourceSettingsPending || state.sourceSettingsRefreshRequired) return;
    const current = ++generation; previewButton.disabled = true;
    try {
      const result = await api("/api/member/v1/settings/source-policies-v2/preview", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ coordinate: source.coordinate, recent_limit: 100, preset: "custom", policy: selectedPolicy() }) });
      if (current !== generation || !form.isConnected || sourcePreferenceInteractionLocked()) return;
      const counts = result.estimated_recent_effect || {};
      preview.replaceChildren(text("strong", `${counts.source_facts || 0} recent source facts · ${counts.triage || 0} for review · ${counts.alerts || 0} alerts · ${counts.context || 0} retained as context`), text("p", `${counts.matching || 0} matches · ${counts.nonmatching || 0} nonmatches · ${counts.changed || 0} changes. Preview only; source history and existing decisions are unchanged.`, "state-note"));
      const examples = document.createElement("ul"); examples.className = "source-preview-examples";
      for (const example of (result.examples || []).filter((value) => !value.synthetic)) {
        const row = document.createElement("li"); const route = example.route || example.proposed || {};
        row.append(text("strong", example.source_title || example.title || example.subject || "Authorized recent source"), text("span", `${sourceRuleRoute(example.previous_route?.triage || example.current_route?.triage || example.before?.triage || example.before)} → ${sourceRuleRoute(route.triage)} · ${sourceRuleRoute(route.alert)} alerts · ${titleCase(route.context || "none")} context`));
        if (example.message_purpose) row.append(text("small", sourcePurposeLabel(example.message_purpose)));
        if (example.reason_codes || example.reasons || route.reasons) row.append(text("small", (example.reason_codes || example.reasons || route.reasons).map(titleCase).join(" · ")));
        if (example.source_event_id) {
          const why = text("button", "Why this handling?", "link-button"); why.type = "button";
          why.addEventListener("click", async () => { if (state.sourceSettingsPending) return; why.disabled = true; try { const result = await api(`/api/member/v1/settings/source-policies-v2/why/${encodeURIComponent(example.source_event_id)}`); if (row.isConnected) row.append(text("p", [result.current?.explanation, result.shadow?.explanation].filter(Boolean).join(" "), "state-note")); } catch (error) { if (row.isConnected) row.append(text("p", error.message, "review-state")); why.disabled = false; } }); row.append(why);
        }
        examples.append(row);
      }
      if (!examples.children.length) examples.append(text("li", "No authorized recent source examples are available. No sample messages were substituted.", "empty"));
      preview.append(examples);
    } catch (error) { if (current === generation) preview.replaceChildren(text("p", `${error.message} Nothing changed.`, "review-state")); }
    finally { if (form.isConnected) sourcePreferenceControlState(previewButton, false); }
  });
  const save = text("button", active ? "Save source preferences" : "Save preview preferences", "button button-primary"); save.type = "submit";
  const reset = text("button", "Reset attention choices", "button button-quiet"); reset.type = "button";
  reset.addEventListener("click", () => { if (state.sourceSettingsPending || state.sourceSettingsRefreshRequired) return; attention.value = "obligations"; advancedTriage.value = "review"; for (const control of purposeControls.values()) control.value = ""; invalidatePreview(); setBanner("Attention choices reset in this form. Save to apply; source access, context, and alerts were preserved."); });
  form.addEventListener("submit", (event) => {
    event.preventDefault(); if (state.sourceSettingsPending || state.sourceSettingsRefreshRequired) return;
    const selected = selectedPolicy(); if (!selected.candidate_kinds.length) { setBanner("Select at least one candidate type. Nothing changed."); return; }
    const body = { expected_revision: matrix.revision, request_id: crypto.randomUUID(), coordinate: source.coordinate, action: "set", preset: "custom", policy: selected };
    runSourcePreferenceMutation(() => api(`/api/member/v1/settings/source-policies-v2/${encodeURIComponent(source.policy_key)}`, { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify(body) }), active ? `${source.label} preferences saved. Source history and provider permissions are unchanged.` : `${source.label} preview preferences saved. Live admission is unchanged.`);
  });
  if (!explicitOnly) actions.append(previewButton, reset);
  actions.append(save); form.append(actions, preview);
  if (explicitOnly) return form;
  const rules = document.createElement("details"); rules.className = "source-similar-rule"; rules.append(text("summary", "Create a narrower rule with exceptions"));
  const coordinate = source.coordinate;
  const hasNativeContainer = coordinate.native_container_kind && coordinate.native_container_kind !== "source" && coordinate.native_container_id;
  const conditions = { provider_account_id: coordinate.provider_account_id, adapter_instance_id: coordinate.adapter_instance_id, ...(coordinate.protected_source_id ? { protected_source_id: coordinate.protected_source_id } : {}), ...(hasNativeContainer ? { native_container_id: coordinate.native_container_id } : {}) };
  rules.append(sourceRuleBuilder({ conditions, label: [source.label, source.account_hint, source.native_container_label].filter(Boolean).join(" · ") }, { canMutate: editable })); form.append(rules);
  return form;
}

function candidateIdentificationSettings(data) {
  const fragment = document.createDocumentFragment();
  const matrix = data.control.source_policy_v2 || { sources: [], presets: [], migrations: [], safety_limitations: [] };
  const legacy = data.control.candidate_identification || { sources: [], non_configurable: [] };
  const active = Boolean(matrix.admission_active || matrix.active);
  const intro = document.createElement("section"); intro.className = "hero-card candidate-policy-intro";
  intro.append(text("p", active ? "YOUR SOURCES" : "YOUR SOURCES · PREVIEW MODE", "eyebrow"), text("h2", "Choose what needs you."), text("p", matrix.explicit_communication_submissions ? "You are the gate: forward selected email, messages, or calendar material to your CoS, or use Add for review. The CoS proposes items in Triage, and you choose what is pushed to your platforms. Personal inboxes are not scanned for obligations." : "Tune each account, conversation, and message purpose. Email, iMessage, and future reviewed sources use the same obligation and learning controls. Disconnected sources keep their existing evidence and candidates."));
  if (!active) intro.append(text("p", "Source preferences are available for preview. Live admission remains on its current policy until this behavior is activated.", "state-note"));
  fragment.append(intro);
  if (!matrix.owner_visible) { const privateState = document.createElement("section"); privateState.className = "state-card"; privateState.append(text("strong", "These policies are owner-private."), text("p", "Return to your own member context to inspect or change source preferences.")); fragment.append(privateState); return fragment; }
  const presets = document.createElement("section"); presets.className = "settings-summary-grid";
  for (const [label, description] of [["Focus on obligations", "Requests, promises, appointments, and open work supported by source evidence."], ["Include possible opportunities", "Also review optional possibilities that may matter to you."], ["Only when I ask", "Keep passive suggestions out of your task queue. Explicit requests remain available."]]) { const card = document.createElement("article"); card.className = "card settings-summary-card"; card.append(text("strong", label), text("small", description)); presets.append(card); }
  if (!matrix.explicit_communication_submissions) fragment.append(presets);
  if ((matrix.migrations || []).length) { const migration = document.createElement("section"); migration.className = "state-card"; migration.append(text("strong", "Some older preferences need an exact account choice."), text("p", `${matrix.migrations.length} source-family preference${matrix.migrations.length === 1 ? "" : "s"} remain retained. They were not copied across every account.`)); fragment.append(migration); }
  const sources = matrix.sources || [];
  const groups = [
    ["Connected and configured sources", sources.filter((source) => !source.direct_authenticated_intent && !["disconnected", "not_connected", "missing_attribution"].includes(source.connection_state)), true],
    ["Disconnected sources and retained history", sources.filter((source) => source.connection_state === "disconnected"), true],
    ["Sources not yet connected", sources.filter((source) => source.connection_state === "not_connected"), false],
    ["Sources needing attribution", sources.filter((source) => source.connection_state === "missing_attribution"), false],
    ["Explicit member requests · always available", sources.filter((source) => source.direct_authenticated_intent), false],
  ];
  for (const [label, items, open] of groups) {
    if (!items.length) continue;
    const section = document.createElement("details"); section.className = "candidate-policy-group"; section.open = open;
    section.append(text("summary", `${label} · ${items.length}`));
    const grid = document.createElement("div"); grid.className = "candidate-policy-grid";
    for (const source of items) grid.append(sourcePolicyV2Card({ ...source, configurable: source.configurable && data.control.identity.can_mutate }, matrix));
    section.append(grid); fragment.append(section);
  }
  if (!sources.length) fragment.append(text("p", "No exact source accounts are available yet. Connect a reviewed source to configure its purposes; retained history remains accessible from All candidates.", "empty"));
  const current = document.createElement("details"); current.className = "candidate-policy-group";
  current.append(text("summary", active ? "Retained source-family preferences" : "Current admission · source-family preferences"));
  current.append(text("p", active ? "These older family preferences remain visible for compatibility. Use exact account and conversation settings above to tune attention." : "These older settings still control admission while the new exact-source controls are in preview mode.", "state-note"));
  const legacyGroups = new Map();
  for (const source of legacy.sources || []) { if (!legacyGroups.has(source.group)) legacyGroups.set(source.group, []); legacyGroups.get(source.group).push(source); }
  for (const [group, items] of legacyGroups) {
    const section = document.createElement("details"); section.className = "candidate-policy-group"; section.append(text("summary", group));
    const grid = document.createElement("div"); grid.className = "candidate-policy-grid";
    const legacyData = active ? { ...data, control: { ...data.control, identity: { ...data.control.identity, can_mutate: false } } } : data;
    for (const source of items) grid.append(candidatePolicyCard(source, legacyData));
    section.append(grid); current.append(section);
  }
  fragment.append(current);
  const invariants = document.createElement("details"); invariants.className = "card candidate-policy-invariants"; invariants.append(text("summary", "Privacy and source history"));
  const list = document.createElement("ul"); list.className = "settings-rule-list"; for (const value of matrix.safety_limitations || []) list.append(text("li", value)); invariants.append(list); fragment.append(invariants);
  return fragment;
}

function defaultPreferenceValue(key, data) {
  const saved = data.control.behavior.preferences || {}; if (saved[key] != null) return saved[key]; const semantic = data.semantics || {};
  const defaults = { information_density: semantic.information_density ?? 2, planning_horizon_days: semantic.planning_horizon_days ?? 7, task_chunk_minutes: semantic.task_chunk_minutes ?? 15, interruption_budget: semantic.proactive_cap ?? 3, reminder_escalation: semantic.reminder_escalation ?? 1, celebration_intensity: semantic.celebration_intensity ?? 1, digest_hour: semantic.digest_hour ?? 8, quiet_start_hour: semantic.quiet_hours?.start ?? 22, quiet_end_hour: semantic.quiet_hours?.end ?? 7, location_cues: semantic.location_cues ?? false }; return defaults[key];
}

function behaviorSettings(data) {
  const fragment = document.createDocumentFragment(); const profiles = document.createElement("section"); profiles.className = "card"; profiles.append(text("h2", "Support profile"), text("p", "Profiles are validated data, not model personalities. They change presentation defaults without changing authority, privacy, or model routing.", "state-note")); const grid = document.createElement("div"); grid.className = "profile-grid";
  for (const profile of data.control.behavior.profiles) { const item = document.createElement("label"); item.className = "profile-choice"; const input = document.createElement("input"); input.type = "radio"; input.name = "profile"; input.value = profile.id; input.checked = profile.id === data.control.behavior.active_profile_id; input.disabled = !data.control.identity.can_mutate; const body = document.createElement("span"); body.append(text("strong", profile.label), text("small", `${titleCase(profile.format)} · ${profile.behavior.available_minutes} minute default steps`)); item.append(input, body); input.addEventListener("change", async () => { if (!input.checked) return; [...grid.querySelectorAll("input")].forEach((node) => { node.disabled = true; }); setBanner(`Activating ${profile.label} as deterministic member defaults…`); try { await api("/api/member/v1/settings/profile", { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify({ profile_id: profile.id, request_id: crypto.randomUUID() }) }); setBanner(`${profile.label} is active. Authority and model routing were unchanged.`); await reloadActiveSurface(); } catch (error) { setBanner(`${error.message} The profile was not changed.`); await reloadActiveSurface(); } }); grid.append(item); }
  profiles.append(grid); fragment.append(profiles);
  const form = document.createElement("form"); form.className = "card preference-form"; form.append(text("h2", "Fine-tune your CoS"), text("p", "These bounded values feed deterministic ranking and presentation. AdministrateMe does not infer them from a diagnosis or conversation.", "state-note")); const fields = document.createElement("div"); fields.className = "preference-grid";
  const definitions = [["information_density", "Information shown at once", 1, 5], ["planning_horizon_days", "Default planning horizon (days)", 1, 14], ["task_chunk_minutes", "Default task chunk (minutes)", 5, 90], ["interruption_budget", "Proactive interruption budget", 0, 12], ["reminder_escalation", "Reminder escalation", 0, 3], ["celebration_intensity", "Celebration intensity", 0, 3], ["digest_hour", "Digest hour", 0, 23], ["quiet_start_hour", "Quiet hours start", 0, 23], ["quiet_end_hour", "Quiet hours end", 0, 23]];
  for (const [name, label, min, max] of definitions) { const input = document.createElement("input"); input.type = "number"; input.name = name; input.min = min; input.max = max; input.required = true; input.value = defaultPreferenceValue(name, data); input.disabled = !data.control.identity.can_mutate; fields.append(field(label, input)); }
  const location = document.createElement("input"); location.type = "checkbox"; location.name = "location_cues"; location.checked = Boolean(defaultPreferenceValue("location_cues", data)); location.disabled = !data.control.identity.can_mutate; const locationLabel = document.createElement("label"); locationLabel.className = "preference-check"; locationLabel.append(location, document.createTextNode(" Allow expiring, typed location cues from an authorized bridge")); fields.append(locationLabel); form.append(fields); const save = text("button", "Save fine-tuning", "button button-primary"); save.type = "submit"; save.disabled = !data.control.identity.can_mutate; form.append(save);
  form.addEventListener("submit", async (event) => { event.preventDefault(); save.disabled = true; const values = {}; for (const [name] of definitions) values[name] = Number(form.elements[name].value); values.location_cues = location.checked; setBanner("Saving bounded CoS preferences…"); try { await api("/api/member/v1/settings/preferences", { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify({ expected_revision: data.control.revision, request_id: crypto.randomUUID(), values }) }); setBanner("CoS preferences saved and propagated to member surfaces."); await reloadActiveSurface(); } catch (error) { setBanner(`${error.message} Preferences were not changed.`); save.disabled = false; } }); fragment.append(form);
  const persona = data.control.behavior.persona; const personaCard = document.createElement("section"); personaCard.className = "card"; personaCard.append(text("h2", "CoS voice"), text("p", persona.label || titleCase(persona.id)), text("small", "Instance-wide persona · wording only · cannot change authority or model routing")); fragment.append(personaCard); return fragment;
}

function bridgeEnrollmentReason(reason) {
  if (reason === "release_unavailable") return "A reviewed release commit is required. Have the installed release checked before retrying enrollment.";
  return reason ? "Enrollment could not finish. Review this recorded failure before retrying." : "Enrollment could not finish. Check its status before trying again.";
}

function bridgeSettings(data) {
  const fragment = document.createDocumentFragment(); fragment.append(appleServicePermissionLink()); const wizard = data.control.bridge_wizard || {}; const current = wizard.current;
  const memberId = data.control.identity.authenticated_member_id;
  const own = data.control.identity.can_mutate && !data.control.identity.viewing_as;
  const pendingKey = `administrateme:bridge-enrollment-pending:${memberId}`;
  let pending = null;
  if (own) {
    try {
      pending = JSON.parse(sessionStorage.getItem(pendingKey) || "null");
      const sameRequest = current && pending && current.request_id === pending.request_id && current.member_id === pending.member_id && current.device_class === pending.device_class && JSON.stringify([...(current.capabilities || [])].sort()) === JSON.stringify(pending.capabilities);
      if (sameRequest) { sessionStorage.removeItem(pendingKey); pending = null; }
    } catch { pending = { request_id: "unavailable" }; }
  }
  let preparationConfirmed = false;
  let helperReady = false;
  let enrollmentSubmit = null;
  function updatePreparation() {
    if (enrollmentSubmit) enrollmentSubmit.disabled = !own || Boolean(pending) || !helperReady || !preparationConfirmed;
  }
  const setup = document.createElement("section"); setup.className = "card bridge-mac-preparation";
  setup.append(text("h2", "1. Prepare your Mac"), text("p", "On the Mac that will run your bridge, download and open the helper, then choose Prepare This Mac. It checks FileVault, Tailscale and developer tools, installs the bridge software, and tells you when preparation is complete. No pairing code or Apple data access is needed yet.", "state-note"));
  const helperStatus = text("p", "Checking the verified Mac helper…", "state-note"); helperStatus.setAttribute("role", "status"); helperStatus.setAttribute("aria-live", "polite");
  const helperDownload = text("button", "Download Mac helper", "button button-primary"); helperDownload.type = "button"; helperDownload.disabled = true;
  const refreshHelper = text("button", "Check helper availability", "button button-secondary"); refreshHelper.type = "button"; refreshHelper.disabled = !own;
  let helperManifest = null;
  async function checkHelper() {
    helperReady = false; helperDownload.disabled = true; refreshHelper.disabled = true; updatePreparation();
    try {
      const result = await api("/api/member/v1/settings/bridges/helper");
      if (!/^[0-9a-f]{40}$/.test(result.release_commit) || !/^[0-9a-f]{64}$/.test(result.sha256) || result.download_url !== "/api/member/v1/settings/bridges/helper/download") throw new Error("The helper release could not be verified.");
      helperManifest = result; helperReady = true; helperDownload.disabled = !own;
      helperStatus.textContent = `Available for Apple-silicon Macs with macOS 15 or later · ${(result.size_bytes / 1048576).toFixed(1)} MB. The download matches this server release.`;
    } catch (error) { helperStatus.textContent = `${error.message} Prepare enrollment after the verified helper is available.`; }
    refreshHelper.disabled = !own; updatePreparation();
  }
  refreshHelper.addEventListener("click", checkHelper);
  helperDownload.addEventListener("click", async () => {
    helperDownload.disabled = true;
    try {
      const response = await fetch(helperManifest.download_url, { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) throw new Error("The helper is unavailable. Check availability again.");
      const bytes = await response.arrayBuffer();
      const digest = [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))].map((value) => value.toString(16).padStart(2, "0")).join("");
      if (digest !== helperManifest.sha256 || bytes.byteLength !== helperManifest.size_bytes) throw new Error("The helper changed or failed verification. Check availability again; this download was not saved.");
      const url = URL.createObjectURL(new Blob([bytes], { type: "application/zip" }));
      const anchor = document.createElement("a"); anchor.href = url; anchor.download = "AdministrateMe-Bridge-Helper.zip"; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 30000);
      helperStatus.textContent = "Verified helper downloaded. Unzip it, open AdministrateMe Bridge Helper, and choose Prepare This Mac. Return here when it says Mac Preparation Complete.";
    } catch (error) { helperStatus.textContent = error.message; }
    helperDownload.disabled = !own;
  });
  const nativeHelp = document.createElement("details"); nativeHelp.append(text("summary", "First-open and Apple account help"), text("p", "This helper is locally signed, not notarized by Apple. If macOS blocks its first launch, review it in System Settings → Privacy & Security and use Open Anyway. The helper does not bypass macOS security. Approve any software installation prompts yourself. Sign in to your Apple account and enable the services you want in macOS before pairing. For a Google calendar displayed in Apple Calendar, select that actual account/calendar; an empty Home calendar is not an error."));
  const confirmation = document.createElement("label"); confirmation.className = "bridge-prepared-confirmation preference-check";
  const prepared = document.createElement("input"); prepared.type = "checkbox"; prepared.disabled = !own; prepared.name = "mac_prepared";
  prepared.addEventListener("change", () => { preparationConfirmed = prepared.checked; updatePreparation(); });
  confirmation.append(prepared, text("span", "The helper says Mac Preparation Complete on the Mac I am connecting."));
  setup.append(helperStatus, helperDownload, refreshHelper, nativeHelp, confirmation); fragment.append(setup);
  if (own) void checkHelper(); else helperStatus.textContent = "Return to your own private view to download the helper and connect your Mac.";
  async function checkEnrollmentStatus() {
    try { await reloadActiveSurface(); }
    catch { setBanner("The enrollment status is not confirmed. Do not repeat the request. Check status again when the connection returns."); }
  }
  if (pending) {
    const uncertain = document.createElement("section"); uncertain.className = "card bridge-enrollment-pending";
    const status = text("p", "The last enrollment request is not confirmed. Do not repeat it. Check status again or have this exact request reviewed before starting another enrollment.", "state-note"); status.setAttribute("role", "status");
    const check = text("button", "Check enrollment status", "button button-secondary"); check.type = "button";
    check.addEventListener("click", async () => { check.disabled = true; await checkEnrollmentStatus(); check.disabled = false; });
    uncertain.append(status, text("small", `Request ${pending.request_id}`), check); fragment.append(uncertain);
  }
  const intro = document.createElement("section"); intro.className = "hero-card bridge-wizard-intro"; intro.append(text("p", "PRIVATE DEVICE CONNECTION", "eyebrow"), text("h2", "Connect Apple Bridge"), text("p", "Native Apple access stays on your Mac. You choose each source, grant Apple permissions locally, and can pause or revoke the bridge at any time. You choose the sources to read during setup. Writing stays off until you enable it for each service; enrollment does not change system observation mode.", "brief-summary"));
  const ownership = document.createElement("ul"); ownership.className = "settings-rule-list"; for (const value of ["Your signed-in identity owns this private bridge.", "Tailscale verifies the device relationship but never substitutes for your consent.", "Apple permission prompts stay under macOS control; the helper cannot click or bypass them.", "No Calendar, Reminders, Contacts, Notes, Messages, or other provider writes are granted."]) ownership.append(text("li", value)); intro.append(ownership); fragment.append(intro);

  const identity = document.createElement("section"); identity.className = "card bridge-member-confirmation"; identity.append(text("p", "MEMBER CONFIRMATION", "eyebrow"), text("h2", data.control.identity.authenticated_display_name), text("p", `Authenticated as ${titleCase(data.control.identity.authenticated_role)} · reading as ${data.control.identity.view_display_name} · actions belong to ${data.control.identity.authenticated_display_name}.`, "state-note")); if (data.control.identity.viewing_as) identity.append(text("p", "View-as is read-only. Return to your own context to connect, pause, repair, or revoke a bridge.", "review-state")); fragment.append(identity);

  if (current) {
    const progress = document.createElement("section"); progress.className = "card bridge-wizard-progress"; progress.append(text("p", "ENROLLMENT PROGRESS", "eyebrow"), text("h2", titleCase(current.state)), text("p", `Request ${current.request_id} · ${titleCase(current.device_class)} · revision ${current.revision}`, "state-note"));
    if (current.state === "failed") {
      const failure = text("p", `This enrollment request was recorded as failed. ${bridgeEnrollmentReason(current.reason)}`, "state-note bridge-enrollment-failure"); failure.setAttribute("role", "status"); progress.append(failure);
    }
    const stages = ["package_ready", "awaiting_transfer", "package_opened", "instance_verified", "member_confirmed", "device_identity_verified", "bridge_paired", "awaiting_permissions", "capabilities_testing", "settling", "active_observation"];
    const stateIndex = stages.includes(current.state) ? (wizard.enrollment_states || []).indexOf(current.state) : -1; const timeline = document.createElement("ol"); timeline.className = "bridge-progress-list"; for (const stage of stages) { const item = text("li", titleCase(stage)); const index = (wizard.enrollment_states || []).indexOf(stage); item.dataset.complete = String(index >= 0 && index <= stateIndex); timeline.append(item); } progress.append(timeline);
    const capabilityGrid = document.createElement("div"); capabilityGrid.className = "bridge-capability-status-grid"; const details = new Map((wizard.capabilities || []).map((item) => [item.capability_id, item])); for (const [capabilityId, capabilityState] of Object.entries(current.capability_states || {})) { const descriptor = details.get(capabilityId) || { label: titleCase(capabilityId) }; const cardNode = document.createElement("article"); cardNode.className = "state-card bridge-capability-status"; cardNode.append(text("strong", descriptor.label), text("span", titleCase(capabilityState.state), `pill state-label state-${String(capabilityState.state).replaceAll("_", "-")}`), text("small", capabilityState.last_successful_observation ? `Last observed ${new Date(capabilityState.last_successful_observation).toLocaleString()}` : capabilityState.reason ? titleCase(capabilityState.reason) : "No provider content is shown here.")); capabilityGrid.append(cardNode); } progress.append(capabilityGrid);
    const buttons = document.createElement("div"); buttons.className = "button-row";
    if (["package_ready", "awaiting_transfer", "package_opened"].includes(current.state)) { const download = text("button", "Download secure handoff", "button button-primary"); download.type = "button"; download.disabled = !own || Boolean(pending); download.addEventListener("click", async () => { download.disabled = true; setBanner("Preparing the encrypted, short-lived handoff…"); try { const response = await fetch(`/api/member/v1/settings/bridges/${encodeURIComponent(current.request_id)}/package`, { method: "POST", credentials: "same-origin", cache: "no-store", headers: { "X-Correlation-Id": crypto.randomUUID() } }); if (!response.ok) throw new Error("The secure handoff is unavailable."); const transferCode = response.headers.get("X-AdministrateMe-Transfer-Code"); if (!transferCode || !navigator.clipboard?.writeText) throw new Error("Secure clipboard access is required to transfer the one-time code."); await navigator.clipboard.writeText(transferCode); const blob = await response.blob(); const url = URL.createObjectURL(blob); const anchor = document.createElement("a"); anchor.href = url; anchor.download = "AdministrateMe Bridge Enrollment.adminmebridge"; anchor.click(); URL.revokeObjectURL(url); setBanner("Package downloaded. The one-time code is in your secure clipboard and was not displayed or placed in the URL."); await reloadActiveSurface(); } catch (error) { setBanner(`${error.message} No transfer secret was displayed.`); download.disabled = false; } }); buttons.append(download); }
    const actions = current.state === "paused"
      ? ["resume", "revoke"]
      : ["partially_active", "degraded", "awaiting_permissions", "bridge_paired"].includes(current.state)
        ? ["pause", "repair", "retry", "revoke"]
        : ["active_observation", "settling", "capabilities_testing"].includes(current.state)
          ? ["pause", "revoke"]
          : current.state === "failed"
            ? ["retry", "revoke"]
            : ["revoked", "expired"].includes(current.state)
              ? []
              : ["revoke"];
    for (const action of actions) { const button = text("button", titleCase(action), action === "revoke" ? "button button-quiet" : "button button-secondary"); button.type = "button"; button.disabled = !own || Boolean(pending); button.addEventListener("click", async () => { button.disabled = true; try { const result = await api(`/api/member/v1/settings/bridges/${encodeURIComponent(current.request_id)}/control`, { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify({ command_id: crypto.randomUUID(), action }) }); setBanner(`Bridge is ${titleCase(result.state)}. No provider effect occurred.`); await reloadActiveSurface(); } catch (error) { setBanner(`${error.message} The bridge was not changed.`); button.disabled = false; } }); buttons.append(button); }
    progress.append(buttons);
    const finishSetup = document.createElement("section"); finishSetup.className = "state-card bridge-finish-setup";
    const permissionLinks = document.createElement("div"); permissionLinks.className = "button-row";
    const appleLink = text("a", "Apple permissions", "button button-secondary"); appleLink.href = "/member/settings?section=apple-services";
    const cosLink = cosPermissionLink(); cosLink.textContent = "CoS permissions";
    permissionLinks.append(appleLink, cosLink);
    finishSetup.append(text("h3", "3. Check sync and choose permissions"), text("p", "Check each selected source above for a successful sync. A healthy empty source has no items to show. Choose service writing permissions and review your private CoS permissions separately. Each actual write still requires its own approval."), permissionLinks);
    progress.append(finishSetup); fragment.append(progress);
  }

  if (!current || ["revoked", "expired", "failed"].includes(current.state)) {
    const form = document.createElement("form"); form.className = "card bridge-form"; form.append(text("p", "SELF-SERVICE WIZARD", "eyebrow"), text("h2", "2. Pair your prepared Mac"), text("p", "Once preparation is complete, review your source choices and create a fresh 30-minute handoff. Open it with the helper on that Mac and approve the native permissions. Installation time will not consume this pairing window.", "state-note"));
    const selfCandidate = (data.control.bridge_candidates || []).find((item) => item.member_id === data.control.identity.authenticated_member_id); const member = document.createElement("input"); member.type = "text"; member.value = selfCandidate?.label || data.control.identity.authenticated_display_name; member.readOnly = true; form.append(field("Bridge owner", member));
    const devices = document.createElement("fieldset"); devices.className = "bridge-device-choice"; devices.append(text("legend", "Which Mac?")); for (const [value, label, note] of [["this_mac", "This Mac", "Download and open the handoff here."], ["another_mac", "Another Mac", "Transfer the package and one-time clipboard code. Tailscale and the reviewed helper are required."]]) { const row = document.createElement("label"); const input = document.createElement("input"); input.type = "radio"; input.name = "device_class"; input.value = value; input.checked = value === "this_mac"; input.disabled = !data.control.identity.can_mutate; const copy = document.createElement("span"); copy.append(text("strong", label), text("small", note)); row.append(input, copy); devices.append(row); } form.append(devices);
    const capabilities = document.createElement("fieldset"); capabilities.className = "bridge-capabilities"; capabilities.append(text("legend", "Choose read-only sources")); for (const descriptor of wizard.capabilities || []) { const wrap = document.createElement("label"); wrap.className = "bridge-capability-choice"; const input = document.createElement("input"); input.type = "checkbox"; input.name = "capability"; input.value = descriptor.capability_id; input.checked = ["apple_calendar", "apple_reminders"].includes(descriptor.capability_id); input.disabled = !data.control.identity.can_mutate || descriptor.implementation !== "available" || descriptor.enrollment_selectable === false; const copy = document.createElement("span"); copy.append(text("strong", descriptor.label), text("small", `${descriptor.administrateme_read_ceiling} · Write ceiling: ${descriptor.administrateme_write_ceiling}`), text("small", descriptor.local_permission)); if (descriptor.enrollment_note) copy.append(text("small", descriptor.enrollment_note)); if (descriptor.implementation !== "available") copy.append(text("span", titleCase(descriptor.implementation), "pill state-label state-unknown")); wrap.append(input, copy); capabilities.append(wrap); } form.append(capabilities);
    const review = document.createElement("section"); review.className = "state-card bridge-review"; review.append(text("strong", "Review"), text("p", "The package binds this member, this instance, the selected device class, exact capabilities, compatible release, and expiry. Pairing alone activates no source. Each connector waits for local permission evidence, adapter authentication, central attestation, and compatible release."), text("p", "Revocation stops central ingestion. Enrollment never creates provider action authority.", "state-note")); form.append(review);
    const submit = text("button", "Prepare enrollment", "button button-primary"); submit.type = "submit"; submit.disabled = !data.control.identity.can_mutate || !selfCandidate; form.append(submit); enrollmentSubmit = submit; updatePreparation();
    if (pending) for (const control of form.querySelectorAll("input,button")) control.disabled = true;
    form.addEventListener("submit", async (event) => {
      event.preventDefault(); if (!own || pending || submit.disabled) return;
      const selected = [...form.querySelectorAll('input[name="capability"]:checked')].map((input) => input.value).sort();
      const deviceClass = form.querySelector('input[name="device_class"]:checked')?.value;
      if (!selected.length) { setBanner("Choose at least one available bridge capability."); return; }
      const body = { expected_revision: data.control.revision, request_id: crypto.randomUUID(), member_id: memberId, device_class: deviceClass, capabilities: selected };
      try { sessionStorage.setItem(pendingKey, JSON.stringify(body)); pending = body; }
      catch { setBanner("This browser cannot safely retain the request reference. No enrollment request was sent."); return; }
      for (const control of form.querySelectorAll("input,button")) control.disabled = true;
      setBanner("Creating an encrypted, signed, short-lived bridge handoff…");
      try {
        const result = await api("/api/member/v1/settings/bridges", { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify(body) });
        setBanner(result.accepted === true ? result.next_step : "The enrollment response was incomplete. Checking the recorded request…");
      } catch (error) {
        const detail = error.body?.detail;
        const notStarted = error.status === 409 && detail?.error === "enrollment_not_started" && detail.request_id === body.request_id && detail.request_recorded === false;
        const invalidBody = error.status === 422 && Array.isArray(detail) && detail.length > 0 && detail.every((item) => Array.isArray(item.loc) && item.loc[0] === "body" && typeof item.type === "string");
        if (notStarted || invalidBody) {
          // Only these explicit pre-append refusals prove no enrollment was
          // recorded. Every transport or preparation failure stays pending.
          try { sessionStorage.removeItem(pendingKey); pending = null; } catch { /* Keep the safe lock if storage is unavailable. */ }
          setBanner(notStarted ? "Settings changed or an enrollment is already active. Review the refreshed status before preparing another request." : "The enrollment choices were rejected before recording. Review the refreshed choices before trying again.");
        } else {
          setBanner(detail?.error === "release_unavailable" ? bridgeEnrollmentReason(detail.error) : "Enrollment did not return a confirmed result. Checking the recorded request…");
        }
      }
      // A failed response can follow a durable Failed record. Never assume
      // nothing was created, and never replay POST after an uncertain result.
      await checkEnrollmentStatus();
      if (form.isConnected) {
        const check = text("button", "Check enrollment status", "button button-secondary"); check.type = "button";
        check.addEventListener("click", async () => { check.disabled = true; await checkEnrollmentStatus(); check.disabled = false; }); form.append(check);
      }
    }); fragment.append(form);
  }
  const reply = data.control.apple_messages_replies || { state: "implementation_unavailable", revision: 0, prohibited: [] };
  const replyCard = document.createElement("section"); replyCard.className = "card apple-messages-reply-control";
  replyCard.append(text("p", "MEMBER-OWNED WRITE CAPABILITY", "eyebrow"), text("h2", "Enable replies from Comms"), text("span", titleCase(reply.state), `pill state-label state-${String(reply.state).replaceAll("_", "-")}`));
  const replyFacts = document.createElement("dl"); replyFacts.className = "settings-facts";
  for (const [label, value] of [["Exact member", data.control.identity.authenticated_display_name], ["Exact bridge", reply.bridge_enrollment_id || "Not connected"], ["Messages account/source", reply.provider_account_id ? `${reply.provider_account_id} · ${reply.source_class}` : "Unavailable"], ["Transport capability", reply.transport ? `${reply.transport} · BlueBubbles ${reply.transport_version || "unverified"}` : "Unsupported"], ["Observation and settlement", reply.observation_settled ? "Settled" : "Not settled"], ["Approval", "Ask every time for the exact body and destination"]]) replyFacts.append(text("dt", label), text("dd", value));
  replyCard.append(replyFacts, text("p", reply.reason || "This permits only a plain-text reply to an exact existing observed chat. Enabling sends nothing.", "state-note"));
  const remains = document.createElement("details"); remains.className = "settings-technical"; remains.append(text("summary", "What remains prohibited")); const prohibited = document.createElement("ul"); prohibited.className = "settings-rule-list"; for (const value of reply.prohibited || []) prohibited.append(text("li", value)); remains.append(prohibited); replyCard.append(remains);
  const replyActions = document.createElement("div"); replyActions.className = "button-row";
  const availableActions = reply.state === "enabled_ask_every_time" ? ["pause", "revoke"] : reply.state === "paused" ? ["enable", "revoke"] : reply.state === "available_but_disabled" ? ["enable", "revoke"] : [];
  for (const action of availableActions) { const button = text("button", action === "enable" ? "Enable ask-every-time replies" : titleCase(action), action === "revoke" ? "button button-quiet" : "button button-primary"); button.type = "button"; button.disabled = !data.control.identity.can_mutate; button.addEventListener("click", async () => { button.disabled = true; setBanner(`${titleCase(action)} Messages replies…`); try { const result = await api("/api/member/v1/settings/apple-messages-replies", { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify({ expected_revision: reply.revision || 0, request_id: crypto.randomUUID(), action }) }); setBanner(`Messages replies are ${titleCase(result.state)}. Enabling or changing this setting produced ${result.provider_effects || 0} provider effects.`); await reloadActiveSurface(); } catch (error) { setBanner(`${error.message} Messages reply authority was not changed.`); button.disabled = false; } }); replyActions.append(button); }
  replyCard.append(replyActions, text("p", "Pause and revoke take effect before bridge delivery. Revocation is terminal for this authority; re-enrollment is required to create a new one. New conversations and CoS-autonomous sending are never granted.", "review-state")); fragment.append(replyCard); return fragment;
}

function privacySettings(data) {
  const fragment = document.createDocumentFragment(); const rules = document.createElement("section"); rules.className = "card"; rules.append(text("h2", "Privacy rules that cannot be toggled")); const list = document.createElement("ul"); list.className = "settings-rule-list"; for (const value of ["Adults control their own private provider credentials and device bridges.", "A principal may manage a child bridge, but cannot silently inspect another adult’s private sources.", "Provider-native sharing does not create AdministrateMe sharing.", "View-as changes what can be read; it never changes who performs an action.", "Privileged records never enter model processing or generic OpenClaw memory."]) list.append(text("li", value)); rules.append(list); fragment.append(rules);
  const household = document.createElement("section"); household.className = "card"; household.append(text("h2", "Household portal identities")); const members = document.createElement("ul"); members.className = "household-settings-list"; for (const member of data.control.household) { const item = document.createElement("li"); const body = document.createElement("div"); body.append(text("strong", member.label), text("small", titleCase(member.role))); item.append(body, text("span", member.portal_identity_bound ? "Tailscale bound" : "No portal login", `pill state-label state-${member.portal_identity_bound ? "healthy" : "not-configured"}`)); members.append(item); } household.append(members); fragment.append(household, registrySettings(data)); return fragment;
}

async function runRegistryLifecycle(kind, item, action, reason = null) {
  const id = item[`${kind}_id`];
  setBanner(`${action === "archive" ? "Archiving" : "Restoring"} ${item.display_name}…`);
  try {
    await api(`/api/member/v1/registry/${encodeURIComponent(kind)}/${encodeURIComponent(id)}/lifecycle`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
      body: JSON.stringify({ request_id: crypto.randomUUID(), expected_last_event_id: item.last_event_id, action, reason }),
    });
    setBanner(action === "archive" ? "Archived without erasing history. Linked records remain intact." : "Restored as an active household record.");
    await reloadActiveSurface();
  } catch (error) {
    setBanner(`${error.message} The household record was not changed.`);
  }
}

function archiveRegistryDialog(kind, item) {
  const form = document.createElement("form"); form.className = "dialog-form";
  form.append(text("p", "Archiving removes this record from active planning while preserving its links and complete event history.", "state-note"));
  const reason = document.createElement("textarea"); reason.rows = 3; reason.maxLength = 500;
  const buttons = document.createElement("div"); buttons.className = "button-row";
  const keep = text("button", "Keep active", "button button-quiet"); keep.type = "button"; keep.addEventListener("click", closeActionDialog);
  const archive = text("button", "Archive record", "button button-primary"); archive.type = "submit"; buttons.append(keep, archive);
  form.append(field("Reason (optional)", reason), buttons);
  form.addEventListener("submit", async (event) => { event.preventDefault(); closeActionDialog(); await runRegistryLifecycle(kind, item, "archive", reason.value.trim() || null); });
  openActionDialog(`Archive ${item.display_name}?`, form);
}

function registrySettings(data) {
  const fragment = document.createDocumentFragment();
  const intro = document.createElement("section"); intro.className = "state-card"; intro.append(text("strong", "Household objects have a reversible lifecycle."), text("p", "Archive stale places, assets, or service accounts without deleting evidence. Provider financial accounts remain read-only provider facts and are managed under Connections.")); fragment.append(intro);
  const registry = data.registry || {};
  for (const [plural, label] of [["places", "Places"], ["assets", "Assets"], ["accounts", "Household service accounts"]]) {
    const kind = plural.slice(0, -1); const section = document.createElement("section"); section.className = "card"; section.append(text("h2", label));
    const rows = registry[plural] || [];
    if (!rows.length) section.append(text("p", `No ${label.toLowerCase()} are recorded.`, "empty"));
    const list = document.createElement("ul"); list.className = "household-settings-list";
    for (const item of rows) {
      const row = document.createElement("li"); const body = document.createElement("div"); const status = item.lifecycle_status || "active";
      body.append(text("strong", item.display_name), text("small", `${titleCase(item.kind)} · ${titleCase(status)}${item.archive_reason ? ` · ${item.archive_reason}` : ""}`));
      const button = text("button", status === "archived" ? "Restore" : "Archive…", status === "archived" ? "button button-primary" : "button button-quiet"); button.type = "button"; button.disabled = !data.control.identity.can_mutate;
      button.addEventListener("click", () => status === "archived" ? runRegistryLifecycle(kind, item, "restore") : archiveRegistryDialog(kind, item));
      row.append(body, button); list.append(row);
    }
    section.append(list); fragment.append(section);
  }
  return fragment;
}

function systemSettings(data) {
  const fragment = document.createDocumentFragment(); const safety = document.createElement("section"); safety.className = "card settings-safety"; safety.append(text("p", "EXTERNAL-WRITE SAFETY", "eyebrow"), text("h2", data.system.observation.active ? "Observation mode is protecting providers" : "Provider writes are enabled behind every gate"), text("p", data.system.observation.explanation)); const safetyFacts = document.createElement("dl"); safetyFacts.className = "settings-facts"; for (const [label, value] of [["External writes execute", data.system.observation.writes_execute ? "Only after all gates" : "No"], ["Python topology", data.system.runtime.architecture], ["OpenClaw health", titleCase(data.system.runtime.openclaw)], ["OpenClaw exact pin", `${data.system.runtime.openclaw_version} · ${String(data.system.runtime.openclaw_commit).slice(0, 12)}`], ["Pin compatibility", titleCase(data.system.runtime.compatibility)], ["Active member agent", data.system.runtime.member_agent_id], ["Peer session scope", data.system.runtime.peer_session_scope], ["Release", titleCase(data.system.runtime.release_state)], ["Backup / rollback", titleCase(data.system.runtime.backup_state)], ["Registered projections", `${data.system.runtime.projection_count} (constitutional count: 15)`]]) safetyFacts.append(text("dt", label), text("dd", value)); safety.append(safetyFacts, text("p", "OpenClaw owns conversations, isolated peer sessions, workspaces, model routing, cron, and its own tool approval. AdministrateMe owns typed household truth, privacy, domain authority, provenance, observation, and reconciliation.", "state-note")); fragment.append(safety);
  const assuranceCapability = data.system.capabilities.find((value) => value.name === "source-assurance");
  if (assuranceCapability?.details?.counts) { const counts = assuranceCapability.details.counts; const assurance = document.createElement("section"); assurance.className = "card"; assurance.append(text("p", "SOURCE ASSURANCE", "eyebrow"), text("h2", assuranceCapability.state === "healthy" ? "Every processed source has an accounted disposition" : "A source fact is missing its required disposition")); const facts = document.createElement("dl"); facts.className = "settings-facts"; for (const [label, value] of [["Source facts", counts.source_facts], ["Accounted obligations", `${counts.accounted} / ${counts.obligations}`], ["Candidate sources", `${counts.eligible_candidates} proposed · ${counts.no_candidate} none`], ["Quarantined / failed", `${counts.quarantined} / ${counts.failed}`], ["Processing / missing", `${counts.pending} / ${counts.missing}`]]) facts.append(text("dt", label), text("dd", value)); assurance.append(facts, text("p", "Empty is a recorded outcome. Missing means the expected assessor, projection, or terminal action result did not arrive after its checkpoint.", "state-note")); fragment.append(assurance); }
  const workbooks = document.createElement("section"); workbooks.className = "card"; workbooks.append(text("h2", "Recovery and workbooks")); const workbookList = document.createElement("ul"); workbookList.className = "household-settings-list"; for (const workbook of data.system.workbooks) { const item = document.createElement("li"); const body = document.createElement("div"); body.append(text("strong", workbook.name), text("small", workbook.updated_at ? `Updated ${new Date(workbook.updated_at).toLocaleString()}` : "No workbook file yet")); item.append(body, text("span", titleCase(workbook.state), `pill state-label state-${String(workbook.state).replaceAll("_", "-")}`)); workbookList.append(item); } workbooks.append(workbookList); fragment.append(workbooks);
  const advanced = document.createElement("details"); advanced.className = "card settings-advanced"; advanced.append(text("summary", "Advanced capability inventory"), text("p", "Pack validation, configuration and runtime provider health are different facts. This inventory is diagnostic and does not grant a capability.", "state-note")); const list = document.createElement("ul"); list.className = "advanced-capability-list"; for (const capability of data.system.capabilities) { const item = document.createElement("li"); const body = document.createElement("div"); body.append(text("strong", capability.name), text("small", capability.kind)); item.append(body, text("span", titleCase(capability.state), `pill state-label state-${String(capability.state).replaceAll("_", "-")}`)); list.append(item); } advanced.append(list); fragment.append(advanced); return fragment;
}

function accountSettings(data) {
  const fragment = document.createDocumentFragment(); fragment.append(identitySummary(data.control.identity)); const explanation = document.createElement("section"); explanation.className = "card"; explanation.append(text("h2", "How sign-in works"), text("p", "Tailscale Serve authenticates the connecting user. AdministrateMe accepts that identity only from its loopback ingress and maps it to one reviewed household member."), text("p", "There is no separate AdministrateMe password or simulated logout. To change identity, switch the signed-in Tailscale user; bootstrap enrollment must review any mapping change.", "state-note")); fragment.append(explanation); return fragment;
}

const COS_PRIVATE_PERMISSIONS = [
  ["read_context", "Read my private AdministrateMe context", "Includes interpreting material I forward to CoS and checking my reply results. This does not connect or monitor my personal inboxes."],
  ["prepare_external_actions", "Prepare external actions", "Needed to prepare replies. Every actual send still needs my exact-text approval and a separate sending approval."],
];

function cosPermissionLink() {
  const link = text("a", "Review my CoS permissions", "button button-secondary");
  link.href = "/member/settings?section=cos-work";
  return link;
}

function cosPrivatePermissionsCard(data, updateDelegations) {
  const card = document.createElement("section"); card.className = "card cos-private-permissions";
  const identity = data.control.identity;
  const own = identity.can_mutate && identity.authenticated_role === "principal" && !identity.viewing_as && identity.authenticated_member_id === identity.view_member_id && identity.actions_as === identity.reads_as;
  card.append(text("h2", "My CoS permissions"), text("p", "Choose what CoS may do in your private AdministrateMe context. These permissions do not grant access to credentials, personal inboxes, another adult’s information, or automatic sending.", "state-note"));
  if (!own) {
    card.append(text("p", "Only a principal in their own private view can review or change these permissions. Return to your own view to continue.", "empty"));
    return card;
  }
  const memberId = identity.authenticated_member_id;
  const pendingKey = `administrateme:cos-permission-pending:${memberId}`;
  let pending = null; let busy = false; let loaded = false; let rows = [];
  try { pending = JSON.parse(sessionStorage.getItem(pendingKey) || "null"); } catch { pending = { action: "unknown" }; }
  const list = document.createElement("ul"); list.className = "settings-contract-list";
  const status = text("p", "Checking your current permissions…", "state-note"); status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
  const form = document.createElement("form"); form.className = "cos-permission-form";
  const choices = document.createElement("fieldset"); choices.append(text("legend", "Permissions I choose"));
  for (const [value, label, description] of COS_PRIVATE_PERMISSIONS) {
    const wrap = document.createElement("label"); wrap.className = "bridge-capability-choice";
    const input = document.createElement("input"); input.type = "checkbox"; input.name = "permission"; input.value = value;
    const copy = document.createElement("span"); copy.append(text("strong", label), text("small", description)); wrap.append(input, copy); choices.append(wrap);
  }
  const duration = selectControl([{ value: "15", label: "15 minutes" }, { value: "60", label: "1 hour" }, { value: "until_revoked", label: "Until revoked" }], "15", "");
  const review = text("button", "Review permissions", "button button-primary"); review.type = "submit";
  const refresh = text("button", "Check permission status", "button button-secondary"); refresh.type = "button";
  form.append(choices, field("Permission duration", duration), review);
  card.append(list, form, status, refresh);
  function ownRows(items) { return items.filter((item) => item.context_scope === "member_private" && item.context_member_id === memberId && item.requested_by_member_id === memberId); }
  function active(item) { return item.active && !item.revoked_at && (!item.expires_at || Date.parse(item.expires_at) > Date.now()); }
  function expiry(item) { return item.expires_at ? `Expires ${new Date(item.expires_at).toLocaleString()}` : "Until revoked"; }
  function permissionLabels(permissions) { return permissions.map((value) => COS_PRIVATE_PERMISSIONS.find(([key]) => key === value)?.[1] || titleCase(value)).join(", "); }
  function lock() {
    const blocked = busy || !loaded || Boolean(pending) || rows.some(active);
    for (const control of form.querySelectorAll("input,select,button")) control.disabled = blocked;
    review.disabled = blocked || !form.querySelector('input[name="permission"]:checked');
    for (const button of list.querySelectorAll("button")) button.disabled = busy || Boolean(pending);
    refresh.disabled = busy;
  }
  function savePending(value) {
    // Persist before the request so navigation cannot turn an uncertain write
    // into a fresh grant. An uncertain result is reconciled by its exact ID.
    if (value) sessionStorage.setItem(pendingKey, JSON.stringify(value));
    else sessionStorage.removeItem(pendingKey);
    pending = value;
  }
  function renderRows() {
    list.replaceChildren();
    for (const item of rows) {
      const row = document.createElement("li"); row.dataset.grantId = item.grant_id;
      const stateLabel = item.revoked_at ? "Revoked" : item.expires_at && Date.parse(item.expires_at) <= Date.now() ? "Expired" : active(item) ? "Active" : "Inactive";
      row.append(text("strong", `${identity.authenticated_display_name} · Private · ${stateLabel}`), text("small", permissionLabels(item.permissions || [])), text("small", expiry(item)));
      if (active(item)) {
        const revoke = text("button", "Revoke these permissions…", "button button-quiet"); revoke.type = "button";
        revoke.addEventListener("click", () => confirmRevocation(item)); row.append(revoke);
      }
      list.append(row);
    }
    if (!rows.length) list.append(text("li", "You have no private CoS permission grants.", "empty"));
    lock();
  }
  async function reconcile() {
    busy = true; lock();
    try {
      const query = pending?.action === "create" && pending.request_id ? `?request_id=${encodeURIComponent(pending.request_id)}` : "";
      const result = await api(`/api/member/v1/cos/delegations${query}`);
      rows = ownRows(result.items || []); loaded = true;
      updateDelegations?.(result.items || []);
      if (pending) {
        const confirmation = result.request_confirmation;
        const requestedId = confirmation?.request_id === pending.request_id && confirmation.state === "recorded" ? confirmation.grant_id : null;
        const confirmedId = pending.action === "create" ? (requestedId && (!pending.grant_id || pending.grant_id === requestedId) ? requestedId : null) : pending.grant_id;
        const match = confirmedId ? rows.find((item) => item.grant_id === confirmedId) : null;
        const sameCreate = match && pending.action === "create" && pending.body && match.context_scope === pending.body.context_scope && match.context_member_id === pending.body.context_member_id && JSON.stringify([...(match.permissions || [])].sort()) === JSON.stringify(pending.body.permissions) && (match.expires_at ? Date.parse(match.expires_at) : null) === (pending.body.expires_at ? Date.parse(pending.body.expires_at) : null);
        if (sameCreate || (match && pending.action === "revoke" && match.revoked_at)) savePending(null);
      }
      status.textContent = pending ? "The last permission change is not confirmed. Do not repeat it. Check status again or have this exact request reviewed before making another change." : rows.some(active) ? "Your permissions are shown above. Revoke an active grant before choosing a replacement; existing permissions are never changed automatically." : "Choose each permission explicitly, then review its scope and expiry. Nothing is granted by opening this page.";
    } catch (error) {
      loaded = false;
      status.textContent = `${error.message} Current permission status is unconfirmed. No new change is allowed until it can be checked.`;
    } finally { busy = false; renderRows(); }
  }
  async function submitChange(change, method, path, body) {
    if (busy || pending || !loaded) return;
    try { savePending(change); } catch {
      status.textContent = "This browser cannot safely retain the request reference. No permission change was requested."; return;
    }
    busy = true; lock(); closeActionDialog(); status.textContent = "Recording your exact permission decision…";
    try {
      const result = await api(path, { method, headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify(body) });
      if (change.action === "create" && result.grant_id) savePending({ ...change, grant_id: result.grant_id });
    } catch (error) {
      if (change.action === "create" && error.status === 409 && error.body?.detail?.code === "private_delegation_already_active") {
        // This exact rejection is definitive: the serialized creation guard
        // refused the append. Every other uncertain result retains its lock.
        savePending(null); setBanner(error.message);
      }
      // Never replay POST/DELETE after any error. Refresh the authoritative
      // list, including when another tab already created the active grant.
    } finally { busy = false; await reconcile(); }
  }
  function confirmRevocation(item) {
    if (busy || pending || !loaded || !active(item)) return;
    const content = document.createElement("form"); content.className = "dialog-form";
    content.append(text("p", `${identity.authenticated_display_name} · My private AdministrateMe context`), text("p", permissionLabels(item.permissions || [])), text("p", expiry(item)), text("p", "Revoke this grant? CoS will no longer have these permissions from it. This does not delete your history or recall messages already sent.", "state-note"));
    const cancel = text("button", "Keep permissions", "button button-quiet"); cancel.type = "button"; cancel.addEventListener("click", closeActionDialog);
    const confirm = text("button", "Revoke these permissions", "button button-primary"); confirm.type = "submit"; content.append(cancel, confirm);
    content.addEventListener("submit", (event) => { event.preventDefault(); confirm.disabled = true; const requestId = crypto.randomUUID(); void submitChange({ action: "revoke", request_id: requestId, grant_id: item.grant_id }, "DELETE", `/api/member/v1/cos/delegations/${encodeURIComponent(item.grant_id)}`, { request_id: requestId, reason: "Member revoked own private CoS permissions in Settings" }); });
    openActionDialog("Revoke my CoS permissions?", content);
  }
  form.addEventListener("change", lock);
  refresh.addEventListener("click", () => { void reconcile(); });
  form.addEventListener("submit", (event) => {
    event.preventDefault(); if (busy || pending || !loaded || rows.some(active)) return;
    const permissions = [...form.querySelectorAll('input[name="permission"]:checked')].map((input) => input.value).sort(); if (!permissions.length) return;
    const expiresAt = duration.value === "until_revoked" ? null : new Date(Date.now() + Number(duration.value) * 60_000).toISOString();
    const content = document.createElement("form"); content.className = "dialog-form cos-permission-confirmation";
    content.append(text("p", `${identity.authenticated_display_name} · My private AdministrateMe context`), text("p", permissionLabels(permissions)), text("p", expiry({ expires_at: expiresAt })), text("p", "Only these selected permissions will be granted to the household CoS for my private context. Personal inbox access and automatic sending remain off. Every actual reply still needs both approvals.", "state-note"));
    const cancel = text("button", "Go back", "button button-quiet"); cancel.type = "button"; cancel.addEventListener("click", closeActionDialog);
    const confirm = text("button", "Grant these permissions", "button button-primary"); confirm.type = "submit"; content.append(cancel, confirm);
    content.addEventListener("submit", (submit) => {
      submit.preventDefault(); if (expiresAt && Date.parse(expiresAt) <= Date.now()) { confirm.disabled = true; content.append(text("p", "This review has expired. Go back and review a new expiry.", "state-note")); return; }
      confirm.disabled = true; const requestId = crypto.randomUUID();
      const body = { request_id: requestId, context_scope: "member_private", context_member_id: memberId, permissions, expires_at: expiresAt, require_no_active_private_grant: true };
      void submitChange({ action: "create", request_id: requestId, body }, "POST", "/api/member/v1/cos/delegations", body);
    });
    openActionDialog("Grant my CoS permissions?", content);
  });
  lock(); void reconcile(); return card;
}

function financeMetadataSettingsCard(data) {
  const card = document.createElement("section"); card.className = "card finance-metadata-settings";
  const identity = data.control.identity;
  card.append(text("h2", "Books connection metadata"), text("p", "Inspect your retained Books bindings and publication history. This shows identifiers and completeness, without financial values. Daily refresh remains separate."));
  if (!identity.can_mutate || identity.authenticated_role !== "principal" || identity.viewing_as || identity.authenticated_member_id !== identity.view_member_id) {
    card.append(text("p", "Available only in your own authenticated principal view.", "state-note")); return card;
  }
  let policy = null; let busy = false;
  const status = text("p", "Checking metadata access…", "state-note"); status.setAttribute("role", "status");
  const form = document.createElement("form");
  const owner = document.createElement("input"); owner.type = "checkbox"; owner.name = "owner_metadata";
  const cos = document.createElement("input"); cos.type = "checkbox"; cos.name = "cos_metadata";
  for (const [input, label] of [[owner, "Allow me to inspect my Books metadata"], [cos, "Allow CoS to inspect my Books metadata"]]) {
    const wrap = document.createElement("label"); wrap.className = "bridge-capability-choice"; wrap.append(input, text("span", label)); form.append(wrap);
  }
  form.append(text("p", "Your own access needs no CoS grant. CoS also needs your existing private-context read permission; this control does not grant it or enable financial advice.", "state-note"));
  const save = text("button", "Save metadata access", "button button-primary"); save.type = "submit";
  const refresh = text("button", "Check metadata access", "button button-secondary"); refresh.type = "button";
  form.append(save, refresh);
  const book = document.createElement("input"); book.type = "text"; book.maxLength = 120;
  const household = document.createElement("input"); household.type = "text"; household.maxLength = 120;
  const selection = document.createElement("details"); selection.append(text("summary", "Select known bindings (optional)"), field("Existing book ID", book), field("Existing household Party ID", household));
  const inspect = text("button", "Inspect retained bindings", "button button-secondary"); inspect.type = "button";
  const output = document.createElement("div"); output.className = "finance-metadata-result";
  function controls() { for (const el of [owner, cos, save, refresh, book, household, inspect]) el.disabled = busy || !policy; refresh.disabled = busy; inspect.disabled = busy || !policy?.owner_read_enabled; }
  function apply(value) { policy = value; owner.checked = value.owner_read_enabled; cos.checked = value.cos_read_enabled; controls(); }
  async function reload() {
    busy = true; controls(); output.replaceChildren();
    try { apply(await api("/api/member/v1/settings/finance-metadata")); status.textContent = policy.owner_read_enabled ? "Your metadata access is enabled." : "Your metadata access is disabled."; }
    catch (error) { policy = null; status.textContent = `Access could not be verified. ${error.message}`; }
    finally { busy = false; controls(); }
  }
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); if (busy || !policy) return;
    busy = true; controls(); output.replaceChildren();
    try {
      apply(await api("/api/member/v1/settings/finance-metadata", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ request_id: crypto.randomUUID(), expected_revision: policy.revision, owner_read_enabled: owner.checked, cos_read_enabled: cos.checked }) }));
      status.textContent = policy.owner_read_enabled ? "Your metadata access is enabled." : "Your metadata access is disabled. History is preserved.";
    } catch (error) { policy = null; status.textContent = `Save could not be verified. Check metadata access before trying again. ${error.message}`; }
    finally { busy = false; controls(); }
  });
  refresh.addEventListener("click", () => { void reload(); });
  inspect.addEventListener("click", async () => {
    if (busy || !policy?.owner_read_enabled) return;
    busy = true; controls(); output.replaceChildren();
    try {
      const result = await api("/api/member/v1/finance/bindings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ book_id: book.value.trim() || null, household_party_id: household.value.trim() || null }) });
      output.dataset.state = result.state;
      output.append(text("h3", titleCase(result.state)), text("p", result.qualification || "Unavailable or incomplete access is not evidence of empty history."));
      const details = document.createElement("details"); const receipt = text("pre", JSON.stringify(result, null, 2)); receipt.style.whiteSpace = "pre-wrap"; receipt.style.overflowWrap = "anywhere"; details.append(text("summary", "Exact binding receipt"), receipt); output.append(details);
    } catch (error) { status.textContent = `Metadata could not be read. ${error.message}`; }
    finally { busy = false; controls(); }
  });
  card.append(status, form, selection, inspect, output); controls(); void reload(); return card;
}

function howCosWorksSettings(data) {
  const fragment = document.createDocumentFragment(); const model = data.control.how_my_cos_works || {}; const identity = data.control.cos_identity || {};
  const identityCard = document.createElement("section"); identityCard.className = "card settings-safety"; identityCard.append(text("p", "SAME COS · ISOLATED MEMBER CONTEXT", "eyebrow"), text("h2", "One household service principal, one private adult context")); const facts = document.createElement("dl"); facts.className = "settings-facts"; for (const [label, value] of [["Service principal", identity.service_principal_id], ["Household Party", identity.party_id], ["Active member", identity.active_member_context], ["OpenClaw agent", identity.openclaw_agent_id], ["Shared generic memory", identity.generic_member_memory_shared ? "Yes" : "No"], ["Raw cross-member context", identity.raw_cross_member_context ? "Yes" : "No"]]) facts.append(text("dt", label), text("dd", value)); identityCard.append(facts); fragment.append(identityCard);
  const agreements = document.createElement("section"); agreements.className = "card"; agreements.append(text("h2", "Effective operating agreements"), text("p", "Typed, versioned agreements are authority. Markdown and conversational wording are not.", "state-note")); const agreementList = document.createElement("ul"); agreementList.className = "settings-contract-list"; for (const item of model.agreements || []) { const row = document.createElement("li"); row.append(text("strong", `${titleCase(item.layer)} · ${titleCase(item.trust_stage)}`), text("small", `${titleCase(item.authority_scope)} · revision ${item.revision} · ${titleCase(item.effective_state)}`), text("small", `May observe: ${(item.may_observe || []).map(titleCase).join(", ") || "None"} · May prepare: ${(item.may_prepare || []).map(titleCase).join(", ") || "None"}`), text("small", item.provider_execution_permitted ? "Provider execution may still require exact approval." : "No provider execution authority.")); agreementList.append(row); } if (!agreementList.children.length) agreementList.append(text("li", "No effective agreement is visible in this context.", "empty")); agreements.append(agreementList); fragment.append(agreements);
  const delegations = document.createElement("details"); delegations.className = "card settings-advanced"; delegations.append(text("summary", "Exact delegations"));
  const delegationList = document.createElement("ul"); delegationList.className = "settings-contract-list"; delegations.append(delegationList);
  function updateDelegations(items) {
    delegationList.replaceChildren();
    for (const item of items) {
      const row = document.createElement("li");
      row.append(text("strong", item.grant_id), text("small", `${titleCase(item.context_scope)} · ${item.active ? "Active" : "Inactive"}`), text("small", (item.permissions || []).map(titleCase).join(", ") || "No permissions"), text("small", item.expires_at ? `Expires ${new Date(item.expires_at).toLocaleString()}` : "Until revoked"));
      delegationList.append(row);
    }
    if (!items.length) delegationList.append(text("li", "No delegation is visible in this context.", "empty"));
  }
  updateDelegations(model.delegations || []);
  fragment.append(cosPrivatePermissionsCard(data, updateDelegations), financeMetadataSettingsCard(data), delegations);
  const compiled = model.compiled_context; if (compiled) { const contextCard = document.createElement("details"); contextCard.className = "card settings-advanced"; contextCard.append(text("summary", "Compiled effective CoS context"), text("p", `Digest ${compiled.digest} · built only from typed state.`, "state-note")); contextCard.append(permissionList("Explicitly excluded", compiled.excluded || [])); fragment.append(contextCard); }
  return fragment;
}

function capabilityCard(item, data) {
  const cardNode = document.createElement("article"); cardNode.className = "card capability-card"; const heading = document.createElement("div"); heading.className = "connection-heading"; const copy = document.createElement("div"); copy.append(text("p", titleCase(item.category), "eyebrow"), text("h2", item.label), text("small", item.capability_id)); heading.append(copy, text("span", item.effective.read_available || item.effective.execute_available ? "Available" : item.effective.requested ? "Requested" : "Not effective", `pill state-label state-${item.effective.read_available || item.effective.execute_available ? "healthy" : item.effective.requested ? "observation-only" : "unknown"}`)); cardNode.append(heading, text("p", item.outcome));
  const axes = document.createElement("div"); axes.className = "capability-axes"; for (const [label, value] of [["Catalog", item.catalog], ["Implementation", item.implementation], ["Install", item.installation], ["Setup", item.account_setup], ["Permission", item.provider_permission], ["Authority", item.administrateme_authority.state], ["OpenClaw", item.openclaw.runtime_state], ["Health", item.runtime_health], ["Freshness", item.freshness], ["Trust", item.trust_stage]]) axes.append(connectionAxis(label, value)); cardNode.append(axes);
  const verbs = document.createElement("div"); verbs.className = "provider-roadmap"; for (const [verb, status] of Object.entries(item.verbs || {})) verbs.append(text("span", `${titleCase(verb)} · ${titleCase(status)}`, "pill pill-lavender")); cardNode.append(verbs);
  const verification = document.createElement("dl"); verification.className = "settings-facts"; for (const [label, value] of [["Catalog version", item.catalog_version], ["Artifact version", item.artifact_version || "Built in"], ["Owner kinds", (item.owner_kinds || []).map(titleCase).join(", ") || "None"], ["Provider options", (item.provider_options || []).map(titleCase).join(", ") || "None"], ["OpenClaw declared tools", (item.openclaw.declared_tools || []).join(", ") || "None"], ["OpenClaw effective tools", (item.openclaw.effective_tools || []).join(", ") || "None"], ["OpenClaw approval", item.openclaw.approval_required_for_side_effects ? "Required for side effects" : "Not applicable"], ["Terminal result", item.verification.terminal_result_support ? "Supported" : "Not supported"], ["Later observation", item.verification.later_observation_support ? "Supported" : "Not supported"], ["Reconciliation", item.verification.reconciliation_support ? "Supported" : "Not supported"]]) verification.append(text("dt", label), text("dd", value)); cardNode.append(verification);
  const prerequisites = document.createElement("details"); prerequisites.className = "settings-technical"; prerequisites.append(text("summary", "Prerequisites, consent, and required actions")); const prerequisiteFacts = document.createElement("dl"); prerequisiteFacts.className = "settings-facts"; for (const [key, value] of Object.entries(item.prerequisite_classes || {})) prerequisiteFacts.append(text("dt", `${titleCase(key)} prerequisite`), text("dd", titleCase(value))); for (const [key, value] of Object.entries(item.required_actions || {})) prerequisiteFacts.append(text("dt", `${titleCase(key)} action`), text("dd", titleCase(value))); for (const [key, value] of Object.entries(item.consent || {})) prerequisiteFacts.append(text("dt", titleCase(key)), text("dd", typeof value === "boolean" ? value ? "Yes" : "No" : titleCase(value))); prerequisites.append(prerequisiteFacts); cardNode.append(prerequisites);
  if (item.unavailable_reason) cardNode.append(text("p", item.unavailable_reason, "state-note")); if (item.prohibited_reason) cardNode.append(text("p", item.prohibited_reason, "review-state"));
  if (item.bridge_state) { const bridge = document.createElement("details"); bridge.className = "settings-technical"; bridge.append(text("summary", "Detailed bridge state")); const bridgeFacts = document.createElement("dl"); bridgeFacts.className = "settings-facts"; for (const [key, value] of Object.entries(item.bridge_state)) bridgeFacts.append(text("dt", titleCase(key)), text("dd", typeof value === "boolean" ? value ? "Yes" : "No" : titleCase(value))); bridge.append(bridgeFacts); cardNode.append(bridge); }
  const activeRequest = item.request?.state === "requested"; if (data.control.identity.can_mutate && (activeRequest || (!item.effective.read_available && !item.effective.execute_available))) { const action = activeRequest ? "cancel" : "request"; const button = text("button", activeRequest ? "Cancel request" : "Request capability", activeRequest ? "button button-quiet" : "button button-primary"); button.type = "button"; button.addEventListener("click", async () => { button.disabled = true; setBanner(`${activeRequest ? "Cancelling" : "Recording"} ${item.label} request…`); try { const result = await api(`/api/member/v1/settings/capabilities/${encodeURIComponent(item.capability_id)}`, { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify({ expected_revision: data.control.capability_request_revision, request_id: crypto.randomUUID(), action, requested_scope: "member_private" }) }); setBanner(result.operational_effects === false ? "Request recorded. Nothing was installed, paired, enrolled, or authorized." : "Request recorded."); await reloadActiveSurface(); } catch (error) { setBanner(`${error.message} The request was not changed.`); button.disabled = false; } }); cardNode.append(button); }
  return cardNode;
}

function capabilitySettings(data) {
  const fragment = document.createDocumentFragment(); const catalog = data.control.capabilities || { items: [], summary: {} }; const intro = document.createElement("section"); intro.className = "hero-card"; intro.append(text("p", `CATALOG ${catalog.catalog_version || "UNKNOWN"}`, "eyebrow"), text("h2", "What the system knows, has, and may do are separate facts."), text("p", "A catalog entry grants nothing. Installation, account setup, local or provider permission, AdministrateMe authority, OpenClaw exposure, runtime health, freshness, trust, and verification are evaluated independently.")); fragment.append(intro);
  const summaries = document.createElement("div"); summaries.className = "settings-summary-grid"; for (const [label, value] of [["Known", catalog.summary?.known || 0], ["Implemented", catalog.summary?.implemented || 0], ["Read available", catalog.summary?.read_available || 0], ["Execute available", catalog.summary?.execute_available || 0], ["Needs attention", catalog.summary?.attention || 0]]) { const cardNode = document.createElement("div"); cardNode.className = "card settings-summary-card"; cardNode.append(text("span", label, "eyebrow"), text("strong", value)); summaries.append(cardNode); } fragment.append(summaries);
  const groups = new Map(); for (const item of catalog.items || []) { const rows = groups.get(item.category) || []; rows.push(item); groups.set(item.category, rows); } for (const [category, items] of groups) { const group = document.createElement("details"); group.className = "capability-group"; group.open = ["foundation", "communications", "planning"].includes(category); group.append(text("summary", `${titleCase(category)} · ${items.length}`)); const grid = document.createElement("div"); grid.className = "settings-card-grid"; for (const item of items) grid.append(capabilityCard(item, data)); group.append(grid); fragment.append(group); }
  return fragment;
}

function learningTrustSettings(data) {
  const fragment = document.createDocumentFragment(); fragment.append(sourceLearningRulesCard(data.control.identity.can_mutate, data.control.source_policy_v2)); const model = data.control.learning_and_trust || { proposals: [], operating_agreement_proposals: [], rules: [], trials: [], suspended_or_revoked: [], inactive_operating_agreements: [] }; const intro = document.createElement("section"); intro.className = "state-card"; intro.append(text("strong", "Trust is capability-specific and evidence-backed."), text("p", "A proposal never self-activates, and success in one domain never silently promotes another.")); fragment.append(intro); for (const [title, rows, empty] of [["Source-policy proposals", model.proposals || [], "No source-policy proposals await review."], ["Operating-agreement proposals", model.operating_agreement_proposals || [], "No operating-agreement proposals await review."], ["Active rules and trials", model.rules || [], "No active learning rules."], ["Suspended, expired, or revoked", [...(model.suspended_or_revoked || []), ...(model.inactive_operating_agreements || [])], "No suspended, expired, or revoked policy."]]) { const section = document.createElement("section"); section.className = "card"; section.append(text("h2", title)); const list = document.createElement("ul"); list.className = "settings-contract-list"; for (const item of rows) { const row = document.createElement("li"); row.append(text("strong", item.policy_proposal_id || item.rule_id || item.agreement_id || "Scoped policy"), text("small", `${titleCase(item.scope_kind || item.authority_scope)} · ${titleCase(item.instruction || item.trust_stage)} · ${titleCase(item.state || item.effective_state || "proposed")}`)); list.append(row); } if (!rows.length) list.append(text("li", empty, "empty")); section.append(list); fragment.append(section); } return fragment;
}

function adminRequestsSettings(data) {
  const fragment = document.createDocumentFragment(); const model = data.control.admin_requests || { visible: false, items: [] }; const intro = document.createElement("section"); intro.className = "state-card"; intro.append(text("strong", "Administrative work can make software available."), text("p", "It cannot complete another adult’s consent, pair their bridge, enroll their account, retrieve credentials, grant authority, or execute a provider action.")); fragment.append(intro); const listCard = document.createElement("section"); listCard.className = "card"; listCard.append(text("h2", "Secret-free requests")); if (!model.visible) listCard.append(text("p", "Admin requests are read-only and unavailable in this view-as context.", "empty")); else { const list = document.createElement("ul"); list.className = "settings-contract-list"; for (const item of model.items || []) { const row = document.createElement("li"); row.append(text("strong", item.desired_outcome || item.capability_id), text("small", `${item.requesting_member_label} · ${titleCase(item.requested_scope)} · ${titleCase(item.state)}`), text("small", "No credentials or provider content · member consent remains incomplete")); list.append(row); } if (!list.children.length) list.append(text("li", "No capability requests need administrative preparation.", "empty")); listCard.append(list); } fragment.append(listCard); return fragment;
}

function cosResponseApprovalCard() {
  const card = document.createElement("section"); card.className = "card cos-response-approval";
  card.append(text("h2", "Approve a CoS reply"), text("p", "Review the exact destination and plain text below. This approves one reply to your own listed address; a separate sending approval is still required.", "state-note"));
  const content = document.createElement("div"); content.append(text("p", "Loading available reply accounts…", "state-note")); card.append(content);
  void (async () => {
    try {
      const options = await api("/api/member/v1/cos/responses/options");
      if (!card.isConnected) return;
      const accounts = options.accounts || [];
      const recipients = (options.recipients || []).filter((recipient) => recipient.channel === "email" || (recipient.channel === "imessage" && /^\+[1-9][0-9]{7,14}$/.test(recipient.address)));
      content.replaceChildren();
      const authority = options.authority;
      if (!authority?.can_prepare) {
        content.append(text("p", authority?.reason || "Your private CoS permissions must be checked before approving a reply.", "state-note"), cosPermissionLink());
        return;
      }
      if (!accounts.length || !recipients.length) {
        content.append(text("p", !accounts.length ? "A connected CoS account is needed before you can approve a reply." : "Add your email address or iMessage phone number to CoS replies before approving a reply to yourself.", "empty"));
        return;
      }
      const form = document.createElement("form");
      const channel = selectControl([{ value: "email", label: "Email" }, { value: "imessage", label: "iMessage" }], accounts[0].channel, "");
      const account = selectControl([], "", "");
      const recipient = selectControl([], "", "");
      const subject = document.createElement("input"); subject.type = "text"; subject.maxLength = 300;
      const body = document.createElement("textarea"); body.maxLength = 8000; body.rows = 8; body.required = true;
      const subjectField = field("Subject", subject);
      form.append(field("Reply channel", channel), field("From", account), field("To", recipient), subjectField, field("Exact reply text", body), text("p", "iMessage replies use one plain-text line without formatting marks, tabs, or spaces at the beginning or end. Use email for multiple lines or text containing formatting marks. Replies have no attachments or group recipients.", "state-note"));
      const approve = text("button", "Approve this exact reply", "button button-primary"); approve.type = "submit";
      const status = text("p", "", "state-note"); status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
      form.append(approve, status, cosPermissionLink()); content.append(form);
      let pending = false; let retry = null;
      function updateChoices() {
        const accountOptions = accounts.map((value, index) => ({ ...value, index })).filter((value) => value.channel === channel.value).map((value) => ({ value: String(value.index), label: value.label }));
        const recipientOptions = recipients.filter((value) => value.channel === channel.value).map((value) => ({ value: value.address, label: value.address }));
        replaceSelectOptions(account, accountOptions, accountOptions[0]?.value || "", accountOptions.length ? "" : "No connected account");
        replaceSelectOptions(recipient, recipientOptions, recipientOptions[0]?.value || "", recipientOptions.length ? "" : "No allowed recipient");
        subjectField.style.display = channel.value === "imessage" ? "none" : "";
        const channelAuthority = authority.channels?.[channel.value];
        approve.disabled = !accountOptions.length || !recipientOptions.length || channelAuthority?.ready !== true;
        status.textContent = channelAuthority?.ready !== true ? channelAuthority?.reason || "Review your CoS permissions before approving a reply on this channel." : !accountOptions.length ? "This CoS channel is not connected for replies." : !recipientOptions.length ? "List your own address for this channel before approving a reply." : "";
        retry = null;
      }
      function unlock() { pending = false; for (const control of form.querySelectorAll("input,select,textarea,button")) control.disabled = false; }
      channel.addEventListener("change", updateChoices); updateChoices();
      form.addEventListener("submit", async (event) => {
        event.preventDefault(); if (pending) return;
        const selected = accounts[Number(account.value)];
        if (!selected || selected.channel !== channel.value || !recipient.value || authority.channels?.[channel.value]?.ready !== true) return;
        const values = { channel: channel.value, service_channel_id: selected.service_channel_id, provider_account_id: selected.provider_account_id, recipient: recipient.value, subject: channel.value === "imessage" ? "" : subject.value, body: body.value };
        const fingerprint = JSON.stringify(values);
        if (!retry || retry.fingerprint !== fingerprint) retry = { fingerprint, requestId: crypto.randomUUID() };
        pending = true; for (const control of form.querySelectorAll("input,select,textarea,button")) control.disabled = true;
        status.textContent = "Recording your approval…";
        try {
          const result = await api("/api/member/v1/cos/responses/approvals", { method: "POST", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify({ ...values, request_id: retry.requestId }) });
          status.textContent = `Reply text approved until ${new Date(result.expires_at).toLocaleTimeString()}. It has not been sent. Continue below to make the separate sending decision.`;
          const continueButton = text("button", "Continue to sending approval", "button button-primary");
          continueButton.type = "button";
          continueButton.disabled = !state.bootstrap.chat.enabled || state.bootstrap.chat.state !== "healthy";
          continueButton.addEventListener("click", (click) => {
            if (state.controller) { status.textContent = "Finish or stop the current conversation before continuing."; return; }
            continueButton.disabled = true;
            $("#chat-panel").classList.add("open"); $("#chat-open").setAttribute("aria-expanded", "true");
            void sendChat(click, {
              message: `Please use administrateme_cos_response to request sending approval for approved reply ${result.approval_event_id}.`,
              displayMessage: "Please send the exact reply I just approved.",
            });
          });
          form.append(continueButton);
          // Leave the approved content immutable in this view. A new approval
          // requires a fresh form and never extends this approval's expiry.
        } catch (error) {
          if (error.body?.detail?.code === "cos_response_authority_required") {
            status.textContent = `${error.message} Review your CoS permissions before approving a reply. No send is confirmed.`;
          } else {
            status.textContent = `${error.message} No send is confirmed. Retrying the same approval is safe.`;
            unlock();
          }
        }
      });
    } catch (error) {
      if (card.isConnected) content.replaceChildren(text("p", `${error.message} Reply approval is unavailable.`, "state-note"));
    }
  })();
  return card;
}

function cosReplySettings(data) {
  const fragment = document.createDocumentFragment();
  const policy = data.control.cos_replies || { visible: false, can_mutate: false };
  const card = document.createElement("section"); card.className = "card cos-reply-settings";
  if (policy.visible) card.dataset.policyRevision = String(policy.revision);
  card.append(text("h2", "CoS replies"), text("p", "CoS can reply only to these recipients in the associated member’s conversations. An empty list blocks all replies. Adding a recipient does not turn on sending or share another member’s information.", "state-note"));
  if (!policy.visible) {
    card.append(text("p", "A household principal can manage recipients from their own Settings view.", "empty"));
    fragment.append(card); return fragment;
  }
  const form = document.createElement("form");
  const rows = document.createElement("div"); rows.className = "cos-reply-recipient-list";
  let pending = false; let retry = null;
  const members = (data.control.household || []).map((member) => ({ value: member.member_id, label: member.label }));
  function addRow(entry = {}) {
    const row = document.createElement("div"); row.className = "preference-grid cos-reply-recipient";
    const member = selectControl(members, entry.member_id || data.control.identity.authenticated_member_id, "");
    const channel = selectControl([{ value: "email", label: "Email" }, { value: "imessage", label: "iMessage" }], entry.channel || "email", "");
    const address = document.createElement("input"); address.type = "text"; address.value = entry.address || ""; address.maxLength = 254; address.autocomplete = "off"; address.spellcheck = false; address.required = true;
    const remove = text("button", "Remove recipient", "button button-quiet"); remove.type = "button";
    remove.addEventListener("click", () => { if (!pending) { row.remove(); retry = null; } });
    row.append(field("Member", member), field("Channel", channel), field("Email address or international phone number", address), remove);
    row.recipientValue = () => ({ member_id: member.value, channel: channel.value, address: address.value });
    rows.append(row);
  }
  for (const entry of policy.entries || []) addRow(entry);
  form.append(rows, text("p", "Enter one bare email address or an iMessage number beginning with + and its country code. Display names, lists and wildcards are not accepted. Email aliases and plus tags must be listed separately.", "state-note"));
  const actions = document.createElement("div"); actions.className = "button-row";
  const add = text("button", "Add recipient", "button button-secondary"); add.type = "button"; add.addEventListener("click", () => { if (!pending && rows.children.length < 100) { addRow(); retry = null; } });
  const save = text("button", "Save recipients", "button button-primary"); save.type = "submit";
  const revoke = text("button", "Revoke all replies", "button button-quiet"); revoke.type = "button";
  const status = text("p", "", "state-note"); status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
  function lock(locked) { pending = locked; for (const control of form.querySelectorAll("input,select,button")) control.disabled = locked || !policy.can_mutate; }
  async function submit(entries) {
    if (pending || !policy.can_mutate) return;
    const fingerprint = JSON.stringify(entries);
    if (!retry || retry.fingerprint !== fingerprint) retry = { fingerprint, requestId: crypto.randomUUID() };
    lock(true); status.textContent = "Saving CoS reply recipients…";
    try {
      await api("/api/member/v1/settings/cos-replies", { method: "PUT", headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() }, body: JSON.stringify({ expected_revision: policy.revision, request_id: retry.requestId, entries }) });
      setBanner(entries.length ? "CoS reply recipients saved. Sending permissions are unchanged." : "All CoS reply recipients revoked. New replies are blocked.");
      await reloadActiveSurface();
    } catch (error) {
      status.textContent = `${error.message} Check the current list before making another change; retrying the same save is safe.`;
      lock(false);
    }
  }
  form.addEventListener("input", () => { if (!pending) retry = null; });
  form.addEventListener("change", () => { if (!pending) retry = null; });
  form.addEventListener("submit", (event) => { event.preventDefault(); void submit([...rows.children].map((row) => row.recipientValue())); });
  revoke.addEventListener("click", () => { void submit([]); });
  actions.append(add, save, revoke); form.append(actions, status); lock(false); card.append(form); fragment.append(card, cosResponseApprovalCard()); return fragment;
}

function renderSettings(data) {
  const root = $("#surface"); root.replaceChildren(); $("#mode-label").textContent = "Private · deterministic control plane";
  root.append(settingsHero(data), settingsNav()); const content = document.createElement("div"); content.className = "settings-content";
  if (state.settingsSection === "overview") { const layout = document.createElement("div"); layout.className = "settings-overview-grid"; layout.append(attentionCard(data.control.needs_attention), identitySummary(data.control.identity, true)); content.append(layout, serviceChannelsCard(data)); const sources = data.control.source_policy_v2?.sources || []; const summaries = document.createElement("div"); summaries.className = "settings-summary-grid"; for (const [title, value, copy, target] of [["Capabilities", data.control.capabilities.summary.known, "Known, implemented, effective, and trusted state shown separately.", "capabilities"], ["Your connections", data.control.connections.length, "Your personal provider accounts; dedicated CoS channels are shown separately.", "connections"], ["Source policies", sources.length, "Exact member, account, workspace, adapter, source-class, and route coordinates.", "source-context"], ["Bridges", data.control.bridges.length, "Per-member device sources with explicit enrollment and consent.", "bridges"], ["CoS profile", data.control.behavior.profiles.find((item) => item.id === data.control.behavior.active_profile_id)?.label || "Not selected", "Validated behavior defaults; no authority or model-routing changes.", "behavior"]]) { const cardNode = document.createElement("button"); cardNode.type = "button"; cardNode.className = "card settings-summary-card"; cardNode.append(text("span", title, "eyebrow"), text("strong", value), text("small", copy)); cardNode.addEventListener("click", () => { state.settingsSection = target; replaceQuery({ section: target }); renderSettings(state.surface); }); summaries.append(cardNode); } content.append(summaries); }
  else if (state.settingsSection === "identity") content.append(accountSettings(data));
  else if (state.settingsSection === "behavior") content.append(behaviorSettings(data));
  else if (state.settingsSection === "source-context") content.append(candidateIdentificationSettings(data));
  else if (state.settingsSection === "cos-work") content.append(howCosWorksSettings(data));
  else if (state.settingsSection === "cos-replies") content.append(cosReplySettings(data));
  else if (state.settingsSection === "capabilities") content.append(capabilitySettings(data));
  else if (state.settingsSection === "connections") content.append(renderConnections(data));
  else if (state.settingsSection === "bridges") content.append(bridgeSettings(data));
  else if (state.settingsSection === "apple-services") content.append(appleServicePermissions(data));
  else if (state.settingsSection === "learning-trust") content.append(learningTrustSettings(data));
  else if (state.settingsSection === "privacy") content.append(privacySettings(data));
  else if (state.settingsSection === "admin-requests") content.append(adminRequestsSettings(data));
  else content.append(systemSettings(data));
  root.append(content);
  setSourceSettingsLock();
}

function freshnessStrip(data) {
  const value = data.freshness;
  const strip = document.createElement("div"); strip.className = "freshness-strip";
  if (!value) return strip;
  strip.append(text("span", titleCase(value.state), `pill state-label state-${String(value.state).replaceAll("_", "-")}`));
  const sourceLabel = data.surface === "finance" && data.cockpit?.accepted_books?.publication_id ? "Books source watermark" : "Latest source";
  const latest = value.latest_source_at ? `${sourceLabel} ${new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value.latest_source_at))}` : "No source watermark yet";
  strip.append(text("span", `${latest} · ${value.row_count} visible record${value.row_count === 1 ? "" : "s"}`));
  if (value.checkpoint_required) strip.append(text("span", value.checkpoint_event_id ? `Checkpoint ${String(value.checkpoint_event_id).slice(-10)}` : "No processing checkpoint yet"));
  if (Number(value.lag_count || 0) > 0) strip.append(text("strong", `${value.lag_count} event${value.lag_count === 1 ? "" : "s"} waiting to process`));
  if (value.degraded_reason) strip.append(text("strong", value.degraded_reason));
  return strip;
}

function renderSurface(data, { preserveTriageView = false } = {}) {
  state.surface = data;
  document.body.dataset.surface = data.surface;
  const root = $("#surface");
  root.setAttribute("aria-busy", "false");
  root.replaceChildren();
  $("#page-title").textContent = data.label;
  $("#mode-label").textContent = "Private member view";
  if (data.surface === "today") {
    state.carouselItems = [...(data.brief?.carousel?.items || [])];
    state.carouselIndex = 0;
    renderToday(data);
  } else if (data.surface === "triage") {
    root.append(freshnessStrip(data), triageSurface(data, { preserveView: preserveTriageView }));
    applyTriageInteractionLock();
  } else if (data.surface === "comms") {
    renderComms(data);
  } else if (data.surface === "reminders") {
    renderReminders(data);
  } else if (data.surface === "people") {
    renderPeople(data);
  } else if (data.surface === "notes") {
    renderNotes(data);
  } else if (data.surface === "finance") {
    renderFinance(data);
  } else if (data.surface === "calendar") {
    renderCalendar(data);
  } else if (data.surface === "scoreboard") {
    const score = data.scoreboard || {};
    const hero = document.createElement("section"); hero.className = "hero-card";
    hero.append(text("p", "YOUR RHYTHM", "eyebrow"), text("h2", `${score.open_count || 0} gentle next steps`, "action-title"), text("p", score.reward_language === "warm" ? "Every small finish counts. You’re building momentum." : "Progress is evidence, not pressure."));
    const stats = document.createElement("dl"); stats.className = "brief-stats";
    for (const [label, value] of [["Active", score.open_count], ["Completed", score.completed_count], ["Closed", score.closed_count], ["Redirected", score.redirected_count]]) {
      const stat = text("div", "", "stat"); stat.append(text("dt", label), text("dd", value || 0)); stats.append(stat);
    }
    hero.append(stats);
    root.append(
      hero,
      card("Up next", score.next_steps),
      card("Outcomes and evidence", score.recent_outcomes, "No completed, closed, or redirected work has been recorded yet."),
      card("Completion reinforcement", score.reward_evidence, "No completion reinforcement event has been recorded yet."),
      rhythmDefinitionsCard(score.rhythms),
      card("Routine and recurrence history", score.rhythm_outcomes, "No recurring occurrence has been completed yet."),
    );
  } else if (data.surface === "settings") {
    renderSettings(data);
  }
}

function renderQuest(data) {
  const root = $("#surface"); root.setAttribute("aria-busy", "false"); root.replaceChildren();
  $("#page-title").textContent = data.surface.title;
  $("#mode-label").textContent = "Temporary lens";
  if (data.state === "degraded") {
    const unavailable = document.createElement("section"); unavailable.className = "state-card";
    unavailable.append(text("h2", "This lens is waiting on a capability."), text("p", "The underlying household records are unchanged."));
    root.append(unavailable); return;
  }
  const intro = document.createElement("section"); intro.className = "hero-card";
  intro.append(text("p", "PRIVATE QUEST", "eyebrow"), text("h2", data.surface.title, "action-title"), text("p", data.surface.purpose));
  root.append(intro);
  for (const result of data.results) {
    const value = result.value;
    if (result.query === "what_now") root.append(whatNowCard(value, data.mode));
    else root.append(card(result.query.replace(".", " · "), Array.isArray(value) ? value : rankedItems(value)));
  }
  const note = document.createElement("section"); note.className = "state-card";
  note.append(text("strong", "Disposable view, stable household truth"), text("p", "Archiving this lens removes only its navigation entry. It never deletes or copies the records shown here."));
  root.append(note);
}

function renderFailure(error) {
  const root = $("#surface"); root.setAttribute("aria-busy", "false"); root.replaceChildren();
  const box = document.createElement("section"); box.className = "state-card";
  box.append(text("h2", error.status === 404 ? "This view is not available to this member." : "This view is temporarily unavailable."));
  box.append(text("p", "Your household data has not been changed. Try again after checking capability health."));
  root.append(box);
}

async function showEvidence(eventId) {
  const dialog = $("#evidence-dialog"); const content = $("#evidence-content");
  if (document.activeElement instanceof HTMLElement) dialogReturnFocus.set(dialog, document.activeElement);
  content.replaceChildren(text("p", "Loading evidence…")); dialog.showModal();
  try {
    const value = await api(`/api/member/v1/evidence/${encodeURIComponent(eventId)}`);
    const dl = document.createElement("dl"); dl.className = "evidence-grid";
    const fields = [
      ["Event", `${value.type} · ${value.event_id}`], ["Source", `${value.source.adapter} / ${value.source.account}`],
      ["Actor", value.actor || "System"], ["Occurred", value.occurred_at], ["Ingested", value.ingested_at],
      ["Confidence", value.confidence ?? "Not asserted"], ["Scope", `${value.scope.owner} · ${value.scope.sensitivity}`],
      ["Why", value.why.causation_id || value.why.correlation_id || "Direct observation"],
      ["Correction", value.correction || "No correction recorded"], ["Approval", value.approval || "Not applicable"],
      ["Delivery", value.delivery || "No delivery claimed"],
    ];
    for (const [label, result] of fields) { dl.append(text("dt", label), text("dd", result)); }
    content.replaceChildren(dl);
  } catch (error) { content.replaceChildren(text("p", error.message)); }
}

function appendMessage(role, value) {
  const item = text("li", value, `message ${role}`);
  $("#chat-messages").append(item);
  item.scrollIntoView({ block: "nearest" });
  return item;
}

async function loadChatHistory() {
  if (!state.bootstrap.chat.enabled || state.bootstrap.chat.state !== "healthy") return;
  try {
    const value = await api("/api/member/v1/chat/history");
    for (const item of value.items) appendMessage(item.role, item.text);
  } catch { $("#chat-state").textContent = "Conversation history is temporarily unavailable."; }
}

const chatReplyApprovals = new Map();

function clearChatReplyApprovals() {
  for (const entry of chatReplyApprovals.values()) { clearTimeout(entry.timer); entry.node.remove(); }
  chatReplyApprovals.clear();
}

async function refreshChatReplyApprovals(isCurrent) {
  if (state.bootstrap.member.authenticated_role !== "principal" || state.bootstrap.member.viewing_as) return;
  const result = await api("/api/member/v1/chat/reply-approvals");
  if (!isCurrent()) return;
  const active = new Set();
  for (const row of result.items || []) {
    const providerAction = row.kind === "provider_action";
    if (providerAction && !["apple_calendar", "apple_reminders", "apple_notes"].includes(row.provider)) continue;
    const expiredCopy = providerAction ? "The earlier action approval changed or expired. This Apple change cannot run." : "The earlier reply approval changed or expired. This reply cannot be sent.";
    const key = `${row.approval_id}:${row.revision}`; active.add(key);
    const existing = chatReplyApprovals.get(key);
    if (existing) {
      if (!row.can_allow && !existing.resolving()) { existing.allow.disabled = true; existing.note.textContent = expiredCopy; }
      continue;
    }
    if (row.expires_at_ms <= Date.now()) continue;
    const pendingKey = `administrateme:native-action-decision:${state.bootstrap.member.authenticated_id}:${key}`;
    let storedDecision = null;
    try { storedDecision = JSON.parse(sessionStorage.getItem(pendingKey) || "null"); } catch { storedDecision = { unknown: true }; }
    const node = document.createElement("li"); node.className = `message assistant cos-send-approval${providerAction ? " apple-action-approval" : ""}`;
    if (providerAction) {
      node.append(text("h3", "Allow this Apple change?"), text("p", `${titleCase(row.provider)} · ${titleCase(row.operation)}`), text("p", `Destination: ${row.destination}`));
      const details = document.createElement("dl"); details.className = "apple-action-review";
      for (const item of row.review || []) details.append(text("dt", item.label), text("dd", item.value)); node.append(details);
    } else {
      node.append(text("h3", "Ready to send this reply?"), text("p", `${row.channel === "imessage" ? "iMessage" : "Email"} to ${row.recipient}`));
      if (row.subject) node.append(text("strong", row.subject));
      const content = text("p", row.body); content.style.whiteSpace = "pre-wrap"; node.append(content);
    }
    const unknownCopy = providerAction ? "No execution decision is confirmed. Check the action result; this control will not retry." : "No sending decision is confirmed. Wait for CoS to report the result; this control will not retry.";
    const approvedCopy = providerAction ? "You approved this exact action. Execution still needs this separate, one-time decision." : "Your reply text is approved. Sending still needs this separate, one-time decision.";
    const note = text("p", storedDecision ? unknownCopy : row.can_allow ? approvedCopy : expiredCopy, "state-note");
    note.setAttribute("role", "status"); note.setAttribute("aria-live", "polite");
    const allow = text("button", providerAction ? "Allow this change once" : "Allow this reply once", "button button-primary"); allow.type = "button"; allow.disabled = !row.can_allow || Boolean(storedDecision);
    const deny = text("button", providerAction ? "Don’t change Apple" : "Don’t send", "button button-secondary"); deny.type = "button"; deny.disabled = Boolean(storedDecision);
    let resolving = Boolean(storedDecision);
    const decide = async (decision) => {
      if (resolving || row.expires_at_ms <= Date.now() || (decision === "allow-once" && !row.can_allow)) return;
      try { sessionStorage.setItem(pendingKey, JSON.stringify({ approval_id: row.approval_id, revision: row.revision, decision, expires_at_ms: row.expires_at_ms })); }
      catch { allow.disabled = true; deny.disabled = true; note.textContent = "This browser cannot retain the decision reference. No decision was requested."; return; }
      resolving = true; allow.disabled = true; deny.disabled = true; note.textContent = providerAction ? "Recording your execution decision…" : "Recording your sending decision…";
      try {
        const resolved = await api("/api/member/v1/chat/reply-approvals", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ approval_id: row.approval_id, revision: row.revision, decision }),
        });
        if (resolved.resolved !== true) { note.textContent = unknownCopy; return; }
        note.textContent = providerAction ? decision === "allow-once" ? "This Apple change is allowed once. CoS will report the result." : "Apple change declined." : decision === "allow-once" ? "Sending allowed once. CoS will report the result." : "Sending declined.";
      } catch { note.textContent = unknownCopy; }
    };
    allow.addEventListener("click", () => { void decide("allow-once"); }); deny.addEventListener("click", () => { void decide("deny"); });
    node.append(note, allow, deny); $("#chat-messages").append(node);
    const timer = setTimeout(() => {
      allow.disabled = true; deny.disabled = true;
      if (!resolving) note.textContent = providerAction ? "This execution approval expired. No Apple change is authorized by this control." : "This sending approval expired. No send is authorized by this control.";
    }, Math.max(0, row.expires_at_ms - Date.now()));
    chatReplyApprovals.set(key, { node, timer, allow, note, resolving: () => resolving });
  }
  for (const [key, entry] of chatReplyApprovals) {
    if (!active.has(key)) { clearTimeout(entry.timer); entry.node.remove(); chatReplyApprovals.delete(key); }
  }
}

async function sendChat(event, handoff = null) {
  event.preventDefault();
  const input = $("#chat-input"); const message = handoff?.message || input.value.trim();
  if (!message || state.controller) return;
  input.value = ""; appendMessage("user", handoff?.displayMessage || message); state.assistantNode = appendMessage("assistant", "");
  state.controller = new AbortController(); $("#chat-stop").hidden = false; $("#chat-send").disabled = true;
  let polling = true; let inFlight = false;
  const poll = async () => {
    if (inFlight || !polling) return;
    inFlight = true;
    try { await refreshChatReplyApprovals(() => polling); }
    catch { if (polling && chatReplyApprovals.size) clearChatReplyApprovals(); }
    finally { inFlight = false; }
  };
  const approvalTimer = setInterval(() => { void poll(); }, 1000); void poll();
  $("#chat-state").textContent = "Preparing context…";
  try {
    const response = await fetch("/api/member/v1/chat/stream", {
      method: "POST", credentials: "same-origin", cache: "no-store", signal: state.controller.signal,
      headers: { "Content-Type": "application/json", "X-Correlation-Id": crypto.randomUUID() },
      body: JSON.stringify({ message, request_id: crypto.randomUUID() }),
    });
    if (!response.ok || !response.body) throw new Error("Chat is temporarily unavailable.");
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
    while (true) {
      const part = await reader.read(); if (part.done) break; buffer += decoder.decode(part.value, { stream: true });
      const frames = buffer.split("\n\n"); buffer = frames.pop() || "";
      for (const frame of frames) {
        const line = frame.split("\n").find((entry) => entry.startsWith("data: ")); if (!line) continue;
        const value = JSON.parse(line.slice(6));
        if (value.kind === "status") $("#chat-state").textContent = value.phase === "starting_model" ? "Thinking with OpenClaw…" : "Preparing context…";
        if (value.kind === "delta") state.assistantNode.textContent = value.replace ? value.text : state.assistantNode.textContent + value.text;
        if (value.kind === "final") $("#chat-state").textContent = "Ready";
        if (value.kind === "error") $("#chat-state").textContent = "The model connection ended. Your household views are still available.";
        if (value.kind === "aborted") $("#chat-state").textContent = "Stopped.";
      }
    }
  } catch (error) {
    if (error.name !== "AbortError") $("#chat-state").textContent = "The model connection ended. Your household views are still available.";
  } finally { polling = false; clearInterval(approvalTimer); clearChatReplyApprovals(); state.controller = null; state.assistantNode = null; $("#chat-stop").hidden = true; $("#chat-send").disabled = false; }
}

function configureChat(policy) {
  const form = $("#chat-form");
  if (!policy.enabled || policy.state !== "healthy") {
    form.hidden = true;
    const copy = policy.reason === "model_provider_not_configured" ? "Chat is enabled for you, but no model provider is configured in OpenClaw yet." : policy.reason === "guardian_disabled_child_chat" ? "Chat is off for this child profile." : "Chat is not enabled for this member.";
    $("#chat-state").textContent = copy;
    if (!policy.enabled) $("#chat-open").hidden = true;
  } else { form.hidden = false; $("#chat-state").textContent = "OpenClaw owns this private conversation."; }
}

async function initialize() {
  const closeChat = () => {
    $("#chat-panel").classList.remove("open");
    $("#chat-open").setAttribute("aria-expanded", "false");
    $("#chat-open").focus();
  };
  $("#chat-close").addEventListener("click", closeChat);
  $("#chat-open").addEventListener("click", () => { $("#chat-panel").classList.add("open"); $("#chat-open").setAttribute("aria-expanded", "true"); $("#chat-input").focus(); });
  $("#chat-panel").addEventListener("keydown", (event) => { if (event.key === "Escape" && $("#chat-panel").classList.contains("open")) { event.preventDefault(); closeChat(); } });
  $("#chat-form").addEventListener("submit", sendChat);
  $("#chat-stop").addEventListener("click", async () => { state.controller?.abort(); await fetch("/api/member/v1/chat/abort", { method: "POST", credentials: "same-origin" }); });
  $("[data-close-dialog]").addEventListener("click", () => $("#evidence-dialog").close());
  $("[data-close-action]").addEventListener("click", closeActionDialog);
  for (const dialog of [$("#evidence-dialog"), $("#action-dialog")]) {
    dialog.addEventListener("close", () => {
      const target = dialogReturnFocus.get(dialog);
      dialogReturnFocus.delete(dialog);
      if (target?.isConnected && !target.matches(":disabled")) target.focus();
    });
  }
  $("#action-dialog").addEventListener("cancel", (event) => {
    if (addForReviewInteractionLocked() && event.currentTarget.querySelector(".add-for-review-form")) event.preventDefault();
  });
  $("#add-for-review").addEventListener("click", openAddForReview);
  try {
    state.bootstrap = await api("/api/member/v1/bootstrap"); renderNavigation(combinedNavigation(state.bootstrap)); configureChat(state.bootstrap.chat);
    const memberButton = $("#member-button"); memberButton.replaceChildren(); memberButton.className = "member-chip";
    const memberCopy = document.createElement("span"); memberCopy.append(text("strong", state.bootstrap.member.authenticated_display_name), text("small", `${titleCase(state.bootstrap.member.authenticated_role)} · ${state.bootstrap.member.authentication_method}`)); memberButton.append(memberCopy);
    memberButton.setAttribute("aria-label", `Open settings for ${state.bootstrap.member.authenticated_display_name}`);
    $("#observation-pill").textContent = state.bootstrap.observation_mode ? "Observation mode" : "Member-safe";
    $("#observation-banner").hidden = !state.bootstrap.observation_mode;
    const viewAs = $("#view-as-banner"); viewAs.hidden = !state.bootstrap.member.viewing_as;
    if (state.bootstrap.member.viewing_as) viewAs.textContent = `Viewing ${state.bootstrap.member.display_name}. Reads follow that member’s scope; actions remain attributed to ${state.bootstrap.member.authenticated_display_name}, and Settings changes are disabled.`;
    $("#add-for-review").disabled = Boolean(state.bootstrap.member.viewing_as);
    const active = currentSurface();
    const params = new URLSearchParams(location.search);
    const requestedView = params.get("view");
    if (active === "today" && ["brief", "carousel"].includes(requestedView)) state.todayView = requestedView;
    if (active === "triage") {
      if (["needs_interpretation", "ready", "alerts", "needs_details", "low_signal", "recurring_exceptions", "learned_rules", "all_candidates", "recent"].includes(requestedView)) state.triageView = requestedView;
      state.triageSelected = params.get("item");
      state.triageMatter = params.get("matter");
      state.triageFocusPending = Boolean(state.triageSelected);
    }
    if (active === "comms") {
      if (["alerts", "focus", "waiting", "snoozed", "quieted", "all"].includes(requestedView)) state.commsView = requestedView;
      state.commsAccount = params.get("account") || "all";
      state.commsThread = params.get("thread");
    }
    if (active === "reminders") {
      if (["now", "scheduled", "places", "anytime", "waiting", "completed"].includes(requestedView)) state.remindersView = requestedView;
      state.remindersAccount = params.get("account") || "all";
      state.remindersList = params.get("list") || "all";
      state.reminderItem = params.get("item");
      state.reminderDeepLinkPending = Boolean(state.reminderItem);
    }
    if (active === "calendar") {
      if (["day", "week", "month", "agenda", "find", "rules"].includes(requestedView)) state.calendarView = requestedView;
      state.calendarAnchor = params.get("date");
      state.calendarSelectedEvent = params.get("event");
    }
    if (active === "people") {
      if (["all", "household", "favorites", "organizations", "suppressed"].includes(requestedView)) state.peopleLens = requestedView;
      state.peopleSelected = params.get("item");
      state.peopleQuery = params.get("q") || "";
    }
    if (active === "notes") {
      if (["all", "recent", "shared", "meetings", "triage"].includes(requestedView)) state.notesLens = requestedView;
      state.notesSource = params.get("source") || "all";
      state.notesSelected = params.get("note");
      state.notesQuery = params.get("q") || "";
    }
    if (active === "settings") {
      const requestedSection = params.get("section");
      if (SETTINGS_SECTIONS.some(([value]) => value === requestedSection)) state.settingsSection = requestedSection;
    }
    if (active === "triage") {
      const requestedSort = params.get("sort");
      if (["attention", "oldest", "newest"].includes(requestedSort)) state.triageSort = requestedSort;
      state.triageSource = params.get("source") || "all";
      state.triageKind = params.get("kind") || "all";
    }
    let path = `/api/member/v1/surfaces/${encodeURIComponent(active)}`;
    if (active === "calendar") {
      const query = new URLSearchParams({ view: state.calendarView });
      if (state.calendarAnchor) query.set("date", state.calendarAnchor);
      path += `?${query}`;
    }
    if (active === "triage") {
      const triageParams = triageRequestParams();
      if (state.triageSelected) triageParams.set("triage_item", state.triageSelected);
      path += `?${triageParams}`;
    }
    let surfaceRequest;
    if (active.startsWith("quest:")) {
      surfaceRequest = api(`/api/member/v1/temporary-surfaces/${encodeURIComponent(active.slice(6))}/evaluate`).then(renderQuest);
    } else if (active === "notes") {
      surfaceRequest = api(path).then(async (data) => {
        if (state.notesQuery) {
          const query = new URLSearchParams({ q: state.notesQuery });
          if (state.notesSource !== "all") query.set("source", state.notesSource.split("|")[0]);
          const result = await api(`/api/member/v1/notes/search?${query}`);
          const originals = new Map((data.notes || []).map((note) => [note.knowledge_id, note]));
          state.notesResults = result.items.map((note) => ({ ...(originals.get(note.knowledge_id) || {}), ...note }));
        }
        renderSurface(data);
      });
    } else if (active === "people") {
      surfaceRequest = api(path).then(async (data) => {
        if (state.peopleQuery) {
          const result = await api(`/api/member/v1/people/search?q=${encodeURIComponent(state.peopleQuery)}`);
          const originals = new Map((data.cockpit?.directory || []).map((person) => [person.party_id, person]));
          state.peopleResults = result.results.map((person) => ({ ...(originals.get(person.party_id) || {}), ...person }));
        }
        renderSurface(data);
      });
    } else {
      surfaceRequest = api(path).then(renderSurface);
    }
    await Promise.all([surfaceRequest, loadChatHistory()]);
  } catch (error) { renderFailure(error); }
}

initialize();
