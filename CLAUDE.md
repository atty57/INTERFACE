# CLAUDE.md

Computer-Use Automation System — take-home for interface.ai (Assignment A).

The architecture spec is `docs/spec.md`. It is an internal build document, not a deliverable: the
brief mandates `/README.md`, `/REPORT.md`, and `/evidence/`, and `REPORT.md` is capped at ~1–3 pages
under seven exact headings. Read `docs/spec.md` §0 (invariants) and §15 (cuts) before proposing
changes.

## How we build

Test-driven, red-green per slice. Write the failing test first, make it pass, then move on.

Tests drive the system the way a caller does. Do not mock the browser and do not stub the target
app — a test that stubs Playwright proves nothing about locator robustness against a frameset,
which is the whole thesis. Faults come from the mock app's own fault parameter, so tests and demo
runs exercise identical paths.

Three seams, and no others without a reason:

1. **The orchestrator**, in process, against a real browser and the real mock app. Almost every test.
2. **Pure functions in discovery** — the generalization pass and prompt construction. No I/O, no model.
3. **The raw web surface**, one test only: calling act off-allowlist must raise. This exists because
   "the policy gate lives in the driver" cannot be falsified from above.

**Three tickets are not test-first, and faking it would be worse than skipping it:**

- **Mock app (#2, #3)** — this is the test fixture itself. Build it, then a thin smoke test.
- **Discovery loop (#7)** — a real model against a live surface, non-deterministic by nature. It is
  exercised once to produce evidence, never in CI. A discovery test with a mocked model would prove
  nothing and would mislead a reader into thinking the loop was verified.
- **Evidence and write-up (#17)** — documentation.

**Keep the red-green loop fast.** From #10 onward the tests boot a browser against a live app. Build
a session-scoped fixture that starts the mock app and browser once per test session, not per test,
or the loop becomes too slow to work in.

## Agent skills

### Issue tracker

Issues live as GitHub issues in `atty57/INTERFACE`, managed via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles, using the default label strings. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
