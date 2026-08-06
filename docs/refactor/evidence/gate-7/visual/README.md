# Gate 7 rendered portal evidence

Status: **PASS**

Run from the repository root:

```powershell
npm.cmd run test:gate7 --prefix tools/console-reference-harness
```

The harness starts credential-free loopback fixtures, uses Google Chrome via playwright-core@1.62.0, exercises every domain surface and role boundary, streams the OpenClaw fixture, evaluates private/promoted/expired/archived quest states, and checks responsive/reduced-motion/keyboard naming invariants.

## Checks

- PASS: canonical fixture seeded
- PASS: /member/today HTTP
- PASS: /member/today title
- PASS: Today defaults to Day Brief
- PASS: Today offers Carousel and What Now
- PASS: Today hides internal profile and member identifiers
- PASS: Today Carousel is interactive
- PASS: /member/triage HTTP
- PASS: /member/triage title
- PASS: triage member context
- PASS: /member/comms HTTP
- PASS: /member/comms title
- PASS: Comms identifies deterministic private focus
- PASS: Comms renders one member-bound account and thread
- PASS: Comms explains focus without a model
- PASS: Comms reply requires independent approval
- PASS: Comms quiet lens is interactive
- PASS: /member/reminders HTTP
- PASS: /member/reminders title
- PASS: Reminders identifies deterministic provider truth
- PASS: Reminders exposes stable activation lenses
- PASS: Reminders does not invent a writable provider
- PASS: /member/people HTTP
- PASS: /member/people title
- PASS: people member context
- PASS: /member/knowledge HTTP
- PASS: /member/knowledge title
- PASS: knowledge member context
- PASS: /member/finance HTTP
- PASS: /member/finance title
- PASS: finance member context
- PASS: /member/calendar HTTP
- PASS: /member/calendar title
- PASS: calendar member context
- PASS: /member/scoreboard HTTP
- PASS: /member/scoreboard title
- PASS: scoreboard member context
- PASS: /member/settings HTTP
- PASS: /member/settings title
- PASS: settings member context
- PASS: ten principal navigation items
- PASS: desktop named controls
- PASS: desktop no horizontal overflow
- PASS: desktop single page heading
- PASS: desktop screen-reader landmarks
- PASS: desktop contrast tokens
- PASS: desktop keyboard focus indicator
- PASS: /member/comms HTTP
- PASS: /member/comms title
- PASS: Comms reflows without phone overflow
- PASS: Comms phone retains focus explanation and reply approval
- PASS: /member/today HTTP
- PASS: /member/today title
- PASS: tablet Day Brief default
- PASS: observation mode visible
- PASS: adult OpenClaw stream rendered
- PASS: browser stores no transcript, session, cache, worker, or service credential
- PASS: tablet reduced-motion named controls
- PASS: tablet reduced-motion no horizontal overflow
- PASS: tablet reduced-motion single page heading
- PASS: tablet reduced-motion screen-reader landmarks
- PASS: tablet reduced-motion contrast tokens
- PASS: tablet reduced-motion keyboard focus indicator
- PASS: reduced motion honored
- PASS: private quest created
- PASS: /member/quests/view_21d21d12befd4809 HTTP
- PASS: /member/quests/view_21d21d12befd4809 title
- PASS: private quest undiscoverable
- PASS: quest promoted by explicit review
- PASS: /member/quests/view_21d21d12befd4809 HTTP
- PASS: /member/quests/view_21d21d12befd4809 title
- PASS: archived quest has no navigation entry
- PASS: expiring quest created
- PASS: expired quest has no navigation entry
- PASS: /member/today HTTP
- PASS: /member/today title
- PASS: child has two routes
- PASS: /member/scoreboard HTTP
- PASS: /member/scoreboard title
- PASS: narrow child named controls
- PASS: narrow child no horizontal overflow
- PASS: narrow child single page heading
- PASS: narrow child screen-reader landmarks
- PASS: narrow child contrast tokens
- PASS: narrow child keyboard focus indicator
- PASS: child direct finance denial
- PASS: /member/scoreboard HTTP
- PASS: /member/scoreboard title
- PASS: shared screen has scoreboard only
- PASS: /member/today HTTP
- PASS: /member/today title
- PASS: degraded chat explicit
- PASS: domain view remains usable during model loss
- PASS: no browser console errors
- PASS: expected unavailable-view 404s handled
- PASS: no uncaught page errors
- PASS: no failed browser requests

