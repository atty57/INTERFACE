# Diagrams

Five views of the system, generated from typed JSON specifications rather than drawn by
hand. Each `.json` is the source of truth; the `.html` beside it is a self-contained
interactive artifact (light/dark, pan/zoom, search, focus, path tracing, PNG/SVG export)
and the `.png` is a 1440×900 render for reading inline.

| Diagram | Type | What it answers |
|---|---|---|
| [`system.architecture`](system.architecture.png) | architecture | The parts, and the two seams that matter: the gate below both engines, the session below everything |
| [`capability-lifecycle.workflow`](capability-lifecycle.workflow.png) | workflow | The whole thesis: a goal becomes a capability, and the capability runs forever without a model |
| [`discovery.sequence`](discovery.sequence.png) | sequence | Who talks to whom during the one run where a model is in the loop |
| [`replay.workflow`](replay.workflow.png) | workflow | One invocation, three possible answers, and where each branch goes |
| [`escalation.lifecycle`](escalation.lifecycle.png) | lifecycle | How the lease moves to a human and how control comes back |

`system.architecture` replaces the ASCII master figure in `REPORT.md` §1.
`discovery.sequence` and `escalation.lifecycle` replace the Mermaid `sequenceDiagram` and
`stateDiagram-v2` blocks in [`../spec.md`](../spec.md) §4 and §7; `replay.workflow` replaces
the §5 `flowchart`. The Mermaid is kept in `spec.md` because it is readable in a plain diff.

## Regenerating

Needs the [archify](https://github.com/tt-a1i/archify) skill and Node 18+:

```bash
cd ~/.claude/skills/archify
node bin/archify.mjs validate <type> <spec>.json --quality showcase --json
node bin/archify.mjs deliver  <type> <spec>.json <out>.html --quality showcase --json
node bin/archify.mjs visual-check <out>.html --json
```

`<type>` is the middle word of the filename. Every diagram here passes all nine artifact
checks at the `showcase` profile with zero composition errors and zero warnings, and passes
browser containment and readability at 1440×900, 1600×1000, 1920×1080 and 2048×1320 in both
themes.

## Known rough edges

Automated checks do not judge composition. Two things a human should see:

- Most diagrams leave an empty band below the legend at 1440×900 — the panels are
  top-weighted rather than filling the height.
- In `escalation.lifecycle`, the over-rail return path crosses the `01 / Run state` lane
  label, and the renderer emits an empty third lane band.

Both are cosmetic and neither affects what the diagrams assert.
