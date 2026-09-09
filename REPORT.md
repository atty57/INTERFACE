# Design write-up

## 1. Architecture

The system splits **discovery** from **execution**. A model drives the application once; a
typed artifact freezes what it learned; a deterministic executor replays it forever — with a
policy gate under every action and a human lease that can take the wheel of the same live
session.

```
callers ──▶ Catalog ──▶ Orchestrator ──▶ ┌ Discovery engine (LLM) ──▶ Recorder ─┐
                             │           └ Replay engine (NO LLM) ◀── artifact ◀┘
                             │                      │
                   ══════ POLICY GATE ══════════════╪══════ (inside Surface.act)
                                                    ▼
                            Surface: snapshot() · locate() · act()
                              WebSurface (built) · DesktopSurface (stub)
                                                    ▼
                      Session broker: headful Chrome + CDP + ControlLease
```

Three invariants hold it together, each with a test that fails if violated:

- **I1 — the model never decides on the production path.** `cua.replay` has no model client
  in its import graph; `test_no_model_client_is_reachable_from_the_replay_import_graph`
  enforces it structurally rather than by policy.
- **I2 — the gate sits below the model.** `PolicyGate` is called inside `WebSurface.act()`,
  not in a prompt or in the orchestrator.
  `test_calling_act_off_allowlist_on_a_bare_surface_still_raises` bypasses every layer above
  it, because that claim is unfalsifiable from the top.
- **I3 — exactly one holder controls a session.** `ControlLease` on the session;
  `Surface.act()` raises if the caller is not the holder.

A fourth rule governs ambiguity: **fail closed.** Two detectors matching at once is a hard
failure, never a coin flip.

**Key trade-offs.** The *Surface* is the one seam I spent design budget on, because it is
what makes the artifact portable and the desktop story credible. Everything else is
deliberately thin: the catalog is a directory of JSON files, the escalation queue is a list,
the operator console is one page. The model receives a **numbered element digest**, not
pixels — both complete goals at similar rates, but they differ in what gets *recorded*: with
coordinates the Recorder must reverse-map a pixel to an element, and a click landing on a
`<td>` wrapper instead of the `<a>` inside it silently records a descriptor for the wrong
node, discovered weeks later at replay. The digest hands the Recorder the exact handle.

## 2. Artifact schema

The artifact is the contract, so it carries everything replay needs and nothing about how it
was found. `capabilities/member.read_savings_balance/1.0.0.json` is the real one.

```
CapabilityArtifact
  schema_version, capability_id, version, base_url, vendor_product/version_range
  approval_state: draft | approved          requires_secrets: ["core_operator"]
  input_params[]   name, type, pattern, required, sensitivity: public | pii
  outputs[]        name, type, locator, transform
  steps[]          id, action, target: LocatorDescriptor, value, expected_state: Checkpoint,
                   reversibility, recorded_tier, on_error: {business_outcomes, recover, else}
  known_business_outcomes[]  name, detector, severity, message
  recoverable_signatures[]   name, detector, strategy, max_attempts
  success_checkpoint, safety{allowlist…}, provenance{recorded_by, run_id, trace_ref, goal}
```

**Why it is shaped this way.**

- **Surface-agnostic vocabulary.** A `LocatorDescriptor` says *role `textbox`, accessible
  name `Member ID`, inside frame path `main > content`, adjacent to stable text
  `Member ID:`* — never a CSS selector as its primary signal. Every one of those exists in
  Windows UIA, macOS AX and AT-SPI too.
- **Several independent signals per control, ranked.** Replay tries them in order and
  requires exactly one match. Recording only the strongest signal would make every cosmetic
  change a breakage; recording only the weakest makes every reflow one.
- **Secrets are not parameters.** `input_params` carries business inputs the calling agent
  supplies. Credentials are declared in `requires_secrets` as *named handles* the gate
  resolves from the environment at act-time. The consequence is worth stating plainly: the
  calling agent never handles a credential, and no credential can reach the artifact, the
  logs, or a screenshot, because the artifact only ever contains the handle's name.
- **Checkpoints are three kinds only** — text, element state, URL. I dropped whole-subtree
  accessibility snapshots: they assert a page when you mean one heading, so cosmetic change
  breaks them. Over-precision reads as fragility, not rigour.
