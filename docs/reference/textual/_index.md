# Textual framework documentation

**Purpose in this build:** Textual is the TUI framework used by the bootstrap wizard (`adminme init`) in Phase B. The wizard is the first thing the operator interacts with on the Mac Mini after cloning the repo, so polish matters. All prompts in `prompts/16-bootstrap-wizard.md` reference this mirror.

**Source:** https://github.com/Textualize/textual (`docs/` subtree, MkDocs source)
**Fetched:** 2026-04-22
**License:** MIT (Textualize/textual/LICENSE)

## Mirror scope

Every `.md` / `.mdx` file under `docs/`, preserving directory structure. Images and code examples omitted.

## Key files for bootstrap wizard

- `getting_started.md` — Basics.
- `guide/app.md` — App lifecycle (what `adminme init` extends).
- `guide/screens.md` — Screen navigation (bootstrap has ~15 screens).
- `guide/reactivity.md` — State management.
- `guide/testing.md` — How to test the wizard.
- `guide/widgets.md`, `guide/input.md`, `guide/layout.md` — Widget/layout primitives.

## Known gaps

None. The MkDocs source is complete.
