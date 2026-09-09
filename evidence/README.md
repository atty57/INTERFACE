# Evidence

One directory per run. Each holds `steps.jsonl` — an append-only, redacted, structured log
of every action, every policy decision (allowed, denied and escalated alike), every
classification, every recovery attempt and every human action — plus masked screenshots
under `shots/`.

Every write passes through the redactor. After a run that signs on and looks up a
PII-flagged member, no credential and no parameter value appears anywhere under this
directory; `test_no_secret_value_appears_anywhere_beneath_the_evidence_tree` greps the tree
byte-wise to prove it.

| Directory | Command | Result |
|---|---|---|
| `discovery-*` | `cua record --goal "look up member 12345 and read their savings balance" --planner openrouter` | A real model-driven run: sign on, search, member detail. `done` in 6 steps, every control resolved at locator tier 1. Emitted `capabilities/member.read_savings_balance/1.0.0.json` (copied here as `artifact.json`). The `decision` records show what the model chose each turn; the `shots/step-N-observed.png` files are exactly what it saw. |
| `replay-ok-*` | `cua replay --params '{"member_id":"12345"}'` | `Success{savings: 4182.55}`, every step resolved at locator tier 1 |
| `replay-notfound-*` | `cua replay --params '{"member_id":"99999"}'` | `BusinessOutcome{record_not_found, severity: info}` — an answer, not a crash |
| `replay-denied-*` | `cua replay --params '{"member_id":"77777"}'` | `BusinessOutcome{permission_denied, severity: warn}` |
| `replay-recovered-*` | `cua replay --fault timeout` | The session really expires; `re_login` recovers and the step retries → `Success` |
| `replay-ambiguous-*` | `cua replay --fault ambiguous` | `Failure{ambiguous_state}` — the page matched the success checkpoint *and* `record_not_found`, so the system refused to guess |
| `replay-escalate-*` | `cua replay --fault dialog --console-port 8765` | Bounded recovery exhausts after its capped 2 attempts, an intervention is raised with the full context bundle, nobody claims it → `Failure{escalation_timeout}` |
| `replay-tampered-*` | `cua replay --store-root examples/tampered-artifact` | `Failure{escalation_required}` at `s6`, expecting *"human confirmation of an irreversible action"* — a different failure class from an allowlist denial, because they are different facts — the artifact in `examples/tampered-artifact/` is the committed one with a hand-added `Close Account` step relabelled `"safe"`. The gate recomputes reversibility from the control itself and blocks it anyway. The account is never closed. |

Reading a log:

```bash
python -c "import json;[print(json.loads(l)['event'], json.loads(l).get('step','')) \
  for l in open('evidence/replay-ok-*/steps.jsonl')]"
```

Useful `event` values: `action` (with its `decision` and the locator `tier` used),
`classified` (the detector race's verdict), `recovery_attempt` / `recovery_exhausted`,
`intervention_raised`, `lease_claimed`, `human_action`, `re_anchor_failed`, `tier_drift`,
`replay_finished`.
