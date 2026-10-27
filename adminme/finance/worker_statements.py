"""Exact reviewed Money statements exposed to the calculation child.

Every statement binds tenant, owner and book as its first three parameters.
A changed handler statement must be reviewed here before the worker can use it.
This is intentionally a closed domain seam, not a general database RPC.
"""

MONEY_STATEMENTS = frozenset(
    {
        "INSERT INTO finance_dated_event_bindings VALUES (?,?,?,?,?,?,?)",
        "INSERT INTO finance_dated_projection_events VALUES (?,?,?,?,?,?,?,?)",
        "INSERT INTO finance_dated_projection_heads VALUES (?,?,?,?) ON CONFLICT(tenant_id,owner_id,book_id) DO UPDATE SET projection_id=excluded.projection_id",
        "INSERT INTO finance_dated_projection_refs VALUES (?,?,?,?,?,?,?,?,?)",
        "INSERT INTO finance_dated_projections VALUES (?,?,?,?,?,?,?)",
        "INSERT INTO finance_dated_shared_events VALUES (?,?,?,?,?,?)",
        "INSERT INTO finance_provenance_blobs VALUES (?,?,?,?,?,?,?,?,?)",
        "INSERT INTO finance_publication_heads VALUES (?,?,?,?) ON CONFLICT(tenant_id,owner_id,book_id) DO UPDATE SET publication_id=excluded.publication_id",
        "INSERT INTO finance_publication_metrics VALUES (?,?,?,?,?,?,?,?)",
        "INSERT INTO finance_publications VALUES (?,?,?,?,?,?,?,?)",
        "SELECT canonical_json FROM finance_provenance_blobs WHERE tenant_id=? AND owner_id=? AND book_id=? AND root_sha256=? AND content_sha256=?",
        "SELECT canonical_json,approved_evidence_json FROM finance_provenance_blobs WHERE tenant_id=? AND owner_id=? AND book_id=? AND root_sha256=? AND kind='index' AND content_sha256=root_sha256",
        "SELECT content_sha256 FROM finance_dated_projections WHERE tenant_id=? AND owner_id=? AND book_id=? AND projection_id=?",
        "SELECT content_sha256 FROM finance_provenance_blobs WHERE tenant_id=? AND owner_id=? AND book_id=? AND root_sha256=? AND kind!='index' ORDER BY content_sha256 LIMIT 129",
        "SELECT content_sha256 FROM finance_publications WHERE tenant_id=? AND owner_id=? AND book_id=? AND publication_id=?",
        "SELECT content_sha256, publication_json FROM finance_publications WHERE tenant_id=? AND owner_id=? AND book_id=? AND publication_id=?",
        "SELECT kind,canonical_json,approved_evidence_json FROM finance_provenance_blobs WHERE tenant_id=? AND owner_id=? AND book_id=? AND root_sha256=? AND content_sha256=?",
        "SELECT projection_id FROM finance_dated_projection_heads WHERE tenant_id=? AND owner_id=? AND book_id=?",
        "SELECT publication_id FROM finance_publication_heads WHERE tenant_id=? AND owner_id=? AND book_id=?",
        "SELECT publication_json FROM finance_publications WHERE tenant_id=? AND owner_id=? AND book_id=? AND publication_id=?",
    }
)
