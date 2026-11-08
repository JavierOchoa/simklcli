# Local verification

How to sign in from a checkout and verify the CLI against your own Simkl
account, including the live smoke.

1. simklcli ships its public AUTH V2 app ID (type **TV, devices & command
   line**), so no registration is needed. To test against your own app, create
   an **AUTH V2** app at
   [Simkl Developer Settings](https://simkl.com/settings/developer/) with that
   type and set `SIMKL_CLIENT_ID` to its public ID; on PowerShell, use
   `$env:SIMKL_CLIENT_ID = 'your-client-id'`. This type has no client secret
   or redirect URL.
2. Run from the checkout:

   ```sh
   uv sync --locked
   uv run simkl auth login
   ```

   Open the printed approval link and select Allow while signed into the
   account you intend to use. Credentials default to the OS keyring; choose
   `--storage file` explicitly only when a usable keyring is unavailable.
3. Login refuses to replace a working stored login. An expired or revoked login
   reconnects the same account and keeps its Library Snapshot; a different
   account is rejected before replacing any local state. Use explicit logout
   first only when you intend to switch accounts; logout deletes the prior
   snapshot.
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
   your stored credential or a fresh `SIMKL_ACCESS_TOKEN` override. It writes
   `smoke-backup.json` before modifying the Movie and produces a success receipt
   only after restoring and verifying it. A process kill or API outage can
   interrupt restoration; recover from the backup before trying again.

   Simkl couples Completed Movies to Watched State. The smoke explicitly moves
   the Movie back to Plan to Watch to clear Watched State and checks that its
   User Rating remains intact. The subsequent Unmark Watched call checks the
   already-unwatched case; unmarking a Completed Movie directly is rejected
   before any write. Episode unmarking is covered by the offline tests.
   Each run clears its prior success receipt, so a failed run cannot leave an
   earlier success looking current.

The Access Token lasts seven days; its paired Refresh Token lasts 180 days,
sliding on use. Refresh does not rotate the Refresh Token, but invalidates the
old Access Token immediately. The CLI locks and rereads shared credentials to
prevent competing refreshes. `auth status` and offline reads never refresh.

Search and External ID resolution need sign-in. An anonymous known-ID
lookup requires both `simkl:<id>` and `--kind`. A token belongs to its issuing
client ID; mismatched overrides fail before using it. Environment overrides
do not persist or refresh and never borrow stored refresh credentials.

PIN polling handles pending approval and `slow_down`, with a local deadline.
The device flow uses its private device code and does **not** use PKCE. Tokens
and device codes are never printed. The browser authorization-code flow, which
this CLI does not use, requires PKCE.

For local logout without a remote request, use `simkl auth logout --local-only`.
Normal logout also requests grant revocation; Simkl's acknowledgement is
reported without claiming proof of revocation. Neither mode removes an
environment variable.

References: [device PIN flow](https://api.simkl.org/api-reference/oauth2-device),
[token lifecycle](https://api.simkl.org/api-reference/oauth2-tokens),
[scope rules](https://api.simkl.org/api-reference/oauth2-scopes).
