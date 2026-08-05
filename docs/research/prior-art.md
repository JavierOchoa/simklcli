# Prior art: Simkl CLI tools and API wrappers

Research date: 2026-08-05. Question source: GitHub issue #3 (verbatim):

> What prior art exists for a Simkl CLI? Search GitHub, PyPI, and npm for existing Simkl CLI tools and API wrappers (any language). For each: what it covers (auth flow used, command set), stars/maintenance state, and what is worth borrowing or differentiating from. Also record whether the PyPI names `simklcli` and `simkl` appear taken.

Scope: existing Simkl CLI tools and Simkl API wrappers in any language, found via GitHub search (`simkl`, `simkl cli`, `simkl api`, `simkl` + Python filter), the npm registry search API, the PyPI JSON API, and crates.io. Star counts and dates are as observed on 2026-08-05.

## TL;DR

- The Simkl-specific CLI space is nearly empty. Exactly one purpose-built general Simkl CLI exists: **bjarkehs/simkl-cli** (TypeScript, 3 stars, first commits January 2026).
- No Python Simkl CLI and no general-purpose Python Simkl API wrapper exists on PyPI at all.
- PyPI names **`simklcli` and `simkl` are both available** (HTTP 404 from the PyPI JSON API).
- Simkl's official API docs explicitly endorse a **PIN authentication flow "for TVs and CLIs"**; both the existing CLI and the official Kodi add-on use it.

## PyPI name availability

Checked 2026-08-05 against the PyPI JSON API (`https://pypi.org/pypi/<name>/json`); HTTP 404 from this endpoint means the name is unregistered.

| Name | Status | Evidence |
|---|---|---|
| `simklcli` | **Available** (404) | <https://pypi.org/pypi/simklcli/json> |
| `simkl` | **Available** (404) | <https://pypi.org/pypi/simkl/json> |

For context, these adjacent Python names are also unregistered (all 404): `pysimkl`, `simkl-api`, `simkl-python`, `py-simkl`, `simkl-wrapper`. The only Simkl-related package found on PyPI is `simkl-mps` (a scrobbler, see below). PyPI's HTML search page (`https://pypi.org/search/?q=simkl`) could not be scraped directly — it returns a JavaScript "Client Challenge" page to non-browser clients — so the per-name JSON API checks above are the authoritative evidence.

## CLI tools

### bjarkehs/simkl-cli — the one direct prior art

- URL: <https://github.com/bjarkehs/simkl-cli>; npm: <https://www.npmjs.com/package/simkl-cli>
- TypeScript (Bun/Node ≥ 18), MIT license. Installs a `simkl` binary (`bin: { "simkl": "dist/cli.js" }`, per npm registry metadata); built with commander, chalk, conf.
- Maintenance: created 2026-01-10, last push 2026-02-22; npm versions 0.1.0 (2026-01-10) and 0.3.1 (2026-02-22). Young, early-stage, but not abandoned as of research date. 3 GitHub stars.
- Auth flow: **Simkl PIN flow**. Setup is `simkl config --client-id YOUR_CLIENT_ID`, then `simkl auth` which directs the user to <https://simkl.com/pin> to enter a code (per the project README).
- Command set (from README):
  - `simkl search "<title>"` with `--type movie|show|anime` and `--json` output
  - `simkl watchlist` with `--type` / `--status` filters
  - `simkl watch "<title>" 1x05 | S01E05 | 5` — mark episodes watched, incl. comma lists (`1x05,1x06`), ranges (`1-5`), `--movie` for films, `--imdb tt1234567` for ID-based marking
  - `simkl list --title ... --status plantowatch|watching|...` — add items to lists
  - `simkl unwatch --title ... --season N --episodes M` — remove from watched history
  - `simkl config --show | --path`
  - API types are generated from an OpenAPI spec (`bun run generate:types`).