- **Decoupled from the transcript.** The transcript is *how we found out*; the artifact is
  *what we know*. The transcript holds dead ends, retries and untrusted page text, none of
  which should be executable, reviewable, or diffable. The Recorder is the one-way door and
  emits only from actions that reached a verified checkpoint.
- **Stored as reviewable JSON in git.** A schema change shows up as a pull-request diff,
  which is a stronger answer to "reviewable" than a database — and committing it is what
  lets a clean clone replay with no credentials.

**The generalization pass is what turns a run into a capability.** Values the operator
supplied become typed parameters (`12345` → `${member_id}`, flagged `pii`, pattern
inferred); credentials become handles; values read off the final screen become typed
outputs (`Savings` → `savings: money`, `parse_currency`); concrete identifiers in routes
become patterns. Checkpoints are rewritten too — missing that is the bug that replays
perfectly against the member it was recorded on and fails on every other one, which is why
it is tested directly against a fixed trace rather than only end to end.

## 3. Determinism & error handling

**Determinism is structural, not procedural.** The replay engine has no model client
injected and none reachable by import. It runs with no API key present, and a test asserts
that.

**Locating.** The frame path is walked first — framesets are the number-one legacy failure
mode. Then a five-tier ladder: role + accessible name → label/placeholder → visible text →
anchor-relative (stable label text plus a row/adjacency relation) → scoped structural
selector. The first tier resolving to **exactly one** element wins. More than one is an
error, never "take the first" — and never a fall-through to a weaker tier either, because
resolving an ambiguous strong signal by another route is the same mistake with extra steps.
It surfaces as `ambiguous_state`, distinct from `locator_unresolved`.

The tier that resolved is returned in `tiers_used` and
compared against `recorded_tier` — which the Recorder captures by resolving the descriptor
it is about to write, before acting, so it is a real ladder result rather than an artefact
of how discovery targeted the control. Resolving *below* the recorded tier warns and logs but
never fails, and a capability sliding from tier 1 to tier 5 over weeks is a capability about
to break. That is the UI-drift signal, and per-tenant it is also §4's fork signal.

**Waiting is on expected state, never on a fixed duration.**

**Classification races all detectors under one timeout.** The naive design checks the
success checkpoint, then the business outcomes, in sequence — which is wrong: on a
still-loading page neither matches yet and you misclassify a slow load as a failure. Instead
the engine polls every 250 ms until the step's timeout, reading the screen once per pass and
evaluating the step's checkpoint, the business outcomes that step declares, and the
recoverable signatures against that single reading. Exactly one match classifies. Zero at
timeout is `checkpoint_missed`. **Two or more is `ambiguous_state`** — the system never
guesses between two readings of the screen. The mock's `?fault=ambiguous` produces a page
carrying both "Member Detail" and "No member found"; a sequential checker calls it a
success.

**Three outcomes, and collapsing the middle one is the expensive mistake.**

```
Success         { outputs, run_id, evidence_ref, duration_ms, tiers_used }
BusinessOutcome { name, severity, message, partial_outputs, step_id, … }
Failure         { class, step_id, expected, observed, evidence_ref, escalation_id? }
class ∈ capability_unavailable | invalid_input | policy_denied | escalation_required |
        locator_unresolved | checkpoint_missed | ambiguous_state | session_lost |
        surface_error | operator_aborted | escalation_timeout
```

"No such member" is an *answer* the calling agent must act on, returned as data with a
severity. The rule encoded in the classifier: if a competent human operator would report it
to the customer it is a BusinessOutcome; if they would file a ticket it is a Failure.

**Bounded recovery, never improvisation.** Three named strategies — `dismiss_known_dialog`,
`retry_backoff`, `re_login` — each declared by the capability for that step, each capped by
`max_attempts`, each logged with its attempt number. Exhaustion is a hard failure, not a
loop. `re_login` replays the artifact's own prefix rather than only re-authenticating,
because the failing step depends on the state those steps built.

There is deliberately **no LLM heal tier**: see §7.

## 4. Heterogeneity & multi-tenant

