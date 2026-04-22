---
source_url: https://plaid.com/docs/errors/
fetched: 2026-04-22
page_title: Errors
---

# Errors

A comprehensive breakdown of all Plaid error codes.

## Most common errors

The following are the most common errors that may occur in response to an API call even if your implementation is correct. This list of common errors excludes Item errors that occur only during the Link flow (typically due to bad data entry by the end user), such as `INVALID_CREDENTIALS`, and errors that can occur only due to sending invalid input, such as `INVALID_FIELD`. It is recommended that your integration should, at minimum, handle each of the errors below that are applicable to your product and/or integration mode.

In the table below, "institution-based products" refers to any product or integration that connects to a bank or other financial institution (i.e., most Plaid products); it excludes products such as Identity Verification, Monitor, Enrich, and Document Income that do not involve making a connection to a financial institution.

| Error | Applies to | Summary |
| --- | --- | --- |
| `ITEM_LOGIN_REQUIRED` | All institution-based products | Item has expired credentials or consent. |
| `PRODUCT_NOT_READY` | Signal, Assets, Income, Check, Auth, `/transactions/get` | Plaid hasn't finished obtaining the data needed to fulfill your request. |
| `PRODUCTS_NOT_SUPPORTED` | All institution-based products | The product endpoint isn't compatible with this Item. |
| `TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION` | `/transactions/sync` | An update was received during Transactions pagination. |
| `NO_ACCOUNTS` | All institution-based products | Couldn't find any open accounts. |
| `NO_AUTH_ACCOUNTS` | Auth | Couldn't find any debitable checking, savings, or cash management accounts. |
| `NO_LIABILITY_ACCOUNTS` | Liabilities | Couldn't find any credit accounts. |
| `NO_INVESTMENT_ACCOUNTS` | Investments | Couldn't find any investment accounts. |
| `ACCESS_NOT_GRANTED` | All institution-based products | The end user didn't grant an OAuth permission required for your request. |
| `ADDITIONAL_CONSENT_REQUIRED` | Integrations in the US or Canada that add products to Items after Link | The end user didn't grant a data scope required for your request. |
| `INSTITUTION_NOT_RESPONDING` | All institution-based products | Temporary financial institution connectivity outage. |
| `INSTITUTION_DOWN` | All institution-based products | Temporary financial institution connectivity outage. |
| `RATE_LIMIT_EXCEEDED` | Applications that batch-process Plaid API calls or have heavy traffic spikes | Too many requests made too quickly, or API usage caps hit. |
| `INTERNAL_SERVER_ERROR` | All products | Internal error or financial institution error not otherwise specified. |

## Errors overview

### Item errors

Occur when an Item may be invalid or not supported on Plaid's platform.

- `ACCESS_NOT_GRANTED`
- `INSTANT_MATCH_FAILED`
- `INSUFFICIENT_CREDENTIALS`
- `INVALID_CREDENTIALS`
- `INVALID_MFA`
- `INVALID_OTP`
- `INVALID_PHONE_NUMBER`
- `INVALID_SEND_METHOD`
- `INVALID_UPDATED_USERNAME`
- `ITEM_CONCURRENTLY_DELETED`
- `ITEM_LOCKED`
- `ITEM_LOGIN_REQUIRED`
- `ITEM_NOT_FOUND`
- `ITEM_NOT_SUPPORTED`
- `MANUAL_VERIFICATION_REQUIRED`
- `MFA_NOT_SUPPORTED`
- `NO_ACCOUNTS`
- `NO_AUTH_ACCOUNTS` (also: `no-depository-accounts`)
- `NO_INVESTMENT_ACCOUNTS`
- `NO_INVESTMENT_AUTH_ACCOUNTS`
- `NO_LIABILITY_ACCOUNTS`
- `PASSWORD_RESET_REQUIRED`
- `PRODUCT_NOT_ENABLED`
- `PRODUCT_NOT_READY`
- `PRODUCTS_NOT_SUPPORTED`
- `USER_INPUT_TIMEOUT`
- `USER_SETUP_REQUIRED`

### Institution errors

Occur when there are errors for the requested financial institution.

