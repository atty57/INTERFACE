# Computer-Use Automation System

An LLM drives a legacy back-office application once, and that successful run is recorded as
a typed, versioned **capability artifact**. From then on a deterministic executor replays
that artifact with **no model in the decision loop**, returning a typed result: success with
outputs, a known business outcome, or a debuggable failure. When automation cannot safely
finish, it hands control of the *same live browser session* to a human, records what they
did, and resumes.

The design write-up is [`REPORT.md`](REPORT.md). The internal architecture document is
[`docs/spec.md`](docs/spec.md).

---

## Setup

Requires Python 3.11+ and about 150 MB for a Chromium download.

```bash
python -m venv .venv && .venv/Scripts/activate      # Windows
# python3 -m venv .venv && source .venv/bin/activate  # macOS / Linux

pip install -e ".[dev]"
python -m playwright install chromium

cp .env.example .env        # synthetic credentials only; nothing real is in here
```

### Keys and configuration

| Variable | Needed for | Notes |
|---|---|---|
| `CUA_OPERATOR_USERNAME` / `CUA_OPERATOR_PASSWORD` | the mock app's sign-on | synthetic; defaults work |
| `CUA_SECRET_CORE_OPERATOR_USERNAME` / `_PASSWORD` | the `core_operator` secret handle | what the gate resolves at act-time |
| `ANTHROPIC_API_KEY` | **recording only** (`--planner claude`) | Claude Opus 5 |
| `OPENROUTER_API_KEY` | **recording only** (`--planner openrouter`) | alternative model route |

`.env` is read automatically and is git-ignored.

**Replay needs no model key at all.** The artifact produced by discovery is committed to
`capabilities/`, so a clean clone reproduces every replay demonstration with zero
credentials configured. That is not a convenience — it is the strongest available
demonstration that no model is on the production path: you *cannot* supply a key, and
replay works anyway.

---

## Demo path

Start the target application in one terminal and leave it running:

```bash
python -m cua serve                     # http://127.0.0.1:8000
```

You can sign on by hand at that URL with the credentials above — the app is a frameset with
table layout and no automation attributes, which is the point.

### 1. Record a capability (needs a model key)

```bash
python -m cua record \
  --goal "look up member 12345 and read their savings balance" \
  --target http://127.0.0.1:8000
```

Claude drives the app through a numbered element digest, the Recorder converts each verified
action into a multi-signal locator plus a checkpoint, and the generalization pass turns the
run into a capability. Output lands in `capabilities/` (as `draft`) and `evidence/discovery-*`.

Without a model key, reproduce the same recording with a deterministic planner — same loop,
same gate, same Recorder, same generalization pass, and `provenance.recorded_by` says so:

```bash
python -m cua record --goal "look up member 12345 and read their savings balance" \
  --target http://127.0.0.1:8000 --planner scripted --headless
python -m cua approve --capability member.read_savings_balance --artifact-version 1.0.0
```

### 2. Replay it — no key required

```bash
# success, with a typed output
python -m cua replay --capability member.read_savings_balance \
  --params '{"member_id": "12345"}'
# → {"kind": "success", "outputs": {"savings": 4182.55}, "tiers_used": {...}}

# a different member: the capability is parameterized, not a macro
python -m cua replay --capability member.read_savings_balance \
  --params '{"member_id": "54321"}'

# an answer, not a crash
python -m cua replay --capability member.read_savings_balance \
  --params '{"member_id": "99999"}'
# → {"kind": "business_outcome", "name": "record_not_found", "severity": "info"}

# a restricted record
python -m cua replay --capability member.read_savings_balance \
  --params '{"member_id": "77777"}'
# → {"kind": "business_outcome", "name": "permission_denied", "severity": "warn"}

# rejected before a browser ever opens
python -m cua replay --capability member.read_savings_balance \
  --params '{"member_id": "not-a-number"}'
# → {"kind": "failure", "failure_class": "invalid_input"}
```

### 3. Error handling and recovery

```bash
# an expired session, re-authenticated and retried — still a success
python -m cua replay --capability member.read_savings_balance \
  --params '{"member_id": "12345"}' --fault timeout

# a slow page, waited out on expected state rather than a fixed sleep
python -m cua replay --capability member.read_savings_balance \
  --params '{"member_id": "12345"}' --fault slow

# a page matching two detectors at once — a hard failure, never a guess
python -m cua replay --capability member.read_savings_balance \
  --params '{"member_id": "12345"}' --fault ambiguous
# → {"kind": "failure", "failure_class": "ambiguous_state"}
```

### 4. Escalation and human handoff

```bash
python -m cua replay --capability member.read_savings_balance \
  --params '{"member_id": "12345"}' --fault dialog \
  --headful --console-port 8765 --escalation-timeout 300
```

A stuck notice keeps coming back, bounded recovery exhausts, and an intervention is raised.
Open <http://127.0.0.1:8765/operator>: it shows the goal, the step, why it stopped, the last
verified checkpoint, and a masked screenshot.

- **Claim** takes the lease. Automation is now physically unable to act — `Surface.act()`
  raises. The Chrome window automation was driving is still on screen, still signed on.
- Fix it by hand in that window (dismiss the notice and reach the member detail screen).
- **Done** hands control back. The engine re-asserts where it should be before doing
  anything; if it cannot place the screen, the request goes back to you rather than acting
  blindly. **Abort** ends the run with `operator_aborted`.

### 5. The agent-facing surface

```bash
python -m cua catalog        # every approved capability as a tool definition
python -m cua digest http://127.0.0.1:8000    # what the model sees instead of pixels
```

---

## Tests

```bash
python -m pytest             # ~125 tests, real browser, real mock app
python -m mypy
python -m ruff check .
```

Tests drive the system the way a caller does. Nothing mocks the browser and nothing stubs
the target app — a test that stubbed Playwright would prove nothing about locator robustness
against a frameset, which is the whole thesis. Faults come from the mock application's own
`?fault=` parameter, so tests and demo runs exercise identical code paths.

---

## Layout

```
src/cua/
  surface/    the load-bearing seam: snapshot / locate / act  (web driver + desktop stub)
  artifact/   the capability artifact: Pydantic model, store, JSON-Schema export
  discover/   the LLM loop, prompts, stuck detection, recorder, generalization pass
  replay/     the production path: locator ladder, detector race, bounded recovery
  policy/     allowlist, reversibility, secrets, redaction — the gate lives inside act()
  session/    browser process, CDP attach, control lease, human-activity capture
  escalate/   intervention broker and the operator console
  catalog/    resolve by name, export schemas for tool-calling
  mock_bank/  the target: frameset, table layout, no test IDs, ?fault= injection
capabilities/ the committed artifact — this is what makes key-free replay work
evidence/     one directory per run: steps.jsonl, masked screenshots, artifact
docs/         spec.md (architecture), faults.md (every injectable condition)
```
