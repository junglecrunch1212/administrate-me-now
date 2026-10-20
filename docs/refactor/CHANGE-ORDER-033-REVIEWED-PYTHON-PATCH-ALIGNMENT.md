# CO-033: Reviewed Python patch alignment

Date: 2026-10-19

This **MODERNIZES** ADR-0006's exact Python baseline to 3.14.7, retaining
`python@3.14`, the compatibility range, locked dependencies, Node 24.19.0,
and the exact OpenClaw commit. CI uses the same Python patch on Linux and macOS.
The owner separately authorized the Python alignment, guarded runtime migration,
required copies of existing Keychain credentials, and continued deployment.

An installed formula may resolve a retained release's interpreter to a newer
patch while its immutable manifest still records the original pin. This
**SUPERSEDES** unconditional original-patch matching only for an explicitly
listed retained commit in the reviewed controller manifest. Before selecting
the alternate exact patch, the updater must verify the entire retained source
tree against that Git commit, including its original manifest and dependency
lock. The declaration must match the original preferred version, formula and
compatibility range, and select the controller's exact preferred forward patch
within the same Python major/minor. Missing declarations retain the original
strict pin; malformed, duplicate, contradictory or broader declarations fail.

The only initial declaration covers retained release
`e12156545c02bd6ed0673ce7b5c1dc3f69313903`, declared 3.14.6, running 3.14.7.
Hosted acceptance verifies its archived source and actual interpreter, starts
its production composition with synthetic encrypted data, and proves restart
and replay. Editing that archive's dependency lock must invalidate the proof.
This declaration confers no authority to prepare new candidates under a
different pin or to downgrade to another historical commit.

The same source-first retained validation applies to pre-switch rollback
eligibility, interrupted activation recovery and explicit transactional
rollback. Existing interpreter-path, private-directory, Poetry and Node checks
remain mandatory. Candidate preparation continues to require its own exact pin.

This **PRESERVES** CO-032's two-stage intake deployment. This release remains a
rollback prerequisite and does not initialize the new intake epoch or retire
the existing triage queue. Only a later protected-main cutover release may do
that, with the prerequisite retained. Durable intake-policy checks continue to
refuse rollback to an unaware release after cutover, regardless of Python
compatibility.

Also **PRESERVE** the explicit runtime-migration flag, immutable releases,
protected pull-request checks, encrypted backup, bounded settlement, full
Doctor, private member acceptance, recovery and observation evidence, unchanged
provider/acquisition configuration, and zero unintended external effects.
No required function or safety gate is deferred.

## Recovery of a pre-existing readiness gap

The prerequisite deployment starts from a readable, observation-only service
whose old readiness evidence may be expired or bound to a pre-reboot host
fingerprint. Refreshing that evidence on rollback is neither truthful nor
always possible. Refusing its refresh must not strand the predecessor stopped.

This **SUPERSEDES** the assumption that every recovered predecessor was fully
deployment-ready before the attempted upgrade. Before mutation, the controller
may journal a narrowly validated baseline containing only the existing five
attestation-related blocker names, the exact original readiness-file digest,
and current host/instance fingerprints. All operational dimensions, pipelines,
projections, source accounting, observation containment and zero-effect checks
must already pass. Unknown or unsafe production readiness cannot create this
baseline. Missing baseline evidence grants no exception.

On interrupted recovery or rollback through the exact committed activation
history, preserve those original readiness bytes. Require the same host and
instance, unchanged restored controls, two fresh safe runtime observations,
and a full selected-release Doctor whose only failed checks are readiness and
its derived process-health check. LaunchAgent definitions and running services
must still be proved. Any new blocker, provider effect, source-accounting error,
process failure or unrelated Doctor failure refuses successful recovery.

**MODERNIZE** recovery settlement: each safe observation must show zero lag for
every registered projection and reactive pipeline, followed by a current
source/effect audit with no refresh in progress. A current audit collected
before consumers finish does not prove settlement. Replay or a temporary
`observation_effects` gap may wait only within the existing absolute operation
budget and checkpoint-progress watchdog, reserving time for full Doctor.
Malformed consumer evidence, failed accounting and provider effects fail closed.
**PRESERVE** the exact saved blocker allowlist and original evidence: an added
observation gap must disappear before recovery succeeds; waiting does not
refresh an attestation or make expired evidence ready.

Report and journal `preexisting_attestation_gaps` rather than `healthy` when
that baseline is restored. The dashboard and readiness endpoint remain blocked;
timestamps and the original observation origin remain unchanged. Successful
candidate activation still requires full readiness and full Doctor success.
This is restoration of an existing safe operating state, not deployment
approval or refreshed physical evidence.
