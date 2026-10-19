# CO-034: Bounded release selection with complete recovery history

Date: 2026-10-19. **MODERNIZE** the immutable deployment and recovery contract
in the bootstrap topology contract and CO-019/032/033.

The live release selector is limited to 64 validated entries. Previously,
reaching that valid limit prevented every further forward deployment. The
guarded supervisor now admits a valid full selector to the journaled updater;
oversized, duplicated, unsafe or malformed chains still fail before mutation.

Journaled activation retains the exact complete predecessor selector bytes and
digest before any selection, together with the existing fresh encrypted backup,
rehearsal and rollback-eligibility proof. Its new live selector contains the new
release and at most the 63 newest predecessor entries. The original selector
and its omitted oldest entry remain in the durable activation journal/history.
No release directory, backup, journal or encrypted event is deleted. Every
original chain entry is validated before constructing the bounded view.

Interrupted activation restores the original selector bytes. Explicit rollback
requires the exact committed activation history, validates every archived
release coordinate including the omitted tail, and compares the current
predecessor view with the bounded view derived from that history. It restores
the complete original selector, not the shortened view. A missing archive or
mismatched history cannot authorize rollback. Subsequent forward deployments
repeat this transaction without raising the parser limit or editing live
metadata by hand. The unjournaled credential-free legacy test path retains its
full-chain refusal because it cannot provide durable recovery history.

**PRESERVE** source verification, immutable release/lifecycle protections,
candidate-bound readiness, observation mode, acquisition scope, intake epoch
downgrade guards, recovery deadlines, full Doctor, and all product functions.
This remains the rollback-prerequisite release; the later CO-032 cutover is
unchanged. This order authorizes no provider or runtime-baseline expansion.

Hosted proofs cover full-selector forward activation, exact rollback, another
fresh forward activation, unchanged retained directories and audit history,
bounded-dataset continuity, oversized/malformed tail refusal, and interrupted
selection followed by real production-runtime recovery with unchanged owner
evidence and zero provider effects. All protected and aggregate release checks
remain required before deployment.
