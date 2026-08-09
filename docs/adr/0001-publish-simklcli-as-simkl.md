# Publish `simklcli` as the `simkl` command

## Context

The repository and public package need a durable name before the first release,
and users need one supported installation contract across macOS, Windows, and
Linux. Distribution names, console commands, version tags, and release
credentials become expensive to change after publication.

## Decision

- Publish the PyPI distribution as `simklcli` and expose the `simkl` console
  command.
- Support Python 3.11 and newer. Use uv for dependency management and building,
  commit `uv.lock`, and use Hatchling with an explicit version as the build
  backend.
- Prefer `uv tool install simklcli`; support `pipx install simklcli` as the
  alternative. Ephemeral uv execution is `uvx --from simklcli simkl`.
- Begin at version `0.1.0` and use Semantic Versioning with `vX.Y.Z` Git tags.
  Minor releases may break compatibility before `1.0.0`; `1.0.0` establishes a
  stable command and configuration contract.
- Build both a wheel and source distribution. A release must reject a tag that
  does not match the package version.
- License the project under MIT.
- Publish from a `v*` GitHub Actions workflow only after checks pass and a
  protected `pypi` environment approves the release. Use PyPI Trusted
  Publishing through OIDC; store no long-lived PyPI credential.
- PyPI is the only official release channel. Native executables, Homebrew,
  WinGet, Scoop, and platform-specific packages are neither launch requirements
  nor promised roadmap items. Third-party packages remain unofficial and
  downstream-maintained.
- Publish concise GitHub Release notes. Do not maintain a duplicate
  `CHANGELOG.md` before 1.0.

## Consequences

The distribution and executable deliberately have different names. Release and
smoke-test automation must exercise both built artifacts and the installed
`simkl` entry point. Additional distribution channels require a new decision
based on demonstrated demand and their signing, testing, and maintenance cost.
