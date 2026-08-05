# Gate 7 rendered portal evidence

Status: **PASS**

Run from the repository root:

```powershell
npm.cmd run test:gate7 --prefix tools/console-reference-harness
```

The harness starts credential-free loopback fixtures, uses installed Microsoft Edge through pinned Playwright, exercises every domain surface and role boundary, streams the OpenClaw fixture, evaluates private/promoted/expired/archived quest states, and checks responsive/reduced-motion/keyboard naming invariants.

## Checks

- PASS: canonical fixture seeded
- PASS: /member/today HTTP
- PASS: /member/today title
- PASS: today carousel mode
- PASS: /member/inbox HTTP
- PASS: /member/inbox title
- PASS: inbox carousel mode
- PASS: /member/people HTTP
- PASS: /member/people title
- PASS: people carousel mode
- PASS: /member/knowledge HTTP
- PASS: /member/knowledge title
- PASS: knowledge carousel mode
- PASS: /member/finance HTTP
- PASS: /member/finance title
- PASS: finance carousel mode
- PASS: /member/calendar HTTP
- PASS: /member/calendar title
- PASS: calendar carousel mode
- PASS: /member/scoreboard HTTP
- PASS: /member/scoreboard title
- PASS: scoreboard carousel mode
- PASS: /member/settings HTTP
- PASS: /member/settings title
- PASS: settings carousel mode
- PASS: eight principal navigation items
- PASS: desktop named controls
- PASS: desktop no horizontal overflow
- PASS: desktop single page heading
- PASS: desktop screen-reader landmarks
- PASS: desktop contrast tokens
- PASS: desktop keyboard focus indicator
- PASS: /member/today HTTP
- PASS: /member/today title
- PASS: compressed profile
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
- PASS: /member/quests/view_b15e7b2fbed042a6 HTTP
- PASS: /member/quests/view_b15e7b2fbed042a6 title
- PASS: private quest undiscoverable
- PASS: quest promoted by explicit review
- PASS: /member/quests/view_b15e7b2fbed042a6 HTTP
- PASS: /member/quests/view_b15e7b2fbed042a6 title
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

- `surface-today-carousel-desktop.png` — 89736 bytes, SHA-256 `0047944c8607d0666b189841dcecf9cca4cba134f024ebf60d2c609ec7b412b1`
- `surface-inbox-carousel-desktop.png` — 81139 bytes, SHA-256 `2ad235036db11e7cc8de2cfe7242fe903607a3c6546f10f0e4cb5e497e050cdf`
- `surface-people-carousel-desktop.png` — 85831 bytes, SHA-256 `934236abc4e3132467b3ab5c4e63f7404dd9c49ab43980c54c029e73435b8175`
- `surface-knowledge-carousel-desktop.png` — 90384 bytes, SHA-256 `59db415c8ddb47da5cc3dae1a1ebaa1e0494cb886fedc2f89b787e5e9068cbf3`
- `surface-finance-carousel-desktop.png` — 76248 bytes, SHA-256 `3f0079f46839735ad6e59d3413d1f9b6db2c622996ecb0194053451b531b36f4`
- `surface-calendar-carousel-desktop.png` — 75580 bytes, SHA-256 `f95b008171d41c9b419d169992f2a658035153d61aaa4525e67d4136f9241dd6`
- `surface-scoreboard-carousel-desktop.png` — 87583 bytes, SHA-256 `ae4c20cdffcf1062e3cf8b0ce4ec92dd7eb728971d8d7bfe555d267c64ee5ee6`
- `surface-settings-carousel-desktop.png` — 160717 bytes, SHA-256 `bf075fe247130b1d1a9f9dc27cbe0dcadf208c66c99ee7abcfb519cc6cff4e38`
- `mode-compressed-observation-tablet.png` — 69213 bytes, SHA-256 `9088836c6c18173485c4619bc81f07340733be4e7cc000b6f9372ecdab6b25a6`
- `adult-chat-streamed-tablet.png` — 64212 bytes, SHA-256 `762fcc6bd352ea7d5fe6d018587d6ea029621f8830dd37fd2e17ceea3f687f4d`
- `quest-private-compressed.png` — 151177 bytes, SHA-256 `748fc3469e05d326567d1dcf665b320303035be5ea2a2b20bbd247e3332faba9`
- `quest-promoted-carousel.png` — 181388 bytes, SHA-256 `eafbc7523e343443b4e54bf5205ab2a41475c2b1d32b026fc54a10e2a2c3905a`
- `quest-archived-bookmark.png` — 89935 bytes, SHA-256 `2810a61cc86853856e0460dc7fe424d71c9eb1bd8bc9b0f5ce93fc0fc93e3820`
- `quest-expired-bookmark.png` — 89935 bytes, SHA-256 `2810a61cc86853856e0460dc7fe424d71c9eb1bd8bc9b0f5ce93fc0fc93e3820`
- `mode-child-today-narrow.png` — 45540 bytes, SHA-256 `eae42ea4cdcbd9c5a25b11d049f3b112c6c8ac8971d2d27e06c8046f43a92948`
- `mode-child-scoreboard-narrow.png` — 43721 bytes, SHA-256 `8461521294324ca91bf5407ff78fd65b20ae307e66661807e5d10e8b412eb106`
- `shared-screen-scoreboard.png` — 73231 bytes, SHA-256 `0ee50d530f1961d63f4d7ace9b21e8a1d51c06112e875729c1f84c8b12a4277f`
- `adult-chat-degraded-domain-healthy.png` — 71641 bytes, SHA-256 `546a85e439586c0a18143ea49f0123020a7a07c573a22e7cb143131a708dd305`
