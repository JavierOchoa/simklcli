# simklcli

## Agent skills

### Issue tracker

Issues are tracked as GitHub issues in this repo, using the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### Build specification

The consolidated v1 product and engineering contract is `docs/spec.md`. Treat
it as authoritative together with `CONTEXT.md` and the ADRs; GitHub issue
comments provide provenance rather than a second specification surface.