- Worth borrowing: the command grammar is close to a proven tracker-CLI shape — flexible episode syntax (`1x05`, `S01E05`, ranges), `--imdb` escape hatch, `--json` flag on search. PIN auth confirms the CLI-appropriate flow.
- Differentiation angles: it is Node/Bun-based (requires a JS runtime; npm install), pre-1.0 (0.3.1), and has minimal adoption (3 stars). No Python/pipx-native alternative exists.

### szaqalek/MovieBuddy — Python CLI, discovery-only

- URL: <https://github.com/szaqalek/MovieBuddy> (video demo linked in README)
- Python (requests, figlet, tabulate), interactive menu-driven CLI. 1 star, last push 2024-12-05 — appears to be a one-off course project; unmaintained.
- Auth: none (no OAuth). Uses only a Simkl **client ID / API key** read from a local `env.py`, i.e. purely public metadata endpoints.
- Coverage: search movies by name (results in a table), "upcoming releases" list, random/top-500-by-genre movie discovery. No watchlist, no mark-as-watched, no ratings.
- Worth borrowing: confirms Simkl's search/discovery endpoints are usable with just a client ID — an unauthenticated fallback mode is feasible. The interactive-menu UX itself is not a model for a pipx-style CLI.

### One-off export/migration scripts (Python, CLI-shaped but single-purpose)

- BurN-30/simkl-plex-tautulli-to-letterboxd — <https://github.com/BurN-30/simkl-plex-tautulli-to-letterboxd> — 0 stars, Jan–Feb 2026. Exports Simkl (or Plex/Tautulli) history to Letterboxd CSV.
- NilSilva/simklExporter — <https://github.com/NilSilva/simklExporter> — 21 stars, last push 2023-08-19. Export-focused.
- Josephhouma/Sim — <https://github.com/Josephhouma/Sim> — 0 stars, Apr 2025. Extracts Simkl lists to CSV for migration.
- MrMimeDanceTime/simkl-json-export — <https://github.com/MrMimeDanceTime/simkl-json-export> — 1 star, Dec 2025.

These confirm demand for data export but none implements the core tracking loop (auth + search + mark-as-watched + watchlist + ratings).

### daviddavo/trackl — abandoned Python tracker (2016)

- URL: <https://github.com/daviddavo/trackl> — 1 star, **archived**, last push 2016-10-29. README states "THIS APP IS NOT AVAILABLE FOR USE YET"; a GTK/daemon scrobbler forked from trackma ideas. Historically notable only as evidence the idea predates everything else; nothing to borrow.

## API wrappers (any language)

| Project | Lang | Stars | Last activity (2026-08-05) | Notes |
|---|---|---|---|---|
| <https://github.com/dvcol/simkl-http-client> (npm `@dvcol/simkl-http-client`) | TypeScript | 2 | Last npm publish 1.1.8 on 2024-08-26 | Fetch-based typed HTTP client covering request/response types for the Simkl API. Same author maintains analogous Trakt/TMDB clients. The most complete typed wrapper found. |
| <https://github.com/noelrohi/simkl-node> (npm `simkl-node` 0.2.3) | TypeScript | 1 | Last push/publish 2024-05-02 | Small Node wrapper for api.simkl.com. |
| <https://github.com/vankasteelj/simkl-api> | JavaScript (Node) | 1 | Single commit 2025-05-18 | Skeleton; README is one line ("NodeJS API wrapper for Simkl.com"). Not published to npm (`simkl-api` on npm is 404). |
| <https://github.com/srgsf/simkl-client> | Java | 2 | Last push 2020-08-26 | Retrofit2 + Moshi wrapper. Abandoned. |
| <https://codeberg.org/slundi/simkl> (crate [`simkl` v0.0.1](https://crates.io/crates/simkl); GH mirror <https://github.com/slundi/simkl>) | Rust | 0 (mirror) | Crate published ~May 2026 (per crates.io "3 months ago"); mirror push 2025-08-24 | Serde data structures for parsing Simkl API responses; 400 all-time crate downloads. |

npm name checks (2026-08-05, registry API): `simkl-cli` is **taken** (by bjarkehs, above); `simkl` and `simkl-api` return 404 (unregistered).

