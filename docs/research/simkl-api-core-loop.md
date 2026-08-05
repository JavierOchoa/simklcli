# Simkl API — core tracking loop research

Factual findings for the simklcli core loop (auth, search/lookup, mark-as-watched, watchlist, ratings), extracted from Simkl's official API documentation. Research for GitHub issue #2.

**Date:** 2026-08-05

**Sources consulted:**

- https://api.simkl.org/llms-full.txt (full doc site flattened, ~15k lines — primary source for everything below)
- https://api.simkl.org/openapi.json (referenced by docs; exists, HTTP 200 — not analyzed in depth)
- https://simkl.docs.apiary.io/ (legacy Apiary docs — still online but a JavaScript-only app shell; content not server-rendered, not usable as a primary source. The current canonical docs are api.simkl.org.)

Individual section sources are cited at the end of each section.

---

## 1. OAuth2 flows available to a CLI

Simkl offers **three** token flows, all OAuth 2.0 variants. All require a `client_id` obtained by creating an app at `https://simkl.com/settings/developer/` (free, no approval). Confidential clients also get a `client_secret`; public clients can ignore it.

### 1.1 The three flows

| Flow | Target client | `client_secret` | `redirect_uri` |
|---|---|---|---|
| OAuth 2.0 authorization code | Server-side web apps | Yes | Yes (pre-registered, byte-for-byte match) |
| OAuth 2.0 + PKCE (RFC 7636) | Mobile, SPA, browser extensions, desktop binaries | No (`code_verifier`/`code_challenge`) | Yes, OR omit entirely if the app has no registered redirect URI (consent then completes on simkl.com) |
| PIN ("device flow") | TVs, consoles, watches, **CLIs**, media-server plugins | No | No |

The docs' platform table maps **"CLI tool, system service, daemon" → PIN**. PKCE with a localhost loopback redirect (`http://127.0.0.1:PORT/callback` — docs say use `127.0.0.1`, not `localhost`) is the documented option for desktop apps that can open a browser.

### 1.2 PIN flow (the CLI-recommended flow)

1. `GET https://api.simkl.com/oauth/pin?client_id=…` returns:
   ```json
   {
     "result": "OK",
     "device_code": "DEVICE_CODE",
     "user_code": "ABCDE",
     "verification_uri": "https://simkl.com/pin",
     "verification_url": "https://simkl.com/pin",
     "expires_in": 900,
     "interval": 5
   }
   ```
   - `user_code` is the 5-character code shown to the user; user enters it at `https://simkl.com/pin`.
   - `expires_in` = 900 s (15 min), `interval` = 5 s (poll cadence).
   - `device_code` is the **literal string `"DEVICE_CODE"`** — a placeholder for RFC 8628 response-shape compatibility, not a real token. Only `user_code` matters.
   - `verification_url` is an alias of `verification_uri`.
   - Optional `redirect` query param: URL the simkl.com/pin page sends the user to after approval; must be pre-registered. Docs say it's "mostly relevant for browser-extension and web-flavoured PIN integrations."
2. Poll `GET https://api.simkl.com/oauth/pin/{USER_CODE}?client_id=…` every `interval` seconds:
   - Pending: `{ "result": "KO", "message": "Authorization pending" }`
   - Approved: `{ "result": "OK", "access_token": "…" }`
   - After success the server deletes the code; further polls (or polls on any unknown code) fall through to the *create-a-new-code* branch and return a fresh init response with a new `user_code`. Docs instruct: treat any poll response containing `device_code` as "original code is gone, stop polling."
3. The PIN flow is **not RFC 8628-compliant** on the wire (placeholder `device_code`, polling via `GET /oauth/pin/{user_code}` instead of `POST /oauth/token` with the device-code grant, pending is `result: "KO"` not `400 authorization_pending`). Generic device-flow libraries won't work; a custom client is needed.

### 1.3 OAuth authorization-code flow (confidential and PKCE)

Two hosts, a documented common pitfall:

