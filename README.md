# TeslaMate MCP

Read-only MCP tools backed by [TeslaMateAPI](https://github.com/tobiasehlert/teslamateapi).
No direct database access, Tesla account credentials, vehicle commands, or wake-ups.

## Setup help

Start with the [step-by-step setup guide](docs/setup.md): choose bearer-only
access or optional Google sign-in for ChatGPT, then follow the credential,
deployment, connection-check and troubleshooting instructions.
**A working bearer-only deployment does not need to be changed.**

## Tools

`list_cars`, `get_car`, `get_status`, `get_battery_health`, `list_charges`,
`get_current_charge`, `get_charge`, `list_drives`, `get_drive`, `get_updates`,
`get_global_settings`, `get_api_info`.

Upstream `/logging` is deliberately not exposed: despite its name, its GET
handler lists enabled commands, not logging state, and requires command support.
When no charge is active, `get_current_charge` returns the upstream absence
message as an MCP tool error. Other upstream application errors are sanitized.

Identifiers are positive TeslaMate integer IDs, not VINs. History tools accept
`start_date` / `end_date` as timezone-aware RFC3339 timestamps and optional
`location`; drives also accept `min_distance` / `max_distance`. Both history
tools accept `page` (default 1) and `page_size` (default 100, maximum 500).
Results are newest first; increment `page` until fewer than `page_size` records
are returned. Do not treat a single page as the complete history.
Dates normalize to UTC. Units and response JSON are retained from TeslaMateAPI.
Top-level arrays appear under `result` in structured MCP content (text remains unchanged).

History responses are limited to 4 MiB with a 20-second total request deadline.
Prefer narrow date ranges and use pagination for longer histories.
The adapter requests uncompressed upstream responses and rejects compressed
responses before decoding, keeping the size guard effective against expansion.
Status is last logged data and can be stale. Available endpoints/filters depend
on the installed TeslaMateAPI version; unsupported endpoints return an upstream
HTTP error rather than changing the API service.

## Docker / existing Tesla stack

1. Put the source in a `teslamate-mcp/` subdirectory of the existing stack.
2. Merge the service from `compose.example.yml` into its `compose.yml`, changing
   build context to `./teslamate-mcp`.
3. Generate a distinct token (at least 32 non-whitespace characters).
4. Prefer a restricted `env_file: ./teslamate-mcp/runtime.env` (mode `600`)
   rather than inline secrets. Place `MCP_BEARER_TOKEN` and optional
   `TESLAMATE_API_TOKEN` / `TESLAMATE_API_TOKEN_HEADER` there, and remove their
   corresponding interpolated environment keys from the service.
5. Back up and validate compose, then build/start **only** the new service:

   ```sh
   docker compose -p tesla config --quiet
   docker compose -p tesla up -d --no-deps --build teslamate-mcp
   ```

Do not run `down` on the Tesla stack. No host port is published. The container
runs as UID 10001, has a read-only root filesystem, and has no Docker socket.

## Reverse proxy / tunnel

Create an HTTPS HTTP resource in your reverse proxy or tunnel:

| Setting | Value |
| --- | --- |
| Public hostname | `mcp.example.com` |
| Internal target | `http://teslamate-mcp:8000` |
| MCP URL | `https://mcp.example.com/mcp` |
| Optional health path | `/healthz` |

Preserve `/mcp` (do not strip it), Authorization, Accept, Content-Type, and
`MCP-Protocol-Version` headers. Allow POST, GET, DELETE and streaming responses.
Do not replace MCP bearer authentication with Pangolin's interactive browser
login: machine clients generally cannot complete it. The MCP validates the
bearer token itself; disable interactive resource auth only for this dedicated
resource, or configure a client-compatible Pangolin policy that preserves the
MCP Authorization header. Use HTTPS publicly. Static bearer access is always
available; optionally enable Google OAuth below for ChatGPT.

The application also validates Origin when supplied, and restricts Host to the
public hostname, localhost, and `teslamate-mcp`. If Newt rewrites Host, ensure it
uses `teslamate-mcp:8000` or preserves the public hostname.

## Client configuration

For clients that support Streamable HTTP with custom headers:

```json
{
  "mcpServers": {
    "teslamate": {
      "url": "https://mcp.example.com/mcp",
      "headers": {
        "Authorization": "Bearer REPLACE_WITH_MCP_TOKEN"
      }
    }
  }
}
```

The exact wrapper/transport key depends on your MCP client. Clients requiring
OAuth rather than static bearer headers can use the optional Google integration.
Never paste NAS credentials into MCP configuration.

## ChatGPT with Google sign-in

Google OAuth is **off by default**, including its extra packages and Docker
storage. The original `compose.example.yml` remains bearer-only. Python users
opt in with `pip install '.[oauth]'`; Docker users opt in with the
`INSTALL_OAUTH=true` build argument (see `compose.oauth.example.yml`).
Without Google settings, the server does not import the OAuth packages.

When enabled, this server bridges ChatGPT's OAuth
registration flow to a Google **Web application** client. Google credentials
stay on the server; ChatGPT receives server-specific tokens, not Google tokens.
Existing static-bearer clients continue working.

1. Create a Google Cloud project and configure Google Auth Platform (External
   audience for personal Gmail accounts). During Testing, add your account as a
   test user. Create a Web application OAuth client with exactly this redirect:
   `https://mcp.example.com/auth/callback`. JavaScript origins are not needed.
2. Configure **all** the following in the service's protected `runtime.env`.
   Use your actual current Client ID and rotated secret; never paste secrets
   into chat or commit them. Keep existing MCP/upstream token settings.

   ```dotenv
   MCP_GOOGLE_CLIENT_ID=REPLACE_WITH_GOOGLE_CLIENT_ID
   MCP_GOOGLE_CLIENT_SECRET=REPLACE_WITH_ROTATED_GOOGLE_SECRET
    MCP_GOOGLE_ALLOWED_EMAILS=user@example.com
    MCP_OAUTH_BASE_URL=https://mcp.example.com
   MCP_OAUTH_SIGNING_KEY=REPLACE_WITH_RANDOM_SIGNING_KEY
   MCP_OAUTH_STORAGE_KEY=REPLACE_WITH_FERNET_KEY
   MCP_OAUTH_STORAGE_DIR=/var/lib/teslamate-mcp/oauth
   ```

   Generate keys privately using the Python environment containing this app:

   ```sh
   python -c 'import secrets; print(secrets.token_urlsafe(48))'
   python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
   ```

   Use distinct keys. Keep `runtime.env` mode `600`. If using Compose `env_file`,
   remove corresponding interpolated `environment` keys: empty inline values
   override an env file. Partial OAuth configuration fails startup.
3. Explicitly opt in using `compose.oauth.example.yml` alongside the base file,
   or merge its additions into **only** the existing stack's MCP service.
   Set `build.args.INSTALL_OAUTH: "true"` and merge the named OAuth volume.
   Mount `teslamate_mcp_oauth:/var/lib/teslamate-mcp`; the image creates this
   directory as UID/GID 10001, mode 700. The volume preserves encrypted sessions
   across container replacement. Keep both keys stable and back them up securely
   with the volume. Changing keys or deleting state requires relinking clients.
   This file-backed setup supports **one process/instance only**.
4. Pangolin must forward the **whole dedicated hostname**, not only `/mcp`.
   Discovery, `/register`, `/authorize`, `/consent`, `/auth/callback`, `/token`,
   and `/revoke` must reach the server. Preserve query strings, cookies,
   Authorization and protocol headers. Do not put browser-only tunnel login in
   front of these endpoints. Configure edge rate limits on public registration
   and authorization endpoints to limit unauthenticated storage/resource abuse.
5. Rebuild/recreate **only** `teslamate-mcp` after validating Compose. Verify
   `/.well-known/oauth-authorization-server` returns public JSON and `/mcp`
   returns 401 with a `resource_metadata` challenge without credentials.
6. In ChatGPT, add `https://mcp.example.com/mcp`, select **OAuth**, and use
   automatic registration/discovery when offered. Leave downstream OAuth client
   credential fields blank; **do not enter the Google Client ID/Secret there**.
   Approve the server's client consent page, then sign in to Google.

Only explicitly allowlisted **verified** email addresses can access MCP through
Google. A login hint or Google test-user list is not an authorization policy.
Only identity/email scopes are requested: no Gmail or Drive access.
Unlisted users may finish Google's sign-in, but MCP rejects their credentials.

Testing/Production publishing status and Google's token policies can affect
reconnection and consent; use Google's current guidance when moving to
Production. Publishing does not remove this server's allowlist.

Do not enable verbose HTTP/auth debug logging in production: OAuth codes,
browser transaction state and upstream credentials are sensitive. The app
redacts credential-bearing diagnostic query URLs and disables Uvicorn access
logs; configure the tunnel's access logging to avoid OAuth query strings too.
Mocked automated tests cannot prove Google's real credentials, cookies through
Pangolin, or ChatGPT's workspace permissions. A real browser login and tool call
are required after deployment.

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[test,oauth]'
.venv/bin/pytest -q
```

The compose configuration test needs Docker Compose, but tests do not require a
running TeslaMateAPI. API calls are tested at the HTTP boundary with HTTPX.
Run locally with `MCP_BEARER_TOKEN`, `TESLAMATE_API_URL`, optional upstream token,
and `MCP_ALLOWED_ORIGINS` (comma-separated exact origins) in your environment:

```sh
.venv/bin/python -m teslamate_mcp
```

Treat telemetry, location history, the upstream API token, and MCP token as
private. Keep the existing API protected separately; this adapter does not
change its configuration.
