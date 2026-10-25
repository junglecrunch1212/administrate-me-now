---
name: administrateme-household
description: Read or propose changes to authorized AdministrateMe household state through the native typed tools.
metadata: {"openclaw":{"requires":{"config":["plugins.entries.administrateme.enabled"]}}}
---

# AdministrateMe household operations

Use the narrow `administrateme_*` tools for typed household truth. Never treat
the OpenClaw operator, tool caller, or `senderIsOwner` status as household
membership; the AdministrateMe process independently maps trusted
channel/account/actor provenance.

- Use `administrateme_state` or `administrateme_what_now` for reads.
- Use `administrateme_add_for_review` to save the member's exact words in Triage. It never creates canonical household truth without member confirmation.
- For a genuinely ambiguous request mixing your own Apple reminder with automatic email, use `administrateme_cos_reminder_choice` to offer its exact numbered final reply. Use the same tool with the fresh answer; never treat “1” as an isolated create request. Both engines independently authorize only the selected reminder. Email choice remains unresolved and schedules nothing.
- Use `administrateme_review` for pending commitment decisions.
- Use `administrateme_lifecycle` for complete/defer/snooze/block/unblock.
- Use `administrateme_correct` only for its allowlisted fields.
- Use `administrateme_prepare_google_tasks_create_once` only when a B03 domain
  approval event already names the exact command and content. Use
  `administrateme_execute_google_tasks_create_once` only after showing the
  exact secret-safe preview and receiving the independent allow-once approval.
  Never change the account, task list, task fields, expiry, or command. Read
  settlement with `administrateme_google_tasks_create_state`; never retry an
  ambiguous or used command.
- Use `administrateme_skill` only with a Phase 3 skill id and schema-valid
  input. That wrapper makes one OpenClaw-owned `llm-task` call; never choose a
  provider or implement fallback here.

When responding normally in an enrolled private iMessage conversation, provide
your final answer as ordinary text. The host handles delivery when the member
has explicitly granted standing self-reply permission; do not call a sending
tool or ask for another approval merely to answer them. Do not claim a reply was
delivered before the host's native receipt exists. This permission does not
authorize contacting anyone else or performing another external action. If no
standing grant exists, the host fails closed and exact manual approval remains
available.

Sensitive tools are optional and require a per-call OpenClaw permission in
addition to AdministrateMe authorization. A denial or unavailable response is
final. Do not copy generic conversation into AdministrateMe. Only the explicit
consent-marked bridge event contract may emit a domain observation.