- **Authorize (browser):** `GET https://simkl.com/oauth/authorize?response_type=code&client_id=…&redirect_uri=…&state=…&app-name=…&app-version=…` — on host `simkl.com`, **not** `api.simkl.com`. PKCE clients add `code_challenge` + `code_challenge_method=S256` (`S256` or `plain`, case-sensitive).
- **Token exchange:** `POST https://api.simkl.com/oauth/token` with `grant_type=authorization_code`, `code`, `client_id`, and either `client_secret` + `redirect_uri` (confidential) or `code_verifier` (PKCE). Accepts both `application/x-www-form-urlencoded` and `application/json` bodies; client credentials may go in the body or in an `Authorization: Basic` header.
- Success response:
  ```json
  { "access_token": "…", "token_type": "bearer", "scope": "public", "expires_in": 157680000 }
  ```
- Token endpoint errors: `403 empty_field` (missing field), `403 redirect_failed` (redirect_uri mismatch), `401 secret_error` (wrong secret or PKCE verification failed), `401 grant_error` (code invalid/expired/used). 401s carry `WWW-Authenticate: Bearer realm="api.simkl.com", error="…"`.
- The authorization `code` is **single-use and short-lived** — any exchange attempt (success or failure) consumes it.
- User denial does **not** redirect back with `error=access_denied`; Simkl redirects to `/` on simkl.com. Docs advise treating "no callback within ~5 minutes" as denial.
- Mobile: embedded WebViews are blocked by Simkl's federated login providers; must use system browser / Custom Tab / ASWebAuthenticationSession.

### 1.4 Token lifecycle and scopes

- `expires_in: 157680000` (5 years in seconds) — docs call it "more of a sentinel than a refresh hint." **No refresh-token grant exists.** Tokens remain valid until the user revokes the app at `https://simkl.com/settings/connected-apps/`; after revocation all authenticated calls return `401 user_token_failed`.
- Re-running any flow for the same `(app, user)` pair returns the **same** `access_token` (no rotation).
- **Scopes:** every token returns `scope: "public"`. Docs state explicitly: "There's no granular permission system — every token grants every permission your app has been approved for." No other scope values are documented anywhere.

### 1.5 Every-request requirements

On **every** API call (public or authenticated):

- URL params: `client_id`, `app-name`, `app-version` (e.g. `?client_id=…&app-name=my-app-name&app-version=1.0`). `client_id` may alternatively be sent as the `simkl-api-key` header.
- Header: `User-Agent: <app-name>/<app-version>`.
- Authenticated calls add `Authorization: Bearer <access_token>`; POSTs add `Content-Type: application/json`.

Sources: https://api.simkl.org/api-reference/auth, https://api.simkl.org/api-reference/oauth, https://api.simkl.org/api-reference/oauth-pkce, https://api.simkl.org/api-reference/pin, https://api.simkl.org/api-reference/simkl/authorize, https://api.simkl.org/api-reference/simkl/exchange-token, https://api.simkl.org/api-reference/simkl/get-pin, https://api.simkl.org/api-reference/simkl/check-pin, https://api.simkl.org/authentication, https://api.simkl.org/conventions/headers, https://api.simkl.org/api-reference/introduction

---

## 2. Search / lookup

Base URL for everything: `https://api.simkl.com`.

### 2.1 Text search — `GET /search/{type}` (no auth)

- `{type}` ∈ `movie`, `tv`, `anime`.
- Query params: `q` (required; matches `title` and `all_titles[]`), `page` (default 1, hard cap 20), `limit` (default 10, hard cap 50), `extended=simple|full` (`full` adds `all_titles[]`, `url`, `ep_count`, `rank`, `status`, `ratings{simkl,imdb,mal}`).
- Response: paginated array with `X-Pagination-*` headers. Per-item fields: `title`, `year`, `poster` (path fragment), `ids` (`{ simkl_id, slug, tmdb? }`), `endpoint_type`. Quirk: searching `/search/movie` returns items with `endpoint_type: "movies"` (extra "s").
- Empty result = `200 []`; invalid page/limit silently clamp (no 400); `412 client_id_failed` on bad client_id; `500` on server error.

### 2.2 External-ID lookup — `GET /search/id` (no auth, but discouraged)

- Query param per ID, e.g. `?imdb=tt4574334`. Accepts IMDB, TMDB, TVDB, MAL, AniDB, AniList, Kitsu, anisearch, anime-planet, livechart, letterboxd, Netflix, Trakt slug.
- Returns type + title + poster + `ids` block.
- Docs warn repeatedly: **do not loop this endpoint** (hits origin every call, rate limits). The recommended alternative is two steps: `GET /redirect` (read `Location` header, parse Simkl ID) → cached detail endpoint (`GET /movies/{id}` etc.). Batch resolution >20 IDs: "contact us on Discord first."

