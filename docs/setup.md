# Setup help

## 1. Choose your mode

| What you need | Mode | What to install |
| --- | --- | --- |
| Keep an existing working client | Bearer-only (default) | No changes |
| A client supporting custom Authorization headers | Bearer-only | Base installation |
| ChatGPT's OAuth connection using Google sign-in | Optional Google OAuth | OAuth extra/build plus Google configuration |

Both modes require authentication. **Optional OAuth does not mean anonymous
access.** Never disable authentication to fix a ChatGPT connection error.
Google OAuth adds another authentication method; your existing MCP bearer token
continues to work.

This guide documents setup; reading it does not deploy anything.

## 2. Bearer-only setup

Already working? Stop here unless you specifically want to enable Google OAuth.
Do not add Google settings, change the build argument, or mount OAuth storage.

For a new installation, follow [Docker / existing Tesla stack](../README.md#docker--existing-tesla-stack).
The main points are:

1. Place the source in the existing Tesla stack's `teslamate-mcp/` directory.
2. Merge `compose.example.yml` into the existing stack; set the build context to
   `./teslamate-mcp`. Do not replace the rest of the stack.
3. Store a strong, distinct `MCP_BEARER_TOKEN` in a private `runtime.env`.
   Preserve any separate upstream TeslaMateAPI credentials.
4. Use the stack's network so `http://teslamateapi:8080` is reachable.
5. Validate Compose and start only `teslamate-mcp`; never run `compose down`.
6. Point the dedicated HTTPS tunnel resource at `http://teslamate-mcp:8000`.

For a compatible MCP client, use:

```json
{
  "mcpServers": {
    "teslamate": {
      "url": "https://mcp.example.com/mcp",
      "headers": {
        "Authorization": "Bearer REPLACE_WITH_YOUR_MCP_TOKEN"
      }
    }
  }
}
```

Use the **MCP** token, not the TeslaMateAPI token, Google secret, or host password.
The exact configuration wrapper depends on the client.

ChatGPT's “No authentication” option will not work against this protected
endpoint. For ChatGPT, continue with the optional OAuth steps.

## 3. Optional: create a Google OAuth client

Sign in to [Google Cloud Console](https://console.cloud.google.com/) with the
Google account that will own the OAuth client.

1. Use the project selector to create/select a project, for example **TeslaMate MCP**.
2. Open [Google Auth Platform](https://console.cloud.google.com/auth/overview).
   Choose **Get started** if prompted.
3. Configure the app:
   - App name: **TeslaMate MCP**
   - Support/contact email: your Google email
   - Audience: **External** for a personal Gmail account
4. Under **Audience**, leave the app in **Testing** initially and add your
   email under **Test users**.
5. Under [Clients](https://console.cloud.google.com/auth/clients), choose
   **Create client → Web application**.
6. Name it **TeslaMate MCP Server**.
7. Add this exact **Authorized redirect URI**, without a trailing slash:

   ```text
   https://mcp.example.com/auth/callback
   ```

8. Leave **Authorized JavaScript origins** empty; this is a server-side flow.
9. Create the client and save its Client ID and Client Secret privately.
   Download the credentials immediately if offered; the secret may not be shown again.

You do not need Gmail/Drive APIs, paid hosting, or a service-account key.
The server requests only OpenID identity and email access. If configuring scopes
under **Data Access**, use `openid` and
`https://www.googleapis.com/auth/userinfo.email`; do not add Gmail/Drive scopes.

The Google callback above is **not** ChatGPT's callback. The server bridges the
two flows automatically.

If a secret was shared in chat or a screenshot, rotate/delete it in Google's
client settings before proceeding. Do not send the replacement to an assistant.

## 4. Store the optional OAuth settings privately

Create a protected `runtime.env` next to the service source. **Do not overwrite
an existing runtime file with `.env.example`**: preserve the working bearer
token and upstream API settings. Add all seven settings below, replacing the
placeholders:

```dotenv
MCP_GOOGLE_CLIENT_ID=REPLACE_WITH_GOOGLE_CLIENT_ID
MCP_GOOGLE_CLIENT_SECRET=REPLACE_WITH_CURRENT_GOOGLE_CLIENT_SECRET
MCP_GOOGLE_ALLOWED_EMAILS=user@example.com
MCP_OAUTH_BASE_URL=https://mcp.example.com
MCP_OAUTH_SIGNING_KEY=REPLACE_WITH_RANDOM_SIGNING_KEY
MCP_OAUTH_STORAGE_KEY=REPLACE_WITH_FERNET_KEY
MCP_OAUTH_STORAGE_DIR=/var/lib/teslamate-mcp/oauth
```

Generate the two keys **separately in a private terminal on your workstation**
(these commands do not require the OAuth packages):

```sh
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
python3 -c 'import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'
```

The first output is the signing key; the second is the Fernet storage key.
Copy each into the appropriate setting. Do not reuse either as an MCP/API token,
and do not paste their values into chat, screenshots, shell arguments, or source files.

Keep the secret file mode `600` and its parent directory mode `700`:

```sh
chmod 700 ./teslamate-mcp
chmod 600 ./teslamate-mcp/runtime.env
```

Back up the keys securely along with the OAuth volume. Keep them stable across
rebuilds; losing/changing them can require clients to sign in again.

## 5. Explicitly opt in to the OAuth installation

### Existing TeslaMate stack

Back up the current stack Compose file, protected runtime file, and working MCP
image before making changes. Preserve other services and existing volumes.

Merge only the OAuth additions into the **existing** MCP service. For example,
these are the relevant fields, **not a replacement for the complete service**:

```yaml
services:
  teslamate-mcp:
    build:
      context: ./teslamate-mcp
      args:
        INSTALL_OAUTH: "true"
    env_file:
      - ./teslamate-mcp/runtime.env
    volumes:
      - teslamate_mcp_oauth:/var/lib/teslamate-mcp

volumes:
  teslamate_mcp_oauth:
```

Keep the existing image name, network, restart policy, healthcheck, resource
limits and security settings. Add the new top-level volume alongside existing
volumes; do not replace them.

**Important:** remove duplicate `environment` entries for secrets/settings you
put in `env_file`, including the seven OAuth keys. Inline `environment` values,
even empty ones, override `env_file`. Preserve unrelated environment settings,
such as `TESLAMATE_API_URL` and `MCP_ALLOWED_ORIGINS`.

The OAuth base URL must also appear in `MCP_ALLOWED_ORIGINS`; set both to your
public HTTPS origin.

The image initializes a fresh volume with UID/GID 10001 permissions. Do not make
the directory world-writable if an old volume has incorrect ownership.
This storage configuration is for one server process/instance.

From the existing stack directory, validate the configuration:

```sh
docker compose -p tesla config --quiet
```

Only if validation succeeds and you intend to deploy:

```sh
docker compose -p tesla up -d --no-deps --build teslamate-mcp
docker compose -p tesla ps teslamate-mcp
```

Do not run `compose down` or print expanded `compose config`: it contains secrets.

### Using the example files instead

Outside the existing stack, the optional override is explicit:

```sh
docker compose --env-file runtime.env -f compose.example.yml -f compose.oauth.example.yml config --quiet
docker compose --env-file runtime.env -f compose.example.yml -f compose.oauth.example.yml up -d --no-deps --build teslamate-mcp
```

Run from the source directory with a protected `runtime.env`. These examples
create their own Compose project/network: connect the service to your existing
API network separately. **Do not use them to replace the existing stack.**
Here `--env-file` supplies Compose interpolation, unlike the service-level
`env_file` described above.

### Python without Docker

Install from the source directory:

```sh
python3 -m venv .venv
.venv/bin/pip install '.[oauth]'
```

Provide the settings through a secure environment mechanism, using an absolute,
private, writable `MCP_OAUTH_STORAGE_DIR` on that host, then run:

```sh
.venv/bin/python -m teslamate_mcp
```

The application does **not** automatically load `runtime.env`.
For bearer-only Python use, install `.` instead of `.[oauth]`.

## 6. Check the reverse proxy and public endpoint

Forward the **whole** dedicated hostname to `http://teslamate-mcp:8000`, not
only the `/mcp` path. Preserve Authorization, cookies, query strings, Accept,
Content-Type and MCP protocol headers. Allow OAuth form POSTs as well as MCP
POST/GET/DELETE and streaming.

OAuth discovery, registration, consent, callback, token and revocation paths must
reach the application without a browser-only tunnel login. Keep authentication
inside the MCP server; do not expose the underlying TeslaMateAPI. Configure edge
rate limits on registration/authorization, and avoid logging OAuth query strings.

These checks send **no credentials**:

```sh
curl -i --max-time 15 https://mcp.example.com/healthz
curl -i --max-time 15 https://mcp.example.com/.well-known/oauth-authorization-server
curl -i --max-time 15 https://mcp.example.com/mcp
```

Expected with OAuth enabled:

- Health: HTTP 200 and `{"status":"ok"}`.
- Discovery: HTTP 200 JSON, with authorization/token/registration URLs on this hostname.
- MCP: HTTP 401 JSON and a `WWW-Authenticate` header containing `resource_metadata`.
  **This 401 is correct without a token.**

In bearer-only mode, discovery is not available and the MCP 401 has no OAuth
metadata. Never use `curl -k` to bypass certificate errors.

## 7. Connect ChatGPT

1. Open ChatGPT's apps/plugins settings; enable developer mode if your account
   requires it. Workspace administrators may restrict custom MCP connections.
2. Add a custom MCP server with URL:
    `https://mcp.example.com/mcp`.
3. Select **OAuth**, not **No authentication**.
4. Use automatic registration/discovery if offered. Leave downstream OAuth
   client credentials blank: **Google's Client ID/Secret belong on your server,
   not in those ChatGPT fields**.
5. Approve the MCP server's client consent page.
6. Sign in as an allowlisted Google account and approve identity/email access.
7. Install/enable the resulting app and test: “Use TeslaMate to list my logged cars.”
8. Confirm the previous bearer client still works.

Only explicitly allowlisted, verified Google email addresses can read telemetry.
Google's test-user list is not a substitute for the server's allowlist.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| “Der MCP-Server hat den Zugriff verweigert” with No authentication | This server is protected. Use a bearer-capable client, or explicitly enable OAuth and choose OAuth in ChatGPT. |
| OAuth discovery returns 401 | Confirm the OAuth build was deployed and all seven settings reach the container; check the tunnel forwards the full hostname. |
| Plain-text 401 or tunnel login page | Check the proxy access policy and header forwarding. The application's missing-bearer response is JSON. |
| “Optional .[oauth] dependencies” startup error | Rebuild with `INSTALL_OAUTH: "true"` or install `.[oauth]`. Adding credentials alone does not install the extra. |
| Partial OAuth configuration startup error | Supply all seven OAuth settings; check inline environment values aren't overriding `env_file`. |
| Google `redirect_uri_mismatch` | Match `https://mcp.example.com/auth/callback` exactly, with HTTPS and no trailing slash. |
| Google `invalid_client` | Check the current client ID/secret pair belongs to the selected Web application client and is not a deleted/rotated credential. |
| Google blocks the test user | Use External audience and add your account under Audience → Test users. |
| Google login succeeds but MCP still returns 401 | Confirm the verified email is in `MCP_GOOGLE_ALLOWED_EMAILS`; check Google connectivity and the actual client credentials. |
| Browser/session mismatch during consent | Restart the connection flow in the same browser; preserve cookies through the proxy. Do not disable consent/browser binding. |
| Storage permission error | Check the OAuth volume is mounted and writable by UID/GID 10001, while the container root remains read-only. |
| Relinking needed after rebuild | Check the same volume and signing/storage keys were retained. Also check Google's current consent/token policies. |
| TLS or Host error | Fix the certificate/hostname or forwarded Host; do not bypass TLS/Host checks. |

Keep logs private. Avoid verbose HTTP/auth debug logging; failure messages may
include transaction IDs even though credential-bearing query URLs are redacted.
Do not share runtime files, Google credentials, tokens or raw OAuth callback URLs.

## Roll back to bearer-only

To preserve the known-working deployment, prefer restoring **only this service's**
previous image and its backed-up configuration. Do not restore an entire stack
backup over unrelated subsequent changes.

If using the new source in bearer-only mode instead:

1. Remove/empty **all seven** OAuth settings from the MCP container's environment.
   Leaving only some configured intentionally fails startup.
2. Remove `INSTALL_OAUTH: "true"`/the OAuth override and remove only the OAuth
   service mount if desired. Keep the named volume itself for possible reuse.
3. Keep the existing `MCP_BEARER_TOKEN` and upstream settings.
4. Validate Compose, then rebuild/recreate only `teslamate-mcp`.
5. Verify the old bearer client still works.

Do not delete the OAuth volume/keys or change the bearer token as part of a
routine rollback. ChatGPT's Google connection stops working in bearer-only mode;
existing bearer clients remain supported.
