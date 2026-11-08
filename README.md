# simklcli

`simklcli` is a command-line client for [Simkl](https://simkl.com/): search
the catalog, manage your Library, mark Episodes and Movies watched, and set
User Ratings. It keeps a dated Library Snapshot so you can read your Library
offline. The PyPI package installs the `simkl` command.

## Install

Python 3.11 or newer is required. Install the published package from
[PyPI](https://pypi.org/project/simklcli/) with:

```sh
uv tool install simklcli
# or: pipx install simklcli
```

Upgrade with `uv tool upgrade simklcli` (or `pipx upgrade simklcli`), and check
the installed version with `simkl --version`.

## Quick start

```sh
simkl auth login                          # approve simklcli on simkl.com
simkl search "Cowboy Bebop" --kind anime
simkl library set-status simkl:3708 watching
simkl watched mark simkl:3708 --episode 1
simkl library list --status watching
```

Every command has `--help`.

## Authentication

`simkl auth login` uses Simkl's PIN Authorization: it prints a link and a short
code, and opens your browser when it can. Approve the application on Simkl and
the CLI finishes signing in. No developer registration, client secret, or
redirect URL is needed.

```sh
simkl auth login
simkl auth status            # local only, no network
simkl auth status --check    # validate with Simkl and refresh the display name
simkl auth logout
```

Credentials are stored in the OS keyring. If your platform has no usable
keyring, choose plaintext-file storage explicitly with
`simkl auth login --storage file`.

Access Tokens are refreshed automatically. If a login expires or is revoked,
run `simkl auth login` again with the same account: your Library Snapshot is
kept, and a failed or cancelled login leaves the existing credentials in place.
To switch accounts, log out first.

`simkl auth logout` removes local credentials and the Library Snapshot and asks
Simkl to revoke the grant. Use `--local-only` to skip the revocation request.

Search and External ID lookups require sign-in. Without signing in you can
still look up a known Simkl ID:
`simkl lookup simkl:<id> --kind movie|show|anime`.

### Using your own Simkl app

The CLI ships with simklcli's public client ID. To use your own app instead,
register an **AUTH V2** application of type **TV, devices & command line** in
[Simkl Developer Settings](https://simkl.com/settings/developer/) and set
`SIMKL_CLIENT_ID` to its client ID before logging in. Stored credentials
remember which app issued them, so later commands don't need the variable; a
different `SIMKL_CLIENT_ID` is rejected until you log out.

### Automation

Set `SIMKL_ACCESS_TOKEN` to use an Access Token without storing it. It takes
precedence over a stored login and must come from the built-in app or the app
named by `SIMKL_CLIENT_ID`. It is never refreshed or written to disk, so supply
a fresh token when it expires (after seven days).

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
```

### Media References

Commands accept a Media Reference:

- a Simkl ID: `simkl:3708`;
- an External ID such as `imdb:`, `tmdb:`, `tvdb:`, `mal:`, `anidb:`,
  `anilist:`, `kitsu:`, `letterboxd:`, or `trakt:`. TMDB IDs require
  `--kind movie` or `--kind show`;
- title text, optionally narrowed with `--kind` and `--year`.

An ambiguous title lists candidates. Interactive terminals let you pick one;
scripts fail instead of guessing. `search` accepts `--page` (1–20) and
`--limit` (1–50 results per media kind).

### Episodes

- Movies and standalone Anime take no Episode options.
- Shows take `--season N --episode N`, `--season N --all`, or `--all`.
- Episodic Anime take `--episode N` (no Season) or `--all`.
- `--episode-id tvdb:<id>` or `--episode-id anidb:<id>` selects an Episode by
  External ID within the given parent.

Bulk marks (`--all`) cover aired Episodes only. If a write's outcome can't be
confirmed, for example because the connection dropped, the CLI says the outcome
is unknown and prints how to verify it. Don't retry blindly.

### Watched State, List Status, and ratings

- `watched mark --rewatch` records another Viewing and requires Simkl PRO or
  VIP. `--rewatch-id` resumes a known Rewatch session.
- `watched unmark` keeps the item in your Library with its List Status and
  User Rating. `library remove` deletes the item along with its Watched State
  and User Rating.
- Simkl treats a Completed Movie as watched, so `watched unmark` refuses that
  case. Move the Movie to `plan-to-watch` or `dropped` with
  `library set-status` instead; its User Rating is kept.
- User Ratings range from 1 to 10 and apply to Movies, Shows, and Anime.

### Offline Library

`simkl library list` syncs with Simkl and updates the local Library Snapshot.
`simkl library list --offline` reads that snapshot without network access and
reports its age; it needs one complete online read first. If a write succeeds
but the snapshot update fails, the CLI says so and the next online read catches
up. `simkl library repair` rebuilds the snapshot from Simkl. Writes are never
queued for later.

## Scripting

Every command accepts `--json`: stdout contains exactly one JSON result,
diagnostics go to stderr, and prompts are disabled. Whole-item removal, bulk
updates, and logout need `--yes` in JSON mode or when not attached to a
terminal.

```sh
simkl auth status --json
simkl library list --offline --json
```

## Local data

| | Linux | macOS | Windows |
| --- | --- | --- | --- |
| Account metadata (`auth.json`) | `~/.config/simklcli/` | `~/Library/Application Support/simklcli/` | `%LOCALAPPDATA%\simklcli\` |
| Library Snapshots | `~/.local/share/simklcli/` | `~/Library/Application Support/simklcli/` | `%LOCALAPPDATA%\simklcli\` |

Tokens live in the OS keyring unless you chose `--storage file`, in which case
they are stored in `auth.json` with owner-only permissions where the platform
supports them. `simkl auth logout` deletes both the credentials and the
snapshot.

## Development

```sh
uv sync --locked
uv run ruff check .
uv run ruff format --check src tests scripts
uv run mypy src tests scripts
uv run pytest
uv build --no-sources
```

From a checkout, run the CLI with `uv run simkl …`. To run your checkout as
the global `simkl` command, install it in editable mode; code changes apply
without reinstalling. Uninstall it before installing the PyPI release, since
both provide `simkl`.

```sh
uv tool install --editable .   # run this checkout as `simkl`
uv tool uninstall simklcli     # remove it again
```

Merge-gating tests use authored HTTPX responses and never contact Simkl. See
[local verification](docs/local-verification.md) for signing in from a
checkout and running the live smoke against your own account.

## Releases

The [release guide](docs/releasing.md) covers the mandatory live smoke,
restoration backup, GitHub environments, PyPI Trusted Publishing, and version
tag checks. The manually triggered smoke is the only live test workflow.

## Project map

- [`docs/spec.md`](docs/spec.md): product and engineering specification
- [`CONTEXT.md`](CONTEXT.md): domain vocabulary
- [`docs/adr/`](docs/adr/): architectural decisions
- [`docs/research/`](docs/research/): cited API and prior-art research
- [`src/simklcli/`](src/simklcli/): the CLI
- [`tests/`](tests/): offline unit, HTTPX transport, and Typer CLI tests
- [`scripts/live_smoke.py`](scripts/live_smoke.py): live pre-release smoke

## License

MIT. See [`LICENSE`](LICENSE).
