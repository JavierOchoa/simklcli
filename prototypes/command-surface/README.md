# PROTOTYPE — `simkl` command surface

Throwaway Typer stub for deciding the public CLI grammar. It makes no network
requests, stores no credentials, and never changes a Simkl account.

Run it from the repository root:

```sh
./prototypes/command-surface/run --help
```

The launcher creates a disposable Python environment under the system temporary
directory on first run. If `uv` is installed, this works too:

```sh
uv run prototypes/command-surface/app.py --help
```

Useful reactions:

```sh
./prototypes/command-surface/run search "Cowboy Bebop" --kind anime
./prototypes/command-surface/run lookup simkl:3708 --json
./prototypes/command-surface/run library list --status plan-to-watch
./prototypes/command-surface/run library set-status simkl:3708 completed
./prototypes/command-surface/run watched mark simkl:3708 --episode 1 --at 2026-08-08T20:00:00Z
./prototypes/command-surface/run watched unmark simkl:3708
./prototypes/command-surface/run rating set simkl:3708 10
```

`watched mark` and `watched unmark` intentionally accept the same target
selectors. For standalone items, unmarking preserves Library membership, List
Status, and User Rating; the eventual implementation hides and verifies Simkl's
multi-request workaround.

The design hypothesis is documented by `surface`:

```sh
./prototypes/command-surface/run surface
```