**No Python API wrapper exists on PyPI** (verified: `simkl`, `simklcli`, `pysimkl`, `simkl-api`, `simkl-python`, `py-simkl`, `simkl-wrapper` all 404) and none surfaced on GitHub search. Any Python CLI must build its own client layer; the Python reference code that exists lives inside consumer apps (below).

## Python consumer apps worth studying for API usage (not CLIs)

- **SIMKL/script.simkl** — <https://github.com/SIMKL/script.simkl> — the official Kodi add-on, Python, 57 stars, active (last push 2025-11-09). First-party reference implementation of Simkl auth and scrobbling from Python.
- **ByteTrix/Media-Player-Scrobbler-for-Simkl** — <https://github.com/ByteTrix/Media-Player-Scrobbler-for-Simkl> — Python, GPL-3.0, 62 stars, very active (last push 2026-07-27). Cross-platform automatic scrobbler (VLC/MPV/PotPlayer/MPC-HC) with offline queueing and filename parsing. Distributed **on PyPI as [`simkl-mps`](https://pypi.org/project/simkl-mps/) (v2.4.1, released 2026-07-03) with `pipx install "simkl-mps[linux]"` instructions** — proof the pipx distribution path works for a Simkl Python tool. It is a tray/background app, not an interactive CLI; GPL-3.0 license means its code cannot be reused in a permissively-licensed project, but its architecture (offline queue, player-title parsing) is worth studying.
- **srevinsaju/simkl-mcp** — <https://github.com/srevinsaju/simkl-mcp> — TypeScript MCP server for Simkl (Cloudflare Workers + Bun), 8 stars, created 2025-12-26. Uses OAuth with redirect URI rather than PIN; shows an alternative auth deployment.
- The Plex/Jellyfin/Emby ↔ Simkl sync ecosystem (Python): cenodude/CrossWatch (728 stars, active) <https://github.com/cenodude/CrossWatch>, Drakonis96/plexytrack (60 stars) <https://github.com/Drakonis96/plexytrack>, and others — bulk-sync tools, not interactive CLIs.

## Official API facts relevant to auth and coverage

- Official docs: <https://api.simkl.org/> (GitHub repo SIMKL/API, API Blueprint, 41 stars, active — last push 2026-05-22: <https://github.com/SIMKL/API>; legacy Apiary mirror: <https://simkl.docs.apiary.io/>).
- Auth (<https://api.simkl.org/authentication>): three documented flows — **OAuth 2.0** (server-side web apps with a client secret), **Public PKCE** (mobile/SPA/browser extensions), and **PIN ("for TVs and CLIs")**. Tokens are long-lived: docs advertise `expires_in: 157680000` (5 years) and validity until the user revokes the app. bjarkehs/simkl-cli uses the PIN flow; the official Kodi add-on also authenticates through Simkl's device/PIN mechanism.
- Sync model (<https://api.simkl.org/>): two-phase sync — check `/sync/activities` timestamps, then fetch only changed items via `/sync/all-items/` with `date_from`. The docs state the sync guide ships "reference implementations in Node and Python."

## Borrow / differentiate observations (facts, not recommendations)

1. **bjarkehs/simkl-cli already validates the v1 command surface** — search with type filters, watchlist with status filters, `watch` with S01E05/range/multi-episode grammar, list management, unwatch, config — and the PIN auth flow. Its README is the closest existing spec for this exact scope. It is TypeScript/npm, pre-1.0, 3 stars.
2. **PIN is the officially sanctioned CLI auth flow** per Simkl's own docs, and both the existing CLI and the first-party Kodi add-on use it; OAuth-with-secret and PKCE are documented as alternatives for other app types.
3. **The Python gap is total**: no Python CLI, no Python wrapper on PyPI, and the names `simkl` / `simklcli` are unregistered. The only active Python Simkl projects are consumer apps (official Kodi add-on; GPL-licensed simkl-mps), which double as the only Python API-usage references and as evidence that pipx distribution of a Simkl tool works in practice.
