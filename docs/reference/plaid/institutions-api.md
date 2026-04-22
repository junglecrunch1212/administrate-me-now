---
source_url: https://plaid.com/docs/api/institutions/
fetched: 2026-04-22
page_title: Institutions endpoints
---

# Institutions endpoints

Fetch data about supported institutions.

## Institution coverage

For a user-friendly overview of which institutions Plaid supports, and the product coverage at each institution, see the [US and Canada Coverage Explorer](https://plaid.com/docs/institutions/) or [European Coverage Explorer](https://plaid.com/docs/institutions/europe/).

The [status dashboard](https://dashboard.plaid.com/activity/status) also provides a browsable view of institutions and supported products, with a focus on reporting institution health and downtimes.

For more detailed institution information, or to access this data programmatically, use the API endpoints described on this page.

### Supported countries

For a list of which products are supported for each country, see supported products by country or the docs for the specific product you are interested in.

By default, customers in the United States and Canada receive access to institutions in all countries in Sandbox, and to United States and Canada in Production. To gain access to additional countries in Production, [file a product access Support ticket](https://dashboard.plaid.com/support/new/product-and-development/product-troubleshooting/request-product-access).

## Endpoints

| Endpoint | Description |
| --- | --- |
| `/institutions/get` | Get a list of all supported institutions meeting specified criteria. |
| `/institutions/get_by_id` | Get details about a specific institution. |
| `/institutions/search` | Look up an institution by name. |

> **Note:** The interface for these endpoints changed in API version `2020-09-14`. If you are using an older API version, see API versioning.

## `/institutions/get`

Get details of all supported institutions.

Returns a JSON response containing details on all financial institutions currently supported by Plaid. Because Plaid supports thousands of institutions, results are paginated.

If there is no overlap between an institution's enabled products and a client's enabled products, then the institution will be filtered out from the response. As a result, the number of institutions returned may not match the count specified in the call.

### Request fields

| Field | Type | Description |
| --- | --- | --- |
| `client_id` | string | Your Plaid API `client_id`. |
| `secret` | string | Your Plaid API `secret`. |
| `count` | integer (required) | Total number of institutions to return. Range: 1–500. |
| `offset` | integer (required) | Number of institutions to skip. Minimum: 0. |
| `country_codes` | [string] (required) | ISO-3166-1 alpha-2 country codes. |
| `options` | object | Optional filter object for products, routing numbers, OAuth, and metadata. |
| `options.products` | [string] | Filter based on which products are supported by the institution. Values: `assets`, `auth`, `balance`, `employment`, `identity`, `cra_base_report`, `cra_income_insights`, `cra_cashflow_insights`, `cra_lend_score`, `cra_network_insights`, `cra_partner_insights`, `income_verification`, `identity_verification`, `investments`, `liabilities`, `payment_initiation`, `standing_orders`, `transactions`. |
| `options.routing_numbers` | [string] | Specify an array of routing numbers to filter institutions. The response will only return institutions that match all of the routing numbers in the array. |
| `options.oauth` | boolean | Limit results to institutions with or without OAuth login flows. |
| `options.include_optional_metadata` | boolean | When `true`, return the institution's homepage URL, logo, and primary brand color. Default: `false`. |
| `options.include_auth_metadata` | boolean | When `true`, returns metadata related to the Auth product indicating which auth methods are supported. Default: `false`. |
| `options.include_payment_initiation_metadata` | boolean | When `true`, returns metadata related to the Payment Initiation product indicating which payment configurations are supported. Default: `false`. |

### Example request

```json
{
  "count": 10,
  "offset": 0,
  "country_codes": ["US"]
}
```

### Example response

```json
{
  "institutions": [
    {
      "country_codes": ["US"],
      "institution_id": "ins_1",
      "name": "Bank of America",
      "products": [
        "assets",
        "auth",
        "balance",
        "transactions",
        "identity",
        "liabilities"
      ],
      "routing_numbers": [
        "011000138",
        "011200365",
        "011400495"
      ],
      "dtc_numbers": [
        "2236",
        "0955",
        "1367"
      ],
      "oauth": false
    }
  ],
  "request_id": "tbFyCEqkU774ZGG",
  "total": 11384
}
```

## `/institutions/get_by_id`

Get details of an institution.

Returns a JSON response containing details on a specified financial institution currently supported by Plaid.

> **Versioning note:** API versions `2019-05-29` and earlier allow use of the `public_key` parameter instead of the `client_id` and `secret` to authenticate to this endpoint. The `public_key` has been deprecated; all customers are encouraged to use `client_id` and `secret` instead.

### Request fields

| Field | Type | Description |
| --- | --- | --- |
| `client_id` | string | Your Plaid API `client_id`. May be provided in the `PLAID-CLIENT-ID` header or request body. |
| `secret` | string | Your Plaid API `secret`. May be provided in the `PLAID-SECRET` header or request body. |
| `institution_id` | string (required) | The ID of the institution to get details about. Min length: 1. |
| `country_codes` | [string] (required) | Specify which country or countries to include institutions from, using the ISO-3166-1 alpha-2 country code standard. |
| `options` | object | Specifies optional parameters. Must not be `null` if provided. |
| `options.include_optional_metadata` | boolean | When `true`, return an institution's logo, brand color, and URL. Default: `false`. |
| `options.include_status` | boolean | If `true`, the response will include status information about the institution. Default: `false`. |
| `options.include_auth_metadata` | boolean | When `true`, returns metadata related to the Auth product indicating which auth methods are supported. Default: `false`. |
| `options.include_payment_initiation_metadata` | boolean | When `true`, returns metadata related to the Payment Initiation product indicating which payment configurations are supported. Default: `false`. |

### Example request

```json
{
  "institution_id": "ins_109512",
  "country_codes": ["US"]
}
```

### Example response

```json
{
  "institution": {
    "country_codes": ["US"],
    "institution_id": "ins_109512",
    "name": "Houndstooth Bank",
    "products": [
      "auth",
      "balance",
      "identity",
      "transactions"
    ],
    "routing_numbers": [
      "011000138",
      "011200365",
      "011400495"
    ],
    "dtc_numbers": [
      "2236",
      "0955",
      "1367"
    ],
    "oauth": false,
    "status": {
      "item_logins": {
        "status": "HEALTHY",
        "last_status_change": "2019-02-15T15:53:00Z",
        "breakdown": {
          "success": 0.9,
          "error_plaid": 0.01,
          "error_institution": 0.09
        }
      },
      "transactions_updates": {
        "status": "HEALTHY",
        "last_status_change": "2019-02-12T08:22:00Z",
        "breakdown": {
          "success": 0.95,
          "error_plaid": 0.02,
          "error_institution": 0.03,
          "refresh_interval": "NORMAL"
        }
      },
      "auth": {
        "status": "HEALTHY",
        "last_status_change": "2019-02-15T15:53:00Z",
        "breakdown": {
          "success": 0.91,
          "error_plaid": 0.01,
          "error_institution": 0.08
        }
      },
      "identity": {
        "status": "DEGRADED",
        "last_status_change": "2019-02-15T15:50:00Z",
        "breakdown": {
          "success": 0.42,
          "error_plaid": 0.08,
          "error_institution": 0.5
        }
      },
      "investments_updates": {
        "status": "HEALTHY",
        "last_status_change": "2019-02-12T08:22:00Z",
        "breakdown": {
          "success": 0.95,
          "error_plaid": 0.02,
          "error_institution": 0.03,
          "refresh_interval": "NORMAL"
        }
      },
      "liabilities_updates": {
        "status": "HEALTHY",
        "last_status_change": "2019-02-12T08:22:00Z",
        "breakdown": {
          "success": 0.95,
          "error_plaid": 0.02,
          "error_institution": 0.03,
          "refresh_interval": "NORMAL"
        }
      },
      "liabilities": {
        "status": "HEALTHY",
        "last_status_change": "2019-02-15T15:53:00Z",
        "breakdown": {
          "success": 0.89,
          "error_plaid": 0.02,
          "error_institution": 0.09
        }
      },
      "investments": {
        "status": "HEALTHY",
        "last_status_change": "2019-02-15T15:53:00Z",
        "breakdown": {
          "success": 0.89,
          "error_plaid": 0.02,
          "error_institution": 0.09
        }
      }
    },
    "primary_color": "#004966",
    "url": "https://plaid.com",
    "logo": null
  },
  "request_id": "m8MDnv9okwxFNBV"
}
```

### Status object structure

The `status` object contains the following sub-fields, each representing the health of a specific request type. For each sub-field, `success`, `error_plaid`, and `error_institution` sum to `1`.

- **`item_logins`** — Representation of login attempt health.
  - `status` (deprecated string): `HEALTHY`, `DEGRADED`, or `DOWN`.
  - `last_status_change` (string): ISO 8601 formatted timestamp.
  - `breakdown` (object): `success`, `error_plaid`, `error_institution`.
- **`transactions_updates`** — Same fields as `item_logins`, plus `breakdown.refresh_interval` which is one of `NORMAL`, `DELAYED`, or `STOPPED`.
- **`auth`** — Same structure as `item_logins`.
- **`identity`** — Same structure as `item_logins`.
- **`investments_updates`** — Same structure as `transactions_updates` (includes `refresh_interval`).
- **`liabilities_updates`** — Same structure as `transactions_updates` (includes `refresh_interval`).
- **`liabilities`** — Same structure as `item_logins`.
- **`investments`** — Same structure as `item_logins`.

## `/institutions/search`

Returns a JSON response containing details for institutions that match the query parameters, up to a maximum of ten institutions per query.

> **Versioning note:** API versions `2019-05-29` and earlier allow use of the `public_key` parameter instead of the `client_id` and `secret` parameters to authenticate to this endpoint. The `public_key` parameter has since been deprecated; all customers are encouraged to use `client_id` and `secret` instead.

### Request fields

| Field | Type | Description |
| --- | --- | --- |
| `client_id` | string | Your Plaid API `client_id`. May be provided in the `PLAID-CLIENT-ID` header or request body. |
| `secret` | string | Your Plaid API `secret`. May be provided in the `PLAID-SECRET` header or request body. |
| `query` | string (required) | The search query. Institutions with names matching the query are returned. Min length: 1. |
| `products` | [string] | Filter institutions by supported products. Min items: 1. Values: `assets`, `auth`, `balance`, `employment`, `identity`, `income_verification`, `investments`, `liabilities`, `identity_verification`, `payment_initiation`, `standing_orders`, `statements`, `transactions`. |
| `country_codes` | [string] (required) | ISO-3166-1 alpha-2 country codes. Values: `US`, `GB`, `ES`, `NL`, `FR`, `IE`, `CA`, `DE`, `IT`, `PL`, `DK`, `NO`, `SE`, `EE`, `LT`, `LV`, `PT`, `BE`, `AT`, `FI`. |
| `options.oauth` | boolean | Limit results to institutions with or without OAuth login flows. |
| `options.include_optional_metadata` | boolean | When `true`, return the institution's homepage URL, logo, and primary brand color. |
| `options.include_auth_metadata` | boolean | When `true`, returns metadata on Auth product auth methods supported. Default: `false`. |
| `options.include_payment_initiation_metadata` | boolean | When `true`, returns metadata on Payment Initiation configurations supported. Default: `false`. |
| `options.payment_initiation` | object | Additional filter options for Payment Initiation: `payment_id` (string), `consent_id` (string). |

### Example request

```json
{
  "client_id": "your_client_id",
  "secret": "your_secret",
  "query": "Bank of America",
  "products": ["transactions"],
  "country_codes": ["US"],
  "options": {
    "include_optional_metadata": true,
    "include_auth_metadata": true
  }
}
```

### Example response

```json
{
  "institutions": [
    {
      "institution_id": "ins_1",
      "name": "Bank of America",
      "products": [
        "assets",
        "auth",
        "balance",
        "transactions",
        "identity",
        "liabilities"
      ],
      "country_codes": ["US"],
      "routing_numbers": [
        "011000138",
        "011200365",
        "011400495"
      ],
      "dtc_numbers": [
        "2236",
        "0955",
        "1367"
      ],
      "oauth": false,
      "url": "https://example.com",
      "primary_color": "#004966",
      "logo": null
    }
  ],
  "request_id": "tbFyCEqkU774ZGG"
}
```

The `/institutions/search` response does not include a `status` object in its standard response. Status information is only available through the `/institutions/get_by_id` endpoint when `options.include_status` is set to `true`.

## Webhooks

This page's underlying API reference also includes an Institutions webhook section (not reproduced here — see the source URL). The Institutions endpoints do not emit Item-specific webhooks; institution status is polled through `/institutions/get_by_id` with `options.include_status`.