- `INSTITUTION_DOWN`
- `INSTITUTION_NO_LONGER_SUPPORTED`
- `INSTITUTION_NOT_AVAILABLE`
- `INSTITUTION_NOT_ENABLED_IN_ENVIRONMENT`
- `INSTITUTION_NOT_RESPONDING`
- `INSTITUTION_REGISTRATION_REQUIRED`
- `UNAUTHORIZED_INSTITUTION`
- `UNSUPPORTED_RESPONSE`

### API errors

Occur during planned maintenance and in response to API errors.

- `INTERNAL_SERVER_ERROR` (also: `plaid-internal-error`)
- `PLANNED_MAINTENANCE`

### Assets errors

Occur for errors related to Asset endpoints.

- `PRODUCT_NOT_ENABLED`
- `DATA_UNAVAILABLE`
- `PRODUCT_NOT_READY`
- `ASSET_REPORT_GENERATION_FAILED`
- `INVALID_PARENT`
- `INSIGHTS_NOT_ENABLED`
- `INSIGHTS_PREVIOUSLY_NOT_ENABLED`
- `DATA_QUALITY_CHECK_FAILED`

### Payment errors

Occur for errors related to Payment Initiation endpoints.

- `PAYMENT_BLOCKED`
- `PAYMENT_CANCELLED`
- `PAYMENT_INSUFFICIENT_FUNDS`
- `PAYMENT_INVALID_RECIPIENT`
- `PAYMENT_INVALID_REFERENCE`
- `PAYMENT_INVALID_SCHEDULE`
- `PAYMENT_REJECTED`
- `PAYMENT_SCHEME_NOT_SUPPORTED`
- `PAYMENT_CONSENT_INVALID_CONSTRAINTS`
- `PAYMENT_CONSENT_CANCELLED`

### Virtual Account errors

Occur for errors related to Virtual Account endpoints.

- `TRANSACTION_INSUFFICIENT_FUNDS`
- `TRANSACTION_AMOUNT_EXCEEDED`
- `TRANSACTION_ON_SAME_ACCOUNT`
- `TRANSACTION_CURRENCY_MISMATCH`
- `TRANSACTION_IBAN_INVALID`
- `TRANSACTION_BACS_INVALID`
- `TRANSACTION_FAST_PAY_DISABLED`
- `TRANSACTION_EXECUTION_FAILED`
- `NONIDENTICAL_REQUEST`
- `REQUEST_CONFLICT`

### Transactions errors

Occur for errors related to Transactions endpoints.

- `TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION`

### Transfer errors

Occur for errors related to Transfer endpoints.

- `TRANSFER_NETWORK_LIMIT_EXCEEDED`
- `TRANSFER_ACCOUNT_BLOCKED`
- `TRANSFER_NOT_CANCELLABLE`
- `TRANSFER_UNSUPPORTED_ACCOUNT_TYPE`
- `TRANSFER_FORBIDDEN_ACH_CLASS`
- `TRANSFER_UI_UNAUTHORIZED`
- `TRANSFER_ORIGINATOR_NOT_FOUND`
- `INCOMPLETE_CUSTOMER_ONBOARDING`
- `UNAUTHORIZED_ACCESS`

### Signal errors

Occur for errors related to Signal endpoints.

- `ADDENDUM_NOT_SIGNED`
- `CLIENT_TRANSACTION_ID_ALREADY_IN_USE`
- `INVALID_CONFIGURATION_STATE`
- `NOT_ENABLED_FOR_SIGNAL_TRANSACTION_SCORE_RULESETS`
- `RULESET_NOT_FOUND`
- `SIGNAL_TRANSACTION_NOT_INITIATED`

### Income errors

Occur for errors related to Income endpoints.

- `INCOME_VERIFICATION_DOCUMENT_NOT_FOUND`
- `INCOME_VERIFICATION_FAILED`
- `INCOME_VERIFICATION_NOT_FOUND`
- `INCOME_VERIFICATION_UPLOAD_ERROR`
- `PRODUCT_NOT_ENABLED`
- `PRODUCT_NOT_READY`
- `VERIFICATION_STATUS_PENDING_APPROVAL`
- `EMPLOYMENT_NOT_FOUND`

### Sandbox errors

Occur when invalid parameters are supplied in the Sandbox environment.

- `SANDBOX_PRODUCT_NOT_ENABLED`
- `SANDBOX_WEBHOOK_INVALID`
- `SANDBOX_TRANSFER_EVENT_TRANSITION_INVALID`

### Invalid Request errors

