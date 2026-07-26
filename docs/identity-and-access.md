# Identity and access

OpenEnterprise Twin v0.6 supports three explicit authentication modes:

| Mode | Intended use | Browser support | Production |
| --- | --- | --- | --- |
| `local` | Local development and isolated tests | Yes, without a login redirect | Rejected |
| `oidc` | Human users through an enterprise identity provider | Yes, authorization code + PKCE | Recommended |
| `api_key` | One tenant-bound machine or service account | No | Supported |

Authentication always produces an immutable principal with `subject`,
`tenant_id`, `roles` and `authentication_method`. The API, not the browser,
enforces authorization and tenant ownership.

## OIDC registration

Register the frontend as a public browser client. Do not create or expose a
client secret.

Required client settings:

- authorization code flow;
- PKCE with `S256`;
- exact callback URL, for example
  `https://twin.example.com/auth/callback`;
- exact post-logout URL, for example `https://twin.example.com/`;
- scopes `openid profile`;
- short-lived access tokens whose audience is `openenterprise-twin`;
- asymmetric signing with one configured algorithm, normally `RS256`.

The access token must include:

| Claim | Contract |
| --- | --- |
| `iss` | Exact configured issuer, including its trailing slash convention |
| `aud` | Exact API audience |
| `sub` | Stable identity-provider subject |
| `iat`, `nbf`, `exp` | Valid token lifetime |
| `tenant_id` | Lowercase tenant identifier matching `[a-z0-9][a-z0-9_-]*` |
| `roles` | Non-empty array containing only supported roles |

Configure the API:

```bash
OPENENTERPRISE_TWIN_DEPLOYMENT_ENVIRONMENT=production
OPENENTERPRISE_TWIN_AUTHENTICATION_MODE=oidc
OPENENTERPRISE_TWIN_OIDC_ISSUER=https://identity.example/
OPENENTERPRISE_TWIN_OIDC_AUDIENCE=openenterprise-twin
OPENENTERPRISE_TWIN_OIDC_JWKS_URL=https://identity.example/.well-known/jwks.json
OPENENTERPRISE_TWIN_OIDC_ALGORITHMS='["RS256"]'
OPENENTERPRISE_TWIN_OIDC_TENANT_CLAIM=tenant_id
OPENENTERPRISE_TWIN_OIDC_ROLES_CLAIM=roles
```

Build the browser with public OIDC configuration:

```bash
VITE_AUTH_MODE=oidc
VITE_OIDC_AUTHORITY=https://identity.example/
VITE_OIDC_CLIENT_ID=openenterprise-twin-cockpit
VITE_OIDC_REDIRECT_URI=https://twin.example.com/auth/callback
VITE_OIDC_POST_LOGOUT_REDIRECT_URI=https://twin.example.com/
VITE_OIDC_SCOPE="openid profile"
```

Set the frontend container's CSP extension to the exact identity-provider
origin:

```bash
OIDC_CONNECT_SRC=https://identity.example
```

`OIDC_CONNECT_SRC` is inserted only into `connect-src`. Keep it empty for local
authentication and never use a wildcard.

## Token validation

The API:

- accepts bearer tokens only through `Authorization`;
- validates the configured algorithm before selecting a key;
- fetches only the configured JWKS URL with redirects disabled, a timeout and a
  response-size limit;
- caches keys for a bounded TTL and refreshes once when an unknown `kid`
  indicates rotation;
- validates signature, issuer, audience, subject, lifetime, tenant and roles;
- rejects mixed API-key and bearer credentials;
- returns a stable `401 authentication_required` without token details.

The browser uses `oidc-client-ts`. Authorization state and the short-lived OIDC
user are session-scoped so a tab can complete the redirect, while the active
API bearer is also held in application memory. No refresh token or client
secret is configured. A strict CSP and the absence of raw-HTML sinks reduce,
but cannot eliminate, the residual risk of token theft after a browser XSS.

## Roles

| Capability | `viewer` | `analyst` | `approver` | `admin` |
| --- | :---: | :---: | :---: | :---: |
| Read tenant resources and jobs | ✓ | ✓ | ✓ | ✓ |
| Create scenarios and analytical work |  | ✓ |  | ✓ |
| Cancel active analytical jobs |  | ✓ |  | ✓ |
| Move evidence into review-ready states |  | ✓ |  | ✓ |
| Approve governed decisions |  |  | ✓ | ✓ |
| Read operational system information |  |  |  | ✓ |

Exact route policy is defined by FastAPI security dependencies and covered by
the authorization matrix. Frontend role checks improve navigation and
feedback; they are never the authorization boundary.

Approval separation is identity-bound. A principal cannot approve a decision
when its authenticated subject is the actor that submitted the governed
transition.

## Tenant bootstrap

Tenant identifiers originate from trusted deployment configuration:

- OIDC mode: the validated tenant claim;
- API-key mode: `OPENENTERPRISE_TWIN_SERVICE_ACCOUNT_TENANT_ID`;
- local mode: `OPENENTERPRISE_TWIN_LOCAL_TENANT_ID`.

Every persisted business row has a non-null tenant owner. Repositories require
an explicit tenant at construction, filter every lookup and mutation by that
tenant, and use composite tenant-aware uniqueness and foreign keys. There is no
request header or query parameter that can switch tenant.

A new tenant is bootstrapped by:

1. provisioning identity-provider claims and role assignments;
2. authenticating an administrator for the new tenant;
3. creating or importing that tenant's baseline resources through the API;
4. verifying `/api/v1/session` returns the expected effective identity;
5. running a smoke scenario and checking its job/result remain invisible to a
   principal from another tenant.

## Service accounts and rotation

API-key mode maps exactly one secret to one configured principal:

```bash
OPENENTERPRISE_TWIN_AUTHENTICATION_MODE=api_key
OPENENTERPRISE_TWIN_API_KEY=<secret-manager-value-at-least-32-characters>
OPENENTERPRISE_TWIN_SERVICE_ACCOUNT_SUBJECT=planning-worker
OPENENTERPRISE_TWIN_SERVICE_ACCOUNT_TENANT_ID=northstar
OPENENTERPRISE_TWIN_SERVICE_ACCOUNT_ROLES='["analyst"]'
```

Send the key in `X-API-Key` from the machine client. The frontend Nginx proxy
does not inject service-account credentials into browser requests.

Rotation procedure:

1. create a new high-entropy secret in the secret manager;
2. deploy the API with the new value during a controlled client cutover;
3. update the machine client and verify `/api/v1/session`;
4. revoke the old secret;
5. inspect authentication failures and audit events for unexpected old-key use.

The built-in adapter intentionally supports one active key. Deployments that
need overlap, per-client revocation or many service accounts should provide an
external gateway or identity adapter rather than broadening the browser
contract.

## Access troubleshooting

| Symptom | Verify |
| --- | --- |
| Browser loops at sign-in | Exact callback URL, issuer slash convention, client type and PKCE support |
| API returns `401` | Signature key, `kid`, issuer, audience, lifetime and required claims |
| API returns `403` | Effective roles from `/api/v1/session` and the route policy |
| Resource returns `404` for one user | Tenant claim and tenant ownership; cross-tenant resources deliberately look absent |
| OIDC works but browser calls are blocked | `OIDC_CONNECT_SRC`, API same-origin proxy and constrained CORS configuration |

