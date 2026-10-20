# Recovery of a failed member forward

This MODERNIZE extends CO-031's member-selected intake and the CoS channel
activation contract. It PRESERVES the original source, member decisions,
append-only history, pinned OpenClaw model seam, private scope, and human
confirmation before household or provider work. It does not revive passive
personal-channel assessment.

## Runtime recovery

A complete private `triage.intent.submitted@2` member forward can fail when
the model transport times out or is temporarily unavailable. Its failed
assessment remains a failure until a valid, explicitly requested replacement
assessment exists. A restart or ordinary delivery retry must not create a new
model invocation.

Recovery names the immutable submission and its exact latest failed assessment.
Only a recognized transient model-seam failure is eligible. Validation examines
the complete reason list; the first reason can describe the holding item rather
than the transport failure. Incomplete evidence, malformed receipts, scope or
tenant mismatches, a changed latest assessment, existing proposals, publication
holds, and member decisions are not permission to retry.

The recovery command is distinct from retired passive-source recovery. It
contains no source text and grants no household or provider authority. At most
eight exact targets can be requested together, at most two interpretations run
concurrently, and at most three recovery attempts are allowed per submission.
Each accepted attempt has its own bounded model invocation identity and a
replacement feature receipt linked to both the request and the prior failure.
Duplicate delivery reuses its durable outcome, including after live mode resumes
or a later release is selected. A refused request has an audited terminal
outcome. A member decision arriving during extraction prevents late candidate
publication.

The source audit accepts a replacement only after validating its complete
private lineage. Original source and failed assessment bytes remain unchanged.
Only proposals may result; members still confirm them in Triage.

The note-extraction skill has a bounded 120-second completion budget for the
complete selected forward. Its prompt, output schema, model routing, and token
limit remain unchanged. Clean compatible successes can be reused; a version
change alone does not grant retry authority for a recorded failure.

## Installing recovery support on a failed predecessor

Ordinary deployment continues to require healthy prerequisites and observation
mode. A separate private repair manifest may describe a bounded known failure
set when the selected predecessor does not yet support submission recovery.
The manifest binds the immutable predecessor and merged candidate, owner and
operator authorization, exact submissions and failed assessments, transient
reasons, and the retained source-count floor. Contact coordinates and source
content do not belong in this manifest or repository.

The maintenance operation validates the exact failure set online and against
the stopped encrypted log. All unrelated health, privacy, identity, source,
consumer, and provider-effect checks remain mandatory. No missing or pending
source, additional failure, unknown effect, or provider write is admitted by
this exception. The existing operation lock, reply hold, encrypted backup,
restore rehearsal, deactivation, and observation-window history remain in force.
Containment reports the preserved degraded baseline honestly; it never labels
the failed source healthy.

HTR-10 exports retain the original degraded source audit and failed counts.
Their review receipt is `repair_pending` and binds the private manifest digest.
Candidate-pending evidence carries the same binding. It cannot be used by an
ordinary activation or with an absent, changed, or different-candidate manifest.
Provider-effect integrity and every retained observation interval still require
their full zero-effect proof.

The guarded updater revalidates the declared baseline and appends only the exact
recovery commands inside its stopped-service transaction. The selected
candidate waits within its existing deployment deadline for only those exact
requests to finish. The old failed assessment remains visible during that wait;
an unrelated failure or a failed terminal retry stops activation. The candidate
must then pass normal healthy settlement, full Doctor, source accounting,
privacy/isolation, and provider-effect checks before activation commits.
Failure returns to the retained contained baseline with observation and reply
holds intact. Rollback retains every newly appended attempt and validates its
lineage without erasing the log. A pending repair receipt is not evidence of
successful repair or permission to enable live replies.

## Required evidence

Production-composition tests must cover successful recovery, duplicate delivery,
restart, stale or forged lineage, cross-member/tenant refusal, exhausted attempts,
member decisions during extraction, and preservation of every original receipt.
Guarded deployment tests must cover exact baseline acceptance, refusal of an
extra failure or effect, interruption, rollback, and final ordinary healthy
settlement. HTR-10 and pending-evidence tests must prove that a repair manifest
cannot authorize a different failure set or an ordinary activation.

Behavioral tests run in isolated CI or an authorized disposable lab. Live
readiness and authenticated member-browser checks are separate evidence.
