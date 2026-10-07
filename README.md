# simklcli

`simklcli` is a public command-line client for the core Simkl tracking loop:
authentication, catalog search and lookup, Library management, Watched State,
and User Ratings. The PyPI distribution exposes the shorter `simkl` command.

The CLI supports Simkl PIN Authorization, catalog search and lookup, Library
management, Watched State, and User Ratings. It reconciles account state online
and retains a dated Library Snapshot for explicit offline reads.

## Install

Python 3.11 or newer is required. Once the first release is published, install
the supported package with:

```sh
uv tool install simklcli
# or: pipx install simklcli
```

## Authentication

Register an **AUTH V2** application as **TV, devices & command line** at
[Simkl Developer Settings](https://simkl.com/settings/developer/). For local use,
set its public client ID before the first login:

```sh
export SIMKL_CLIENT_ID='your-v2-client-id'
uv sync --locked
uv run simkl auth login
uv run simkl auth status --check
uv run simkl search "Cowboy Bebop" --kind anime
uv run simkl library list
uv run simkl library list --offline
```

Login uses the V2 device-based PIN Authorization flow. Open the printed link
and approve the application on Simkl; no client secret, redirect URL, or PKCE
parameters are used by this flow. The V2 app ID is stored with its credential,
so later invocations can use it without the environment variable. An override
must match the app that issued the stored tokens.

```sh
simkl auth login
simkl auth status
simkl auth status --check
simkl auth logout
```

Login stores the Access Token and Refresh Token together in the OS keyring.
If the platform has no usable keyring, plaintext storage must be
selected explicitly with `simkl auth login --storage file`. Set
`SIMKL_ACCESS_TOKEN` for a non-persisted automation override; it takes
precedence over local credentials and requires its issuing V2 `SIMKL_CLIENT_ID`.
Environment overrides are not refreshed or persisted; supply a fresh Access
Token when it expires. The old embedded V1 client ID has been removed.

Access Tokens last seven days; the CLI refreshes shortly before expiry or once
after an explicit token rejection. Refresh Tokens last 180 days with sliding
expiry. Refresh and storage changes share an account lock across processes.
Search and External ID resolution require sign-in. An anonymous known-ID lookup
is available with `simkl lookup simkl:<id> --kind movie|show|anime`.

An existing V1 account reconnects through `simkl auth login` without first
logging out: approval must match the recorded account, and its Library Snapshot
is retained. A failed or cancelled login preserves the existing credentials.
Expired or revoked grants require explicit login and keep offline snapshots.
See the [migration guide](docs/auth-v2-migration.md) for local verification.

Logout removes local credentials and the Library Snapshot and makes a
best-effort request to revoke the stored V2 grant. `--local-only` skips that
request. Simkl acknowledges revocation without revealing whether a token was
valid; the CLI reports acknowledgement rather than proof of revocation.

Use `simkl auth status --json` for a single machine-readable stdout payload.
The default status check is local-only; `--check` explicitly validates the
Access Token with Simkl and refreshes the display name.

## Development

```sh
uv sync --locked
uv run ruff check .
uv run mypy src tests scripts
uv run pytest
uv build --no-sources
```

## Tracking

```sh
simkl search "Cowboy Bebop" --kind anime
simkl lookup imdb:tt0133093
simkl lookup tmdb:603 --kind movie
simkl library list --kind anime --status watching
simkl library set-status simkl:3708 plan-to-watch
simkl watched mark simkl:3708 --episode 1 --at 2026-08-08T20:00:00Z
simkl watched mark simkl:3708 --all --yes
simkl watched unmark simkl:3708 --episode 1
simkl rating set simkl:3708 9
simkl rating remove simkl:3708
simkl library remove simkl:3708 --yes
simkl library list --offline --json
simkl library repair
```

Media References accept `simkl:<id>`, supported provider-qualified External
IDs, or title text with optional `--kind` and `--year`. TMDB IDs require
`--kind movie` or `--kind show`. An ambiguous title returns candidates and fails
in scripts; interactive terminals can select a candidate. Search accepts
`--page` (1–20) and `--limit` (1–50 results per media kind).

Movies and standalone Anime take no Episode selectors. Shows require
`--season N --episode N`; episodic Anime uses `--episode N` without a Season.
Both accept `--all`; Shows also accept `--season N --all`. Bulk marks select
aired Episodes. `--episode-id tvdb:<id>` or `--episode-id anidb:<id>` accepts an
episode-level External ID with a parent Media Reference. When a response to
such a write is lost, parent state alone cannot prove which Episode changed,
so the CLI reports an unknown outcome instead of encouraging a blind retry.

`watched mark --rewatch` explicitly records another Viewing and requires
Simkl PRO or VIP. Use `--rewatch-id` to resume a specific known session.
Unmark Watched preserves Library membership, List Status, and User Rating;
`library remove` deletes all three along with Watched State.

Every command accepts `--json`: stdout contains one JSON result, diagnostics
go to stderr, and prompts are disabled. Whole-item removal, bulk updates, and
logout require `--yes` in JSON or noninteractive use. User Ratings range from
1 to 10 and apply to Movies, Shows, and Anime.

Environment-token accounts can read their snapshots offline after one online
Library read validates their identity. Only a token fingerprint and account ID
are retained for that association; the Access Token is never persisted.

Online Library reads check activities, merge changed List Status and rating
state, and compare IDs when removals occur. Offline reads require a complete
snapshot and report its age. Focused writes work before that first complete
read. A successful remote write followed by failed reconciliation reports
success and dirty local state; the next online read reconciles it. No writes
are queued or automatically replayed.

## Releases

The [release guide](docs/releasing.md) describes the mandatory live smoke,
restoration backup, GitHub environments, PyPI Trusted Publishing, and version
tag checks. Merge-gating tests use authored HTTPX responses and never contact
Simkl; the manually triggered smoke is the only live test workflow.

## Project map

- [`docs/spec.md`](docs/spec.md) — build-ready v1 product and engineering specification
- [`CONTEXT.md`](CONTEXT.md) — canonical domain vocabulary
- [`docs/adr/`](docs/adr/) — architectural decisions
- [`docs/research/`](docs/research/) — cited API and prior-art research
- [`src/simklcli/`](src/simklcli/) — installable tracking CLI
- [`tests/`](tests/) — offline unit, HTTPX transport, and Typer CLI tests
- [`prototypes/command-surface/`](prototypes/command-surface/) — runnable Typer command-surface prototype

Run the prototype from the repository root:

```sh
./prototypes/command-surface/run surface
./prototypes/command-surface/run --help
```

## License

MIT. See [`LICENSE`](LICENSE).
