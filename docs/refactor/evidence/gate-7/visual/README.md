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
- PASS: Notes fixture seeded
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
- PASS: /member/notes HTTP
- PASS: /member/notes title
- PASS: Notes identifies private authorized recall
- PASS: Notes preserves provider sources and master list
- PASS: Notes detail retains exact source identity
- PASS: Notes links candidates to Triage without inline approval
- PASS: Notes sharing is explicit and read-search only
- PASS: Notes deterministic search is interactive
- PASS: Notes search state survives a URL reload
- PASS: Notes meeting lens is presentation-only
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
- PASS: /member/notes HTTP
- PASS: /member/notes title
- PASS: Notes reflows without phone overflow
- PASS: Notes phone retains sources, search, and source evidence
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
- PASS: /member/quests/view_2046d83566284f0a HTTP
- PASS: /member/quests/view_2046d83566284f0a title
- PASS: private quest undiscoverable
- PASS: quest promoted by explicit review
- PASS: /member/quests/view_2046d83566284f0a HTTP
- PASS: /member/quests/view_2046d83566284f0a title
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

- `surface-today-day-brief-desktop.png` — 268222 bytes, SHA-256 `8e4273166f113c5fe9eb07ad5eea78070a4ee4e8cee701fe345a68fc6a757ab7`
- `surface-today-carousel-desktop.png` — 151205 bytes, SHA-256 `c27f28b96edadd3ce4012c45ed50577556b8f240564d5002751950b87ded41ee`
- `surface-triage-desktop.png` — 261889 bytes, SHA-256 `0c571a96d29ef2ccf75bbbc7786ed1577819843b70f2c4a5f23427bbcd15555c`
- `surface-comms-focus-desktop.png` — 289856 bytes, SHA-256 `cfca22415a93d4427a3b5564c35c63a05844be2616ba6a11c83995c5983b9cc6`
- `surface-reminders-desktop.png` — 215997 bytes, SHA-256 `46677873be139c8bf5d2b25a03bef6c2baee99ed9c6a37361dbb8a386d54ff7e`
- `surface-people-desktop.png` — 128732 bytes, SHA-256 `4266c2e9da0492aaa567e41ca573809c9539ed76add057ec04600ac983a20f1c`
- `surface-notes-meetings-desktop.png` — 265243 bytes, SHA-256 `2db9883f0049e04c42740bdebe7f0f35cc1a6680cda8aeb611acae56da62ae12`
- `surface-finance-desktop.png` — 106764 bytes, SHA-256 `0bd0e8ede0647ce267ec59e5b53ddd450b1ea8b3f135f37c456afa6c2cd05fb3`
- `surface-calendar-week-desktop.png` — 345916 bytes, SHA-256 `962cd9542d21330529f2e5deaa56b3212d41ca9eb01fc90f75ec1c6323a1ccfe`
- `surface-calendar-month-desktop.png` — 221181 bytes, SHA-256 `37a91ad4e9c741a885c08cc859e21ff2e613ee9eec60537c37ebc18a7b281ef8`
- `surface-scoreboard-desktop.png` — 120849 bytes, SHA-256 `fd7c0f792915917a2eb55fc58c53abbb4da850546ec724670c9d8a103d6c54ea`
- `surface-settings-desktop.png` — 276653 bytes, SHA-256 `7b72f6a036c22aa477683c54dc18f44b21d5ba96ea7d59e1a4c44914f24de724`
- `surface-comms-focus-narrow.png` — 177622 bytes, SHA-256 `90feeea27b5af90c3d19084bfa4f90e8732022cd656a8dcccc5e50f2711cae6f`
- `surface-notes-narrow.png` — 195957 bytes, SHA-256 `dba333b6a3c968dc0825549678f478f4150389d5c380cba7fc5d5e4b6b834ca5`
- `mode-compressed-observation-tablet.png` — 150297 bytes, SHA-256 `56b907bf4fc158ae4c587fdeece3b9e3a49d0fa9ea5e327ee1cb68ba62a927ab`
- `adult-chat-streamed-tablet.png` — 123377 bytes, SHA-256 `230490d6902078eb7133b7cd20b3859a63b47d37dc0bdf98036314cc4745147a`
- `quest-private-compressed.png` — 198236 bytes, SHA-256 `5475df1642430e01ffb517ac861dc93157696a09b8e1b454cfdfd4981d8cf5cb`
- `quest-promoted-carousel.png` — 213343 bytes, SHA-256 `e6bec3d8f76e4926906d21b6a84c2f44006698ae6e37a149716d09593493303c`
- `quest-archived-bookmark.png` — 106849 bytes, SHA-256 `0744a8393312b2807cca36b4e92e2957cf76daab22c68a08035061b24d8718e3`
- `quest-expired-bookmark.png` — 106849 bytes, SHA-256 `0744a8393312b2807cca36b4e92e2957cf76daab22c68a08035061b24d8718e3`
- `mode-child-today-narrow.png` — 107430 bytes, SHA-256 `34212b2d001e5571bb762e3198202e5291fe106af3c2ed4797ac73d774c1e593`
- `mode-child-scoreboard-narrow.png` — 60322 bytes, SHA-256 `575b425065a77f7590bf05b6efd14327c8f4514d0ea8c987d1344a4bc205995e`
- `shared-screen-scoreboard.png` — 92830 bytes, SHA-256 `b915c7580f0171f6cd648ecae4ed63aa6dd15a2d874d641d3d79da8372900b2e`
- `adult-chat-degraded-domain-healthy.png` — 176694 bytes, SHA-256 `bf5b0be063115bc88bb3752a70e795f13d46952c6477acae658d9e56b54c9df9`
