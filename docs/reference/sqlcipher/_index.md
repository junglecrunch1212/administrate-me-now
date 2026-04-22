# SQLCipher documentation

**Purpose in this build:** SQLCipher encrypts the event log and projection databases at rest. The master key lives in macOS Keychain (or 1Password). Every SQLite connection this build opens is an SQLCipher-enabled connection and must issue `PRAGMA key = ...` before any query.

**Source:** https://github.com/sqlcipher/sqlcipher
**Fetched:** 2026-04-22
**License:** BSD-style (sqlcipher/LICENSE.md, (c) 2025 ZETETIC LLC)

## Files mirrored

- `README.md` — Repo overview: build instructions, compile-time options, link-time options.
- `CHANGELOG.md` — Version history — check here when upgrading.
- `pragma-source-excerpt.md` — The `sqlcipher_codec_pragma()` function verbatim from `src/sqlcipher.c` (lines 2573–3205). This is the exhaustive, authoritative list of SQLCipher-specific PRAGMAs. Reading the if/else chain in that function gives the exact syntax and semantics for:
  - `key`, `rekey` — set/rotate master key
  - `cipher_migrate`, `cipher_version`, `cipher_provider`
  - `cipher_page_size`, `cipher_kdf_iter`, `cipher_kdf_algorithm`
  - `cipher_hmac_algorithm`, `cipher_salt`, `cipher_plaintext_header_size`
  - `cipher_default_*` counterparts
  - `cipher_integrity_check`, `cipher_export`

## How to use for build questions

- "How do I set the key?" → search `pragma-source-excerpt.md` for `zLeft, "key"`.
- "What's the default KDF iteration count?" → README covers defaults; source excerpt covers how to change.
- "Can I downgrade to older SQLCipher compatibility?" → CHANGELOG + `cipher_migrate` handling in the excerpt.

## Partial coverage / gap

The narrative user guide at https://www.zetetic.net/sqlcipher/sqlcipher-api/ is NOT mirrored (host not on allowlist). The source excerpt is authoritative for PRAGMAs; the narrative guide mainly groups and explains what the PRAGMAs mean. For build purposes this is sufficient.

If a future operator wants the narrative guide, it is a documented gap — see `../_gaps.md`.
