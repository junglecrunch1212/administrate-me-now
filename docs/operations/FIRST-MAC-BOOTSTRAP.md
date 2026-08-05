# First reviewed Mac bootstrap

The canonical non-programmer setup path is now the more detailed
[`MAC-MINI-SETUP-READ-ALONG.md`](MAC-MINI-SETUP-READ-ALONG.md). Keep that page
open beside the Terminal and follow it from the release stop sign through the
trust period.

Do not use an older copy of this file from another branch. In particular:

- deploy only an independently reviewed commit merged to `main`;
- pass its full SHA with `--expected-commit`;
- use a separate fixture instance for the credential-free rehearsal;
- do not use production `--dry-run`;
- keep observation mode on;
- expose only `127.0.0.1:3330` through private Tailscale Serve, never Funnel;
- enter provider credentials only in OpenClaw or the provider’s own page.

The read-along records the remaining independent-review/live-Mac gates, exact
source and bridge commands, and the safe diagnostic to attach for live
troubleshooting.
