"""The one page of the UI. No external files, and every value from a run is written as text, never as markup."""

from clew.views.style import TOKENS

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Clew</title>
<style>
/*TOKENS*/
* { box-sizing: border-box; }
body { margin: 0; background: hsl(var(--background)); color: hsl(var(--foreground));
       font: 400 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
header { display: flex; align-items: baseline; gap: 1.5rem; padding: 1rem 1.5rem 0;
         border-bottom: 1px solid hsl(var(--border)); background: hsl(var(--card)); flex-wrap: wrap; }
header h1 { font-size: 1.25rem; margin: 0 0 .75rem; letter-spacing: -.02em; }
nav { display: flex; gap: .25rem; }
nav button { border: 0; background: none; padding: .5rem .9rem .7rem; font: inherit; cursor: pointer;
             color: hsl(var(--steel)); border-bottom: 2px solid transparent; }
nav button[aria-selected="true"] { color: hsl(var(--foreground)); border-bottom-color: hsl(var(--accent)); font-weight: 600; }
#badges { margin-left: auto; display: flex; gap: .5rem; padding-bottom: .75rem; flex-wrap: wrap; }
.badge { font-size: .78rem; padding: .15rem .55rem; border-radius: 999px; border: 1px solid hsl(var(--border));
         color: hsl(var(--steel)); background: hsl(var(--muted)); white-space: nowrap; }
.badge.ok { color: hsl(var(--ok)); background: hsl(var(--ok-tint)); border-color: hsl(var(--ok) / .3); }
.badge.bad { color: hsl(var(--destructive)); background: hsl(var(--destructive-tint)); border-color: hsl(var(--destructive) / .3); }
#runbar { max-width: 78rem; margin: 1rem auto 0; padding: 0 1.5rem; }
#runbar .card { margin-bottom: 0; }
#summary { display: flex; align-items: baseline; gap: 1rem; flex-wrap: wrap; }
#summary strong { font-weight: 600; }
main { display: grid; grid-template-columns: minmax(20rem, 26rem) 1fr; gap: 1.5rem; padding: 1rem 1.5rem 1.5rem; max-width: 78rem; margin: 0 auto; }
@media (max-width: 860px) { main { grid-template-columns: 1fr; padding: 1rem; } #runbar { padding: 0 1rem; } }
section.card { background: hsl(var(--card)); border: 1px solid hsl(var(--border)); border-radius: var(--radius); padding: 1rem 1.1rem; margin-bottom: 1rem; }
h2 { font-size: .74rem; letter-spacing: .1em; text-transform: uppercase; color: hsl(var(--accent-strong)); margin: 0 0 .6rem; font-weight: 600; }
label { display: block; font-size: .82rem; color: hsl(var(--steel)); margin: .7rem 0 .25rem; }
input[type=text], select, textarea { width: 100%; font: inherit; padding: .45rem .6rem; border: 1px solid hsl(var(--border));
       border-radius: calc(var(--radius) - 2px); background: hsl(var(--card)); color: inherit; }
textarea { min-height: 6rem; resize: vertical; }
.mono, input.path { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .82rem; }
.row { display: flex; gap: .5rem; }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
@media (max-width: 860px) { .cols { grid-template-columns: 1fr; } }
button.plain, button.go { font: inherit; border-radius: calc(var(--radius) - 2px); cursor: pointer; padding: .45rem .8rem; }
button.plain { border: 1px solid hsl(var(--border)); background: hsl(var(--card)); color: inherit; }
button.go { border: 0; background: hsl(var(--primary)); color: white; font-weight: 600; padding: .6rem 1.4rem; }
button:disabled { opacity: .5; cursor: not-allowed; }
#folders { max-height: 9rem; overflow: auto; border: 1px solid hsl(var(--hairline)); border-radius: calc(var(--radius) - 2px); margin-top: .5rem; }
#folders button { display: block; width: 100%; text-align: left; border: 0; background: none; padding: .3rem .6rem; font: inherit; cursor: pointer; }
#folders button:hover { background: hsl(var(--muted)); }
.note { font-size: .82rem; color: hsl(var(--steel)); margin: .4rem 0 0; }
.error { color: hsl(var(--destructive)); font-size: .88rem; margin-top: .6rem; }
.check { display: flex; align-items: center; gap: .5rem; margin-top: .7rem; color: hsl(var(--steel)); font-size: .9rem; }
ol.steps { list-style: none; padding: 0; margin: 0; }
ol.steps li { display: grid; grid-template-columns: 1.4rem 7.5rem 1fr; gap: .5rem; padding: .45rem 0; border-bottom: 1px solid hsl(var(--hairline)); align-items: baseline; }
ol.steps li:last-child { border-bottom: 0; }
.dot { width: .7rem; height: .7rem; border-radius: 50%; background: hsl(var(--border)); display: inline-block; }
li.done .dot { background: hsl(var(--ok)); }
li.running .dot { background: hsl(var(--accent)); animation: pulse 1s infinite; }
li.failed .dot { background: hsl(var(--destructive)); }
li.open .dot { background: hsl(var(--accent)); }
li.open { font-weight: 600; }
li.skipped { color: hsl(var(--steel)); }
@keyframes pulse { 50% { opacity: .35; } }
@media (prefers-reduced-motion: reduce) { li.running .dot { animation: none; } }
.kv { display: grid; grid-template-columns: 8rem 1fr; gap: .2rem .8rem; margin: 0; }
.kv dt { color: hsl(var(--steel)); font-size: .85rem; }
.kv dd { margin: 0; overflow-wrap: anywhere; }
table { border-collapse: collapse; width: 100%; font-size: .88rem; margin-top: .6rem; }
th, td { text-align: left; padding: .3rem .5rem; border-bottom: 1px solid hsl(var(--hairline)); }
th { color: hsl(var(--steel)); font-weight: 500; }
pre { margin: 0; font-size: .78rem; white-space: pre-wrap; overflow-wrap: anywhere; max-height: 16rem; overflow: auto; }
pre.code { max-height: 34rem; padding: .6rem .7rem; background: hsl(var(--muted)); border-radius: calc(var(--radius) - 2px);
           font-family: ui-monospace, SFMono-Regular, Menlo, monospace; margin: .5rem 0; }
td.pass { color: hsl(var(--ok)); }
td.miss { color: hsl(var(--destructive)); font-weight: 600; }
details summary { cursor: pointer; color: hsl(var(--steel)); font-size: .85rem; }
[hidden] { display: none !important; }
:focus-visible { outline: 2px solid hsl(var(--accent)); outline-offset: 2px; }
</style>
</head>
<body>
<header>
  <h1>Clew</h1>
  <nav role="tablist">
    <button role="tab" aria-selected="true" data-tab="impact">Impact</button>
    <button role="tab" aria-selected="false" data-tab="drift">Drift</button>
    <button role="tab" aria-selected="false" data-tab="reclaim">Reclaim</button>
    <button role="tab" aria-selected="false" data-tab="providers">Providers</button>
  </nav>
  <div id="badges"></div>
</header>

<div id="runbar">
  <section class="card">
    <div id="summary">
      <h2 style="margin:0">Run</h2>
      <span id="run-summary">No run picked yet</span>
      <button class="plain" id="run-change">Change</button>
    </div>
    <div id="picker">
      <div class="cols">
        <div>
          <label for="path">Launch folder</label>
          <div class="row">
            <input type="text" class="path" id="path" spellcheck="false" placeholder="where the workflow was started">
            <button class="plain" id="pick" title="Pick a .zip or .tgz of the .lineage folder, or of the folder the workflow was launched from">Browse</button>
            <button class="plain" id="open">Open</button>
            <button class="plain" id="up" title="Parent folder">Up</button>
            <input type="file" id="record-files" accept=".zip,.tgz,.tar,.tar.gz,application/zip,application/gzip,application/x-tar" hidden>
          </div>
          <div id="folders"></div>
        </div>
        <div>
          <label for="engine">Engine</label>
          <select id="engine"><option value="">Detect from the folder</option></select>
          <label for="run">Run</label>
          <select id="run" disabled></select>
          <p class="note" id="found">Open the folder the workflow was launched from: click through the list or type its path. From your own machine, zip its .lineage folder and Browse to it, or drop the zip here.</p>
          <p style="margin:.4rem 0 0"><button class="plain" id="near" hidden></button>
            <button class="plain" id="unread" hidden>Have an agent write an extractor for it</button></p>
        </div>
      </div>
    </div>
  </section>
</div>

<main id="tab-impact">
  <div>
    <section class="card" id="incident-card" hidden>
      <h2>Incident</h2>
      <label for="incident">What changed or went wrong</label>
      <textarea id="incident" placeholder="The duplicate marking step flags optical duplicates wrongly on patterned flowcells."></textarea>
      <label for="adapter">Adapter</label>
      <select id="adapter"><option value="">None: the run's tools and files only</option></select>
      <div id="adapter-flags"></div>
      <label for="work">Work folder (optional, settles the verdicts)</label>
      <input type="text" class="path" id="work" spellcheck="false">
      <label for="results">Results folder (optional)</label>
      <input type="text" class="path" id="results" spellcheck="false">
      <div class="check">
        <input type="checkbox" id="act" disabled>
        <label for="act" style="margin:0">Act on the plan (not built yet)</label>
      </div>
      <p style="margin:1rem 0 0"><button class="go" id="go" disabled>Run</button></p>
      <p class="error" id="error" hidden></p>
    </section>
  </div>

  <div>
    <section class="card" id="progress-card" hidden>
      <h2>Progress</h2>
      <ol class="steps" id="steps"><li class="skipped"><span></span><span>Nothing run yet</span><span></span></li></ol>
      <p class="note" id="spent"></p>
    </section>
    <section class="card" id="triage-card" hidden><h2>Triage</h2><dl class="kv" id="triage"></dl></section>
    <section class="card" id="review-card" hidden><h2>Recommendation</h2><dl class="kv" id="review"></dl></section>
    <section class="card" id="decide-card" hidden>
      <h2>Your decision</h2>
      <p class="note" style="margin-top:0">Held. Ask a trigger triage offered, or dismiss. Recorded under your name.</p>
      <label for="actor">Your name</label>
      <input type="text" id="actor" autocomplete="off">
      <label for="choice">Trigger</label>
      <select id="choice"></select>
      <label for="why">Reason (optional)</label>
      <input type="text" id="why" autocomplete="off">
      <p class="row" style="margin:1rem 0 0">
        <button class="go" id="ask">Ask</button>
        <button class="plain" id="dismiss">Dismiss</button>
      </p>
    </section>
    <section class="card" id="decided-card" hidden><h2>Decision</h2><dl class="kv" id="decided"></dl></section>
    <section class="card" id="plan-card" hidden><h2>Plan</h2><dl class="kv" id="plan"></dl><table id="items"></table><p class="note" id="more"></p></section>
    <section class="card" id="evidence-card" hidden><h2>Evidence</h2><dl class="kv" id="evidence"></dl></section>
    <section class="card" id="log-card" hidden><details><summary>Agent log</summary><pre id="log"></pre></details></section>
  </div>
</main>

<main id="tab-drift" hidden><section class="card"><h2>Drift</h2><p>Not built yet. The command works: <code>clew drift</code>.</p></section></main>
<main id="tab-reclaim" hidden><section class="card"><h2>Reclaim</h2><p>Not built yet. The command works: <code>clew reclaim</code>.</p></section></main>

<main id="tab-providers" hidden>
  <div>
    <section class="card">
      <h2>Build</h2>
      <select id="b-what" aria-label="What to build">
        <option value="adapter">Adapter: what an id means in a pipeline Clew reads</option>
        <option value="extractor">Extractor: a run record Clew cannot read</option>
      </select>
      <p class="note" id="b-about"></p>
      <div id="b-for-adapter">
        <label for="b-sheet">Launch sheet (optional)</label>
        <div class="row">
          <input type="text" class="path" id="b-sheet" spellcheck="false">
          <button class="plain" id="b-pick">Browse</button>
        </div>
        <p class="note">The agent reads it, so its contents go to the model.</p>
      </div>
      <div id="b-for-extractor" hidden>
        <label for="b-record">Record folder</label>
        <div class="row">
          <input type="text" class="path" id="b-record" spellcheck="false">
          <button class="plain" id="b-pick-record">Browse</button>
        </div>
        <p class="note">The agent reads it. Pick the run's own folder.</p>
      </div>
      <label for="b-name">Name</label>
      <input type="text" id="b-name" autocomplete="off" spellcheck="false" placeholder="lowercase, digits, - or _">
      <div id="b-adapter-brief">
        <label for="b-kind">Kind: what one id is called</label>
        <input type="text" id="b-kind" autocomplete="off" spellcheck="false" placeholder="unit, or several: unit, batch">
        <div class="check">
          <input type="checkbox" id="b-removable">
          <label for="b-removable" style="margin:0">May be withdrawn: outputs only it fed may be deleted</label>
        </div>
        <div class="check">
          <input type="checkbox" id="b-separable">
          <label for="b-separable" style="margin:0">Its share of a step's output can be dropped in place</label>
        </div>
      </div>
      <label for="b-notes">Notes for the agent (optional)</label>
      <textarea id="b-notes" style="min-height:4rem"></textarea>
      <p style="margin:1rem 0 0"><button class="go" id="b-go" disabled>Build</button></p>
      <p class="note" id="b-who"></p>
      <p class="error" id="b-error" hidden></p>
    </section>

    <section class="card">
      <h2>Installed</h2>
      <p class="note" style="margin-top:0">Approved on this machine, for every install.</p>
      <p class="note mono" id="b-folder"></p>
      <table id="b-local"></table>
    </section>
  </div>

  <div>
    <section class="card">
      <h2>Progress</h2>
      <ol class="steps" id="b-steps"><li class="skipped"><span></span><span>Nothing built yet</span><span></span></li></ol>
      <p class="note" id="b-spent"></p>
    </section>
    <section class="card" id="b-judge-card" hidden>
      <h2>Conformance check</h2>
      <p class="note" id="b-judge-note" style="margin-top:0"></p>
      <dl class="kv" id="b-kinds"></dl>
      <table id="b-checks"></table>
      <p style="margin:.8rem 0 0"><button class="plain" id="b-judge" hidden>Check again</button></p>
    </section>
    <section class="card" id="b-code-card" hidden>
      <h2 id="b-code-title">The code</h2>
      <p class="note mono" id="b-hash" style="margin-top:0"></p>
      <pre class="code" id="b-code"></pre>
      <details><summary>The agent's own tests</summary><pre class="code" id="b-tests"></pre></details>
    </section>
    <section class="card" id="b-approve-card" hidden>
      <h2>Your approval</h2>
      <p class="note" style="margin-top:0">Your name and the file's hash go into the record. A changed file stops loading.</p>
      <label for="b-actor">Your name</label>
      <input type="text" id="b-actor" autocomplete="off">
      <p style="margin:1rem 0 0"><button class="go" id="b-install">Approve and install</button></p>
    </section>
    <section class="card" id="b-approved-card" hidden><h2>Approval</h2><dl class="kv" id="b-approved"></dl></section>
    <section class="card" id="b-log-card" hidden><details><summary>Agent log</summary><pre id="b-log"></pre></details></section>
  </div>
</main>

<script>
"use strict";
const token = new URLSearchParams(location.search).get("t") || "";
const $ = (id) => document.getElementById(id);

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (cls) node.className = cls;
  return node;
}

async function api(path, body) {
  const reply = await fetch(path, {method: "POST", body: JSON.stringify(body || {}),
    headers: {"Content-Type": "application/json", "X-Clew-Token": token}});
  const data = await reply.json().catch(() => ({error: "the server sent no answer"}));
  if (!reply.ok) throw new Error(data.error || ("error " + reply.status));
  return data;
}

function fail(message, where) {
  const line = $(where || "error");
  line.textContent = message; line.hidden = !message;
}

function pairs(list, rows) {
  list.replaceChildren();
  for (const [name, value] of rows) {
    if (value === null || value === undefined || value === "") continue;
    list.append(el("dt", name), el("dd", value));
  }
}

let here = null, engines = [], adapters = [], polling = null, ready = false, current = null, built = null;
let picking = true;

const ABOUT = {
  adapter: "Chosen by name when you ask. Needs one run of the pipeline, picked above, and its sheet. Independent of extractors.",
  extractor: "Chosen by recognising the folder. Needs one record of the engine. Independent of adapters. Nothing you build replaces what Clew ships."};

function picked() { return here && here.engine && $("run").value; }

function ready_to_run() {
  const chosen = picked();
  $("run-summary").textContent = chosen ?
    here.engine + " run " + $("run").value + ", " + here.runs.length + " in " + here.path : "No run picked yet";
  $("picker").hidden = !picking && chosen;
  $("incident-card").hidden = $("progress-card").hidden = !chosen;
  $("run-change").textContent = $("picker").hidden ? "Change" : "Done";
  $("run-change").disabled = !chosen;
  $("go").disabled = !(ready && chosen && $("incident").value.trim()) || polling !== null;
  const extractor = $("b-what").value === "extractor";
  $("b-about").textContent = ABOUT[$("b-what").value];
  $("b-for-adapter").hidden = $("b-adapter-brief").hidden = extractor;
  $("b-for-extractor").hidden = !extractor;
  const briefed = extractor ? $("b-record").value.trim() : chosen && $("b-kind").value.trim();
  $("b-go").disabled = !(ready && $("b-name").value.trim() && briefed) || polling !== null;
}

function adapter_fields() {
  const chosen = adapters.find((a) => a.name === $("adapter").value);
  $("adapter-flags").replaceChildren(...(chosen ? chosen.flags : []).flatMap((f) => {
    const label = el("label", f.flag.replace(/^--/, "") + " file: " + f.help);
    const input = el("input"); input.type = "text"; input.className = "path"; input.dataset.flag = f.flag;
    input.spellcheck = false;
    return [label, input];
  }));
}

async function open(path) {
  try {
    here = await api("/api/browse", {path});
  } catch (bad) { $("found").textContent = bad.message; return; }
  $("path").value = here.path;
  $("up").disabled = !here.parent;
  if (here.notes && here.notes.length) $("found").textContent = here.notes.join(" ");
  $("folders").replaceChildren(...here.folders.map((name) => {
    const button = el("button", name);
    button.addEventListener("click", () => open(here.path + (here.path.endsWith("/") ? "" : "/") + name));
    return button;
  }));
  const run = $("run");
  run.replaceChildren(...here.runs.map((r) => {
    const option = el("option", r.name + (r.timestamp ? "   " + r.timestamp : ""));
    option.value = r.name;
    return option;
  }));
  run.disabled = here.runs.length === 0;
  const wanted = $("engine").value;
  if (here.engine) {
    $("found").textContent = ($("found").textContent && here.notes && here.notes.length ? $("found").textContent + " " : "") +
      here.engine + " record, " + here.runs.length + " run" + (here.runs.length === 1 ? "" : "s") +
      (wanted && wanted !== here.engine ? ". You chose " + wanted + ", this folder is " + here.engine + "." : "");
    if (here.work_root && !$("work").value) $("work").value = here.work_root;
  } else {
    $("found").textContent = "No run record here." +
      (here.nearby ? " The " + here.nearby.engine + " record is in " + here.nearby.name + "." :
       " Open the folder the workflow was launched from.");
  }
  $("near").hidden = !here.nearby || !!here.engine;
  $("unread").hidden = !!here.engine || !!here.nearby || !here.launchlike;
  if (here.nearby) $("near").textContent = "Use " + here.nearby.name;
  picking = !picked();
  ready_to_run();
}

function progress(list, spent, job) {
  list.replaceChildren(...job.steps.map((step) => {
    const item = el("li", null, step.state);
    const dot = el("span"); dot.append(el("span", null, "dot"));
    item.append(dot, el("span", step.label), el("span", step.detail || (step.state === "running" ? "working" : "")));
    return item;
  }));
  spent.textContent = job.turns === null ? "" :
    job.turns + " agent turns" + (job.cost === null ? "" : ", $" + job.cost.toFixed(3));
}

function listed(state) {
  // The builder's pickers point at files on this machine through its dialog; none here, none shown.
  $("b-pick").hidden = $("b-pick-record").hidden = !state.dialog;
  engines = state.engines;
  $("engine").replaceChildren($("engine").firstElementChild, ...engines.map((e) => {
    const o = el("option", e.folder ? e.name : e.name + " (by id only)");
    o.value = e.name; o.disabled = !e.folder; return o;
  }));
  adapters = state.adapters;
  const chosen = $("adapter").value;
  $("adapter").replaceChildren($("adapter").firstElementChild, ...adapters.map((a) => {
    const o = el("option", a.name + (a.kinds.length ? ": " + a.kinds.join(", ") : "")); o.value = a.name;
    o.selected = a.name === chosen; return o;
  }));
  $("b-folder").textContent = state.providers;
  const head = el("tr"); head.append(el("th", "File"), el("th", "Provider"), el("th", "State"));
  const rows = state.local.map((one) => {
    const row = el("tr");
    row.append(el("td", one.file), el("td", one.name || ""),
      el("td", one.problem ? "Refused: " + one.problem : "approved by " + one.actor +
        (one.approved_at ? ", " + one.approved_at.slice(0, 10) : ""), one.problem ? "miss" : ""));
    return row;
  });
  if (rows.length === 0) { const none = el("tr"); none.append(el("td", "None yet")); rows.push(none); }
  $("b-local").replaceChildren(head, ...rows);
}

function show_build(job) {
  progress($("b-steps"), $("b-spent"), job);
  const v = job.verdict; $("b-judge-card").hidden = !v;
  const extractor = job.what === "extractor";
  $("b-judge-note").textContent = extractor ?
    "The graph is well formed and the folder is recognised. Whether the lineage is true: compare the counts with the run you know, and read the code." :
    "Clew can use it. Whether the matching is right: read the code.";
  $("b-code-title").textContent = "The " + job.what;
  const counted = (counts) => Object.entries(counts || {}).map(([name, n]) => n + " " + name).join(", ");
  if (v) {
    const g = v.graph || {};
    pairs($("b-kinds"), extractor ? [
      ["Tasks", g.tasks === undefined ? "" : g.tasks + ", joined by " + g.edges + " files read"],
      ["Steps", counted(g.processes)], ["Statuses", counted(g.statuses)],
      ["Outside inputs", (g.external || []).join(", ")],
      ["Runs listed", (g.runs || []).map((r) => r.name + " (" + r.tasks + " tasks)").join(", ")],
      ["Not covered", (g.coverage || []).join(" ")]] :
      v.kinds.flatMap((k) => [
      ["Kind", k.kind + ": " + k.about + " (" + k.mode + ")"],
      ["Ids", k.ids + " found, " + k.reached + " reach a task" + (k.ids > k.reached ? ", " + (k.ids - k.reached) + " reach none" : "")],
      ["Reach none", k.unreached.join(", ")]]));
    const head = el("tr"); head.append(el("th", "Check"), el("th", "Result"), el("th", "Why"));
    $("b-checks").replaceChildren(head, ...v.checks.map((c) => {
      const row = el("tr");
      row.append(el("td", c.name), el("td", c.passed ? "passed" : "FAILED", c.passed ? "pass" : "miss"), el("td", c.detail));
      return row;
    }));
  }
  $("b-judge").hidden = !job.can_judge;
  $("b-code-card").hidden = job.code === null;
  $("b-code").textContent = job.code || "";
  $("b-tests").textContent = job.tests || "The agent left no tests.";
  $("b-hash").textContent = v ? "sha256 " + v.sha256 : "";
  $("b-approve-card").hidden = !job.can_install;
  const a = job.approval; $("b-approved-card").hidden = !a;
  if (a) pairs($("b-approved"), [["Approved by", a.actor], ["At", a.approved_at], ["File hash", a.sha256],
    ["Written by", a.written_by],
    ["Use it", extractor ? "Open its folder in the run bar. Clew reads it as a " + a.name + " record." :
      "Choose " + a.name + " under Adapter on the Impact tab, with its sheet."]]);
  $("b-log-card").hidden = job.log.length === 0;
  $("b-log").textContent = job.log.join("\\n");
  fail(job.state === "failed" ? (job.error || "the build failed") : "", "b-error");
}

function show(job) {
  progress($("steps"), $("spent"), job);

  const t = job.triage; $("triage-card").hidden = !t;
  if (t) pairs($("triage"), [["Outcome", t.outcome], ["Choice", t.choice],
    ["Confidence", t.confidence === null ? "none, matched by name" : t.confidence.toFixed(2)],
    ["Matched on", t.meaning],
    ["Also weighed", (t.others || []).map((o) => o.option + " " + o.probability.toFixed(2)).join(", ")],
    ["Trigger", t.trigger], ["Decision", t.reason],
    ["Asked", t.backend + (t.model ? " " + t.model : "") + ", " + t.options + " options" +
      (t.pipeline ? ", " + t.pipeline + " adapter" : "")],
    ["Left out", (t.notes || []).join(" ")]]);

  const p = job.plan; $("plan-card").hidden = !p;
  if (p) {
    pairs($("plan"), [["Trigger", p.trigger], ["Affected", p.tasks_affected + " of " + p.tasks_total + " tasks"],
      ["Verdicts", Object.entries(p.actions).map(([k, v]) => v + " " + k).join(", ")], ["Policy", p.policy]]);
    const head = el("tr"); head.append(el("th", "Task"), el("th", "Verdict"), el("th", "Rule"));
    $("items").replaceChildren(head, ...p.items.map((i) => {
      const row = el("tr"); row.append(el("td", i.process), el("td", i.action), el("td", i.rule)); return row;
    }));
    $("more").textContent = p.more ? "and " + p.more + " more in plan.json" : "";
  }

  $("evidence-card").hidden = !job.bundle;
  if (job.bundle) pairs($("evidence"), [["Bundle", job.bundle],
    ["Check", job.verified === null ? "checking" : job.verified ? "verified: every verdict recomputes" : "DID NOT VERIFY"]]);

  const r = job.review; $("review-card").hidden = !r;
  if (r) pairs($("review"), [["Suggests", r.verdict], ["Trigger", r.trigger], ["Reason", r.reason],
    ["Status", r.status], ["By", r.recommended_by]]);

  const c = job.choices; $("decide-card").hidden = !c;
  if (c && $("choice").dataset.job !== job.job) {
    $("choice").replaceChildren(...c.triggers.map((trigger) => {
      const o = el("option", trigger + (trigger === c.suggested ? "   (suggested)" : "")); o.value = trigger;
      o.selected = trigger === c.suggested; return o;
    }));
    $("choice").dataset.job = job.job;
  }
  const d = job.decision; $("decided-card").hidden = !d;
  if (d) pairs($("decided"), [["Decided", d.decision + (d.trigger ? " " + d.trigger : "")], ["By", d.actor],
    ["Reason", d.reason], ["At", d.decided_at]]);

  $("log-card").hidden = job.log.length === 0;
  $("log").textContent = job.log.join("\\n");
  fail(job.state === "failed" ? (job.error || "the run failed") : "");
}

async function settle(action) {
  fail("");
  if (!$("actor").value.trim()) { fail("Write your name first."); return; }
  try {
    const job = await api("/api/decide", {job: current, action, actor: $("actor").value,
      trigger: $("choice").value, reason: $("why").value});
    show(job);
    if (job.state === "running" && polling === null) {
      polling = setInterval(() => poll(current), 1000);
      ready_to_run();
    }
  } catch (bad) { fail(bad.message); }
}

async function poll(name) {
  const where = name === built ? "b-error" : "error";
  try {
    const job = await api("/api/job", {job: name});
    (job.kind === "build" ? show_build : show)(job);
    if (job.state !== "running") { clearInterval(polling); polling = null; ready_to_run(); }
  } catch (bad) { clearInterval(polling); polling = null; fail(bad.message, where); ready_to_run(); }
}

$("b-go").addEventListener("click", async () => {
  fail("", "b-error");
  try {
    const started = await api("/api/build", $("b-what").value === "extractor" ?
      {what: "extractor", record: $("b-record").value, name: $("b-name").value, notes: $("b-notes").value} :
      {path: here.path, run: $("run").value, name: $("b-name").value,
       kind: $("b-kind").value, removable: $("b-removable").checked, separable: $("b-separable").checked,
       notes: $("b-notes").value, sheet: $("b-sheet").value});
    built = started.job;
    polling = setInterval(() => poll(started.job), 1000);
    ready_to_run();
    poll(started.job);
  } catch (bad) { fail(bad.message, "b-error"); }
});
$("b-install").addEventListener("click", async () => {
  fail("", "b-error");
  if (!$("b-actor").value.trim()) { fail("Write your name first.", "b-error"); return; }
  try {
    show_build(await api("/api/install", {job: built, actor: $("b-actor").value}));
    listed(await api("/api/state"));
    if (here) open(here.path);
  } catch (bad) { fail(bad.message, "b-error"); }
});
$("b-judge").addEventListener("click", async () => {
  fail("", "b-error");
  try {
    show_build(await api("/api/judge", {job: built}));
    polling = setInterval(() => poll(built), 1000);
    ready_to_run();
  } catch (bad) { fail(bad.message, "b-error"); }
});
$("b-pick-record").addEventListener("click", async () => {
  $("b-pick-record").disabled = true;
  try {
    const chosen = await api("/api/pick", {path: $("b-record").value || (here ? here.path : "")});
    if (chosen.path) { $("b-record").value = chosen.path; ready_to_run(); }
    else if (!chosen.available) fail("No folder dialog here. Type the path.", "b-error");
  } catch (bad) { fail(bad.message, "b-error"); }
  $("b-pick-record").disabled = false;
});
$("b-what").addEventListener("change", ready_to_run);
$("b-record").addEventListener("input", ready_to_run);
$("unread").addEventListener("click", () => {
  $("b-what").value = "extractor";
  $("b-record").value = here.path;
  tab_to("providers");
  ready_to_run();
});
$("b-pick").addEventListener("click", async () => {
  $("b-pick").disabled = true;
  try {
    const chosen = await api("/api/pick", {path: here ? here.path : "", what: "file"});
    if (chosen.path) $("b-sheet").value = chosen.path;
    else if (!chosen.available) fail("No file dialog here. Type the path.", "b-error");
  } catch (bad) { fail(bad.message, "b-error"); }
  $("b-pick").disabled = false;
});
$("b-name").addEventListener("input", ready_to_run);
$("b-kind").addEventListener("input", ready_to_run);

$("go").addEventListener("click", async () => {
  fail("");
  try {
    const adapter_args = {};
    for (const input of $("adapter-flags").querySelectorAll("input")) adapter_args[input.dataset.flag] = input.value;
    const started = await api("/api/run", {path: here.path, run: $("run").value, incident: $("incident").value,
      work_root: $("work").value, results: $("results").value,
      pipeline: $("adapter").value, adapter_args});
    current = started.job;
    polling = setInterval(() => poll(started.job), 1000);
    ready_to_run();
    poll(started.job);
  } catch (bad) { fail(bad.message); }
});
$("open").addEventListener("click", () => open($("path").value));
$("ask").addEventListener("click", () => settle("ask"));
$("dismiss").addEventListener("click", () => settle("dismiss"));
$("near").addEventListener("click", () => here && here.nearby && open(here.nearby.path));
// Browse takes an archive of the record from the person's machine, a .zip of .lineage
// made in Finder say, the same on a laptop and in a Codespace: one file, chosen in an
// instant, where a folder of ten thousand records stalls a browser's folder chooser.
// What is already on this machine is reached through the list and the path box.
$("pick").addEventListener("click", () => $("record-files").click());
$("path").addEventListener("keydown", (event) => { if (event.key === "Enter") open($("path").value); });

const ARCHIVE = /\.(zip|tgz|tar|tar\.gz)$/i;

async function sendArchive(blob, name) {
  const form = new FormData();
  form.append("archive", blob, name);
  $("found").textContent = "Uploading " + name + ", " + Math.round(blob.size / 1048576) + " MB";
  $("pick").disabled = true;
  try {
    const reply = await fetch("/api/upload", {method: "POST", body: form, headers: {"X-Clew-Token": token}});
    const data = await reply.json().catch(() => ({error: "the server sent no answer"}));
    if (!reply.ok) throw new Error(data.error || ("error " + reply.status));
    await open(data.path);
    if (data.notes && data.notes.length) $("found").textContent += " " + data.notes.join(" ");
  } catch (bad) { $("found").textContent = bad.message; }
  $("pick").disabled = false;
}

$("record-files").addEventListener("change", async () => {
  const file = $("record-files").files[0];
  $("record-files").value = "";
  if (!file) return;
  if (!ARCHIVE.test(file.name)) { $("found").textContent = "Pick a .zip or .tgz of the .lineage folder."; return; }
  await sendArchive(file, file.name);
});

// The same archive, dropped on the run card instead of chosen.
$("picker").addEventListener("dragover", (event) => { event.preventDefault(); });
$("picker").addEventListener("drop", async (event) => {
  event.preventDefault();
  const file = [...event.dataTransfer.files].find((f) => ARCHIVE.test(f.name));
  if (!file) { $("found").textContent = "Drop a .zip or .tgz of the launch folder, or of .lineage."; return; }
  await sendArchive(file, file.name);
});
$("up").addEventListener("click", () => here && here.parent && open(here.parent));
$("run-change").addEventListener("click", () => { picking = $("picker").hidden; ready_to_run(); });
$("incident").addEventListener("input", ready_to_run);
$("run").addEventListener("change", ready_to_run);
$("engine").addEventListener("change", () => here && open(here.path));
$("adapter").addEventListener("change", adapter_fields);

function tab_to(name) {
  for (const other of document.querySelectorAll("nav button")) {
    other.setAttribute("aria-selected", String(other.dataset.tab === name));
    $("tab-" + other.dataset.tab).hidden = other.dataset.tab !== name;
  }
}
for (const tab of document.querySelectorAll("nav button")) tab.addEventListener("click", () => tab_to(tab.dataset.tab));

(async () => {
  try {
    const state = await api("/api/state");
    $("badges").append(
      el("span", state.classifier === "jev" ? "Triage: Jev" : "Triage: name matching, no Jev key", "badge" + (state.classifier === "jev" ? " ok" : "")),
      el("span", !state.mainsheet ? "Agent: Mainsheet not installed" : state.api_key ? "Agent: ready" : "Agent: stored login, no API key",
         "badge" + (!state.mainsheet ? " bad" : state.api_key ? " ok" : "")));
    listed(state);
    $("b-who").textContent = "An agent on " + state.builder.model + " writes it in a sandbox. A few minutes.";
    ready = state.mainsheet;
    if (!ready) {
      const off = "Mainsheet is not installed: pip install \\"clew-lineage[agent]\\".";
      fail(off); fail(off, "b-error");
    }
    ready_to_run();
    open(state.start);
  } catch (bad) {
    fail(bad.message);
  }
})();
</script>
</body>
</html>
""".replace("/*TOKENS*/", TOKENS)
