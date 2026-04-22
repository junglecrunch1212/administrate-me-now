---
source_url: https://plaid.com/docs/link/update-mode/
fetched: 2026-04-22
page_title: Updating Items via Link
---

# Updating Items via Link

#### Use update mode to add permissions to Items, or to resolve ITEM_LOGIN_REQUIRED status

![Plaid Link update mode (with account selection enabled): Connect account, enter credentials, select accounts, and see success confirmation screen.](https://plaid.com/assets/img/docs/update-mode.png)

## When to use update mode

Update mode refers to any Link session launched for an Item after that Item's initial creation. Update mode is used when an existing Item requires input from a user, such as to update credentials or to grant additional consent.

One common use of update mode is to update authentication or authorization for an Item. This can be required when access to an existing Item stops working: for example, if the end user changes a password on an Item that does not use an OAuth connection, or if authorization for the Item has expired. Update mode can also be used pre-emptively to renew authorization for an Item.

Update mode can also be used to request permissions that the user did not originally grant during the initial Link flow. This can include specific OAuth permissions, access to new accounts, or additional product scopes or use cases. Update mode can be used to manage [permissions granted via OAuth](https://plaid.com/docs/link/oauth/#scoped-consent), as well as permissions granted directly through Plaid via [Account Select](https://plaid.com/docs/link/customization/#account-select) or [Data Transparency Messaging](https://plaid.com/docs/link/data-transparency-messaging-migration-guide/). When used to request additional permissions, Plaid will show a streamlined Link experience in update mode. For example, if a user is being asked to add a new product, they may not need to log in to their financial institution.

For some older Items created before March 2023, data for all accounts may be available, even if the user did not select the account in Account Select, and access to new accounts may be granted without needing to use the update mode flow. If these Items are at OAuth institutions, they will be updated to use the current Account Select behavior when they are sent through update mode. For more details, see [Account Select](https://plaid.com/docs/link/customization/#account-select).

Update mode is also used to launch feature-specific flows that require returning to Link. For example, update mode is used for confirming the micro-deposit verification code in the [Same-Day Micro-Deposits](https://plaid.com/docs/auth/coverage/same-day/) Auth flow or to collect statement copies when using [Identity Document Upload](https://plaid.com/docs/identity/identity-document-upload/). For details on using update mode with these flows, see the product-specific pages.

### Resolving ITEM_LOGIN_REQUIRED, PENDING_EXPIRATION, or PENDING_DISCONNECT

Receiving an [`ITEM_LOGIN_REQUIRED`](https://plaid.com/docs/errors/item/#item_login_required) error or a [`PENDING_EXPIRATION`](https://plaid.com/docs/api/items/#pending_expiration) or [`PENDING_DISCONNECT`](https://plaid.com/docs/api/items/#pending_expiration) webhook indicates that the Item should be re-initialized via update mode.

If you receive the `ITEM_LOGIN_REQUIRED` error after calling a Plaid endpoint, implement Link in update mode during the user flow, and ask the user to re-authenticate before proceeding to the next step in your flow.

If you receive the `ITEM_LOGIN_REQUIRED` error via the [`ITEM: ERROR`](https://plaid.com/docs/api/items/#error) webhook, or if you receive either the [`PENDING_EXPIRATION`](https://plaid.com/docs/api/items/#pending_expiration) or [`PENDING_DISCONNECT`](https://plaid.com/docs/api/items/#pending_disconnect) webhook, re-authenticate with Link in update mode when the user is next in your app. You will need to tell your user (using in-app messaging and/or notifications such as email or text message) to return to your app to fix their Item.

When resolving these issues, for most institutions, Plaid will present an abbreviated re-authentication flow requesting only the minimum user input required to repair the Item. For example, if the Item entered an error state because the user's OTP token expired, the user may be prompted to provide another OTP token, but not to fully re-login to the institution.

#### Self-healing Item notifications

If a user has connected the same account via Plaid to multiple different apps, resolving the `ITEM_LOGIN_REQUIRED` error for an Item in one app may also fix Items in other apps. If one of your Items is fixed in this way, Plaid will notify you via the [`LOGIN_REPAIRED`](https://plaid.com/docs/api/items/#login_repaired) webhook. Upon receiving this webhook, you can dismiss any messaging you are presenting to the user telling them to fix their Item via update mode.

### Requesting additional products, accounts, permissions, or use cases

You can use update mode to request your users to share new accounts with you. For instructions, see [Using update mode to request new accounts](https://plaid.com/docs/link/update-mode/#using-update-mode-to-request-new-accounts). Receiving a [`NEW_ACCOUNTS_AVAILABLE`](https://plaid.com/docs/api/items/#new_accounts_available) webhook indicates that Plaid has detected new accounts that you may want to ask your users to share.

Update mode is required when adding Assets, Statements, Income, or Plaid Check Consumer Report to an existing Item. For instructions, see [Adding credit products](https://plaid.com/docs/link/update-mode/#adding-credit-products).

For Items enabled for Data Transparency Messaging, update mode is required to add additional product and use case consents. For instructions, see the [Data Transparency Messaging](https://plaid.com/docs/link/update-mode/#data-transparency-messaging) section of this page.

Update mode can optionally be used for resolving errors when adding Auth or Identity; for more details, see [Resolving `ACCESS_NOT_GRANTED` or `NO_AUTH_ACCOUNTS` errors via product validations](https://plaid.com/docs/link/update-mode/#resolving-access_not_granted-or-no_auth_accounts-errors-via-product-validations).

Update mode can be used to request OAuth permissions: triggering update mode for an OAuth institution will cause the user to re-enter the OAuth flow, where they can then grant any required OAuth permissions they failed to grant originally, or restore OAuth permissions they may have revoked. The update mode flow for OAuth institutions will also contain guidance recommending which permissions the user should grant: for more details, see the [OAuth documentation](https://plaid.com/docs/link/oauth/#scoped-consent).

When an Item is sent through update mode, users can also choose to revoke access they had previously granted. If you lose access to necessary accounts or OAuth permissions after the user completes the update mode flow, you may need to send the user through update mode again. For more details, see [Managing consent revocation](https://plaid.com/docs/link/oauth/#managing-consent-revocation). To prevent users from revoking access to the Auth or Identity products, you can also use [Update Mode with Product Validations](https://plaid.com/docs/link/update-mode/#resolving-access_not_granted-or-no_auth_accounts-errors-via-product-validations).

### Refreshing Item expiration dates

[Certain institutions](https://plaid.com/docs/link/oauth/#refreshing-item-consent) enforce expiration dates on Item consent. You can find an Item's consent expiration date by calling [`/item/get`](https://plaid.com/docs/api/items/#itemget) and checking the `consent_expiration_time` value in the response.

If an Item has an expiration date, update mode can be used to renew consent on that Item. Whenever an Item is successfully sent through update mode, the Item consent expiration date will be updated as though the Item were newly created.

Seven days before an Item's consent expires, you will receive a [`PENDING_DISCONNECT` (US/CA) or `PENDING_EXPIRATION` (UK/EU) webhook](https://plaid.com/docs/link/update-mode/#resolving-item_login_required-pending_expiration-or-pending_disconnect) to notify you to launch update mode. However, you do not need to wait for this webhook in order to refresh the Item.

## Using update mode

### With an access token

To use update mode for an Item, initialize Link with a `link_token` configured with the `access_token` for the Item that you wish to update.

### With a user id or user token

To use update mode for a user, initialize Link with a `link_token` configured with the `user_token` (if you have one) or the `user_id` (for newer Plaid Check integrations that don't use the `user_token`) for the user whose items you wish to update and the [`update.user`](https://plaid.com/docs/api/link/#link-token-create-request-update-user) field set to `true`. This is the only way to use update mode if you are using [Plaid Check](https://plaid.com/docs/check/) products. In Link, the user will be prompted to repair the broken Item. If there are multiple Items in need of repair, the user will be prompted to fix the most recently broken Item. To repair all items associated with a user, you will need to send the user through Link once for each broken Item (you can reuse the same Link token). You can retrieve the status of all Items associated with a user by calling the [`/user/items/get`](https://plaid.com/docs/api/users/#useritemsget) endpoint.

### With a transfer authorization id

To use update mode for a Transfer authorization with `USER_ACTION_REQUIRED` as the decision rationale code, initialize Link with a `link_token` configured with `transfer.authorization_id` set for the authorization that you wish to update; no access token is required. For more details, see the [Transfer documentation](https://plaid.com/docs/transfer/creating-transfers/#repairing-items-in-item_login_required-state).

### Sample code

No products should be added to the `products` array and no product-specific request parameters should be specified when creating a `link_token` for update mode, unless you are using update mode to [add Assets, Statements, Income, or Plaid Check Consumer Report](https://plaid.com/docs/link/update-mode/#adding-credit-products), or if you are using Auth and/or Identity and wish to use the beta [Update Mode with Product Validations flow](https://plaid.com/docs/link/update-mode/#resolving-access_not_granted-or-no_auth_accounts-errors-via-product-validations). If you are using update mode to [add consented products](https://plaid.com/docs/link/update-mode/#requesting-additional-consented-products), the products must be added to the `additional_consented_products` array instead.

You can obtain a `link_token` using the [`/link/token/create`](https://plaid.com/docs/api/link/#linktokencreate) endpoint.

If your integration uses redirect URIs normally, create the Link token with a redirect URI, as described in [Configure your Link token with your redirect URI](https://plaid.com/docs/link/oauth/#configure-your-link-token-with-your-redirect-uri).

```javascript
// Create a one-time use link_token for the Item.
// This link_token can be used to initialize Link
// in update mode for the user
const configs = {
  user: {
    client_user_id: 'UNIQUE_USER_ID',
  },
  client_name: 'Your App Name Here',
  country_codes: [CountryCode.Us],
  language: 'en',
  webhook: 'https://webhook.sample.com',
  redirect_uri: "https://www.sample.com/redirect.html",
  access_token: 'ENTER_YOUR_ACCESS_TOKEN_HERE',
};
app.post('/create_link_token', async (request, response, next) => {
  const linkTokenResponse = await client.linkTokenCreate(configs);

  // Use the link_token to initialize Link
  response.json({ link_token: linkTokenResponse.data.link_token });
});
```

Link auto-detects the appropriate institution and handles the credential and multi-factor authentication process, if needed.

An Item's `access_token` does not change when using Link in update mode, so there is no need to repeat the exchange token process. Likewise, a User's `user_token` or `user_id` does not change when using Link in update mode, so there is no need to create a new `user_token` or `user_id`.

```javascript
// Initialize Link with the token parameter
// set to the generated link_token for the Item
const linkHandler = Plaid.create({
  token: 'GENERATED_LINK_TOKEN',
  onSuccess: (public_token, metadata) => {
    // You do not need to repeat the /item/public_token/exchange
    // process when a user uses Link in update mode.
    // The Item's access_token has not changed.
  },
  onExit: (err, metadata) => {
    // The user exited the Link flow.
    if (err != null) {
      // The user encountered a Plaid API error prior
      // to exiting.
    }
    // metadata contains the most recent API request ID and the
    // Link session ID. Storing this information is helpful
    // for support.
  },
});
```

When an Item is restored from the `ITEM_LOGIN_REQUIRED` state via update mode, if it has been initialized with a product that sends Item webhooks (such as Transactions or Investments), the next webhook fired for the Item will include data for all missed information back to the last time Plaid made a successful connection to the Item.

If the Item has been initialized with Transactions, and was in an error state for over 24 hours, Plaid will check for new transactions immediately after update mode is complete.

### Using update mode to request new accounts

The use of update mode to select new accounts is not available for end user financial institution accounts in the UK or EU. In those regions, delete the old Item using [`/item/remove`](https://plaid.com/docs/api/items/#itemremove) and create a new one instead.

You can allow end users to add new accounts to an Item by enabling Account Select when initializing Link in update mode. To do so, first initialize Link for update mode by creating a `link_token` using the [`/link/token/create`](https://plaid.com/docs/api/link/#linktokencreate) endpoint, passing in the `access_token` and other parameters as you normally would in update mode.

In addition, make sure you specify the following:

1. The `update.account_selection_enabled` flag set to `true`.
2. (Optional) A `link_customization_name`. The settings on this customization will determine which View Behavior for the [Account Select pane](https://plaid.com/docs/link/customization/#account-select) is shown in the update mode flow.
3. (Optional) Any `account_filters` to specify [account filtering](https://plaid.com/docs/api/link/#link-token-create-request-account-filters). Note that this field can only be set for update mode if `update.account_selection_enabled` is set to `true`.

Once your user has updated their account selection, all selected accounts will be shared in the [`accounts`](https://plaid.com/docs/link/web/#link-web-onsuccess-metadata-accounts) field in the [`onSuccess()`](https://plaid.com/docs/link/web/#onsuccess) callback from Link. Any de-selected accounts will no longer be shared with you. You will only be able to receive data for these accounts the user selects in update mode going forward.

Adding an account through update mode will cause Plaid to fetch data for the newly added account. However, [recurring transactions](https://plaid.com/docs/transactions/#recurring-transactions) streams will not be calculated for the new account until either the next periodic transactions update for the Item occurs or the [`/transactions/refresh`](https://plaid.com/docs/api/products/transactions/#transactionsrefresh) endpoint is called.

This update mode flow can also be used to remove accounts from an Item. We recommend that you remove any data associated with accounts that your user has de-selected. Note that Chase is an exception to the ability to remove accounts via update mode; to remove access to a specific account on a Chase Item, the end user must do so through the online Chase Security Center.

**Example /link/token/create call to request new accounts in update mode**

```bash
curl -X POST https://sandbox.plaid.com/link/token/create \
-H 'Content-Type: application/json' \
-d '{
  "client_id": "${PLAID_CLIENT_ID}",
  "secret": "${PLAID_SECRET}",
  "client_name": "My App",
  "user": { "client_user_id": "${UNIQUE_USER_ID}" },
  "country_codes": ["US"],
  "language": "en",
  "webhook": "https://webhook.sample.com",
  "access_token": "${ACCESS_TOKEN}",
  "link_customization_name": "account_selection_v2_customization",
  "redirect_uri": "https://www.sample.com/redirect.html",
  "update": { "account_selection_enabled": true }
}'
```

When using the Assets product specifically, if a user selects additional accounts during update mode but does not successfully complete the Link flow, Assets authorization will be revoked from the Item. If this occurs, have the user go through a new Link flow in order to generate an Asset Report, or, if you have Data Transparency Messaging enabled, use update mode to re-authorize the Item for assets, as described in the next section.

If the Item is initialized with Auth or Transfer, then at certain OAuth institutions, the end user may not be given the option to select non-depository accounts in the Update Mode flow. If you need to add accounts that are not compatible with Auth (for example, because you are adding a new product to the Item), contact your Plaid Account Manager to request that the "skip adding account filters in Update Mode" option be enabled for your account.

If you are using update mode to add a debitable checking or savings account in response to a `NO_AUTH_ACCOUNTS` error, see [Resolving `ACCESS_NOT_GRANTED` or `NO_AUTH_ACCOUNTS` errors via product validations](https://plaid.com/docs/link/update-mode/#resolving-access_not_granted-or-no_auth_accounts-errors-via-product-validations) for a better method to resolve this error.

### Resolving ACCESS_NOT_GRANTED or NO_AUTH_ACCOUNTS errors via Product Validations

> **Note:** To request access to Update Mode with Product Validations, contact support or your Plaid account manager. If you attempt to use this flow before being granted access, the update mode flow will work, but the product validations will not be applied.

The Auth and Identity products can be added to an Item post-Link, by calling an Auth or Identity related endpoint, rather than including `auth` or `identity` in the products array. However, if the user did not share the necessary permissions or accounts to support these products, the Item will return an `ACCESS_NOT_GRANTED` or `NO_AUTH_ACCOUNTS` error. Update Mode with Product Validations (UMPV) applies product-specific validations to the selections a user makes in the update mode flow, resulting in higher conversion than resolving these errors via regular update mode.

The process to use UMPV is the same as described in [Using update mode](https://plaid.com/docs/link/update-mode/#using-update-mode), except that you will also include the `products` array in the request with the value `auth` and/or `identity` for the product(s) you wish to validate. You must use the `products` array with UMPV; including `auth` or `identity` in other fields such as `required_if_supported_products` or `optional_products` will not activate UMPV.

UMPV will enforce the same level of product validation as is normally used on an initial Link attempt: the user will be instructed on which permissions to grant, and if they do not make these selections, they will be prompted to go back through the flow. In the case of `NO_AUTH_ACCOUNTS`, the account selection flow will also be automatically enabled if necessary.

UMPV can also be used preventatively, to prevent users in the update mode flow from accidentally removing permissions they have already granted. Applying UMPV to any update mode session for an Auth- or Identity-enabled Item will prompt users to fix their selections if they remove the accounts or permissions required for these products.

UMPV can only be used for `auth` or `identity`. It is also not compatible with [Adding credit products](https://plaid.com/docs/link/update-mode/#adding-credit-products); the two cannot be used in a single update mode flow, although they can be used on the same Item, via separate update mode sessions.

### Adding credit products

Credit products (Assets, Statements, Income, and Plaid Check Consumer Report) have unique consent flows in Link. To add one of these products to an Item that did not previously have it enabled, you will need to send the user through update mode so that they can go through the product-specific consent flow before the product is added to the Item.

The process to do this is the same as described in [Using update mode](https://plaid.com/docs/link/update-mode/#using-update-mode), except that in the [`/link/token/create`](https://plaid.com/docs/api/link/#linktokencreate) request, you will also include the `products` array in the request with the value for the product you wish to add, such as `assets`, `statements`, `income_verification`, `cra_base_report`, etc. You will also need to add any required product-specific fields — for example, if adding `statements`, you will need to add the `statements` configuration object. For examples and details, see the [`/link/token/create`](https://plaid.com/docs/api/link/#linktokencreate) documentation.

If the user connected their account less than two years ago, they can bypass the Link credentials pane and complete just the consent panes. Otherwise, they will be prompted to complete the full flow.

## Data Transparency Messaging

For Items that are enabled for [Data Transparency Messaging (DTM)](https://plaid.com/docs/link/data-transparency-messaging-migration-guide/), update mode is required for any of the following: requesting additional consented products, requesting additional consented use cases, or renewing consent.

### Requesting additional consented products

For Items with [Data Transparency Messaging](https://plaid.com/docs/link/data-transparency-messaging-migration-guide/) enabled, you need user consent to access new products. To do so, initialize Link for update mode by creating a `link_token` using the [`/link/token/create`](https://plaid.com/docs/api/link/#linktokencreate) endpoint and passing in the `access_token`. For OAuth institutions, ensure you configure the Link token with a redirect URI, as described in [Configure your Link token with your redirect URI](https://plaid.com/docs/link/oauth/#configure-your-link-token-with-your-redirect-uri).

In addition, make sure you specify the following when calling [`/link/token/create`](https://plaid.com/docs/api/link/#linktokencreate) to add an additional consented product in update mode:

- The `additional_consented_products` field should be set to include **any new products you want to gather consent for.**
  - For example, if Link was initialized with just Transactions and you want to upgrade to Identity, you would pass in `identity` to the `additional_consented_products` field.
  - To see the currently authorized and consented products on an Item, use the [`/item/get`](https://plaid.com/docs/api/items/#itemget) endpoint.
- The `link_customization_name` should be set to a customization with Data Transparency Messaging enabled. The use case string should also be broadened to include your reasons for accessing the new data. If the use case is not customized, the default use case will be present on the Data Transparency Messaging pane.

If the upgrade was successful, you will receive the `onSuccess()` callback and you will have access to the API endpoints for all of the products you passed into Link update mode. The new products will only be billed once the related API endpoints are called, even if they would otherwise be billed upon Item initialization (e.g., Transactions).

When using update mode to add product consent for an Item with DTM enabled, you must use the `additional_consented_products` parameter, not the `products` parameter, unless you are adding [Assets, Statements, Income, or Plaid Check Consumer Report](https://plaid.com/docs/link/update-mode/#adding-credit-products) or using [Product Validations (beta)](https://plaid.com/docs/link/update-mode/#resolving-access_not_granted-or-no_auth_accounts-errors-via-product-validations).

### Requesting additional use cases

For Items with DTM enabled, your user will be prompted to consent to their data being used for specific use cases, not just specific products. If you need to obtain consent for additional use cases, you can update your Link customization to add the use cases, then send your user through update mode.

## Update mode and Layer

During the [Layer](https://plaid.com/docs/layer/) flow, if the end user has an Item in an unhealthy state that requires update mode, this Item will still be surfaced in Link. If the user chooses to share this Item via Layer, they will automatically be sent through update mode as part of the Layer flow.

## Update mode at PNC with Auth

When using Update mode with Auth for Items at PNC, the Item's account and routing number may need to be refreshed after the update mode flow is complete. It's recommended to call [`/auth/get`](https://plaid.com/docs/api/products/auth/#authget) (or [`/processor/token/create`](https://plaid.com/docs/api/processors/#processortokencreate) or [`/processor/stripe/bank_account_token/create`](https://plaid.com/docs/api/processors/#processorstripebank_account_tokencreate)) after completing update mode for these Items. For more details, see [PNC TAN expiration](https://plaid.com/docs/auth/#pnc-tan-expiration). This issue only impacts PNC Items used with the Auth product; you do not need to do this for Items at other institutions or when using products other than Auth.

## Processor tokens

When using update mode on an Item associated with a processor token, all existing processor tokens will automatically receive the updates, since they are linked to the access token, which does not change during update mode. However, if the user changes the linked account (e.g. by selecting a different account via update mode), you will need to generate a new processor token by calling [`/processor/token/create`](https://plaid.com/docs/api/processors/#processortokencreate).

## Testing update mode

Update mode can be tested in the Sandbox using the [`/sandbox/item/reset_login`](https://plaid.com/docs/api/sandbox/#sandboxitemreset_login) endpoint, which will force a given Item into an `ITEM_LOGIN_REQUIRED` state. For user-based products, like Consumer Report or Bank Income, [`/sandbox/user/reset_login`](https://plaid.com/docs/api/sandbox/#sandboxuserreset_login) can be used to force all Items associated with a given user into an `ITEM_LOGIN_REQUIRED` state.

## Example React code in Plaid Pattern

For a real-life example that illustrates the handling of update mode, see [linkTokens.js](https://github.com/plaid/pattern/blob/master/server/routes/linkTokens.js#L30-L35) and [LaunchLink.tsx](https://github.com/plaid/pattern/blob/master/client/src/components/LaunchLink.tsx#L42-L46). These files contain the Link update mode code for the React-based [Plaid Pattern](https://github.com/plaid/pattern) sample app.