Occur when a request is malformed and cannot be processed.

- `MISSING_FIELDS`
- `UNKNOWN_FIELDS`
- `INVALID_FIELD`
- `INVALID_CONFIGURATION`
- `INCOMPATIBLE_API_VERSION`
- `INVALID_BODY`
- `INVALID_HEADERS`
- `NOT_FOUND`
- `NO_LONGER_AVAILABLE`
- `SANDBOX_ONLY`
- `INVALID_ACCOUNT_NUMBER`

### Invalid Input errors

Occur when all fields are provided, but the values provided are incorrect in some way.

- `ADDITIONAL_CONSENT_REQUIRED`
- `DIRECT_INTEGRATION_NOT_ENABLED`
- `INCORRECT_DEPOSIT_VERIFICATION`
- `INVALID_ACCESS_TOKEN`
- `INVALID_ACCOUNT_ID`
- `INVALID_API_KEYS`
- `INVALID_AUDIT_COPY_TOKEN`
- `INVALID_INSTITUTION`
- `INVALID_LINK_CUSTOMIZATION`
- `INVALID_PROCESSOR_TOKEN`
- `INVALID_PRODUCT`
- `INVALID_PUBLIC_TOKEN`
- `INVALID_LINK_TOKEN`
- `INVALID_STRIPE_ACCOUNT`
- `INVALID_USER_ID`
- `INVALID_USER_IDENTITY_DATA`
- `INVALID_USER_TOKEN`
- `INVALID_WEBHOOK_VERIFICATION_KEY_ID`
- `PROFILE_ACCESS_FORBIDDEN`
- `PROFILE_AUTHENTICATION_FAILED`
- `UNAUTHORIZED_ENVIRONMENT`
- `UNAUTHORIZED_ROUTE_ACCESS`
- `USER_PERMISSION_REVOKED`
- `TOO_MANY_VERIFICATION_ATTEMPTS`

### Invalid Result errors

Occur when a request is valid, but the output would be unusable for any supported flow.

- `PLAID_DIRECT_ITEM_IMPORT_RETURNED_INVALID_MFA`

### Rate Limit Exceeded errors

Occur when an excessive number of requests are made in a short period of time.

- `ACCOUNTS_LIMIT`
- `ACCOUNTS_BALANCE_GET_LIMIT`
- `AUTH_LIMIT`
- `BALANCE_LIMIT`
- `CREDITS_EXHAUSTED`
- `IDENTITY_LIMIT`
- `INSTITUTIONS_GET_LIMIT`
- `INSTITUTIONS_GET_BY_ID_LIMIT`
- `INSTITUTION_RATE_LIMIT`
- `INVESTMENT_HOLDINGS_GET_LIMIT`
- `INVESTMENT_TRANSACTIONS_LIMIT`
- `ITEM_GET_LIMIT`
- `RATE_LIMIT`
- `TRANSACTIONS_LIMIT`
- `TRANSACTIONS_REFRESH_LIMIT`
- `TRANSACTIONS_SYNC_LIMIT`
- `TRIAL_CONNECTION_LIMIT`

### ReCAPTCHA errors

Occur when a ReCAPTCHA challenge has been presented or failed during the link process.

- `RECAPTCHA_REQUIRED`
- `RECAPTCHA_BAD`

### OAuth errors

Occur when there is an error in OAuth authentication.

- `INCORRECT_OAUTH_NONCE`
- `INCORRECT_LINK_TOKEN`
- `OAUTH_STATE_ID_ALREADY_PROCESSED`
- `OAUTH_STATE_ID_NOT_FOUND`

### Micro-deposits errors

Occur when there is an error with micro-deposits.

- `BANK_TRANSFER_ACCOUNT_BLOCKED`

### Partner errors

Occur when there is an error with creating or managing end customers.

- `CUSTOMER_NOT_FOUND`
- `FLOWDOWN_NOT_COMPLETE`
- `QUESTIONNAIRE_NOT_COMPLETE`
- `CUSTOMER_NOT_READY_FOR_ENABLEMENT`
- `CUSTOMER_ALREADY_ENABLED`
- `CUSTOMER_ALREADY_CREATED`
- `LOGO_REQUIRED`
- `INVALID_LOGO`
- `CONTACT_REQUIRED`
- `ASSETS_UNDER_MANAGEMENT_REQUIRED`
- `CUSTOMER_REMOVAL_NOT_ALLOWED`
- `OAUTH_REGISTRATION_ERROR`

