# simklcli

`simklcli` is a public command-line client for the core Simkl tracking loop:
authentication, catalog search and lookup, Library management, Watched State,
and User Ratings. The PyPI distribution exposes the shorter `simkl` command.

The first implementation slice provides Simkl PIN Authorization and the local
Authenticated Account lifecycle. The remaining tracking commands are still
represented only by the deliberately mock-only command-surface prototype.

## Install

Python 3.11 or newer is required. Once the first release is published, install
the supported package with:

```sh
uv tool install simklcli
# or: pipx install simklcli
```

## Authentication

```sh
simkl auth login
simkl auth status
simkl auth status --check
simkl auth logout
```

Login uses Simkl PIN Authorization and stores one Access Token in the OS
keyring. If the platform has no usable keyring, plaintext storage must be
selected explicitly with `simkl auth login --storage file`. Set
`SIMKL_ACCESS_TOKEN` for a non-persisted automation override; it takes
precedence over local credentials. `SIMKL_CLIENT_ID` may override the embedded
public client ID during development.

Use `simkl auth status --json` for a single machine-readable stdout payload.
The default status check is local-only; `--check` explicitly validates the
Access Token with Simkl and refreshes the display name.

## Development

```sh
uv sync --locked
uv run ruff check .
uv run mypy src tests
uv run pytest
uv build --no-sources
```

## Project map

- [`docs/spec.md`](docs/spec.md) — build-ready v1 product and engineering specification
- [`CONTEXT.md`](CONTEXT.md) — canonical domain vocabulary
- [`docs/adr/`](docs/adr/) — architectural decisions
- [`docs/research/`](docs/research/) — cited API and prior-art research
- [`src/simklcli/`](src/simklcli/) — installable authentication slice
- [`tests/`](tests/) — offline unit, HTTPX transport, and Typer CLI tests
- [`prototypes/command-surface/`](prototypes/command-surface/) — runnable Typer command-surface prototype

Run the prototype from the repository root:

```sh
./prototypes/command-surface/run surface
./prototypes/command-surface/run --help
```

## License

MIT. See [`LICENSE`](LICENSE).
