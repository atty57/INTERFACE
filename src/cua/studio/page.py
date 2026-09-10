"""The studio page: server-sent HTML, vanilla fetch polling, no build step.

A framework here would mean node_modules and a second toolchain in a Python project that
is meant to be easy to run. This is one file you can read.
"""

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Computer-use studio</title>
<style>
  :root {
    --bg:#f6f7f9; --panel:#fff; --line:#dfe3e8; --ink:#14171a; --muted:#5b6672;
    --accent:#00308f; --ok:#0a7d33; --warn:#8a5a00; --bad:#a01818; --code:#f0f2f5;
  }
  * { box-sizing:border-box }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif }
  header { background:var(--accent); color:#fff; padding:.9rem 1.4rem;
           display:flex; align-items:baseline; gap:1rem }
  header h1 { font-size:1.05rem; margin:0; font-weight:600 }
  header span { color:#c7d4f0; font-size:.85rem }
  main { display:grid; grid-template-columns:minmax(340px,1fr) minmax(420px,1.35fr);
         gap:1.1rem; padding:1.1rem; align-items:start }
  @media (max-width:900px) { main { grid-template-columns:1fr } }
  section { background:var(--panel); border:1px solid var(--line); border-radius:8px;
            padding:1rem 1.1rem; margin-bottom:1.1rem }
  h2 { font-size:.82rem; text-transform:uppercase; letter-spacing:.06em;
       color:var(--muted); margin:0 0 .8rem }
  label { display:block; font-size:.8rem; color:var(--muted); margin:.6rem 0 .2rem }
  input,select,textarea { width:100%; padding:.5rem .6rem; border:1px solid var(--line);
    border-radius:5px; font:inherit; background:#fff }
  textarea { resize:vertical; min-height:3.6rem }
  button { background:var(--accent); color:#fff; border:0; border-radius:5px;
    padding:.55rem 1rem; font:inherit; font-weight:500; cursor:pointer; margin-top:.8rem }
  button.ghost { background:#fff; color:var(--accent); border:1px solid var(--accent) }
  button.bad { background:var(--bad) }
  button:disabled { opacity:.45; cursor:not-allowed }
  .hint { color:var(--muted); font-size:.78rem; margin:.35rem 0 0 }
  .tabs { display:flex; gap:.4rem; margin-bottom:.9rem }
  .tabs button { margin:0; background:#eceff3; color:var(--ink); font-weight:500 }
  .tabs button[aria-selected=true] { background:var(--accent); color:#fff }
  table { width:100%; border-collapse:collapse; font-size:.82rem }
  th,td { text-align:left; padding:.35rem .5rem; border-bottom:1px solid var(--line);
          vertical-align:top }
  th { color:var(--muted); font-weight:600 }
  code { background:var(--code); padding:.05rem .3rem; border-radius:3px; font-size:.92em }
  .pill { display:inline-block; padding:.1rem .5rem; border-radius:99px; font-size:.74rem;
          font-weight:600 }
  .pill.running { background:#e4ecff; color:var(--accent) }
  .pill.finished { background:#e2f5e7; color:var(--ok) }
  .pill.failed { background:#fbe4e4; color:var(--bad) }
  .pill.queued { background:#eceff3; color:var(--muted) }
  .pill.awaiting_operator { background:#fdf0d5; color:var(--warn) }
  .runs li { list-style:none; padding:.45rem .3rem; border-bottom:1px solid var(--line);
             cursor:pointer; display:flex; justify-content:space-between; gap:.6rem }
  .runs li[aria-current=true] { background:#f0f4ff }
  .runs ul { margin:0; padding:0 }
  .runs .label { overflow:hidden; text-overflow:ellipsis; white-space:nowrap }
  img.shot { width:100%; border:1px solid var(--line); border-radius:5px; margin-top:.5rem }
  .intervention { border:2px solid var(--warn); border-radius:7px; padding:.9rem;
                  background:#fffdf6; margin-bottom:.9rem }
  .verbs { display:flex; gap:.5rem }
  .result { background:var(--code); padding:.7rem; border-radius:5px; overflow:auto;
            font-size:.8rem; white-space:pre-wrap; word-break:break-word }
</style>
</head>
<body>
<header>
  <h1>Computer-use studio</h1>
  <span>the agent discovers once &middot; the artifact replays forever &middot; a human can take the wheel</span>
</header>

<main>
  <div>
    <section>
      <div class="tabs" role="tablist">
        <button role="tab" aria-selected="true" data-tab="goal">Run a goal</button>
        <button role="tab" aria-selected="false" data-tab="capability">Invoke a capability</button>
      </div>

      <form id="goal-form">
        <label for="goal">What should the agent do?</label>
        <textarea id="goal" required
          placeholder="look up member 12345 and read their savings balance"></textarea>
        <label for="target">Where?</label>
        <input id="target" required value="http://127.0.0.1:8000" spellcheck="false">
        <label for="planner">Model</label>
        <select id="planner">
          <option value="claude">Claude Opus 5 (needs ANTHROPIC_API_KEY)</option>
          <option value="openrouter">OpenRouter (needs OPENROUTER_API_KEY)</option>
          <option value="scripted">Scripted — no model, for reproducing a recording</option>
        </select>
        <button type="submit">Start discovery</button>
        <p class="hint">A model drives the app once. Every action passes the policy gate,
          and the successful run is recorded as a reusable capability.</p>
      </form>

      <form id="capability-form" hidden>
        <label for="capability">Capability</label>
        <select id="capability"></select>
        <div id="params"></div>
        <label for="fault">Inject a condition (optional)</label>
        <select id="fault"><option value="">none</option></select>
        <button type="submit">Invoke</button>
        <p class="hint">No model is involved. The form above is generated from the
          capability's own JSON Schema, so the types are the artifact's, not the page's.</p>
      </form>
    </section>

    <section class="runs">
      <h2>Runs</h2>
      <ul id="runs"><li><span class="label">Nothing yet.</span></li></ul>
    </section>
  </div>

  <div>
    <section id="detail">
      <h2>Live run</h2>
      <p class="hint">Start something on the left. The browser window the agent drives opens
        on your desktop — that is the live view, and it is the window you take over.</p>
    </section>
  </div>
</main>

<script>
const $ = s => document.querySelector(s);
let selected = null, capabilities = [];

document.querySelectorAll('[role=tab]').forEach(tab => tab.onclick = () => {
  document.querySelectorAll('[role=tab]').forEach(t =>
    t.setAttribute('aria-selected', String(t === tab)));
  $('#goal-form').hidden = tab.dataset.tab !== 'goal';
  $('#capability-form').hidden = tab.dataset.tab !== 'capability';
});

const esc = s => String(s ?? '').replace(/[&<>"]/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

async function loadCapabilities() {
  capabilities = await (await fetch('/api/capabilities')).json();
  const select = $('#capability');
  select.innerHTML = capabilities.map(c =>
    `<option value="${esc(c.name)}" ${c.unavailable ? 'disabled' : ''}>` +
    `${esc(c.name)}${c.unavailable ? ' — not approved' : ''}</option>`).join('');
  select.onchange = renderParams;
  renderParams();
  const faults = await (await fetch('/api/faults')).json();
  $('#fault').innerHTML = '<option value="">none</option>' +
    faults.map(f => `<option value="${esc(f)}">${esc(f)}</option>`).join('');
}

/* The input form is the artifact's own contract, rendered. */
function renderParams() {
  const chosen = capabilities.find(c => c.name === $('#capability').value);
  const schema = chosen && chosen.input_schema;
  if (!schema) { $('#params').innerHTML = ''; return; }
  const required = schema.required || [];
  $('#params').innerHTML = Object.entries(schema.properties || {}).map(([name, spec]) => `
    <label for="p-${esc(name)}">${esc(name)}
      <span style="color:#8a93a0">${esc(spec.type)}${required.includes(name) ? ' · required' : ''}
      ${spec.pattern ? '· <code>' + esc(spec.pattern) + '</code>' : ''}</span></label>
    <input id="p-${esc(name)}" data-param="${esc(name)}"
      ${spec.pattern ? `pattern="${esc(spec.pattern)}"` : ''}
      ${required.includes(name) ? 'required' : ''}
      placeholder="${esc(spec.description || '')}">`).join('');
}

$('#goal-form').onsubmit = async e => {
  e.preventDefault();
  const body = { goal: $('#goal').value, target: $('#target').value,
                 planner: $('#planner').value };
  const { run_id } = await post('/api/record', body);
  selected = run_id; refresh();
};

$('#capability-form').onsubmit = async e => {
  e.preventDefault();
  const params = {};
  document.querySelectorAll('[data-param]').forEach(i => {
    if (i.value !== '') params[i.dataset.param] = i.value;
  });
  const body = { capability: $('#capability').value, params, fault: $('#fault').value };
  const { run_id } = await post('/api/replay', body);
  selected = run_id; refresh();
};

async function post(url, body) {
  const response = await fetch(url,
    { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body) });
  return response.json();
}

async function refresh() {
  const runs = await (await fetch('/api/runs')).json();
  if (!selected && runs.length) selected = runs[0].id;
  $('#runs').innerHTML = runs.length ? runs.map(r => `
    <li data-id="${esc(r.id)}" aria-current="${r.id === selected}">
      <span class="label">${esc(r.label)}</span>
      <span class="pill ${esc(r.status)}">${esc(r.status.replace('_',' '))}</span>
    </li>`).join('') : '<li><span class="label">Nothing yet.</span></li>';
  document.querySelectorAll('#runs li[data-id]').forEach(li =>
    li.onclick = () => { selected = li.dataset.id; refresh(); });
  if (selected) await renderDetail(selected);
}

async function renderDetail(runId) {
  const response = await fetch('/api/runs/' + runId);
  if (!response.ok) return;
  const { run, events, interventions, has_screenshot } = await response.json();

  const rows = events.map(e => {
    const detail = e.event === 'action'
      ? `${esc((e.action||{}).kind)} ${esc(e.label||'')} ` +
        `[${esc((e.decision||{}).verdict)}]${e.tier ? ' tier ' + e.tier : ''}`
      : e.event === 'classified'
        ? `${esc(e.kind)}: ${esc((e.matched||[]).join(', ') || 'nothing')}`
        : esc(e.strategy || e.reason || e.signature || e.target ||
              (e.result ? e.result.kind : '') || '');
    const step = e.step || e.step_id || '';
    return `<tr><td>${step ? '<code>' + esc(step) + '</code>' : ''}</td>
            <td>${esc(e.event)}</td><td>${detail}</td></tr>`;
  }).join('');

  const panels = interventions.map(i => `
    <div class="intervention">
      <b>The agent stopped and needs you</b> — step <code>${esc(i.step_id)}</code>
      <table>
        <tr><th>Why</th><td>${esc(i.why_stopped)}</td></tr>
        <tr><th>Expected</th><td><code>${esc(i.expected)}</code></td></tr>
        <tr><th>Observed</th><td><code>${esc(i.observed)}</code></td></tr>
        <tr><th>Last good</th><td>${esc(i.last_good_checkpoint)}</td></tr>
        <tr><th>It wanted to</th><td>${esc(i.proposed_action)}</td></tr>
      </table>
      <p class="hint">Claim takes the lease — automation becomes unable to act. Fix it by
        hand in the Chrome window on your desktop, then hand control back.</p>
      <div class="verbs">
        <button class="ghost" onclick="verb('${esc(run.id)}','${esc(i.id)}','claim')">Claim</button>
        <button onclick="verb('${esc(run.id)}','${esc(i.id)}','done')">Done</button>
        <button class="bad" onclick="verb('${esc(run.id)}','${esc(i.id)}','abort')">Abort</button>
      </div>
    </div>`).join('');

  $('#detail').innerHTML = `
    <h2>Live run <span class="pill ${esc(run.status)}">${esc(run.status.replace('_',' '))}</span></h2>
    <p style="margin:.2rem 0 .8rem">${esc(run.label)}</p>
    ${panels}
    ${run.result ? `<div class="result">${esc(JSON.stringify(run.result, null, 2))}</div>` : ''}
    ${run.error ? `<div class="result" style="color:var(--bad)">${esc(run.error)}</div>` : ''}
    ${has_screenshot ? `<img class="shot" alt="the agent's most recent view"
       src="/api/runs/${esc(run.id)}/screenshot?t=${Date.now()}">` : ''}
    <h2 style="margin-top:1rem">What the agent did</h2>
    ${rows ? `<table><tr><th>Step</th><th>Event</th><th>Detail</th></tr>${rows}</table>`
           : '<p class="hint">Nothing recorded yet.</p>'}`;
}

async function verb(runId, requestId, name) {
  const outcome = await post(`/api/runs/${runId}/intervention/${requestId}/${name}`, {});
  if (outcome.ok === false) alert(outcome.reason);
  refresh();
}

loadCapabilities();
refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>
"""
