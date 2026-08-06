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
- PASS: eight principal navigation items
- PASS: desktop named controls
- PASS: desktop no horizontal overflow
- PASS: desktop single page heading
- PASS: desktop screen-reader landmarks
- PASS: desktop contrast tokens
- PASS: desktop keyboard focus indicator
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
- PASS: /member/quests/view_f1312242a20547e4 HTTP
- PASS: /member/quests/view_f1312242a20547e4 title
- PASS: private quest undiscoverable
- PASS: quest promoted by explicit review
- PASS: /member/quests/view_f1312242a20547e4 HTTP
- PASS: /member/quests/view_f1312242a20547e4 title
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

- `surface-today-day-brief-desktop.png` — 163679 bytes, SHA-256 `e468c616c6eab83e6414eebaa17b3560c9b7378701157552e1b49717cd19b849`
- `surface-today-carousel-desktop.png` — 125055 bytes, SHA-256 `30e5ab7a5874420c736c4582761ea2881cb3e9845e84881af591b466137fd799`
- `surface-triage-desktop.png` — 137903 bytes, SHA-256 `a1eb109129f7a04e2a4e846c124b6d08c2a0814a56a2bc3fac19a755c4e8f368`
- `surface-people-desktop.png` — 109581 bytes, SHA-256 `8d019b7331e3368370fc2825fc505576a3377162df5dae43be7e70f2b6890a69`
- `surface-knowledge-desktop.png` — 111566 bytes, SHA-256 `25249686af26b68448cdbe77a2bd0c20c5ac472a96362ed8e0eeb54137fa3a7e`
- `surface-finance-desktop.png` — 94364 bytes, SHA-256 `d3c6cd59745eb034866c006e06e03c249be2b0b3014dd8b2fa1b4e5e68a6bb00`
- `surface-calendar-desktop.png` — 92848 bytes, SHA-256 `0c38013d9c9ba9d57c69bfa832d9babee8b3aa28fcffbe7a2f7ffe1fa464fe87`
- `surface-scoreboard-desktop.png` — 107701 bytes, SHA-256 `e6d394bf1eae4d19e4ebfbe2337c81aadfd29028d57f6254d60ef49fab245045`
- `surface-settings-desktop.png` — 231060 bytes, SHA-256 `3ac87eacb6237f22dc11f482ef81888ed0a2d114726b0b8bf684d4a13dced526`
- `mode-compressed-observation-tablet.png` — 115653 bytes, SHA-256 `0e7d4f2f89d7ca8911d1fdcb567a77350f7dd46ecae2613005a77994d5a9ad32`
- `adult-chat-streamed-tablet.png` — 99441 bytes, SHA-256 `26c00e36846dd1f5264054ad3fa43c7d4c485adac5da3e44e86f0099ccb785c0`
- `quest-private-compressed.png` — 183619 bytes, SHA-256 `045370e6422acceedde0120e8e1d2725f4102eb18e2cf8e30ffd2eaecef61a7a`
- `quest-promoted-carousel.png` — 220605 bytes, SHA-256 `5de7630310903b728da63f9d62fe4abb2d7e551e20c6959a5b77f0b7b0b6ec3d`
- `quest-archived-bookmark.png` — 103259 bytes, SHA-256 `c7cf5a0ce83cf2183e43193cdc49bfc3c34c632de7d739539923fc6a0a0bd57f`
- `quest-expired-bookmark.png` — 103261 bytes, SHA-256 `ff3babfb94783c14812e987d094d0e77b2b592d0f76cb83363f498dd4b70a66c`
- `mode-child-today-narrow.png` — 109524 bytes, SHA-256 `64fbdebc9cb10059567085eebe5ac52b6ea2cb333f77485b1566a42f955429c2`
- `mode-child-scoreboard-narrow.png` — 50258 bytes, SHA-256 `ab79a691ed199334963158713f607680ec7a4d7787449bad783cb0da3b6b3746`
- `shared-screen-scoreboard.png` — 82792 bytes, SHA-256 `4c4138c5d5c63e44655a8f8d2e86be7096f987966cef672c6b1eb4d8cb4e30ad`
- `adult-chat-degraded-domain-healthy.png` — 126320 bytes, SHA-256 `1330770b89c26dcc3c9dec9c38f641e9df248a6f46657567c6b2d70cf1e403de`
