# Microsoft Entra ID setup (real tenant)

How to move Groundline sign-in from the local mock IdP to a real Microsoft
Entra ID tenant. The code path is the same; this is configuration only.
Real-tenant verification, HTTPS and the dev-login cutover are tracked in
GL-3-14.

## 1. App registration (tenant admin)

In the Entra admin center: **Identity → Applications → App registrations →
New registration**.

1. **Name**: `Groundline` (any name).
2. **Supported account types**: *Accounts in this organizational directory
   only* (single tenant). External SMEs join as **B2B guests** of this tenant;
   no separate mechanism is needed.
3. **Redirect URI**: platform **Web**, value
   `https://<groundline-host>/v1/auth/sso/callback`. It must be the address
   users' browsers load the web app from, because the API redirects to `/`
   after sign-in. Entra only accepts `http://` for `localhost`; any other host
   needs HTTPS (GL-3-14 adds the TLS proxy).
4. **Authentication**: under *Implicit grant and hybrid flows*, leave both
   boxes unticked. Groundline uses the authorization code flow with PKCE and
   reads the ID token from the token endpoint.
5. **Certificates & secrets → New client secret**. Pick an expiry, record the
   expiry date, and plan the rotation (a secret that expires breaks sign-in).
   Copy the **value** (it is shown once). It is a secret: deliver it through
   the deploy environment's secret store, never a commit or chat.
6. **Token configuration → Add groups claim**. Select **Security groups**, and
   for the **ID** token choose **Group ID**. Groundline maps roles by group
   **object ID (GUID)** (PRD open decision 6), not by display name.
7. **API permissions**: the default `User.Read` (delegated) is enough. The
   `openid`, `profile` and `email` scopes need no extra consent.
8. Record the **Directory (tenant) ID** and **Application (client) ID** from
   the Overview page.

## 2. Groups → roles

Create (or reuse) one security group per role, add members, and record each
group's **Object ID**:

| Role | Can (design §5) | Example group |
|---|---|---|
| admin | everything, plus token management | `Groundline-Admins` |
| editor | create datasets, change schema, cut versions, import, assign | `Groundline-DataScience` |
| annotator | edit rows, comment, change row status | `Claims-Assessors` |
| viewer | read and export | *(anyone else, via `default`)* |

A user in several mapped groups gets the **highest** role. Anyone signing in
with no mapped group gets `default` (viewer). Roles are re-synced from
`groups` on every sign-in.

**Groups overage.** If a user belongs to more than 200 groups, Entra omits
`groups` from the ID token and sends `_claim_names` instead. Groundline does
not call Microsoft Graph to resolve that. Such users get the **default role**,
and a warning is logged. Workarounds: use *groups assigned to the application*
in the groups-claim settings, or move to app roles (open decision 6).

## 3. Environment changes: mock → real Entra

| Variable | Mock (local, `sso-mock` profile) | Real Entra |
|---|---|---|
| `AUTH_SSO_ENABLED` | `true` | `true` |
| `OIDC_DISCOVERY_URL` | `http://mock-oidc.localhost:8090/entra/.well-known/openid-configuration` | **empty**, so it is derived as `https://login.microsoftonline.com/<ENTRA_TENANT_ID>/v2.0/.well-known/openid-configuration` |
| `ENTRA_TENANT_ID` | empty | Directory (tenant) ID |
| `ENTRA_CLIENT_ID` | `groundline-local` | Application (client) ID |
| `ENTRA_CLIENT_SECRET` | `mock-secret` | the client secret value (**secret**) |
| `OIDC_REDIRECT_URI` | `http://localhost:5173/v1/auth/sso/callback` | `https://<groundline-host>/v1/auth/sso/callback` (exactly as registered) |
| `OIDC_SCOPES` | `openid,profile,email` | `openid,profile,email` |
| `ROLE_MAPPING` | mock fixture GUIDs | `{"<admins-group-oid>":"admin","<editors-group-oid>":"editor","<annotators-group-oid>":"annotator","default":"viewer"}` |

Set these in the deploy environment (e.g. `infra/.env` or a secret store), not
in `docker-compose.yml`, and don't start the `sso-mock` profile. Then
recreate the api:
`docker compose -f infra/docker-compose.yml up -d --build api`.

Dev login stays on until the GL-3-14 cutover (`AUTH_DEV_LOGIN=false`,
`APP_ENV=prod`). Until then, don't expose the deployment beyond a trusted
network.

## 4. Check it

1. Open the web app and choose **Sign in with Microsoft**. You should reach the
   tenant's sign-in page, then return signed in.
2. `GET /v1/auth/me` shows the user with the role their groups map to.
3. A user in no mapped group signs in as **viewer**.
4. Anything that differs from what the mock assumed (claim names, `email`
   missing for guests, `groups` format) gets recorded in GL-3-14.
