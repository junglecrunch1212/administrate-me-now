# Messaging adapters (L1)

Messaging-family adapters go here — but note: iMessage (via BlueBubbles),
Telegram, and Discord are owned by OpenClaw as channel plugins, not by
AdministrateMe. Per SYSTEM_INVARIANTS.md §8 invariant 3, OpenClaw owns all
channel transport. Inbound conversation state arrives in AdministrateMe via
the `openclaw-memory-bridge` plugin (DECISIONS.md §D6).

Remaining messaging-family adapters (e.g. a generic SMTP/IMAP inbound, or
webhooks) live here as standalone Python adapters.

Filled in by prompt 11 and channel-specific prompts.
