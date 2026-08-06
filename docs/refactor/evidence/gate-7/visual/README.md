# Gate 7 rendered portal evidence

Status: **PASS**

Run from the repository root:

```powershell
npm.cmd run test:gate7 --prefix tools/console-reference-harness
```

The harness starts credential-free loopback fixtures, uses Google Chrome via playwright-core@1.62.0, exercises every domain surface and role boundary, streams the OpenClaw fixture, evaluates private/promoted/expired/archived quest states, and checks responsive/reduced-motion/keyboard naming invariants.

## Checks

- PASS: canonical fixture seeded
- PASS: calendar fixture seeded
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
- PASS: Calendar identifies deterministic policy
- PASS: Calendar defaults to a spatial Week
- PASS: Calendar exposes source and capacity lenses
- PASS: Calendar separates all-day and timed events
- PASS: Calendar visibility is view-only
- PASS: Calendar detail preserves timezone and buffer provenance
- PASS: Calendar Month is an anchored six-week grid
- PASS: Calendar Agenda remains available
- PASS: Find time offers explicit duration
- PASS: Calendar policy editing is progressively disclosed
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
- PASS: /member/quests/view_d7e1a5794a4d49bd HTTP
- PASS: /member/quests/view_d7e1a5794a4d49bd title
- PASS: private quest undiscoverable
- PASS: quest promoted by explicit review
- PASS: /member/quests/view_d7e1a5794a4d49bd HTTP
- PASS: /member/quests/view_d7e1a5794a4d49bd title
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

- `surface-today-day-brief-desktop.png` — 214236 bytes, SHA-256 `6b635466464849c425cf3c2285a098706fb4430e98b812031e4c87c625efb63e`
- `surface-today-carousel-desktop.png` — 133202 bytes, SHA-256 `4ec90cdcda39f3bfa7aed08945c223b0837df687831a30fc265ab3851874bb14`
- `surface-triage-desktop.png` — 140243 bytes, SHA-256 `9139dc4631c1645f779dedfd448128f884878c1cdfdd057a96d5773adadf4e7d`
- `surface-comms-focus-desktop.png` — 236780 bytes, SHA-256 `872d68fe125853de22b5dee0cf5a363227923cfd92cc8ae00a114d42f2e75b11`
- `surface-reminders-desktop.png` — 199170 bytes, SHA-256 `5a24126228f4c8a47c3275b1f1ca2b77effbcfa385f612d15cd4f025c4ed5aef`
- `surface-people-desktop.png` — 112271 bytes, SHA-256 `f9aad39be7ebd9906881c5a4e468a705100d243e6d6f883e222f71781e7889a8`
- `surface-knowledge-desktop.png` — 114145 bytes, SHA-256 `e4f9aeb9bbe7ad60f6cdc4b62606cc561a40a85f7bc72f54b12f35b58e664021`
- `surface-finance-desktop.png` — 96877 bytes, SHA-256 `8c299b3520a16646b963a6af408e3597a03b105e662f97e10ae617e398027f4b`
- `surface-calendar-week-desktop.png` — 314096 bytes, SHA-256 `a51d1052c98d3282c57fd68b201d0056a4bd2255e96280ff4e224f6209e6d8f4`
- `surface-calendar-month-desktop.png` — 197610 bytes, SHA-256 `41bb4c26d73e5b6bcc3dc52b00ee5b4a8c0b2b8ff18c5e81c4f5fd2720219f44`
- `surface-scoreboard-desktop.png` — 110285 bytes, SHA-256 `65cc77fdee481d7ea2075a9e9dc8f828a5cbfcd0bac0d9ced93c363e7c9632aa`
- `surface-settings-desktop.png` — 233675 bytes, SHA-256 `561ac57435d86e9a627a1814ba99a91bc8ab00c282f33f1f1ab3a7534f279c07`
- `surface-comms-focus-narrow.png` — 150304 bytes, SHA-256 `7013073ca06eeac6382856fc832f06902a13081f0942f1f5f12b75e5c960b198`
- `mode-compressed-observation-tablet.png` — 126432 bytes, SHA-256 `2c6272f58b5a0cb9e9c2aeb0c3d13473c1320e9b60b10709b9af08862acb432f`
- `adult-chat-streamed-tablet.png` — 109493 bytes, SHA-256 `68fae741f19e68ec6f355402390e2f6307f1f1c22ad2c7f17129334faf561e82`
- `quest-private-compressed.png` — 186221 bytes, SHA-256 `a3cffc285744dd3cd256e7c11683a8c645388e7ad927662bf9a67681529d7455`
- `quest-promoted-carousel.png` — 200154 bytes, SHA-256 `498bd5847ba02db6dabda12994dbc36d65b04d20882003e59c7b72950537ab38`
- `quest-archived-bookmark.png` — 105943 bytes, SHA-256 `369ef0fb43cfa507d26d3c0e09235087bcd892d7f777f11e3042411c465cef27`
- `quest-expired-bookmark.png` — 105943 bytes, SHA-256 `369ef0fb43cfa507d26d3c0e09235087bcd892d7f777f11e3042411c465cef27`
- `mode-child-today-narrow.png` — 92591 bytes, SHA-256 `5c895a8b463d4a23600d7b3a12983d3b017fbec55cef81015446beab083cc432`
- `mode-child-scoreboard-narrow.png` — 49989 bytes, SHA-256 `c6c297a4ce74f87aa31a01a8321242b14db7bef29375e5df9b936062b9ca47d1`
- `shared-screen-scoreboard.png` — 82792 bytes, SHA-256 `b48fc482ce8b80b622735ad38a1329c5527794ee9ea5490302d984e0f4c189fa`
- `adult-chat-degraded-domain-healthy.png` — 168026 bytes, SHA-256 `373f57c749f492eef9a06ea5b1f97388fb63a56c15dd32af39baebe678eef7ec`
