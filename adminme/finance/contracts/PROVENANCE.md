# Closed Books provenance consumer — candidate wire formats

**MODERNIZE:** enforce the supplied `books-journal-unit.v1` proposal at private
owner ingress and encrypted replay. The exact proposal file is retained with
SHA256 `ce2fdc0d7c291486b71921a4353e4fc53ff16074869a6f07560d35c4a53f3f31`;
its canonical JSON hash is
`4851c2e74ee8054a670d97fe16b3a461b86ea5e8116a5178fdca821d5e626d8c`.
The accounting and dated JSON schemas and their pinned hashes remain unchanged.
No private source, authoritative IDs, grants or activation are supplied here.

The provided proposal defined semantics and limits but not complete JSON layouts
for index, chunk and evidence documents. The strict Pydantic models in
`publication_provenance.py` and the shapes below are a concrete **draft** for
producer review; they are not a claim that an existing producer emits them.
The `hbsource1` dated-reference convention also requires producer review.

## Exact native committed-count predicate

**SUPERSEDE, narrowly:** the earlier proposed boolean acceptance witness is
replaced by the agreed `books-native-committed-count.v1` predicate. Original
`effective_value` CellData is retained as exactly one `numberValue` member.
It must be a finite integral number exactly 1; booleans, strings, missing/errors,
zero and other numbers reject. There is no truthiness or numeric-to-boolean
conversion. The native row keeps its literal formula unchanged:

```
=IF(AF{row}="","",COUNTIFS('Refresh Runs'!A:A,AF{row},'Refresh Runs'!D:D,"COMMITTED"))
```

The physical source row determines `{row}`. The unchanged complete typed row
hash must match independently pinned readback and acceptance evidence. The run
ID is nonempty, contains none of `*?~`, and matches exactly one COMMITTED record
in independently pinned complete run-control evidence. COUNTIFS matching is
case-insensitive; strings are otherwise retained unchanged. Fresh readback is
identified by its own capture time. It cannot backdate native acceptance.
This predicate's semantic agreement is not proof of any private/native line.

## Draft package and evidence shapes

`ClosedPackage` holds canonical index bytes, a closed hash-to-bytes mapping, and
an independently approved evidence-hash set supplied separately at owner ingress.
The package cannot approve its own evidence. No paths, URLs, implicit fetching,
credentials or boolean trust callback are accepted. Live acquisition/approval
of those independent pins is not implemented by this seam.

`ProvenanceIndex` declares contract `books-provenance-index.v1`, unit contract,
exact tenant/owner/book, accepted-source snapshot hash, accepted-unit-set hash,
ordered chunk references and evidence references. Each reference pins hash and
exact bytes; chunks also pin zero-based ordinal and unit count. Evidence lists
are sorted by digest. The index is at most 256 KiB; all referenced blobs together
are capped at 128, each at most 1 MiB, with at most 256 units per data chunk.
The aggregate index plus blobs is capped at 32 MiB. This conservative blob count
includes evidence documents. These are enforcement limits, not measured capacity.

A `books-provenance-chunk.v1` object contains ordinal and lexically ordered units.
Each unit has the immutable ID, exact three-field body, physical source row and
line proof. All 39 native row fields are required. Missing/extra fields reject;
null/empty/numeric/string/bool/formula values retain their canonical bytes.
Line proof names native line/run IDs, the explicit count predicate, original
CellData, readback/acceptance/run-control hashes and the accepted basis.

Each independently pinned `books-line-readback.v1` or
`books-line-acceptance.v1` document contains `source_snapshot_sha256`, its own
`captured_at`, and at most 256 `lines`. Each line names native line ID, complete
native-row hash and committed run. Readback additionally includes source row
and original `effective_value`; acceptance instead includes `acceptance_basis`.
Every referenced line must be used and every unit must have both witnesses.
Capture cannot be later than the new publication. Blank source revisions retain
the original null/empty value and require historical/manual/reversal proof.

The one `books-held-observations.v1` document contains the same source snapshot
hash and exact `observations` pairs of original raw revision and canonical event.
Its revision set equals envelope exclusions. Accepted units must match neither
the held revision set nor the held event set, including blank-source units.

The one `books-committed-runs.v1` document contains the same source snapshot hash
and the complete bounded `runs` list of original `run_id`/`status` pairs (at most
2,048). It must be independently approved as a complete run-control census;
an arbitrary caller-provided subset is not qualifying source evidence.

## Ingress, persistence, replay and recovery

The existing owner importer freezes the closed input mapping, validates every
blob and relationship before archiving or accepting, and rechecks current owner
settings at every archive write and before publication append. Journal and
publication IDs are recomputed. Mixed namespaces, unverified predecessors,
dropped historical units/lines and dropped holds reject. Prior proof is loaded
from its private archive and independently reverified before transition.

`finance.provenance_archived` retains each canonical blob and the owner-approved
index pins in privileged encrypted append-only events. The encrypted money
projection provides a tenant/owner/book/root/hash lookup. Roots and bytes are
immutable; repeated writes are idempotent. Incomplete archival cannot advance
a publication head. Replay verifies the same closure; missing proof raises
`provenance_recovery_hold` and must never skip validation. Recovery restores the
missing retained proof and replays; it does not delete accepted history.

A dated publication against this namespace must include one owner-only evidence
reference `hbsource1:<accepted-source-snapshot-sha256>`, with matching artifact
reference, content hash and source-table hash. It remains anchored to the exact
accounting publication/hash and accepted revision subset. No forecast is
recalculated; the producer seals the dated envelope after binding. This explicit
draft convention avoids treating the unrelated engine-input hash as that proof.

Proof validation/archival runs outside CoS turns. Query and prompt surfaces never
load proof rows. Limits, source fit, complete native acceptance, encrypted
migration/recovery compatibility and current resource gates still need their
respective evidence before live import/activation. Rollback after new proof
history may require disabling finance on a schema-aware runtime; no predecessor
compatibility is asserted by this candidate.
