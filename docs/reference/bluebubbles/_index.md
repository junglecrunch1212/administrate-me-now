# BlueBubbles documentation

**Purpose in this build:** BlueBubbles is the iMessage bridge AdministrateMe depends on. The `messaging:bluebubbles_adminme` adapter (REFERENCE_EXAMPLES.md §1) subscribes to BlueBubbles' WebSocket for incoming iMessages and POSTs to the REST API for outbound. Installed on the Mac Mini during Phase B bootstrap.

**Source:** https://github.com/BlueBubblesApp/bluebubbles-docs (GitBook source)
**Fetched:** 2026-04-22
**License:** Apache-2.0 (per repo's licenses-legal.md)

## Mirror scope

Every `.md` file under the repo, preserving directory structure:

- `server/` — BlueBubbles Server (Mac app) docs: installation, REST API, WebSocket events, private-API setup, troubleshooting.
- `private-api/` — Private API add-on (for tapbacks, reactions, typing indicators).
- `clients/` — BlueBubbles client app docs (end-user; mostly not relevant to our adapter).
- `home/` — Landing/overview pages.
- `blog/` — Release notes (occasionally useful for breaking-change detection).

## Key files for the adapter build

- `server/developer-guides/` — REST endpoint and WebSocket event reference (this is the API the AdministrateMe adapter speaks to).
- `server/basic-guides/` — Setup flow mirrored by bootstrap Section 2.
- `server/advanced/` — Port forwarding, authentication, private-API behaviors.

## Known gaps

None. GitBook source is complete; if specific pages on https://docs.bluebubbles.app render better, the markdown source here is the same content.
