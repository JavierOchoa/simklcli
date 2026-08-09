# `simklcli` v1 Build Specification

Status: build-ready

Last updated: 2026-08-09

This document consolidates the decisions resolved in GitHub issues #5–#12. The
canonical vocabulary is in [`../CONTEXT.md`](../CONTEXT.md), architectural
decisions are in [`adr/`](adr/), and the factual API basis is in
[`research/simkl-api-core-loop.md`](research/simkl-api-core-loop.md).

## Product boundary

`simklcli` is a public Python CLI for the core Simkl tracking loop:

- Simkl PIN Authorization and one Authenticated Account;
- catalog search and Media Reference lookup;
- reading and changing Library List Status;
- marking and unmarking Watched State;
- setting and removing User Ratings.

Calendar, statistics, discovery, custom lists, scrobbling, a full-screen TUI,
offline write queuing, and persistent Catalog Metadata caching are out of scope.

## Package and command surface

The PyPI distribution is `simklcli`; its executable is `simkl`. Require Python
3.11 or newer and implement the CLI with Typer, HTTPX, and Rich.

```text
simkl auth login|status|logout
simkl search <query>
simkl lookup <media-reference>
simkl library list|set-status|remove|repair
simkl watched mark|unmark <media-reference> [target selectors]
simkl rating set|remove <media-reference>
```

There is no public `sync` command. Online Library reads reconcile automatically;
`library list --offline` reads a dated Library Snapshot and `library repair`
explicitly rebuilds it.

## Domain invariants

- Movie, Show, Anime, and Episode remain distinct. Anime is not a subtype of
  Show in the public model, even where Simkl folds it into `shows[]` responses.
- Movies allow Plan to Watch, Dropped, and Completed. Shows and Anime allow all
  five List Statuses. Invalid combinations fail rather than being silently
  normalized by the CLI.
- Completed is a List Status and does not prove a Viewing.
- A Viewing belongs to a Movie, Episode, or standalone Anime. Marking multiple
  Episodes is a Bulk Watched Update, not one parent Viewing.
- User Ratings apply only to Movies, Shows, and Anime in v1 and use Simkl's 1–10
  scale. Episode ratings are out of scope.
- Simkl ID is canonical identity. Slugs are hints and never identity.

## Media References

A Media Reference accepts:

- a qualified Simkl ID such as `simkl:3708`;
- a qualified External ID such as `imdb:tt0133093`, with media kind supplied
  when the provider namespace requires it;
- title text with optional `--kind` and `--year` disambiguation.

Ambiguous title text may prompt only in an interactive terminal. JSON or
non-interactive use exits nonzero and returns candidates without prompting.
Episode References use parent Show season/episode coordinates, an Anime Episode
Number, or a qualified TVDB/AniDB episode-level External ID.

## Output and command safety

- Human-readable reads use Rich tables and panels.
- `--json` emits one machine-readable stdout payload, disables prompts, and
  sends diagnostics to stderr.
- Destructive whole-item operations and Bulk Watched Updates confirm only in an
  interactive terminal. Scripts and JSON mode must pass `--yes`; missing
  confirmation fails without prompting.
- Ordinary commands never start authorization implicitly.
- Cancellation exits without persisting a partial credential or local update.

## Authentication

Use Simkl PIN Authorization exclusively for v1. Releases embed the public Simkl
`client_id`, never a secret; development may override the registered app.

### Login

- Print the verification URL and short code before a best-effort browser launch
  in an interactive terminal. Browser failure does not fail authorization;
  `--no-open-browser` suppresses launch.
- Honor the server-provided poll interval and expiry. Keep the code visible,
  show an interactive countdown or plain progress, and let Ctrl-C cancel.
- After authorization, call `POST /users/settings` and validate the stable
  account ID and display name before persisting anything.
- Support one Authenticated Account. Switching accounts requires logout, then
  login.

### Credential sources

- `SIMKL_ACCESS_TOKEN` is a non-persisted automation override and takes
  precedence over stored credentials. Never accept an Access Token as a command
  argument.
- While the override exists, report its source, refuse a shadowed persistent
  login, and explain that logout cannot remove the environment variable.
- Default persistent storage is the OS keyring. If no usable keyring exists,
  fail with instructions for explicitly selecting plaintext-file storage; never
  silently downgrade.
- Plaintext storage uses the platform user-configuration directory, owner-only
  permissions where supported, and atomic replacement.

### Status, invalidation, and logout

- `auth status` reports the locally recorded account and active credential source
  without network access; an explicit check validates the token and refreshes
  the display name.
- Tokens are long-lived and have no refresh-token grant.
- On confirmed `401 user_token_failed`, remove the nonempty persisted token and
  account metadata, report it invalid or revoked, and require explicit login. Do
  not remove an environment token.
- Logout removes only local credentials, account metadata, and that account's
  Library Snapshot. Explain that the remote token remains active and direct the
  user to Simkl Connected Apps for revocation.

## HTTP boundary and rate control

Every API request includes `client_id`, `app-name`, and `app-version` query
parameters plus a `User-Agent`; authenticated calls also send the bearer token.
All traffic passes through one rate-control boundary.

- Use an app-scoped cross-process gate for unauthenticated catalog GETs and an
  Authenticated-Account-scoped gate for authenticated GETs and POSTs.
