# CLAUDE.md

Computer-Use Automation System — take-home for interface.ai (Assignment A).

The architecture spec is `docs/spec.md`. It is an internal build document, not a deliverable: the
brief mandates `/README.md`, `/REPORT.md`, and `/evidence/`, and `REPORT.md` is capped at ~1–3 pages
under seven exact headings. Read `docs/spec.md` §0 (invariants) and §15 (cuts) before proposing
changes.

## Agent skills

### Issue tracker

Issues live as GitHub issues in `atty57/INTERFACE`, managed via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles, using the default label strings. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