**Surfaces.** One protocol, three operations: `snapshot() → ElementDigest`,
`locate(descriptor) → handle + tier`, `act(action) → effect`. `WebSurface` is built.
`DesktopSurface` is a protocol-conformant stub whose every method raises, and a test asserts
it satisfies the protocol — so "adding desktop touches three methods, not the schema, the
replay engine, the policy gate, or the escalation model" is verified by the type checker
rather than asserted in prose. There is no separate "legacy web" driver: frame walking and
anchor-relative targeting *are* the legacy handling, and they live in the one web driver.

**Tenants: base plus a thin overlay.** The artifact carries `vendor_product` and
`vendor_version_range`; per-tenant differences are expressed as an overlay with exactly four
permitted operations — rebind `base_url`, alias accessible names, skip or insert a
whitelisted step, and **narrow** the allowlist. An overlay may not reorder steps, change
output types, or widen safety. Needing a fifth operation **is** the fork signal. The format
is specified in `docs/spec.md` §8; resolution code is not built (§7).

**Drift telemetry the ladder already emits** — tier used, checkpoint-miss rate,
ambiguous-state rate, human-intervention rate — bucketed per tenant over a rolling window,
is what opens a fork review. A fork is a recorded decision, never automatic, because the
alternative to reuse is re-recording twenty apps across hundreds of institutions.

## 5. Escalation & handoff

The no-progress detector earned its place during development rather than in theory: the
first real run typed the user ID, clicked Sign On without the password, and looped. It fired
correctly — and the diagnosis exposed two genuine defects it was masking. The model was
being shown the page's *controls* but not its *text*, so a rejected sign-on was
indistinguishable from a screen that never changed; and the loop took only the first of
several parallel tool calls while leaving the rest unanswered, so the model believed it had
typed a password it never typed. Both are fixed — screen text now enters the same
untrusted-data envelope as the digest, and every tool call is answered, the extras with an
explicit "not executed".

**Detecting stuck** uses three independent triggers, in order of preference: an explicit
`stuck(reason)` tool the model may call (cheapest, best context); a no-progress detector
comparing digest fingerprints across consecutive steps (catches the model looping *without*
admitting it — the common failure); and a budget on steps, wall clock, and tokens (the
backstop). On the replay side the triggers are a hard failure or an irreversible action.

**The session is the shared state; only the lease moves.** Chrome runs headful on a
debugging port and automation attaches over CDP. Because the browser is local and visible,
"the human takes control of the live session" is physically true with no streaming
infrastructure — cookies, authentication, navigation state and half-filled forms all survive
because nothing is torn down. This is the payoff for putting the session broker *below* both
engines rather than inside one.

The cycle: automation stops → the lease moves to `NONE`, so `Surface.act()` raises for
everyone → an intervention request is queued carrying the capability and goal, the step, why
it stopped, the last verified checkpoint, a masked screenshot, the proposed action, and a
redacted parameter summary → the operator **claims** (lease → `HUMAN`; automation is now
genuinely unable to act, which a test asserts) → their clicks, field changes and navigations
stream into the evidence trail as `human_actions[]` → **Done** or **Abort**.

The console renders the *flow*, not just the endpoint: every action with its policy verdict
and resolving locator tier, each capped recovery attempt, and the exhaustion — read from the
same `steps.jsonl` the audit trail is built on. Deciding whether to take over is a judgement
about how the run got here, so showing only the final step would make the operator
reconstruct it from logs. Page-derived text is escaped on the way in, because `observed` is
content the target application wrote.

**Handback re-anchors; it never assumes.** The engine re-asserts the current step's expected
state (the operator finished the step → resume without repeating the action) or the previous
step's (they restored the starting state → perform the action again). If neither holds, the
request goes back to the operator rather than acting on a screen it cannot place. Abort ends
the run as `operator_aborted`; an unclaimed queue times out as `escalation_timeout`.

The console is a mock surface over a real mechanism — the lease transitions, the event
capture and the re-anchor are what a production console would drive, unchanged. A
compounding benefit of capturing human actions: repeated intervention at the same step is
the signal to re-record that capability.

## 6. Safety

