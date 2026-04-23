# Compiled profile views

Compiled JSX served as static assets. Per DECISIONS.md §D3, each profile pack
exports a default React functional component accepting
`{member, persona, data, api}` (typed via `@adminme/profile-types`). "View
mode" (`carousel` / `compressed` / `child`) is descriptive vocabulary for
the three reference profile implementations — it is NOT an enum the shell
dispatches on.

Profile packs compile with `esbuild` at install time
(packs/profiles/<id>/compiled/<view>.ssr.js + .client.js + extracted CSS);
this directory is the console-side serving location.

Filled in by prompt 14b/c.
