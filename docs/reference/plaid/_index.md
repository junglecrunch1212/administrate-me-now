# Plaid documentation

**Purpose in this build:** Plaid is the primary financial data adapter (BUILD.md "PLAID — DETAILED SPEC"). The Financial product pack in Phase B uses Plaid's Link, Transactions, Balance, Identity, Investments, and Liabilities endpoints, plus webhook callbacks. The OpenAPI spec below is the API-truth; the SDK docstrings map Python method names to HTTP endpoints.

**Sources:**
- OpenAPI spec: https://github.com/plaid/plaid-openapi (no LICENSE file upstream — Plaid OSS policy, treated as MIT consistent with plaid-python).
- Python SDK: https://github.com/plaid/plaid-python (MIT, (c) 2014-2021 Plaid Inc.).

**Fetched:** 2026-04-22

## Files mirrored

- `openapi.yaml` — Full OpenAPI 3.0.0 spec covering every Plaid endpoint, request/response shape, product coverage, error codes, and webhook event types. Authoritative reference.
- `openapi-README.md` — Upstream repo README explaining how Plaid uses the spec to generate client libraries.
- `openapi-CHANGELOG.md` — Spec changelog; useful when checking which API changes landed in a given Plaid release.
- `python-sdk-plaid_api.py` — Full auto-generated `PlaidApi` class. Verbatim. Use for looking up Python call signatures.
- `python-sdk-docstrings.md` — AST-extracted docstrings (class + public method). Readable summary of what each method does and its arguments.
- `python-sdk-README.md` — plaid-python README with install, auth, and minimal client-construction examples.
- `link-update-mode.md` — Link update mode narrative: re-authenticating an Item after `ITEM_LOGIN_REQUIRED`, `PENDING_EXPIRATION`, or consent expiry; creating a `link_token` with `access_token` in update mode; partner (OAuth) flows. Supplements the `/link/token/create` endpoint in openapi.yaml.
- `errors-taxonomy.md` — Errors narrative: most-common-errors table, full category breakdown (Item, Institution, API, Assets, Payment, and ~15 more), every `error_code` listed with HTTP status and description, full error-object schema.
- `institutions-api.md` — Institutions API narrative: `/institutions/get`, `/institutions/get_by_id`, `/institutions/search`. Full `status` object structure (`item_logins`, `transactions_updates`, `auth`, `identity`, `investments_updates`, `liabilities_updates`, `liabilities`, `investments`) with `breakdown` sub-fields.

## How to use for build questions

- "What fields does `/transactions/sync` return?" → grep `openapi.yaml` for `/transactions/sync` and follow the referenced schemas.
- "How do I call `/link/token/create` from Python?" → grep `python-sdk-docstrings.md` for `link_token_create` to get the method name, then `python-sdk-plaid_api.py` for the exact signature.
- "What webhooks does Plaid send?" → search `openapi.yaml` for `webhook_type` to get the enum of event types.

## Known gaps

None remaining. The three previously-documented narrative gaps (Link update mode, errors taxonomy, institution status narrative) have been filled by manual Chrome clip via Cowork; see `docs/reference/_manifests/2026-04-22-cowork-clips.md`. The Plaid section is now fully mirrored.
