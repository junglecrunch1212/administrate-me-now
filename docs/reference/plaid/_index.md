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

## How to use for build questions

- "What fields does `/transactions/sync` return?" → grep `openapi.yaml` for `/transactions/sync` and follow the referenced schemas.
- "How do I call `/link/token/create` from Python?" → grep `python-sdk-docstrings.md` for `link_token_create` to get the method name, then `python-sdk-plaid_api.py` for the exact signature.
- "What webhooks does Plaid send?" → search `openapi.yaml` for `webhook_type` to get the enum of event types.

## Known gaps

Plaid's narrative documentation at https://plaid.com/docs/ is NOT mirrored (host not on the sandbox allowlist). For the build this affects:

- **Link update mode flow** (https://plaid.com/docs/link/update-mode/) — UX patterns for re-auth after item goes into `ITEM_LOGIN_REQUIRED` state.
- **Errors taxonomy** (https://plaid.com/docs/errors/) — narrative grouping of error codes.
- **Institution status narrative** (https://plaid.com/docs/api/institutions/#institutionsget_by_idstatus) — how to interpret the `status` object.

See `../_gaps.md` for remediation options (widen allowlist to `plaid.com` or defer — narrative guidance is LOW priority since the OpenAPI spec already documents every status/error code).
