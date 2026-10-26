"""Exact synthetic summary compatibility, admitted only under the owned watchdog."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import resource
import sys

import pytest
import sqlcipher3
from fastapi.testclient import TestClient

from adminme.finance import planning_store
from adminme.finance.dated_projection import validate_projection
from adminme.finance.publication import PublicationError, canonical_bytes
from adminme.lib.session import build_internal_session
from adminme.runtime.application import create_application
from tests.acceptance.test_finance_disclosure import (
    enabled_settings,
    import_fixture,
    revoke,
)
from tests.acceptance.test_finance_native_facade import _post
from tests.acceptance import test_finance_planning as existing
from tests.acceptance.test_finance_publications import publication
from tests.acceptance.test_openclaw_native_seam import DEMO_HEADERS, _grant_cos
from tests.fixtures.finance_planning import (
    projection,
    seal,
    with_conservative_minima,
    with_grouped_issues,
    with_unavailable_openings,
)
from tests.fixtures import finance_watchdog as watchdog

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.getenv("FINANCE_WATCHDOG_MODE") != "summary-metadata",
    reason="exact isolated summary metadata admission required",
)
LEGACY_SQL = "SELECT metadata_json FROM finance_dated_projections WHERE tenant_id=? AND owner_id=? AND book_id=? AND projection_id=?"
VIEWS = ("all", "balances", "signals", "dates", "provenance", "clocks")
DAYS = (7, 30, 90, 180, 365)
AXES = (
    "measure",
    "scenario_id",
    "income_basis",
    "opening_basis",
    "plan_case_id",
    "currency",
    "timezone",
    "as_of_date",
)


def typed(value):
    """Keep mapping order, absent keys, float spelling and bool/int distinct."""
    if type(value) is dict:
        return ["dict", [[typed(k), typed(v)] for k, v in value.items()]]
    if type(value) in (list, tuple):
        return [type(value).__name__, [typed(v) for v in value]]
    if type(value) in (str, int, float, bool, bytes, type(None)):
        return [type(value).__name__, repr(value)]
    raise AssertionError(type(value).__name__)


def capture(call):
    try:
        return {"returned": typed(call())}
    except Exception as exc:
        return {
            "exception": type(exc).__module__ + "." + type(exc).__qualname__,
            "args": typed(exc.args),
        }


def record(name, **fields):
    path = Path(os.environ["FINANCE_STORAGE_ARTIFACT_DIR"]) / "summary-results.jsonl"
    raw = (json.dumps({"case": name, **fields}, ensure_ascii=True) + "\n").encode()
    assert len(raw) <= 1024 * 1024
    assert (path.stat().st_size if path.exists() else 0) + len(raw) <= 8 * 1024 * 1024
    with path.open("ab") as stream:
        stream.write(raw)


class ReadConnection:
    """Same builder and database; reference changes only the metadata SELECT."""

    def __init__(self, conn, *, legacy=False):
        self.conn, self.legacy = conn, legacy
        self.statements = []
        self.metadata_statement = None

    def execute(self, sql, parameters=()):
        if "FROM finance_dated_projections WHERE tenant_id=?" in sql:
            if self.legacy:
                sql = LEGACY_SQL
            self.metadata_statement = (sql, parameters)
        self.statements.append(sql)
        return self.conn.execute(sql, parameters)

    def metadata(self):
        assert self.metadata_statement is not None
        return self.conn.execute(*self.metadata_statement).fetchone()[0]


@pytest.fixture(scope="module")
def sample_metadata():
    with sqlcipher3.connect(":memory:") as version_conn:
        record(
            "runtime-start",
            python=sys.version,
            sqlite=version_conn.execute("SELECT sqlite_version()").fetchone()[0],
            sqlcipher=version_conn.execute("PRAGMA cipher_version").fetchone()[0],
            int_max_str_digits=sys.get_int_max_str_digits(),
        )
    version_conn.close()
    accounting = publication()
    value = with_grouped_issues(
        with_conservative_minima(with_unavailable_openings(projection(accounting)))
    )
    validate_projection(value)
    # Only the query fixture extends this unused list. Ingestion proof is tested
    # separately with real validated publication/projection imports below.
    value["accounting_revision_ids"] = [
        f"revision-{n:05d}-" + "x" * 65 for n in range(8022)
    ]
    metadata = {
        k: v
        for k, v in value.items()
        if k
        not in {
            "detail_events",
            "shared_event_inventory",
            "evidence_ids",
            "gaps",
            "gap_issues",
            "evidence",
            "sources",
        }
    }
    return value, canonical_bytes(metadata).decode()


@pytest.fixture
def stored(sample_metadata):
    value, raw = sample_metadata
    owner = build_internal_session(value["owner_id"], "principal", value["tenant_id"])
    conn = sqlcipher3.connect(":memory:")
    schema = Path(planning_store.__file__).parents[1] / "projections/money/schema.sql"
    conn.executescript(schema.read_text())
    key = (value["tenant_id"], value["owner_id"], value["book_id"])
    conn.execute(
        "INSERT INTO finance_dated_projections VALUES (?,?,?,?,?,?,?)",
        (*key, value["projection_id"], value["content_sha256"], raw, "event-synthetic"),
    )
    conn.execute(
        "INSERT INTO finance_dated_projection_heads VALUES (?,?,?,?)",
        (*key, value["projection_id"]),
    )
    conn.execute(
        "INSERT INTO finance_publication_heads VALUES (?,?,?,?)",
        (*key, value["accounting_publication_id"]),
    )
    try:
        yield conn, owner, value, raw
    finally:
        conn.close()


def query(connection, owner, value, **changes):
    args = {
        "book_id": value["book_id"],
        "projection_id": value["projection_id"],
        "context_id": value["contexts"][0]["context_id"],
        "report_id": "planning_projection",
        "horizon_days": 30,
    }
    args.update(changes)
    return planning_store.query_projection(connection, owner, **args)


def matched(stored, **changes):
    conn, owner, value, _ = stored
    before = ReadConnection(conn, legacy=True)
    after = ReadConnection(conn)
    old = capture(lambda: query(before, owner, value, **changes))
    new = capture(lambda: query(after, owner, value, **changes))
    return old, new, before, after


def test_summary_equivalence_all_contexts_horizons_views(stored):
    _, _, value, _ = stored
    digest = hashlib.sha256()
    count = 0
    for index, context in enumerate(value["contexts"]):
        for days in DAYS:
            for view in VIEWS:
                selected = (
                    {"context_id": context["context_id"]}
                    if index % 2
                    else {"context_id": None, "selector": {k: context[k] for k in AXES}}
                )
                old, new, _, _ = matched(
                    stored, **selected, horizon_days=days, view=view
                )
                assert "returned" in old
                if old != new or (index == 0 and days == 7):
                    record(f"matrix-{index}-{days}-{view}", before=old, after=new)
                assert old == new
                digest.update(json.dumps(old, ensure_ascii=True).encode())
                count += 1
        # Both supported coordinate forms identify this same context.
        direct, selected, _, _ = matched(stored, context_id=context["context_id"])
        via_axes, candidate, _, _ = matched(
            stored, context_id=None, selector={k: context[k] for k in AXES}
        )
        assert direct == selected == via_axes == candidate
    assert count == 720
    record(
        "matrix-complete",
        comparisons=count,
        ordered_type_value_sha256=digest.hexdigest(),
    )


SELECTIONS = {
    "negative-offset": {"offset": -1},
    "invalid-limit": {"limit": 0},
    "bool-horizon": {"horizon_days": True},
    "invalid-horizon": {"horizon_days": 8},
    "invalid-view": {"view": "other"},
    "incomplete-selector": {
        "context_id": None,
        "selector": {"measure": "BANK_CASH_SUBSET"},
    },
    "ambiguous-selector": {
        "context_id": None,
        "selector": {
            "measure": "BANK_CASH_SUBSET",
            "scenario_id": "current",
            "income_basis": "OBSERVED_BASE_ONLY",
            "opening_basis": "observed_cash_hypothetical",
        },
    },
    "unknown-context": {"context_id": "absent"},
    "unknown-projection": {"projection_id": "absent"},
    "case-sensitive": {"context_id": "BANK-current-base"},
    "context-and-selector": {"selector": {"measure": "BANK_CASH_SUBSET"}},
    "reference-filter": {"reference_id": "absent"},
    "gap-filter": {"gap_group_id": "absent"},
}


@pytest.mark.parametrize("case", list(SELECTIONS))
def test_summary_selection_errors(stored, case):
    if case == "ambiguous-selector":
        conn, _, _, raw = stored
        metadata = json.loads(raw)
        duplicate = deepcopy(metadata["contexts"][0])
        duplicate["context_id"] = "another-context"
        duplicate["plan_case_id"] = "another-case"
        metadata["contexts"].append(duplicate)
        conn.execute(
            "UPDATE finance_dated_projections SET metadata_json=?",
            (canonical_bytes(metadata).decode(),),
        )
    old, new, _, _ = matched(stored, **SELECTIONS[case])
    record(case, before=old, after=new)
    assert old == new
    if case != "unknown-projection":
        assert "exception" in old


def replace_once(raw, old, new):
    assert old in raw, old
    return raw.replace(old, new, 1)


def field(raw, name, token):
    # Synthetic text construction deliberately avoids decoding malformed input.
    start = raw.index('"accounting_revision_ids":')
    end = raw.index("],", start) + 1
    return raw[:start] + '"' + name + '":' + token + raw[end:]


CORRUPTIONS = (
    "removed-null",
    "removed-number",
    "removed-bool",
    "removed-string",
    "removed-object",
    "removed-mixed",
    "removed-nested",
    "removed-over-limit-int",
    "removed-deep",
    "removed-malformed",
    "removed-empty",
    "missing-removed",
    "duplicate-target-before",
    "duplicate-target-after",
    "duplicate-over-limit-before",
    "duplicate-over-limit-after",
    "escaped-target",
    "escaped-duplicate-before",
    "escaped-duplicate-after",
    "duplicate-used-key",
    "duplicate-used-key-reversed",
    "duplicate-used-over-limit-before",
    "duplicate-used-over-limit-after",
    "escaped-used-key",
    "duplicate-collection-key",
    "used-over-limit-int",
    "used-large-int",
    "used-negative-zero",
    "used-float-underflow",
    "used-float-overflow",
    "removed-nan",
    "removed-infinity",
    "used-nan",
    "used-infinity",
    "utf8-literal",
    "utf8-escaped",
    "escape-control",
    "escape-surrogate",
    "escape-slash",
    "json5-comment",
    "json5-trailing-comma",
    "json5-single-quote",
    "truncated",
    "missing-contexts",
    "wrong-contexts",
    "unselected-malformed-context",
    "missing-horizon",
    "missing-used-field",
    "wrong-root",
    "blob-valid",
    "blob-invalid-utf8",
)
RAW_FALLBACK = set(CORRUPTIONS) - {
    "duplicate-used-over-limit-before",
    "duplicate-used-over-limit-after",
    "removed-empty",
    "duplicate-used-key",
    "duplicate-used-key-reversed",
    "duplicate-collection-key",
    "used-over-limit-int",
    "used-large-int",
    "used-negative-zero",
    "used-float-underflow",
    "used-float-overflow",
    "missing-contexts",
    "wrong-contexts",
    "unselected-malformed-context",
    "missing-horizon",
    "missing-used-field",
}


def corrupted(raw, case):
    over = "1" * (sys.get_int_max_str_digits() + 1)
    removed = {
        "removed-null": "null",
        "removed-number": "123",
        "removed-bool": "true",
        "removed-string": '"revision"',
        "removed-object": '{"a":1}',
        "removed-mixed": '["revision",null]',
        "removed-nested": '[["revision"]]',
        "removed-over-limit-int": "[" + over + "]",
        "removed-deep": "[" * 1100 + "0" + "]" * 1100,
        "removed-malformed": '["revision",]',
        "removed-empty": "[]",
        "removed-nan": "[NaN]",
        "removed-infinity": "[Infinity]",
    }
    if case in removed:
        return field(raw, "accounting_revision_ids", removed[case])
    if case == "missing-removed":
        return field(raw, "not_revision_ids", "[]")
    if case == "escaped-target":
        return field(raw, r"accounting_revision_\u0069ds", '["escaped"]')
    if (
        case.startswith("duplicate-target-")
        or case.startswith("duplicate-over-limit-")
        or case.startswith("escaped-duplicate-")
    ):
        name = (
            r"accounting_revision_\u0069ds"
            if case.startswith("escaped")
            else "accounting_revision_ids"
        )
        token = "[" + over + "]" if "over-limit" in case else '["duplicate"]'
        pair = '"' + name + '":' + token
        return (
            "{" + pair + "," + raw[1:]
            if case.endswith("before")
            else raw[:-1] + "," + pair + "}"
        )
    token = '"lower_minor":1000'
    used = {
        "duplicate-used-key": '"lower_minor":false,"lower_minor":1000',
        "duplicate-used-key-reversed": '"lower_minor":1000,"lower_minor":false',
        "escaped-used-key": r'"lower_minor":1000,"lower_\u006dinor":false',
        "duplicate-used-over-limit-before": '"lower_minor":'
        + over
        + ',"lower_minor":1000',
        "duplicate-used-over-limit-after": '"lower_minor":1000,"lower_minor":' + over,
        "used-over-limit-int": '"lower_minor":' + over,
        "used-large-int": '"lower_minor":' + "9" * 100,
        "used-negative-zero": '"lower_minor":-0.0',
        "used-float-underflow": '"lower_minor":1e-9999',
        "used-float-overflow": '"lower_minor":1e9999',
        "used-nan": '"lower_minor":NaN',
        "used-infinity": '"lower_minor":-Infinity',
    }
    if case in used:
        return replace_once(raw, token, used[case])
    if case == "duplicate-collection-key":
        return replace_once(
            raw,
            '"daily_balances":"unsupported"',
            '"daily_balances":"supported","daily_balances":"unsupported"',
        )
    strings = {
        "utf8-literal": '"INCOMPLETE-é-😀"',
        "utf8-escaped": r'"INCOMPLETE-\u00e9-\ud83d\ude00"',
        "escape-control": r'"line\n\t\u0000end"',
        "escape-surrogate": r'"\ud800"',
        "escape-slash": r'"a\\b\/c\"d"',
    }
    if case in strings:
        return replace_once(
            raw,
            '"mandatory_flow_coverage":"INCOMPLETE"',
            '"mandatory_flow_coverage":' + strings[case],
        )
    if case == "json5-comment":
        return "{/*synthetic*/" + raw[1:]
    if case == "json5-trailing-comma":
        return raw[:-1] + ",}"
    if case == "json5-single-quote":
        return raw.replace('"book_id"', "'book_id'", 1)
    if case == "truncated":
        return raw[:-1]
    if case == "wrong-root":
        return '["scalar"]'
    if case == "blob-valid":
        return raw.encode()
    if case == "blob-invalid-utf8":
        return b'{"accounting_revision_ids":["\xff"]}'
    metadata = json.loads(raw)
    if case == "missing-contexts":
        del metadata["contexts"]
    elif case == "wrong-contexts":
        metadata["contexts"] = None
    elif case == "unselected-malformed-context":
        metadata["contexts"].append({})
    elif case == "missing-horizon":
        metadata["contexts"][0]["horizons"] = []
    elif case == "missing-used-field":
        del metadata["engine_id"]
    else:
        raise AssertionError(case)
    return canonical_bytes(metadata).decode()


def metadata_read(reader):
    try:
        raw = reader.metadata()
    except Exception as exc:
        return None, {
            "exception": type(exc).__module__ + "." + type(exc).__qualname__,
            "args": typed(exc.args),
        }
    return raw, {"returned_type": type(raw).__name__}


def check_corrupted_input(stored, case, changed, *, as_text=False):
    conn, _, _, _ = stored
    sql = (
        "UPDATE finance_dated_projections SET metadata_json=CAST(? AS TEXT)"
        if as_text
        else "UPDATE finance_dated_projections SET metadata_json=?"
    )
    conn.execute(sql, (changed,))
    if as_text:
        storage = conn.execute(
            "SELECT typeof(metadata_json),CAST(metadata_json AS BLOB) FROM finance_dated_projections"
        ).fetchone()
        assert storage[0] == "text" and storage[1] == changed
    old, new, before, after = matched(stored)
    original, metadata_before = metadata_read(before)
    compact, metadata_after = metadata_read(after)
    if "exception" in metadata_before or "exception" in metadata_after:
        record(
            case,
            before=old,
            after=new,
            metadata_before=metadata_before,
            metadata_after=metadata_after,
        )
        assert old == new
        assert metadata_before == metadata_after
        assert "exception" in old
        return

    def decode(value):
        value = json.loads(value)
        if type(value) is dict:
            value.pop("accounting_revision_ids", None)
        return value

    decoder_before = capture(lambda: decode(original))
    decoder_after = capture(lambda: decode(compact))
    record(
        case,
        before=old,
        after=new,
        metadata_before=metadata_before,
        metadata_after=metadata_after,
        decoder_before_exception=decoder_before.get("exception"),
        decoder_after_exception=decoder_after.get("exception"),
        decoder_before_args=decoder_before.get("args"),
        decoder_after_args=decoder_after.get("args"),
        decoder_equal=decoder_before == decoder_after,
        raw_fallback=compact == changed,
    )
    assert old == new
    assert decoder_before == decoder_after
    if case in RAW_FALLBACK:
        assert type(compact) is type(changed) and compact == changed
    if case in {
        "removed-over-limit-int",
        "duplicate-over-limit-before",
        "duplicate-over-limit-after",
        "used-over-limit-int",
        "duplicate-used-over-limit-before",
        "duplicate-used-over-limit-after",
    }:
        assert old["exception"] == "builtins.ValueError"
    if as_text:
        raise AssertionError("invalid UTF-8 TEXT unexpectedly survived the driver read")


@pytest.mark.parametrize("case", list(CORRUPTIONS))
def test_summary_corruption_and_decoder_parity(stored, case):
    _, _, _, raw = stored
    assert sys.get_int_max_str_digits() == 4300  # Observe, never widen the decoder.
    check_corrupted_input(stored, case, corrupted(raw, case))
    if case == "blob-invalid-utf8":
        # Preserve the original BLOB regression, and exercise the distinct
        # driver boundary: bytes deliberately stored with SQLite TEXT class.
        invalid = {
            "invalid-leading": b"\xff",
            "orphan-continuation": b"\x80",
            "overlong-ascii": b"\xc0\xaf",
            "overlong-nul": b"\xc0\x80",
            "truncated-sequence": b"\xe2\x82",
            "surrogate": b"\xed\xa0\x80",
            "above-unicode": b"\xf4\x90\x80\x80",
            "nul-before-invalid": b"\x00\xff",
        }
        template = field(raw, "accounting_revision_ids", '["INVALID-BYTES"]').encode()
        for name, value in invalid.items():
            changed = template.replace(b"INVALID-BYTES", value)
            check_corrupted_input(stored, "text-removed-" + name, changed, as_text=True)
        retained = raw.encode().replace(
            b'"mandatory_flow_coverage":"INCOMPLETE"',
            b'"mandatory_flow_coverage":"\xff"',
            1,
        )
        check_corrupted_input(stored, "text-retained-invalid", retained, as_text=True)


def test_summary_decode_allocation_and_other_reports(stored):
    conn, _, value, raw = stored
    old, new, before, after = matched(stored)
    assert old == new
    original, compact = json.loads(before.metadata()), json.loads(after.metadata())
    ids = original.pop("accounting_revision_ids")
    assert len(ids) == 8022 and "accounting_revision_ids" not in compact
    assert typed(original) == typed(compact)
    assert len(compact["contexts"]) == 24
    assert sum(len(c["horizons"]) for c in compact["contexts"]) == 120
    assert len(before.statements) == len(after.statements) == 3
    assert (
        conn.execute("SELECT metadata_json FROM finance_dated_projections").fetchone()[
            0
        ]
        == raw
    )
    retained = sys.getsizeof(ids) + sum(
        sys.getsizeof(v) for v in {id(v): v for v in ids}.values()
    )
    record(
        "allocation",
        revision_count_before=len(ids),
        revision_count_after=0,
        removed_python_retained_bytes=retained,
        original_input_characters=len(raw),
        compact_input_characters=len(after.metadata()),
        contexts=24,
        horizons=120,
        meaning="getsizeof of decoded revision container and unique strings only; no total/native peak or CPU claim",
    )
    for report in (
        "planning_contexts",
        "planning_events",
        "planning_gaps",
        "planning_issues",
        "planning_evidence",
        "planning_sources",
        "planning_engines",
    ):
        old, new, _, observed = matched(stored, report_id=report)
        assert old == new and observed.metadata_statement[0] == LEGACY_SQL
    assert value["accounting_revision_ids"]  # Fixture remains immutable.


def test_summary_scope_and_current_historical_heads(stored):
    conn, owner, value, _ = stored
    for foreign in (
        build_internal_session("other-owner", "principal", owner.tenant_id),
        build_internal_session(owner.auth_member_id, "principal", "other-tenant"),
    ):
        old = query(ReadConnection(conn, legacy=True), foreign, value)
        new = query(ReadConnection(conn), foreign, value)
        assert typed(old) == typed(new) and new["state"] == "missing_evidence"
    old, new, _, _ = matched(stored, book_id="other-book")
    assert old == new
    conn.execute("UPDATE finance_dated_projection_heads SET projection_id='new-head'")
    conn.execute("UPDATE finance_publication_heads SET publication_id='new-accounting'")
    old, new, _, _ = matched(stored)
    assert old == new
    result = query(ReadConnection(conn), owner, value)
    assert result["projection"]["head_id"] == "new-head"
    assert "accounting_baseline_superseded" in result["limits"]
    old, new, _, _ = matched(stored, projection_id=None)
    assert old == new


def test_summary_replay_and_idempotent_import(tmp_path):
    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        accounting, owner = import_fixture(client)
        value = projection(accounting)
        runtime = client.app.state.runtime
        client.portal.call(planning_store.import_projection, runtime, owner, value)

        async def read():
            return await runtime.projections.read(
                "money", lambda conn: query(conn, owner, value)
            )

        before = client.portal.call(read)
        client.portal.call(runtime.projections.rebuild, "money")
        assert typed(before) == typed(client.portal.call(read))
        assert client.portal.call(
            planning_store.import_projection, runtime, owner, value
        )["idempotent"]
        assert typed(before) == typed(client.portal.call(read))


@pytest.mark.parametrize("position", ["first", "last"])
def test_summary_ingestion_keeps_revision_membership(tmp_path, position):
    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        accounting, owner = import_fixture(client)
        value = projection(accounting)
        value["accounting_revision_ids"].insert(
            0 if position == "first" else len(value["accounting_revision_ids"]),
            "unaccepted-revision",
        )
        with pytest.raises(
            PublicationError, match="projection_unaccepted_accounting_revision"
        ):
            client.portal.call(
                planning_store.import_projection,
                client.app.state.runtime,
                owner,
                seal(value),
            )


@pytest.mark.parametrize("surface", ["native", "member"])
def test_summary_authority_revoked_after_read(tmp_path, monkeypatch, surface):
    with TestClient(create_application(enabled_settings(tmp_path))) as client:
        grant = _grant_cos(client, "summary-revocation")
        accounting, owner = import_fixture(client)
        value = projection(accounting)
        runtime = client.app.state.runtime
        client.portal.call(planning_store.import_projection, runtime, owner, value)
        original = runtime.projections.read

        async def read_then_revoke(name, callback):
            result = await original(name, callback)
            if name == "money":
                if surface == "native":
                    await revoke(runtime, grant)
                else:
                    runtime.settings = replace(runtime.settings, member_identities={})
            return result

        monkeypatch.setattr(runtime.projections, "read", read_then_revoke)
        common = {
            "book_id": value["book_id"],
            "projection_id": value["projection_id"],
            "context_id": value["contexts"][0]["context_id"],
            "report_id": "planning_projection",
            "horizon_days": 30,
        }
        response = (
            _post(
                client,
                "query",
                "summary-revoked",
                publication_id=accounting["publication_id"],
                **common,
            )
            if surface == "native"
            else client.get(
                "/api/member/v1/finance/planning", headers=DEMO_HEADERS, params=common
            )
        )
        assert response.status_code == (403 if surface == "native" else 401)
        assert '"summary":' not in response.text


def test_summary_native_member_delivery_contract(tmp_path):
    existing.test_native_and_member_cards_match_and_new_projection_invalidates_receipt(
        tmp_path
    )


@pytest.mark.parametrize(
    "fault", ["disabled", "wrong_owner", "wrong_hash", "conflicting_retry"]
)
def test_summary_import_boundary(tmp_path, fault):
    existing.test_projection_import_refuses_unauthorized_or_conflicting_boundary(
        tmp_path, fault
    )


def test_summary_runtime_bounds_and_native_versions(stored):
    conn, _, _, _ = stored
    assert resource.getrlimit(resource.RLIMIT_AS) == (1024**3, 1024**3)
    assert resource.getrlimit(resource.RLIMIT_CPU) == (60, 60)
    budget = watchdog.mode_budget("summary-metadata")
    assert (
        budget["work"] == 120 and budget["cleanup"] == 5 and budget["absolute"] == 125
    )
    assert budget["limits"]["whole_tree_cpu_seconds"] == 60
    assert budget["limits"]["whole_tree_rss_bytes"] == 1024**3
    assert budget["limits"]["owned_processes"] == 4
    assert budget["limits"]["scratch_bytes"] == 512 * 1024**2
    assert watchdog.mode_budget("staged-bounded")["work"] == 330
    assert (
        watchdog.mode_budget("staged-bounded")["limits"]["whole_tree_cpu_seconds"]
        == 240
    )
    record(
        "runtime",
        python=sys.version,
        sqlite=conn.execute("SELECT sqlite_version()").fetchone()[0],
        sqlcipher=conn.execute("PRAGMA cipher_version").fetchone()[0],
        int_max_str_digits=sys.get_int_max_str_digits(),
        bounds=budget,
        resource_qualified=False,
        production_qualified=False,
    )