### Check Report errors

Occur when there is an error with creating or retrieving a Check Report.

- `CONSUMER_REPORT_EXPIRED`
- `DATA_UNAVAILABLE`
- `PRODUCT_NOT_READY`
- `INSTITUTION_TRANSACTION_HISTORY_NOT_SUPPORTED`
- `INSUFFICIENT_TRANSACTION_DATA`
- `NO_ACCOUNTS`
- `NETWORK_CONSENT_REQUIRED`
- `DATA_QUALITY_CHECK_FAILED`

### User errors

Occur when there is an error with creating or managing a user.

- `USER_NOT_FOUND`

## Error schema

Errors are identified by `error_code` and categorized by `error_type`. Use these in preference to HTTP status codes to identify and handle specific errors. HTTP status codes are set and provide the broadest categorization of errors: 4xx codes are for developer- or user-related errors, and 5xx codes are for Plaid-related errors, and the status will be 2xx in non-error cases. An Item with a non-null error object will only be part of an API response when calling `/item/get` to view Item status. Otherwise, error fields will be `null` if no error has occurred; if an error has occurred, an error code will be returned instead.

### Properties

**`error_type`** (string) — A broad categorization of the error. Safe for programmatic use.

Possible values: `INVALID_REQUEST`, `INVALID_RESULT`, `INVALID_INPUT`, `INSTITUTION_ERROR`, `RATE_LIMIT_EXCEEDED`, `API_ERROR`, `ITEM_ERROR`, `ASSET_REPORT_ERROR`, `RECAPTCHA_ERROR`, `OAUTH_ERROR`, `PAYMENT_ERROR`, `BANK_TRANSFER_ERROR`, `INCOME_VERIFICATION_ERROR`, `MICRODEPOSITS_ERROR`, `SANDBOX_ERROR`, `PARTNER_ERROR`, `SIGNAL_ERROR`, `TRANSACTIONS_ERROR`, `TRANSACTION_ERROR`, `TRANSFER_ERROR`, `CHECK_REPORT_ERROR`, `CONSUMER_REPORT_ERROR`, `USER_ERROR`.

**`error_code`** (string) — The particular error code. Safe for programmatic use.

**`error_code_reason`** (string) — The specific reason for the error code. Currently, reasons are only supported OAuth-based item errors; `null` will be returned otherwise. Safe for programmatic use.

Possible values:

- `OAUTH_INVALID_TOKEN`: The user's OAuth connection to this institution has been invalidated.
- `OAUTH_CONSENT_EXPIRED`: The user's access consent for this OAuth connection to this institution has expired.
- `OAUTH_USER_REVOKED`: The user's OAuth connection to this institution is invalid because the user revoked their connection.

**`error_message`** (string) — A developer-friendly representation of the error code. This may change over time and is not safe for programmatic use.

**`display_message`** (string) — A user-friendly representation of the error code. `null` if the error is not related to user action. This may change over time and is not safe for programmatic use.

**`request_id`** (string) — A unique ID identifying the request, to be used for troubleshooting purposes. This field will be omitted in errors provided by webhooks.

**`causes`** (array) — In this product, a request can pertain to more than one Item. If an error is returned for such a request, `causes` will return an array of errors containing a breakdown of these errors on the individual Item level, if any can be identified. `causes` will be provided for the `error_type` `ASSET_REPORT_ERROR` or `CHECK_REPORT_ERROR`. `causes` will also not be populated inside an error nested within a warning object.

**`status`** (integer) — The HTTP status code associated with the error. This will only be returned in the response body when the error information is provided via a webhook.

**`documentation_url`** (string) — The URL of a Plaid documentation page with more information about the error.

**`suggested_action`** (string) — Suggested steps for resolving the error.

**`required_account_subtypes`** (array of string) — A list of the account subtypes that were requested via the `account_filters` parameter in `/link/token/create`. Currently only populated for `NO_ACCOUNTS` errors from Items with `investments_auth` as an enabled product.

**`provided_account_subtypes`** (array of string) — A list of the account subtypes that were extracted but did not match the requested subtypes via the `account_filters` parameter in `/link/token/create`. Currently only populated for `NO_ACCOUNTS` errors from Items with `investments_auth` as an enabled product.
