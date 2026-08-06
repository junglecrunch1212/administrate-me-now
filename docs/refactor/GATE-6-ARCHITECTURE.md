# Gate 6 converged architecture

Gate 6 keeps AdministrateMe as the household domain system and OpenClaw as the
assistant platform. AdministrateMe owns facts, policy, projections, scoped
queries, proposals, workbooks, provider reconciliation, and evidence. OpenClaw
owns scheduling, channel delivery, assistant sessions, and model routing.

```mermaid
flowchart LR
    P["Configured providers and member bridges"] --> A["Adapters: Cat A-E"]
    A --> S["EventSink"]
    O["OpenClaw standing orders"] --> D["Domain pipeline contracts"]
    D --> S
    S --> L["Encrypted append-only event log"]
    L --> B["Durable event bus"]
    B --> R["6 reactive pipelines"]
    B --> X["13 domain projections + Triage"]
    X --> API["6 product namespaces"]
    X --> W["2 bidirectional workbooks"]
    X --> Q["Scoped domain search and activity"]
    R --> S
    D --> N["Notification signal and decision"]
    N --> O
    O --> DR["Delivery result"]
    DR --> S
```

## Ownership boundaries

| Concern | Owner | Gate 6 rule |
|---|---|---|
| Household truth and history | AdministrateMe event log | Append first; preserve owner, visibility, sensitivity, source, correlation, and causation. |
| Replay and reads | Thirteen domain projections plus derived Triage | Deterministic apply, checkpoint resume, scoped queries, rebuild from event zero. |
| Member knowledge | `member_knowledge` plus Cat-D bridge | Explicit Apple Notes, Voice Memos, and optional adult Obsidian sources only; never generic conversation memory. |
| Semantic search | `vector_search` | Domain records only; privileged material is never embedded and owner scope is enforced. |
| Scheduling | OpenClaw automations | AdministrateMe stores program contracts and trigger evidence but runs no product cron. |
| Delivery | OpenClaw plugin | AdministrateMe emits signal/decision; only a delivery-result callback may assert an outcome. |
| Model/provider routing | OpenClaw | AdministrateMe makes one pinned skill request and records returned provenance. |
| Extension behavior | Six pack kinds | Profiles and personas are versioned data; activation is staged, tested, atomic, and reversible. |
| Optional providers | Capability registry | Missing configuration is disabled/blocked, never fabricated health or process failure. |

The old `console/` package had no constitutional household surface—it was a
comment-only server, package metadata, and README placeholders. Its deletion
does not delete the Core, Comms, Capture, Automation, Bridge, or System APIs,
the two workbooks, the OpenClaw dashboard, or the reference UX evidence.
