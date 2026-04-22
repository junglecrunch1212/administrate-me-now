# Google Gmail API documentation

**Purpose in this build:** The Gmail adapter (standalone Python, not an OpenClaw channel) uses the Gmail v1 REST API. This TypeScript reference file is the most complete single-file source of endpoint definitions, request/response types, and JSDoc descriptions — far more readable than Google's Discovery JSON.

**Source:** https://github.com/googleapis/google-api-nodejs-client/blob/main/src/apis/gmail/v1.ts
**Fetched:** 2026-04-22
**License:** Apache-2.0 (googleapis/google-api-nodejs-client/LICENSE)

## Files mirrored

- `gmail-v1.ts` — Full TypeScript definition of every Gmail endpoint (14 K lines). JSDoc on each method describes params, auth scopes, and return types. Python callers map method names 1:1 via `google-api-python-client`.
- `gmail-README.md` — Top-level per-API README (install + minimal example).

## How to use for build questions

- "What scopes does `messages.list` need?" → grep `gmail-v1.ts` for `messages.list` and read the adjacent `@memberof` / `@param` JSDoc.
- "What fields are on a `Message`?" → search for `interface Schema$Message` in `gmail-v1.ts`.

## Python mapping

Python uses `google-api-python-client`. Method naming converts to snake_case: TS `gmail.users.messages.list` ↔ Python `service.users().messages().list()`. Argument names match.

## Known gaps

None for API-truth (the TS file is authoritative). Google's narrative docs at https://developers.google.com/gmail/api/ are NOT mirrored (host not on allowlist) but contain only supplementary guides (e.g., threading model, rate limit narrative). Not blocking.