- Keep non-cacheable calls sequential. Start POSTs at least one second apart and
  respect the ten-GETs-per-second ceiling. All waits are interruptible.
- Retry GET responses with 429, 500, 502, or 503 using jittered delays based on
  1, 2, 4, 8, and 16 seconds, honoring `Retry-After`, then fail.
- Do not retry deterministic 400, 401, 403, 404, 409, or 412 responses.
- Never blindly retry a POST after a timeout, connection loss, or 5xx where it
  may have applied. Stop immediately on an explicit POST throttle/block response.
- Inspect Simkl's response bodies, including `error`, `not_found`, and partial
  success data; an HTTP success status alone is insufficient.

### Failed POST verification

After a failed POST, run the corresponding read through the normal GET policy:

- if the intended state is confirmed, succeed and say read-back recovered the
  result;
- if the state is confirmed absent, fail definitively and say rerunning is safe;
- if the read fails or cannot distinguish the outcome, exit nonzero with
  `outcome unknown`, provide a manual verification command, and do not recommend
  a blind retry.

Offline writes fail; they are never queued or replayed. Claim that nothing
changed only when the request definitely was not sent or a read confirms it.

## Catalog Metadata

Do not persist Catalog Metadata in v1. Reuse within one command is allowed, but
later invocations fetch it afresh. Library Display Identity inside a Library
Snapshot is not a Catalog Metadata cache.

## Library Snapshot and reconciliation

Persist one versioned JSON Library Snapshot per Authenticated Account in the
platform user-data directory. Retain List Status, Watched State, User Ratings,
activity positions, and Library Display Identity only.

- Use owner-only permissions, per-account cross-process locking, and atomic
  replacement.
- Rebuild missing, corrupt, or incompatible snapshots; do not migrate old
  snapshot schemas.
- Delete a snapshot when its persisted account metadata is cleared.
- Before every online Library-state read, call the activities endpoint and
  reconcile changed state, including the IDs-only comparison needed to detect
  removals.
- Fail when freshness cannot be established. Explicit offline reads may return
  the last complete snapshot and must report its age.
- Focused state learned before the first full Library read is partial and may not
  be presented as a complete Library.

Writes operate directly on a Media Reference without snapshot initialization.
After success, focused server reconciliation updates the snapshot; Simkl's
resulting state is authoritative. If the write succeeds but reconciliation
fails, report remote success, mark local state dirty, and reconcile before the
next online read without encouraging a write retry.

## Watched State

`watched mark` and `watched unmark` share target selectors:

- a bare Movie or standalone Anime targets the whole item;
- an episodic Show or Anime requires `--episode`, `--season … --all`, or `--all`;
- both support Bulk Watched Updates;
- `mark` additionally supports `--at` and `--rewatch` because those describe a
  Viewing.

Unmark Watched preserves the parent or standalone Media Item's Library
membership, List Status, and User Rating. For a standalone item, capture the
surrounding Library state, perform Simkl's removal and restoration writes
sequentially, then verify the complete final state. Report partial or unknown
outcome when preservation cannot be established; never silently turn Unmark
Watched into Remove from Library.

## Testing and continuous integration

Merge-gating tests never contact Simkl. Use:

1. pure unit tests for domain, retry, and rendering logic;
2. HTTPX `MockTransport` API-client tests asserting exact requests and authored
   Simkl-like responses;
3. Typer `CliRunner` tests covering arguments, prompts, stdout, stderr, JSON, and
   exit codes with a fake API boundary.

Fixtures cover success, partial success, throttling, malformed responses, and
documented ambiguities without credentials or request identifiers. Use pytest,
pytest-cov/Coverage.py, Ruff for lint/imports/formatting, and strict mypy. Require
at least 90% aggregate branch coverage for application code, with justified
inline exclusions.

Every merge runs the full Cartesian product of Ubuntu, macOS, and Windows with
all stable supported CPython minors from 3.11 through latest. A stable aggregate
`CI` check gates `main` and verifies:

- locked dependency synchronization;
- Ruff and strict mypy;
- every test-matrix cell and the branch-coverage threshold;
- `uv build --no-sources` for wheel and sdist;
- isolated installation smoke tests for both artifacts, including `simkl --help`.

The aggregate job runs even after dependency failures and fails unless every
required result succeeds. Pin Actions to immutable SHAs with minimum token
permissions. Require current branches and resolved review conversations; disable
direct pushes and force pushes. Reviewer approval is optional while there is one
maintainer.

A separate mandatory, manually triggered pre-release workflow exercises the
real core loop sequentially against a dedicated Simkl account and reserved media
item. It snapshots and restores initial state even after failures. A release is
not tagged until this live smoke passes.

## Release and support

Use uv, a committed `uv.lock`, and Hatchling. Build wheel and source distribution
artifacts, enforce tag/package-version agreement, and publish SemVer `v*` tags to
PyPI through approved GitHub OIDC Trusted Publishing. Publish GitHub Release
notes and do not maintain a pre-1.0 changelog.

PyPI installation through uv or pipx is the only official distribution
contract. Third-party packages are unofficial; accept package-manager-specific
support only when the core defect reproduces through a supported uv/pipx path.
