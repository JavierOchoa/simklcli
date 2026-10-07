# AUTH V2 migration and local verification

Simkl's AUTH V1 registrations are deprecated, with retirement expected around
April 2027 (date not fixed). Register a separate V2 app and authorize it once:
V1 client IDs and tokens cannot be upgraded or exchanged.

1. Open [Simkl Developer Settings](https://simkl.com/settings/developer/) and
   create an AUTH V2 app named `simklcli` with type **TV, devices & command line**.
   This type has no client secret or redirect URL. Keep your V1 app registered
   while you verify the new connection.
2. Set the new public ID and run from the checkout:

   ```sh
   export SIMKL_CLIENT_ID='your-v2-client-id'
   uv sync --locked
   uv run simkl auth login
   ```

   On PowerShell, use `$env:SIMKL_CLIENT_ID = 'your-v2-client-id'`.
   Open the printed approval link and select Allow while signed into the
   account you intend to use. Credentials default to the OS keyring; choose
   `--storage file` explicitly only when a usable keyring is unavailable.
3. If a V1 credential is present, login reconnects that same account and keeps
   its Library Snapshot. A different account is rejected before replacing any
   local state. Use explicit logout first only when you intend to switch
   accounts; logout deletes the prior snapshot.
4. Verify read-only commands first:

   ```sh
   uv run simkl auth status --check
   uv run simkl search 'The Matrix' --kind movie --year 1999
   uv run simkl lookup imdb:tt0133093
   uv run simkl library list --json
   uv run simkl library list --offline --json
   ```

   The online Library read creates or reconciles the complete snapshot. The
   offline read must succeed without network activity and report its age.
5. For writes, choose one reserved Movie and record its original membership,
   List Status, User Rating, Watched State, and Last Watched At. Use a clean
   item with no Rewatch sessions. The local smoke script makes writes and
   restores that captured state:

   ```sh
   export SIMKL_SMOKE_ACCOUNT_ID='your-numeric-account-id'
   export SIMKL_SMOKE_MEDIA_REFERENCE='simkl:your-reserved-movie-id'
   uv run python scripts/live_smoke.py
   ```

   `auth status --check --json` supplies the numeric account ID. The smoke uses
   your stored V2 credential or a fresh `SIMKL_ACCESS_TOKEN` override. It writes
   `smoke-backup.json` before modifying the Movie and produces a success receipt
   only after restoring and verifying it. A process kill or API outage can
   interrupt restoration; recover from the backup before trying again.

The Access Token lasts seven days; its paired Refresh Token lasts 180 days,
sliding on use. Refresh does not rotate the Refresh Token, but invalidates the
old Access Token immediately. The CLI locks and rereads shared credentials to
prevent competing refreshes. `auth status` and offline reads never refresh.

Search and External ID resolution need sign-in on V2. An anonymous known-ID
lookup requires both `simkl:<id>` and `--kind`. A token belongs to its issuing
client ID; mismatched overrides fail before using it. Environment overrides
do not persist or refresh and never borrow stored refresh credentials.

PIN polling handles pending approval and `slow_down`, with a local deadline.
The device flow uses its private device code and does **not** use PKCE. Tokens
and device codes are never printed. The browser authorization-code flow, which
this CLI does not use, requires PKCE.

For local logout without a remote request, use `simkl auth logout --local-only`.
Normal V2 logout also requests grant revocation; Simkl's acknowledgement is
reported without claiming proof of revocation. Neither mode removes an
environment variable.

References: [official migration guide](https://api.simkl.org/guides/migrating-v1-to-v2),
[device PIN flow](https://api.simkl.org/api-reference/oauth2-device),
[token lifecycle](https://api.simkl.org/api-reference/oauth2-tokens),
[scope rules](https://api.simkl.org/api-reference/oauth2-scopes).
