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
- PASS: nine principal navigation items
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
- PASS: /member/quests/view_5f5e1cd3ecbe4602 HTTP
- PASS: /member/quests/view_5f5e1cd3ecbe4602 title
- PASS: private quest undiscoverable
- PASS: quest promoted by explicit review
- PASS: /member/quests/view_5f5e1cd3ecbe4602 HTTP
- PASS: /member/quests/view_5f5e1cd3ecbe4602 title
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

- `surface-today-day-brief-desktop.png` — 164421 bytes, SHA-256 `62419a2558793761066e358978a49d5a12e8e7664b7be94c26e3a77a6fd0cd78`
- `surface-today-carousel-desktop.png` — 125972 bytes, SHA-256 `e546995e3552dafb4c747e5665ac990e47fdb8b15dd0ea3b8ce8eaeeb6130c3e`
- `surface-triage-desktop.png` — 138977 bytes, SHA-256 `97bedc57e00040d53926b1cfb4af6101bf449ed1dcd9f425988e45e6550d7208`
- `surface-comms-focus-desktop.png` — 235145 bytes, SHA-256 `f412add64ba5ab7a3a2bfd7f2b83f13b0ec113fd7eb197f21cdb629d8dd2b2e4`
- `surface-people-desktop.png` — 110636 bytes, SHA-256 `1ca981d7d27bcf343f5faa8d8bc03f0d2ac1c3281abb57d238dc36baecd2f0db`
- `surface-knowledge-desktop.png` — 112453 bytes, SHA-256 `df50d710bd1ca07be633330340ca6e311196fee515d831b4bc3271d4528a46d6`
- `surface-finance-desktop.png` — 95320 bytes, SHA-256 `335a2594ef5fde91ec26c40548121a4c881f1192e9738518e56ef11e4880ed3e`
- `surface-calendar-desktop.png` — 93865 bytes, SHA-256 `a9dc79e7d344067435e946e7ef601311630f04db0c7000eae3f208328c56e34c`
- `surface-scoreboard-desktop.png` — 108708 bytes, SHA-256 `98829d3cf75d4b163c57907b3a76ea96b4a0b4d1deeaaf0479240e553b1d6563`
- `surface-settings-desktop.png` — 232172 bytes, SHA-256 `ae98df7467bc2bb0168e809e25f04bc64337759fc241887eadc1dbf7a2217ffe`
- `surface-comms-focus-narrow.png` — 149307 bytes, SHA-256 `71fc76fc58f4d1c9bb33a6adb4992a7757855f53250d0114041c213608ec87f3`
- `mode-compressed-observation-tablet.png` — 116484 bytes, SHA-256 `46995722fe8132110893dee4738fd3997c6fd9c9758888cc71183c7400e67d01`
- `adult-chat-streamed-tablet.png` — 100561 bytes, SHA-256 `22a7ffc1a37bf71f4367d7e6bafa4a1ddfd4fc3d1262bd978e5fd2dae3db221d`
- `quest-private-compressed.png` — 184671 bytes, SHA-256 `e08427e9a2569c396823b3c9c66a4cf44c1470362194f5737dea2c105cfac467`
- `quest-promoted-carousel.png` — 221581 bytes, SHA-256 `0a9c8b6025c094a1906d448a270597cc3537e6d294f07b3218a93d7fa9da2dca`
- `quest-archived-bookmark.png` — 104301 bytes, SHA-256 `15f786c7e286ebb21b0bd3d19b67e2f1dc9aa3672752fc15ac6519635193dd77`
- `quest-expired-bookmark.png` — 104301 bytes, SHA-256 `15f786c7e286ebb21b0bd3d19b67e2f1dc9aa3672752fc15ac6519635193dd77`
- `mode-child-today-narrow.png` — 84695 bytes, SHA-256 `709986338162d729ffb07b13ef12ccd0a6cfdfb2c942612e54defbab31a4dc03`
- `mode-child-scoreboard-narrow.png` — 49989 bytes, SHA-256 `c6c297a4ce74f87aa31a01a8321242b14db7bef29375e5df9b936062b9ca47d1`
- `shared-screen-scoreboard.png` — 82792 bytes, SHA-256 `4c4138c5d5c63e44655a8f8d2e86be7096f987966cef672c6b1eb4d8cb4e30ad`
- `adult-chat-degraded-domain-healthy.png` — 127341 bytes, SHA-256 `f6639c2dff848d0811db6af15523f45028f8de61b90c3053841afcf6ffea3cd7`