**Trust boundary.** Page content — DOM text, dialog copy, anything the app says — enters the
model's context inside an `<untrusted-data>` envelope and is never concatenated into
instructions; a test asserts the envelope holds an injection attempt. On the production path
the defence is structural rather than textual: replay has no model, so there is nothing for
injected text to talk to.

**The gate lives inside `Surface.act()`**, below the model, and denies by default:

- **Allowlist** over domains, route globs and action types. A relative navigation target is
  resolved to an absolute URL *before* the domain check, so it cannot slip past.
- **Reversibility**: safe proceeds, risky proceeds flagged and logged, irreversible
  **escalates** — deliberately a different outcome, a different code path *and* a different
  failure class (`escalation_required`, not `policy_denied`) from denial, because "a human
  must confirm this" and "this is out of bounds" are different facts and a caller has to be
  able to branch on which one it got.
- **The gate does not trust the artifact.** Reversibility is recomputed from policy at
  replay time from the control's own label and route, and the *stricter* of that and the
  artifact's claim wins. A hand-edited artifact that appends a `Close Account` step marked
  `"safe"` is still blocked — there is a test that does exactly that and asserts the account
  is never closed. Likewise an artifact may only narrow the deployment's allowlist, never
  widen it, and it cannot switch the gate out of block-irreversible mode.
- **Secrets** resolve from the environment at act-time; the artifact stores the handle name.
- **Redaction is write-through** on every artifact and evidence write, so nothing sensitive
  exists on disk even momentarily. Before a screenshot, flagged values are overwritten in the
  rendered page with bullets, captured, then restored. A test greps the whole evidence tree
  byte-wise for the credential after a run that used it.
- **Denials are evidence too.** The mock's detail screen carries a `Close Account` control
  which appears in the digest — the discovery model can see it and want it, and the gate
  refuses it, with the refusal in the audit trail. That proves the guardrail more
  convincingly than a second capability would.

**Stated limits** — a guardrail model claiming none is not a model. The allowlist bounds
*where*, not *what*: an allowlisted destination can still be misused. Masking catches
**flagged** fields, not arbitrary PII rendered elsewhere on the page. Reversibility is a
recorded human judgment plus a pattern list, not a proof — a dangerous control with a bland
label passes unless its route matches. Prompt injection is unsolved industry-wide; the
defence here is structural, not a claim that the discovery run is immune.

## 7. Cuts

| Cut | Why | What exists instead |
|---|---|---|
| **Desktop driver** | 3.7 does not expect an implementation | Protocol-conformant stub, type-checked, plus §4's design story |
| **LLM self-heal on replay** | Puts a model back on the production path at the one point where a wrong answer is least visible; a silently rewritten locator is a liability in a regulated flow | Scored five-tier ladder plus tier-drift telemetry |
| **Overlay resolution code** | §7 explicitly does not reward building scaling infrastructure | The overlay format and its four permitted operations, fully specified |
| **Coordinate / vision fallback** | The digest is lossless for recording; the fallback is speculative until a run fails without it | The screenshot is still in the model's context for layout reasoning |
| **Real operator console** | 3.6 permits mocking it | Real lease, real CDP-attached session, real event capture, real re-anchor; one HTML page |
| **Approval workflow** | Stretch goal | `approval_state` enforced at replay; approving is a one-field edit reviewed as a PR diff — there is deliberately no command for it |
| **Multi-run stability scoring** | Stretch goal, not chosen | — |

**One honest gap.** A happy-path discovery run cannot see the screens it never visited, so
the Recorder seeds the vendor's known business outcomes and recoverable signatures rather
than discovering them, and the pull-request diff is where a human confirms them. Inventing
them at replay time would be improvisation; omitting them would make every "no such member"
a false alarm. The checkpoint the Recorder proposes is likewise a heuristic — the first new
heading-shaped line on the screen — which is why `s3` in the committed artifact anchors on
`Servicing` rather than `Member Search`: both are correct, one is prettier. Asking the model
to name its own checkpoint alongside each action is the upgrade path.

**Next, in order.** Overlay resolution plus a second mock variant, to prove cross-tenant
reuse against something rather than in prose. Then stability scoring across N replays to
feed the draft → approved gate with evidence instead of a human's say-so. Then the desktop
driver — the only cut that would actually test whether the `Surface` seam is in the right
place.
