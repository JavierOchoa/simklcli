# simklcli

`simklcli` is a planned public command-line client for the core Simkl tracking
loop: authentication, catalog search and lookup, Library management, Watched
State, and User Ratings. The PyPI distribution will expose the shorter `simkl`
command.

The project is currently design-complete and ready for implementation. No code
in this repository changes a real Simkl account yet; the command-surface
prototype is deliberately mock-only.

## Project map

- [`docs/spec.md`](docs/spec.md) — build-ready v1 product and engineering specification
- [`CONTEXT.md`](CONTEXT.md) — canonical domain vocabulary
- [`docs/adr/`](docs/adr/) — architectural decisions
- [`docs/research/`](docs/research/) — cited API and prior-art research
- [`prototypes/command-surface/`](prototypes/command-surface/) — runnable Typer command-surface prototype

Run the prototype from the repository root:

```sh
./prototypes/command-surface/run surface
./prototypes/command-surface/run --help
```

The planned supported installation path is `uv tool install simklcli`, with
`pipx install simklcli` as an alternative after the first release.

## License

MIT. See [`LICENSE`](LICENSE).
