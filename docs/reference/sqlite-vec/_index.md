# sqlite-vec documentation

**Purpose in this build:** `sqlite-vec` is the vector search extension loaded into the projection SQLite databases. L3's `vector_search` projection uses it for semantic retrieval over artifacts, messages, and notes.

**Source:** https://github.com/asg017/sqlite-vec
**Fetched:** 2026-04-22
**License:** Apache-2.0 OR MIT (dual-licensed)

## Files mirrored

- `README.md` — Quickstart, install per-platform, Python/Node bindings.
- `ARCHITECTURE.md` — How the extension is built, how `vec0` virtual tables work.
- `site/` — MkDocs source for the full docs site (api-reference, guides, getting-started, features, using, compiling, versioning).

## Key references for build

- `site/api-reference.md` — All SQL functions (`vec_f32`, `vec_distance_l2`, `vec0` virtual table options, etc.).
- `site/features/` — Feature-by-feature breakdown (ANN indexing, quantization, filtered search).
- `site/using/python.md` — Python binding usage (this is what aiosqlite calls into).

## Known gaps

None. The repo's `site/` is the full published docs.
