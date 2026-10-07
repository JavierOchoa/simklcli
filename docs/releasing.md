# Release procedure

Releases build wheel and source distribution artifacts, require successful CI
and a live smoke for the exact tagged commit, and publish through GitHub OIDC.
Configure the external accounts once before attempting the first release.

## GitHub and PyPI setup

Create the `simkl-smoke` GitHub environment with:

- secret `SIMKL_SMOKE_ACCESS_TOKEN`: a token for this registered application
  on a dedicated Simkl account;
- variable `SIMKL_SMOKE_ACCOUNT_ID`: that account's stable numeric Simkl ID;
- variable `SIMKL_SMOKE_MEDIA_REFERENCE`: `simkl:<id>` for a reserved Movie.

The Movie must have no Rewatch sessions; use a clean reserved item on the
dedicated account. The smoke refuses a mismatched account, unknown original
Viewing time, or visible Rewatch sessions before changing anything. Avoid
concurrent changes to this account while the smoke runs. The workflow serializes
live runs and snapshots the reserved item's state before its first write.

Create the protected `pypi` environment with maintainer approval. On PyPI,
register a Trusted Publisher for this repository, `release.yml`, and environment
`pypi`. No long-lived PyPI token is needed. Protect `main` with the aggregate `CI`
check, require current branches and resolved review conversations, and disable
direct and force pushes as specified in `spec.md`.

## Cut a release

1. Update both `pyproject.toml` and `src/simklcli/__init__.py` to the same SemVer
   version. Update the lockfile and merge only after `CI` succeeds.
2. Run **Live pre-release smoke** (`pre-release.yml`) manually on the exact
   release commit. It validates authentication, search/lookup, Library reads,
   List Status changes, Watched State, User Ratings, offline reads, and removal.
3. Wait for success. The script restores and verifies the reserved item's
   initial Library membership, List Status, Watched State/Last Watched At, and
   User Rating even when a smoke step fails. `smoke-evidence` retains a scoped
   restoration backup and, on success, a receipt. A forced process kill or API
   outage can prevent restoration; recover from that backup before rerunning.
4. Tag that same commit `vX.Y.Z` and push the tag. The release workflow rejects
   mismatched versions and commits without successful CI and live smoke.
5. Approve the `pypi` environment. The workflow installs and checks both built
   artifacts, publishes them, and generates GitHub Release notes.

If any smoke step or restoration fails, the workflow fails and no success
receipt is produced. Do not tag a release until a fresh smoke run succeeds on
the intended commit. Do not blindly replay a tracking POST after an unknown
outcome; use the verification commands reported by the CLI first.
