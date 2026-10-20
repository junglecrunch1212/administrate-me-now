# CO-032: Forwarded-intake deployment readiness

Date: 2026-10-08. Governs the prerequisite release and deployment evidence for
[CO-031](CHANGE-ORDER-031-MEMBER-FORWARDED-OBLIGATIONS.md). It does not enroll a
provider, change acquisition scope, enable writes, or attest an unobserved check.

## Staged release contract

CO-031 correctly refused direct activation over a predecessor that could resume
passive mining after rollback, but did not provide the runnable prerequisite.
This order supplies that prerequisite without changing the forwarding product
decision or using a live configuration switch.

An immutable release declares one of two intake roles in its version manifest:

- `rollback-prerequisite` supports `explicit-member-submission-v1` but declares
  no default cutover policy. With no recorded cutover it performs no passive
  obligation-model work and does not apply the global epoch or retire existing
  Triage candidates at startup. Per-source exclusion receipts are individual
  no-selection decisions, not proof that the historical queue has been retired.
- `cutover` supports and defaults to `explicit-member-submission-v1`. Before
  pipeline replay it records the durable epoch and selectively retires untouched
  automatic candidates according to CO-031.

A validated existing epoch takes precedence over the prerequisite role. A
rollback to that release resumes the same selective retirement and explicit
submission policy; it cannot restore passive extraction. Invalid or ambiguous
manifest/epoch evidence fails closed. The historical fixture switch remains
restricted to credential-free tests and cannot reopen a cutover instance.

The updater validates the durable encrypted-log epoch as well as release
manifests. After cutover, a second rollback from the prerequisite to an unaware
older release must be refused, including interrupted recovery. A manifest with
no default is not authority to ignore a policy already applied to the log.
Policy inspection is read-only and does not construct a runtime or provider.

Deployment uses two protected-main merge releases. First merge and validate the
prerequisite, then activate it through the guarded updater only when its own
bound readiness and backup proofs pass. A later release restores the cutover
default and is separately validated before its guarded activation. Preserve
the prerequisite as the rollback target. Do not edit installed manifests,
releases, environment variables, encrypted history, or release pointers to
simulate either stage. Both stages retain observation mode.

The protected prerequisite release is
`54ed1d753d2a7866743533bd6ab14d7a7a522d30` (PR #440). It includes the reviewed
Python patch alignment and bounded release-history recovery, and passed the
complete hosted Linux and macOS release gates before guarded activation.
The subsequent cutover manifest defaults to `explicit-member-submission-v1`:
production startup records the policy epoch and retires untouched automatic
communication candidates before replay. Retain that prerequisite as the first
rollback target. This **SUPERSEDE** activation applies CO-031's already-tested
intake policy; it does not widen acquisition, enroll channels, enable provider
writes, or change member decisions and source history.

## Evidence freshness and explicitly omitted device coverage

The provider-effect window start is a historical boundary. A long observation
period does not become unsafe solely because that boundary is older than
31 days. Its timestamp must remain valid, unambiguous and no later than the
review. Every HTR-10 review must account for the same original window and exact
event watermark. Current runtime evidence must prove a fresh source audit,
exact window equality, intact terminal accounting and zero unintended effects.
The origin cannot be reset to discard earlier effects. Human checks, review
receipts and backup evidence retain their 31-day freshness requirements.

The owner explicitly requested omission of the second member's device check
for this deployment preparation. Candidate readiness may record exact configured
member IDs as `device_check_exceptions`, separately from actually tested member
IDs. This is an owner-requested coverage exception, never a successful test.
At least one configured member must be tested; unknown IDs, overlap, duplicates,
blanket exemptions and stale evidence fail. The exception remains bound to the
candidate, configuration and dated identity evidence when promoted. Runtime
readiness reports the omitted coverage honestly. This does not alter identity
mapping, member authorization, private scope, tenant isolation or provider
authority. Default readiness still requires every enabled member's device.

Separate administrator existence, Standard runtime account membership,
FileVault enabled state and disabled automatic login can be inspected. They do
not by themselves prove a successful recovery login or FileVault unlock.
Existing evidence may be reused only while actually valid and current; missing
physical evidence must remain unverified. No checkbox or date is fabricated.

An otherwise operational predecessor may export a fresh read-only HTR-10 audit
while only its deployment attestations are stale or mismatched. This collection
path requires healthy topology, provider bindings, OpenClaw and selected bridge
state, healthy capabilities, safe reads and ingestion, active observation,
blocked writes, fresh source accounting, and zero ambiguous or unintended
effects. Only the named binding, recovery, identity, backup and observation
attestation dimensions may block readiness; unknown or operational degradation
still refuses. Candidate code recomputes both audits from the encrypted log at
one watermark and retains the original observation boundary. Export neither
refreshes active readiness nor attests physical checks or authorizes activation.
The subsequent review, pending evidence, backup and guarded activation gates
remain independent. This **MODERNIZE** change removes a circular evidence
collection prerequisite while preserving CO-019's activation requirements.

## Classification and acceptance

| Classification | Change and governing source |
|---|---|
| MODERNIZE | Complete the first-cutover and chained rollback path required by CO-031 and the bootstrap topology contract. |
| MODERNIZE | Distinguish a historical observation boundary from fresh evidence under CO-019; retain exact boundary and zero-effect proof. |
| SUPERSEDE | Permit the owner's explicit, scoped device-coverage omission in CO-019 readiness; retain default full coverage and truthful reporting. |
| PRESERVE | All CO-031 product behavior, source facts, member decisions, required products/projections/adapters/packs/profiles/personas, privacy, governance, observation, pinned OpenClaw seam, backup and immutable release controls. |

Required hosted tests exercise the production composition for prerequisite →
cutover → prerequisite restart/replay, historical-candidate preservation,
zero passive language work, epoch validation, and refusal of chained downgrade
or recovery to unaware code. They also exercise real candidate-pending promotion
with an old observation origin, fresh review evidence, exact boundary matching,
and explicit device-coverage exceptions. Existing full Linux and macOS release
suites, browser checks, isolation and backup/replay proofs remain required.

Source completion does not establish live readiness. Before each activation,
produce an exact private retirement preview, authenticate and rehearse the
encrypted backup, collect genuine current evidence, record a candidate-bound
pending attestation, and let the guarded updater prove its own fresh backup,
rollback eligibility, settlement and full Doctor. Preserve the live acquisition
restriction; any expansion or channel enrollment remains a separate owner scope.