### 2.3 ID resolution — `GET /redirect` (no auth)

- `301 Moved Permanently`; `Location` header points at the Simkl page (e.g. `https://simkl.com/tv/17465/…` → Simkl ID `17465`). Clients must **not follow** the redirect.
- Identifier params: `simkl`, `imdb` (ID or full URL), `tmdb` (**requires `type=movie` or `type=tv`** — TMDB IDs are not unique across types), `tvdb`, `mal`, `anidb`, `anilist`, `kitsu`, `livechart`, `anisearch`, `animeplanet`, `crunchyroll`, `netflix`, `hulu` (beta), `title`+`year`, `season`/`episode`, `type` (`movie`/`tv`/`anime`; `show` matches both tv and anime).
- `to=` action modes: `simkl` (default), `trailer`, `twitter`, `watched` (marks watched on the user's account — requires sign-in).

### 2.4 Cached detail endpoints (no auth; parallel calls explicitly allowed)

- `GET /movies/{simkl_id}` — full movie record.
- `GET /tv/{simkl_id}` — full show record.
- `GET /anime/{simkl_id}` — full anime record.
- `GET /tv/episodes/{simkl_id}` — full episode list (`season`, `episode`, `title`, `aired`, `ids.simkl_id`; specials with `type: "special"`).
- `GET /anime/episodes/{simkl_id}` — anime episode list (single canonical season per AniDB; `season` omitted on regular episodes).

All are Cloudflare-cached by Simkl ID with automatic server-side invalidation. Lookup is by Simkl ID only; external IDs must be resolved via `/redirect` first.

### 2.5 Filename lookup — `POST /search/file`

- Body: `{ "file": "Stranger.Things.S01E03.1080p.WEB.x264-GROUP.mkv", "part": 1, "hash": … }`. Built for desktop scrobblers identifying one playing file; **explicitly not for library scraping**.
- Response discriminated by top-level `type`: `"movie"` (→ `movie` block), `"show"` (→ `show` block), `"episode"` (→ `show` + `episode` blocks). No match returns `200` with `null` (malformed body) or `[]` (no parser match) — never 404.

### 2.6 Watched-status lookup — `POST /sync/watched` (auth)

- Body: array of items (any `ids` combo, or `title`+`year`; optional `season`+`episode` for episode-level checks). Returns a parallel array with `result` (`true` / `false` / `"not_found"`), `simkl`, `list` (current watchlist status or null), `last_watched_at`; `extended=episodes,specials,counters` adds per-season/per-episode breakdowns (100-item cap with `extended=episodes`, else `400 max_items`).
- Docs: use only when you don't cache the full library locally; if you pull `/sync/all-items`, filter client-side instead.

Sources: https://api.simkl.org/api-reference/simkl/search-by-text, https://api.simkl.org/api-reference/simkl/search-by-id, https://api.simkl.org/api-reference/simkl/search-by-file, https://api.simkl.org/api-reference/simkl/redirect, https://api.simkl.org/api-reference/simkl/get-movie, https://api.simkl.org/api-reference/simkl/get-tv-show, https://api.simkl.org/api-reference/simkl/get-anime, https://api.simkl.org/api-reference/simkl/get-tv-episodes, https://api.simkl.org/api-reference/simkl/get-anime-episodes, https://api.simkl.org/api-reference/simkl/get-watched

---

## 3. Mark-as-watched

### 3.1 `POST /sync/history` (auth) — the primary endpoint

Records watch events; unit is the watch event, not list membership. Body keys: `movies[]`, `shows[]`, `anime[]` (anime also accepted under `shows[]`).

Granularity is determined by body shape:

```json
{ "movies": [{ "ids": { "imdb": "tt1201607" } }] }                                  // one movie
{ "shows": [{ "ids": {…}, "status": "completed" }] }                                // whole show
{ "shows": [{ "ids": {…}, "seasons": [{ "number": 2 }] }] }                         // whole season
{ "shows": [{ "ids": {…}, "seasons": [{ "number": 1, "episodes": [{ "number": 1 }] }] }] }  // specific episodes
{ "shows": [{ "ids": {…}, "episodes": [{ "number": 1 }] }] }                        // shorthand → season 1
```

Per-item optional fields:

| Field | Notes |
|---|---|
| `watched_at` | ISO-8601; defaults to request time. `"1970-01-01T00:00:01Z"` is the documented "very long time ago / I don't remember" placeholder. |
| `added_at` | Override watchlist-added time (backups). |
| `status` | Set watchlist status in the same call. |
| `rating` | int 1–10; same effect as a separate `POST /sync/ratings`. |
| `memo` | `{ "text": ≤140 chars, "is_private": bool }`. **Only settable via this endpoint** (see §4 caveat). |
| `is_rewatch` | Force rewatch path; requires `?allow_rewatch=yes` query param. **Simkl Pro/VIP only** — free tier silently no-ops while consuming a rate-limit slot. |
| `use_tvdb_anime_seasons` | Anime only: interpret `season`/`number` as TVDB per-season numbering instead of AniDB sequential. |

Behavior notes:

- Re-posting an already-watched item is a **no-op by default** (duplicate detected and skipped); `?allow_rewatch=yes` creates a separate rewatch row (Pro/VIP).
- Show with no `seasons`/`episodes`/`status`: server picks based on airing status (finished → `completed` + all episodes marked; airing → `watching` + already-aired episodes; unreleased → `watching`, none marked). `?skip_auto_watching=yes` suppresses this when an explicit `status` is also sent.
- The call also moves/re-classifies the item on the watchlist; docs say **do not chain** a follow-up `/sync/add-to-list`.
- Memo-only update: send `ids` + `status` + `memo` (auto-adds the item to the watchlist if absent).

Response (201): `{ "added": { "movies": n, "shows": n, "episodes": n, "statuses": [{ "request": …, "response": { "status": …, "simkl_type": "tv"|"anime"|"movie", "anime_type": … } }] }, "not_found": { "movies": [], "shows": [], "episodes": [] } }`. `added.statuses[].response.status` is the **resolved** status (server may downgrade, e.g. `completed` on a still-airing show → `watching`); `not_found` echoes unmatched input verbatim. Docs: don't infer success from 201 alone — check `not_found`.

Errors: `400 empty_field` (missing per-item field), `400 wrong_parameter` (bad enum). Empty body `{}` returns **201 with zero counts**, not 400.

### 3.2 `POST /sync/history/remove` (auth)

Same body shape as `/sync/history`. Granularity: item with no seasons/episodes → **removed from the library entirely** (history + watchlist entry, and wipes the rating); seasons without episodes → unmark that season; with episodes → unmark those episodes only. Response 201: `{ "deleted": { "movies": n, "shows": n, "episodes": n }, "not_found": { "movies": [], "shows": [] } }` — note: **no `not_found.episodes`**, and no top-level `anime[]` accepted (anime goes in `shows[]`; items sent under `anime[]` are "silently ignored" per docs).

### 3.3 Scrobble endpoints (auth) — real-time playback only

| Endpoint | Purpose |
|---|---|
| `POST /scrobble/start` | Begin/resume a "watching now" session. Body: `progress` (float 0–100) + exactly one of `movie`, `show`+`episode`, `anime`+`episode`. Does **not** mark watched. Prior session ≥80% is auto-scrobbled. |
| `POST /scrobble/pause` | Save resumable progress (cross-device "Continue Watching"). Does not mark watched. |
| `POST /scrobble/stop` | Finalize: `progress ≥ 80` → `action: "scrobble"` (marked watched); `< 80` → `action: "pause"` (saved as playback). Duplicate stop within 1 hour → `409 Conflict` with `watched_at`/`expires_at`. |
| `POST /scrobble/checkin` | Fire-and-forget: item shows as "Watching now"; server extrapolates `(now − checkin) ÷ runtime` and auto-marks watched at 100% (finalization can lag 0–2 min). |

Episode identification: `episode.season` + `episode.number`, or `episode.ids` with **`tvdb` or `anidb` only** (takes precedence if both sent; episode-level imdb/tmdb do not exist on Simkl).

Errors: `400 empty_field`, `400 RATE_LIMIT` (20-second per-user scrobble lock collision — note HTTP 400, **not** 429), `401 user_token_failed`, `404 id_err`.

### 3.4 Which to use (per docs)

- "Mark as watched" button / import / manual logging → `POST /sync/history`.
- Real player events with exact progress → `/scrobble/start|pause|stop`.
- Can't hook pause/stop reliably → `/scrobble/checkin`.
- If already scrobbling, do **not** also call `/sync/history`.

Sources: https://api.simkl.org/api-reference/simkl/add-to-history, https://api.simkl.org/api-reference/simkl/remove-from-history, https://api.simkl.org/guides/mark-as-watched, https://api.simkl.org/api-reference/simkl/scrobble-start, https://api.simkl.org/api-reference/simkl/scrobble-pause, https://api.simkl.org/api-reference/simkl/scrobble-stop, https://api.simkl.org/api-reference/simkl/scrobble-checkin, https://api.simkl.org/api-reference/scrobble

---

## 4. Watchlist

### 4.1 Status model

Five statuses: `watching`, `plantowatch`, `hold`, `dropped`, `completed`. Per-type availability:

| Status | Movies | TV | Anime |
|---|:-:|:-:|:-:|
| `watching` | — | ✓ | ✓ |
| `plantowatch` | ✓ | ✓ | ✓ |
| `hold` | — | ✓ | ✓ |
| `dropped` | ✓ | ✓ | ✓ |
| `completed` | ✓ | ✓ | ✓ |

These are the built-in **Watchlist** buckets. User-curated **Custom Lists** (simkl.com/lists/) are **not available via the API** (V2 Beta backend, no public endpoint, no ETA).

### 4.2 Add/move — `POST /sync/add-to-list` (auth)

- Body: `movies[]` / `shows[]` / `anime[]` arrays; each item carries its own **`to`** field (`watching|plantowatch|hold|dropped|completed`) plus `ids` (any supported keys) or `title`+`year`. Optional per-item `watched_at`, `added_at`.
- The per-item `to` is required — docs state a legacy top-level `to` is **not accepted** (`400 empty_field`, `Missed "to" parameter`). (See Ambiguities — another docs page shows top-level `to` in examples.)
- **Silent rewrites:** movies with `to: "watching"` → `completed`; not-yet-completable shows → `watching` or `plantowatch`. Detect only by comparing sent vs returned `to`. No error code.
- **Memo caveat:** `memo` fields are accepted and echoed back but **silently not persisted**. Only `POST /sync/history` saves memos.
- Removal: use `POST /sync/history/remove`, not this endpoint. A legacy `to: "remove"` value exists but is undocumented and "should not be used in new integrations."
- Response: always **201** (even partial failure): `{ "added": { "movies": [{ "to": …, "ids": …, "type": "movie" }], "shows": […] }, "not_found": { "movies": […], "shows": [] } }`.
- Errors: `400 empty_field` (missing `to`), `400 wrong_parameter` (bad `to` value).

### 4.3 List/read — `GET /sync/all-items/{type}/{status}` (auth)

- Both path segments optional: `/sync/all-items` (full library), `/{type}` (`shows`/`movies`/`anime`), `/{type}/{status}`. Response keyed by `shows`/`movies`/`anime`; empty result = `{}`. `/sync/all-items/movies/watching` and `/movies/hold` are valid URLs that always return empty.
- Key query params: `date_from` (ISO-8601 delta), `extended=simkl_ids_only|ids_only|full|full_anime_seasons`, `include_all_episodes=yes|original`, `episode_watched_at=yes`, `episode_tvdb_id=yes`, `next_watch_info=yes`, `memos=yes`, `anime_type=movies`, `language=en`, `allow_rewatch=yes` (Pro/VIP).
- Items carry `user_rating` (1–10 or null) and `user_rated_at`.
- **Docs-mandated sync loop:** always call `GET /sync/activities` first (per-domain/per-status last-modified timestamps); only call `/sync/all-items?date_from=…` when a timestamp moved. Polling `/sync/all-items` on a timer without gating gets the `client_id` **suspended** ("No warning, no appeal"). Removals aren't surfaced by `date_from` — detect via `removed_from_list` timestamp + `extended=simkl_ids_only` + local diff.

### 4.4 `GET /sync/activities` (auth)

- Returns `all`, `settings.all`, and per-domain (`tv_shows`, `anime`, `movies`) timestamp groups with per-status fields plus `rated_at`, `playback`, `removed_from_list`. Movies lack `watching`/`hold` keys. Described as "the cheapest call in the API."

Sources: https://api.simkl.org/conventions/list-statuses, https://api.simkl.org/api-reference/simkl/add-to-list, https://api.simkl.org/api-reference/simkl/get-all-items, https://api.simkl.org/api-reference/simkl/get-activities, https://api.simkl.org/guides/custom-lists, https://api.simkl.org/guides/sync

---

## 5. Ratings

### 5.1 Write — `POST /sync/ratings` (auth)

- Body: `movies[]` / `shows[]` / `anime[]` arrays; per item: `rating` (int 1–10, required), `ids` (any supported keys; `title`+`year` fallback), optional `rated_at` (ISO-8601, defaults to now).
- Re-rating **overwrites** — no remove-first needed.
- **Out-of-range ratings (0, 11, negatives) are silently rejected**: item lands in `not_found`, HTTP still 201. Client-side validation required.
- **Auto-move side effect:** rating an unlisted item files it — released movie → `completed`, unreleased movie → `plantowatch`, single-episode show → `completed`, other shows/anime → `watching` — and bumps the list timestamp in `/sync/activities`.
- Response 201: `{ "added": { "movies": n, "shows": n, "statuses": [{ "request": …, "response": { "status": … } }] }, "not_found": { "movies": [], "shows": [] } }`. **Anime folds into `shows`** — no `added.anime` / `not_found.anime` keys.
- Alternative: rate in the same call as a watch event via `POST /sync/history`'s per-item `rating` field.

### 5.2 Remove — `POST /sync/ratings/remove` (auth)

- Same body shape minus `rating`. Response 201: `{ "deleted": { "movies": n, "shows": n }, "not_found": { "movies": [], "shows": [] } }`. Counts matched items even if never rated (idempotent). Does **not** remove the item from the watchlist.

### 5.3 Read

- `GET /sync/ratings` (auth, implied by the add-ratings page): all rated items keyed by `movies`/`shows`/`anime`, each with `user_rating` and `user_rated_at`.
- `GET /sync/ratings/{type}/{rating}` (auth): `type` ∈ `movies`/`shows`/`anime`; `rating` = single 1–10 or CSV (`8,9,10`). Supports `extended`, `date_from`, `episode_watched_at`, `memos`, `language`. Silent fallbacks (no 400): unknown type word → 200 cross-type results; out-of-range rating → `200 {}`; omitted rating → entire library of that type.
- Alternatively filter `user_rating` client-side from `/sync/all-items`.
- **Public/community ratings** (no auth): `ratings` block on `GET /movies/{id}` / `/tv/{id}` / `/anime/{id}` (Simkl community average + IMDb/MAL). Also `GET /ratings/{type}` for community ratings of items in the user's watchlist (auth; referenced at https://api.simkl.org/api-reference/simkl/get-watchlist-ratings — not read in detail here).

Sources: https://api.simkl.org/api-reference/simkl/add-ratings, https://api.simkl.org/api-reference/simkl/remove-ratings, https://api.simkl.org/api-reference/simkl/get-user-ratings, https://api.simkl.org/api-reference/ratings

---

## 6. Media model & ID systems

### 6.1 Object shapes

Four standard objects: **Movie**, **Show**, **Anime**, **Episode** (https://api.simkl.org/conventions/standard-media-objects).

- Movie: `{ "title", "year", "ids": {…} }`; minimal form `{ "ids": { "simkl": 53536 } }`.
- Show: same + optional `seasons: [{ "number": n, "episodes": [{ "number": n }] }]`.
- Anime: same as Show + `anime_type` (`tv`, `special`, `ova`, `movie`, `music video`, `ona`) + optional flat `episodes[]`.
- Episode: `season` + `number` (docs: prefer this — stable forever), or `ids` with **`tvdb`/`anidb` only** at episode level; optional `watched_at`. Catalog episode lists return only `ids.simkl_id` (integer).
- **Anime is accepted under both `shows[]` and `anime[]`** on sync writes (resolution is by `ids`); on scrobble, singular `show`/`anime`. `not_found` always buckets unresolved anime under `shows`.
- Anime numbering: Simkl's canonical model is AniDB/anime-native (each cour its own title, episodes restart at 1); TVDB/TMDB western season numbering is accepted and cross-mapped (`use_tvdb_anime_seasons` on `/sync/history`; scrobble responses include both `season`/`number` and `tvdb_season`/`tvdb_number`).

### 6.2 The `ids` object

- `ids.simkl` (also returned as `ids.simkl_id` on some endpoints — readers must accept both): **integer**, globally unique, permanent. The canonical primary key.
- **All other IDs are strings in responses**, including numeric-looking ones (`tmdb`, `tvdb`, `mal`, …). Requests accept both quoted and unquoted forms.
- `slug`: URL hint only — **not unique**, response-only (never send on requests).
- `tmdb` is **not unique across types** (separate movie/tv sequences) — pair with `type=` when resolving via `/redirect`. TMDB has no anime type (anime filed under `tv`).

| Key | Response type | Scope |
|---|---|---|
| `simkl` / `simkl_id` | integer | canonical |
| `slug` | string | response-only URL hint |
| `imdb` | string | movies + shows |
| `tmdb` | string | movies + shows (needs `type` to resolve) |
| `tvdb` | string | shows (+ episode-level) |
| `tvdbslug`, `traktslug`, `mdlslug` | string | URL hints |
| `letterboxd` | string | movies only |
| `mal`, `anidb`, `anilist`, `kitsu` | string | anime (`anidb` also episode-level) |

- **Accepted on requests but never echoed in responses:** `crunchyroll`, `netflix`, `hulu`, `anisearch`, `animeplanet`, `livechart`, `anfo`, `ann`, `tvcom`, `zap2it`, `mdl`, `boxd`.
- Resolution priority on writes: `simkl` first, then external IDs in order, then `title`+`year`, then title-only. Docs: **send every ID you have**; write endpoints (`/scrobble/*`, `/sync/history`, `/sync/add-to-list`, `/sync/ratings`) resolve server-side — no `/search/*` round-trip needed before writing.
- CDN data files (`data.simkl.in`, trending/calendar) use legacy key names for three IDs: `letterboxd`→`letterslug`, `traktslug`→`traktmslug` (movies) / `trakttvslug` (TV/anime), plus `jwtv` (JustWatch) not in the API.

Sources: https://api.simkl.org/conventions/standard-media-objects, https://api.simkl.org/guides/anime, https://api.simkl.org/api-reference/simkl/get-tv-episodes, https://api.simkl.org/api-reference/simkl/get-anime-episodes, https://api.simkl.org/api-reference/simkl/redirect

---

## 7. Rate limits, pagination, errors

### 7.1 Rate limits

- **Per `client_id`** (unauthenticated) and **per access token** (authenticated), tracked separately: **10 GET/sec, 1 POST/sec**.
- Overage: GET → `429 Too Many Requests`; POST → temporary throttling block on the client_id/token, extended on repeat. `412 client_id_failed` signals a total client_id limit / active block.
- **Parallel requests explicitly allowed only** on Cloudflare-cached surfaces: trending/calendar JSON files (`data.simkl.in`), `GET /movies|/tv|/anime/{id}`, `GET /tv|/anime/episodes/{id}`. Everything else (sync, user state, search) must stay sequential.
- Write endpoints accept arrays (docs mention 50+ items per POST) — batching is the documented way to stay under 1 POST/sec.
- Scrobble endpoints have an additional **20-second per-user write lock**; collision returns `400 RATE_LIMIT` (not 429).
- Recommended backoff for `429/500/502/503`: 1s → 2s → 4s → 8s → 16s, give up after 5 attempts, add jitter. `503` may carry `Retry-After`.

### 7.2 Pagination

- Only some endpoints paginate (marked "📄 Pagination"): `GET /search/{type}` (default limit 10, max 50), genre browse (`/tv|/anime|/movies/genres/…`, default/max 60), premieres (default/max 60). **Page hard cap is 20 everywhere** (best case 20×60 = 1,200 items).
- Params: `page` (default 1), `limit` (over-cap values **silently clamp**, no error).
- Response headers: `X-Pagination-Page`, `X-Pagination-Limit`, `X-Pagination-Page-Count`, `X-Pagination-Item-Count`. Stop when Page == Page-Count.
- `/sync/all-items` does **not** paginate — it uses `date_from` deltas. `/sync/playback` returns up to 10,000 items in one shot.

### 7.3 Error conventions

- Standard envelope: `{ "error": "<machine-readable>", "code": <http status>, "message": "<human prose, unstable>" }`. Docs: branch on `error`, never `message`.
- Status codes: `200` (OK), `201` (created — all sync/scrobble POSTs), `204` (DELETE), `302` (redirect endpoint; the reference page actually documents `301` for `/redirect` — see Ambiguities), `400` (`empty_field`, `wrong_parameter`, scrobble `RATE_LIMIT`, `max_items`), `401` (`user_token_failed`, `secret_error`, `grant_error`; carries `WWW-Authenticate: Bearer realm="api.simkl.com"`), `403` (`redirect_failed`, `forbidden`, `empty_field` on token endpoint), `404` (`id_err`, `empty`, `url_failed`), `409` (`already_watched` on duplicate scrobble-stop, with `watched_at`/`expires_at`), `412` (`client_id_failed`), `429` (`rate_limit`), `500` (`internal`), `502`, `503`.
- Documented quirk: **most catalog endpoints don't 404 for missing IDs** — e.g. `/movies/99999999` returns `200 []`.
- Deterministic errors (400/401/403/404/409/412) must not be retried.

Sources: https://api.simkl.org/resources/rate-limits, https://api.simkl.org/conventions/pagination, https://api.simkl.org/conventions/errors, https://api.simkl.org/conventions/headers, https://api.simkl.org/conventions/null-values

---

## Ambiguities / docs silent

1. **`/sync/add-to-list` top-level `to` contradiction.** The endpoint reference says a top-level `to` is "not accepted" (`400 empty_field`) and `to` must be per-item — but the Watchlist-statuses conventions page's own curl examples send `to` at the top level (`{ "to": "completed", "movies": [...] }`). One of the two is stale.
2. **Scopes effectively don't exist.** Only `scope: "public"` is ever returned; there is no documented scope vocabulary or a way to request less/more privilege. The phrase "every permission your app has been approved for" implies an approval dimension that is not documented anywhere.
3. **PIN-flow `redirect` param**: documented as "mostly relevant for browser-extension and web-flavoured PIN integrations" — its usefulness or exact behavior for a plain CLI is not explained.
4. **PIN expiry/edge responses**: the docs specify pending and approved poll shapes, and that polling an unknown/deleted code returns a fresh init response — but never state an explicit "expired" error shape. What an in-flight poll returns at the exact 900s boundary is unspecified.
5. **`/sync/watched` described inconsistently.** The Sync index card titles it "Mark watched — bulk legacy 'watched' write", while its own reference page describes a **read** ("Look up watched status for items") and warns against using it when you already sync the library. Whether a legacy write mode still exists on that endpoint is not clarified.
6. **Redirect status code:** the errors conventions page says `302 Found` is "most commonly returned by the redirect endpoint," while the `/redirect` reference page documents `301 Moved Permanently`. Minor, but contradictory.
7. **Episode-level ratings:** ratings endpoints take `movies[]`/`shows[]`/`anime[]` only; the docs never explicitly state whether individual episodes can be rated. Implied no, but not stated.
8. **`GET /sync/ratings` (unfiltered)**: shown in a JSON snippet on the add-ratings page but has no dedicated reference section; full parameter surface undocumented.
9. **Token TTL semantics:** `expires_in: 157680000` is called "a sentinel" — docs don't say whether tokens ever actually expire at 5 years or only on revocation.
10. **History read endpoint:** there is no `GET /sync/history`; watch history is read via `/sync/all-items` (+`date_from`). This is usable but the docs never spell out "this is how you list raw watch events with per-event timestamps" — `/sync/all-items` gives last-watched state, and per-episode `watched_at` needs `extended=full&episode_watched_at=yes`. Whether a full chronological event log is retrievable is not documented.
11. **Rate-limit headers:** no `X-RateLimit-*` or similar headers are documented — the only signals are the `429`/`412` responses themselves (and `Retry-After` "may" appear on 503).
12. **Max batch size for sync writes:** docs say arrays of "50+ items" are fine and `/sync/watched` caps at 100 with `extended=episodes`, but no hard cap is documented for `/sync/history`, `/sync/add-to-list`, or `/sync/ratings`.
13. **Legacy Apiary docs (simkl.docs.apiary.io)** are still online as a JS-app shell with an older API Blueprint; they may contradict the current api.simkl.org reference (e.g. the old docs historically used different host/params conventions). Current docs make no mention of the Apiary copy; treat api.simkl.org as canonical.
14. **Anime `music video` vs `music` `anime_type`:** the standard-media-objects page lists `music video` as an `anime_type` value while search/airing pages list `music`. Trivial but inconsistent enum spelling across pages.