## Screenshots

- `surface-today-day-brief-desktop.png` — 165636 bytes, SHA-256 `3be528f314405dc624e3f63ab44b06b6f1d155dd5b8ef2c22989e54613de58a1`
- `surface-today-carousel-desktop.png` — 127545 bytes, SHA-256 `6b27cd1946ada2dab71177a9aa6ca1fbfe6d0f8ab0094085bd67db60db30d96d`
- `surface-triage-desktop.png` — 140384 bytes, SHA-256 `213abd7f18af35251bb54d53ebab3c0e44f5e8d65062a39e2f5e9929a0028d6a`
- `surface-comms-focus-desktop.png` — 236742 bytes, SHA-256 `5667580e70c51cc18ce8ef78b92d2ed2714c8e3091bc3454a9a2f2f3e250d21a`
- `surface-reminders-desktop.png` — 199166 bytes, SHA-256 `f1b33ae1e0b2b4c34698bbfc526b2fec5038cecc4f1e639f5d11b3700a96bf05`
- `surface-people-desktop.png` — 112110 bytes, SHA-256 `6102bf1bf09d60973d8c4298eda4909b6f81d495c2421f5c5e38386f152a91cf`
- `surface-knowledge-desktop.png` — 114071 bytes, SHA-256 `fa2fdf459d069a4c30e7b13b007ba081125cc18c1b0506fb49fd48b8b1320792`
- `surface-finance-desktop.png` — 96776 bytes, SHA-256 `fdc4e299fe2665da327b0d8e93cf42257055bacb490114cbfebf9845cab991ba`
- `surface-calendar-desktop.png` — 95431 bytes, SHA-256 `8eceba6613a58769405c1694e1e7b9a533d3b1daa00a09633dcb42bd885c5324`
- `surface-scoreboard-desktop.png` — 110256 bytes, SHA-256 `083b832f76e434a03e40dd704936eb964e04045634fa82dc83d75e96126bd010`
- `surface-settings-desktop.png` — 233643 bytes, SHA-256 `f6e2858a8c4015b84a25191c561b6b6639143f336c47484c0ed1073a12f01f26`
- `surface-comms-focus-narrow.png` — 149977 bytes, SHA-256 `8c3c78e4c67969fa0e559a0deaf846aeb41933fdc714493530bc30c50a8df251`
- `mode-compressed-observation-tablet.png` — 117952 bytes, SHA-256 `933e9430e80c8cef1d540bf3a052dbe5907bff698b0a7ed328198785e75588dc`
- `adult-chat-streamed-tablet.png` — 101793 bytes, SHA-256 `f25c424e432cce4a992ef68a2e2117b6a07f7a991f81e16f66c81052ff2a20e1`
- `quest-private-compressed.png` — 186227 bytes, SHA-256 `53e5863222a89bf93f9e30f50182220122a3433320ac431d3b3487f14103e7a9`
- `quest-promoted-carousel.png` — 223300 bytes, SHA-256 `17a92e0d8088455c1fdba19831dff0d1341203d8ac100f4129abfdfcd42b0f66`
- `quest-archived-bookmark.png` — 105893 bytes, SHA-256 `c3e0dd99afe37e3ce50ce652a3d19bcbf2c8bac26eb7af86fc714aadbd68b0fb`
- `quest-expired-bookmark.png` — 105894 bytes, SHA-256 `2d099bc35a14d2efed984fceffd2297148a714b6dc0722d224cebedd730392f9`
- `mode-child-today-narrow.png` — 84637 bytes, SHA-256 `2f026a0d0f2446fa45dfa8e0df37f3d70385e353c7afdbde1c3f46739590ac00`
- `mode-child-scoreboard-narrow.png` — 49989 bytes, SHA-256 `c6c297a4ce74f87aa31a01a8321242b14db7bef29375e5df9b936062b9ca47d1`
- `shared-screen-scoreboard.png` — 82792 bytes, SHA-256 `b48fc482ce8b80b622735ad38a1329c5527794ee9ea5490302d984e0f4c189fa`
- `adult-chat-degraded-domain-healthy.png` — 128354 bytes, SHA-256 `34cebce2a94803b6f7991d070fdea754207271c8211885a95347c0bfac35bdfb`
