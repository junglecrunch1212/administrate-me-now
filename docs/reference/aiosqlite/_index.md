# aiosqlite documentation

**Purpose in this build:** aiosqlite is the async wrapper around stdlib sqlite3 that the event log and projection layer use (L2/L3). Every connection that executes async queries flows through aiosqlite.

**Source:** https://github.com/omnilib/aiosqlite
**Fetched:** 2026-04-22
**License:** MIT (omnilib/aiosqlite/LICENSE)

## Files mirrored

- `index.rst` — Documentation entry point.
- `api.rst` — Sphinx autodoc directives (pointer to `core.py`).
- `changelog.rst` — Version history.
- `contributing.rst` — Contribution guidelines.
- `core.py` — The main source file. Contains autodoc-ready docstrings that are the source of truth for the API.
- `README.rst` — Repo README with practical usage examples.

## How to use for build questions

- Connection / cursor API: read `core.py` docstrings (every public method documented).
- Context-manager behavior, loop-affinity: `core.py` + `index.rst`.

## Known gaps

None. The package is small and fully self-documented.
