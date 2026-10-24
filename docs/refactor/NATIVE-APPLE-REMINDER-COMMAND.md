# Private chat Apple Reminders command

MODERNIZE the member reminder command contract: an authenticated current private
iMessage can confirm one `create_reminder` through the same domain validation as
the member portal. This supplements the established Apple service permission and
independent OpenClaw allow-once execution; it grants neither new account access
nor permission to send a message. Scheduled self-texts continue to use
`administrateme_self_reminder`.

`administrateme_confirm_apple_reminder` accepts a title, offset-aware `due_at`,
optional notes and selected list, and a stable request ID. The server derives the
member/account from the trusted current native instruction and current exact
Apple service grant. Only a single authorized list can be selected automatically;
otherwise the tool returns the currently permitted list choices. It preserves
source causation and sensitivity. EventKit receives UTC date components,
preserving the requested instant, and the confirmation exposes the normalized
due time. The portal's date-only input remains unchanged.

Confirmation records the existing `member.provider_action.confirmed@1`, with a
member/tenant-namespaced command ID. Its fingerprint describes the typed intent,
not mutable grants. Replays return that same command's receipt; changed content
under the same ID is rejected. The existing independent execution tool produces
`external_sync.write_requested@2`; current grant/device/configuration/list checks
and a fresh command-bound proof remain enforced immediately before bridge use.

`administrateme_apple_reminder_status` reads only the requesting member's exact
command chain, including immutable grant and native approval evidence. Only an
unsuppressed, applied, provider-called terminal from the native dispatcher with
an external ID yields `succeeded`. Pending and ambiguous failures remain explicit
and must not be retried with a new request ID. An independently acquired matching
provider/account/member/list/item is reported as `observed`; this is separate
from the successful provider terminal and does not assert content equality.

PRESERVE existing event versions, bridge attempt ledger, source policies,
confidentiality, final privacy guard, observation suppression, and external
approval ordering. Rollback to the preceding schema-compatible runtime retains
confirmations/results and removes the new tools; it cannot undo a completed Apple
write. Already accepted bridge commands retain the existing independent lifecycle.
Affected verification covers the private native hook/router composition, exact
UTC Foundation components on hosted macOS, permission changes, independent
approval, real bridge dispatch with a synthetic native store, truthful terminal
readback, restart/replay and cross-member isolation. No live provider is used.
