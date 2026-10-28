// Agent Factory dashboard. Every value from the server is inserted as text (never as HTML).
"use strict";

const TOKEN = document.querySelector('meta[name="factory-token"]').content;
const JOBS_EVERY_MS = 5000;
const JOB_EVERY_MS = 3000;
const state = { jobs: [], job: null, jobId: null, task: null, panelKey: "", findingFilter: "open", lastOk: 0 };

// --- DOM helpers ------------------------------------------------------------------------------

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

function svg(tag, attrs, ...children) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, String(v));
  }
  for (const child of children.flat()) {
    if (child !== null && child !== undefined) el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

const usd = (n) => `$${(n || 0).toFixed(2)}`;
const short = (s, n) => (s && s.length > n ? `${s.slice(0, n - 1)}…` : s || "");
const time = (iso) => (iso ? new Date(iso).toLocaleTimeString() : "");
const since = (iso) => {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  return s < 60 ? `${Math.round(s)}s ago` : s < 3600 ? `${Math.round(s / 60)} min ago` : `${(s / 3600).toFixed(1)} h ago`;
};

function badgeClass(value) {
  if (["ACCEPTED", "PASS", "READY_TO_MERGE", "MERGED"].includes(value)) return "badge ok";
  if (["BUILDING", "REVIEW", "CHECKS", "BUILD_TASK", "INTEGRATION", "FEATURE_ACCEPTANCE", "RUNNING"].includes(value)) return "badge run";
  if (["CHANGES", "UNKNOWN", "REWORK", "PARKED", "NEEDS_INFO", "NEEDS_HUMAN", "AWAITING_HUMAN_MERGE", "BUDGET_EXHAUSTED",
       "ANALYSIS_LIMIT_REACHED", "PAUSED"].includes(value)) return "badge warn";
  if (["FAIL_CODE", "FAIL_POLICY", "BLOCKED", "BLOCKED_ENV", "FAILED", "CANCELLED", "REJECTED", "ERROR_ENV", "TIMEOUT"].includes(value)) return "badge bad";
  return "badge";
}
const badge = (value) => h("span", { class: badgeClass(value) }, value || "—");

function bar(fraction, cls, marker) {
  const fill = h("span");
  fill.style.width = `${Math.min(100, Math.max(0, fraction * 100))}%`;
  const el = h("div", { class: `bar ${cls || ""}`, role: "presentation" }, fill);
  if (marker !== undefined && marker > 0 && marker <= 1) {
    const m = h("i", { class: "marker", title: "plan estimate" });
    m.style.left = `${marker * 100}%`;
    el.append(m);
  }
  return el;
}

function toast(text, bad) {
  const el = document.getElementById("toast");
  el.textContent = text;
  el.className = bad ? "toast bad" : "toast";
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.hidden = true; }, bad ? 12000 : 5000);
}

// --- data -------------------------------------------------------------------------------------

async function getJSON(url) {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`${url}: ${res.status}`);
  return res.json();
}

async function act(action, body, confirmText) {
  if (confirmText && !window.confirm(confirmText)) return null;
  const res = await fetch(`/api/job/${state.jobId}/${action}`, {
    method: "POST", headers: { "Content-Type": "application/json", "X-Factory-Token": TOKEN }, body: JSON.stringify(body || {}),
  });
  const out = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
  if (!res.ok || out.ok === false) toast(out.error || out.output || `failed (exit ${out.exit_code})`, true);
  else toast(out.background ? `${out.output}\nThe log appears under "Background run".` : (out.output || "done"));
  state.panelKey = "";  // rebuild the action panel with the new state
  state.jobText = "";
  await refreshJob();
  await refreshJobs();
  return out;
}

async function withBusy(button, fn) {
  button.disabled = true;
  try { await fn(); } finally { button.disabled = false; }
}

// --- jobs list --------------------------------------------------------------------------------

function renderJobs() {
  const nav = document.getElementById("jobs");
  const groups = [
    ["Needs you", state.jobs.filter((j) => j.waiting)],
    ["Running", state.jobs.filter((j) => !j.waiting && !j.terminal && j.state !== "READY_TO_MERGE")],
    ["Ready to merge", state.jobs.filter((j) => j.state === "READY_TO_MERGE")],
    ["Finished", state.jobs.filter((j) => j.terminal)],
  ];
  nav.replaceChildren(...groups.filter(([, list]) => list.length).flatMap(([label, list]) => [
    h("div", { class: "section-label" }, `${label} (${list.length})`),
    ...list.map(jobCard),
  ]));
}

function jobCard(job) {
  const cls = ["job-card", job.waiting && "waiting", job.terminal && "terminal", job.job_id === state.jobId && "selected"].filter(Boolean).join(" ");
  const t = job.tasks;
  return h("a", { class: cls, href: `#/job/${job.job_id}`, "aria-current": job.job_id === state.jobId ? "page" : null },
    h("div", { class: "row" }, h("span", { class: "feature" }, job.feature_id), badge(job.state)),
    h("div", { class: "row small muted" }, h("span", { class: "mono" }, job.job_id), h("span", {}, since(job.updated_at))),
    t.total ? h("div", { class: "small muted" }, `tasks ${t.accepted}/${t.total}`, t.building ? ` · ${t.building} in progress` : "") : null,
    t.total ? bar(t.accepted / t.total) : null,
    h("div", { class: "small muted" }, `${usd(job.budget.spent)} of ${usd(job.budget.limit)}`),
    bar(job.budget.limit ? job.budget.spent / job.budget.limit : 0, "money"),
  );
}

// --- job view ---------------------------------------------------------------------------------

function renderJob() {
  const focused = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.key : null;
  renderJobView();
  if (focused) {  // a re-render must not move keyboard focus
    const again = document.querySelector(`[data-key="${CSS.escape(focused)}"]`);
    if (again) again.focus();
  }
}

function renderJobView() {
  const main = document.getElementById("main");
  const job = state.job;
  if (!job) { main.replaceChildren(h("p", { class: "muted" }, "Select a job.")); return; }
  const keep = actionPanel(job);
  main.replaceChildren(
    h("div", { class: "head" }, h("h1", {}, job.feature_id), badge(job.state),
      h("span", { class: "badge" }, `path ${job.path || "—"}`), h("span", { class: "badge" }, `risk ${job.risk || "—"}`),
      h("span", { class: "mono muted" }, job.job_id)),
    h("p", { class: "goal" }, job.goal || ""),
    keep,
    kpis(job),
    h("div", { class: "grid" },
      h("section", { class: "card wide", "aria-labelledby": "g-title" }, h("h2", { id: "g-title" }, "Tasks"), taskGraph(job), taskDetail(job)),
      costsCard(job), runCard(job), shotsCard(job), findingsCard(job), eventsCard(job)),
  );
}

function kpis(job) {
  const b = job.budget;
  const est = job.plan_estimate;
  const accepted = job.tasks.filter((t) => t.state === "ACCEPTED").length;
  const attempts = job.tasks.reduce((n, t) => n + t.attempts, 0);
  const minutes = (new Date(job.updated_at) - new Date(job.created_at)) / 60000;
  return h("div", { class: "kpis" },
    h("div", { class: "card kpi" }, h("div", { class: "value" }, usd(b.spent)), h("div", { class: "label" }, `spent of ${usd(b.limit)} budget`),
      bar(b.limit ? b.spent / b.limit : 0, `money ${b.limit && b.spent / b.limit > 0.7 ? "high" : ""}`, est && b.limit ? est.estimate_usd / b.limit : undefined)),
    h("div", { class: "card kpi" }, h("div", { class: "value" }, est ? usd(est.estimate_usd) : "—"),
      h("div", { class: "label" }, est ? `plan estimate · ${est.basis}` : "no plan yet")),
    h("div", { class: "card kpi" }, h("div", { class: "value" }, `${accepted}/${job.tasks.length}`), h("div", { class: "label" }, "tasks accepted"),
      bar(job.tasks.length ? accepted / job.tasks.length : 0)),
    h("div", { class: "card kpi" }, h("div", { class: "value" }, String(attempts)), h("div", { class: "label" }, "build attempts")),
    h("div", { class: "card kpi" }, h("div", { class: "value" }, `${Math.round(minutes)} min`), h("div", { class: "label" }, `since submit · updated ${since(job.updated_at)}`)),
  );
}

// The action panel holds form inputs: rebuild it only when what it offers changes.
function actionPanel(job) {
  const pending = job.decisions.filter((d) => d.answer === null || d.answer === undefined);
  const key = JSON.stringify([job.state, job.blocked_reason, pending.map((d) => d.id), job.plan_approved, job.criteria_digest,
                              job.budget.limit, job.run && job.run.alive]);
  if (key === state.panelKey && state.panelEl) return state.panelEl;
  state.panelKey = key;
  const busy = job.run && job.run.alive;
  const parts = [h("h2", {}, job.waiting ? "Needs you" : "Actions")];
  if (job.blocked_reason) parts.push(h("pre", {}, job.blocked_reason));
  parts.push(h("p", { class: "small muted" }, `Next step: ${job.next_step}`));
  if (busy) parts.push(h("p", { class: "small" }, "A background run is in progress; actions wait until it ends."));
  if (pending.length) parts.push(decisionsForm(pending));
  else if (job.plan_estimate && !job.plan_approved && job.state === "NEEDS_HUMAN") parts.push(planForm(job));
  if (job.state === "BUDGET_EXHAUSTED") parts.push(budgetForm(job));
  if (job.state === "AWAITING_HUMAN_MERGE" && job.criteria_digest) parts.push(mergeForm(job));
  const buttons = [];
  if (!job.terminal && !pending.length && job.state !== "READY_TO_MERGE") {
    if (job.waiting) buttons.push(h("button", { class: "primary", disabled: busy, onclick: (e) => withBusy(e.target, () => act("resume")) }, "Resume"));
    else buttons.push(h("button", { class: "primary", disabled: busy, onclick: (e) => withBusy(e.target, () => act("run")) }, "Run"));
  }
  if (!job.terminal) {
    buttons.push(h("button", { class: "danger", onclick: (e) => withBusy(e.target, () => act("cancel", {}, `Cancel ${job.job_id}? This cannot be undone.`)) }, "Cancel job"));
  }
  if (buttons.length) parts.push(h("div", { class: "actions" }, buttons));
  state.panelEl = h("section", { class: `card ${job.waiting ? "attention" : ""}`, "aria-label": "Actions" }, parts);
  return state.panelEl;
}

function decisionsForm(pending) {
  const questions = pending.map((d) => {
    const name = `dec-${d.id}`;
    const options = [...new Set([d.proposed, ...(d.options || [])])];
    const custom = h("input", { type: "text", name: `${name}-custom`, "aria-label": `${d.id}: your own answer`, placeholder: "or your own answer" });
    return h("fieldset", { class: "q" },
      h("legend", {}, `${d.id}. ${d.question}`),
      d.why ? h("p", { class: "small muted" }, d.why) : null,
      options.map((opt, i) => h("label", { class: "opt" },
        h("input", { type: "radio", name, value: opt, checked: i === 0 }),
        h("span", {}, opt, i === 0 ? h("span", { class: "badge" }, "proposed") : null))),
      custom);
  });
  const form = h("form", { "aria-label": "Product decisions" }, questions,
    h("div", { class: "actions" },
      h("button", { type: "submit", class: "primary" }, "Save answers"),
      h("button", { type: "button", onclick: (e) => withBusy(e.target, () => act("decide", { accept_proposed: true })) }, "Accept all proposals")));
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const answers = {};
    for (const d of pending) {
      const custom = form.elements[`dec-${d.id}-custom`].value.trim();
      const picked = form.querySelector(`input[name="dec-${d.id}"]:checked`);
      answers[d.id] = custom || (picked ? picked.value : d.proposed);
    }
    withBusy(form.querySelector("button[type=submit]"), () => act("decide", { answers }));
  });
  return form;
}

function planForm(job) {
  const est = job.plan_estimate;
  const input = h("input", { type: "number", min: "1", step: "1", value: String(est.suggested_limit_usd), "aria-label": "Job budget in USD" });
  return h("div", {},
    h("p", {}, `The plan has ${est.tasks} tasks. Estimate ${usd(est.estimate_usd)} (${est.basis}); suggested budget ${usd(est.suggested_limit_usd)}.`),
    h("label", {}, "Budget (USD) ", input),
    h("div", { class: "actions" },
      h("button", { class: "primary", onclick: (e) => withBusy(e.target, async () => {
        const out = await act("approve-plan", { budget: Number(input.value) });
        if (out && out.ok) await act("resume");
      }) }, "Approve plan and build")));
}

function budgetForm(job) {
  const to = h("input", { type: "number", min: "1", step: "1", value: String(Math.ceil(job.budget.limit * 1.3)), "aria-label": "New budget in USD" });
  const reason = h("input", { type: "text", value: "", placeholder: "reason (required)", "aria-label": "Reason for the higher budget" });
  return h("div", {}, h("label", {}, "New budget (USD) ", to), h("label", {}, "Reason ", reason),
    h("div", { class: "actions" }, h("button", { class: "primary", onclick: (e) => withBusy(e.target, async () => {
      const out = await act("raise-budget", { to: Number(to.value), reason: reason.value });
      if (out && out.ok) await act("resume");
    }) }, "Raise budget and resume")));
}

function mergeForm(job) {
  const reason = h("input", { type: "text", placeholder: "reason (needed to reject)", "aria-label": "Reason for rejecting" });
  return h("div", {},
    h("p", { class: "small" }, "Candidate digest: ", h("span", { class: "mono" }, job.criteria_digest)),
    h("div", { class: "actions" },
      h("button", { class: "primary", onclick: (e) => withBusy(e.target, () => act("merge-approve", { revision: job.criteria_digest }, "Approve and merge?")) }, "Approve merge"),
      reason,
      h("button", { class: "danger", onclick: (e) => withBusy(e.target, () => act("merge-reject", { revision: job.criteria_digest, reason: reason.value })) }, "Reject")));
}

// --- task graph -------------------------------------------------------------------------------

function levels(tasks) {
  const byId = Object.fromEntries(tasks.map((t) => [t.task_id, t]));
  const memo = {};
  const level = (id, seen = new Set()) => {
    if (memo[id] !== undefined) return memo[id];
    if (seen.has(id) || !byId[id]) return 0;
    seen.add(id);
    memo[id] = Math.max(-1, ...byId[id].depends_on.map((d) => level(d, seen))) + 1;
    return memo[id];
  };
  tasks.forEach((t) => level(t.task_id));
  return memo;
}

function taskGraph(job) {
  if (!job.tasks.length) return h("p", { class: "muted" }, "No tasks yet (analysis and planning run first).");
  const lv = levels(job.tasks);
  const columns = [];
  job.tasks.forEach((t) => { (columns[lv[t.task_id]] ||= []).push(t); });
  const W = 170, H = 58, GX = 60, GY = 16;
  const pos = {};
  columns.forEach((col, x) => col.forEach((t, y) => { pos[t.task_id] = { x: 10 + x * (W + GX), y: 10 + y * (H + GY) }; }));
  const width = 20 + columns.length * (W + GX) - GX;
  const height = 20 + Math.max(...columns.map((c) => c.length)) * (H + GY) - GY;
  const edges = job.tasks.flatMap((t) => t.depends_on.filter((d) => pos[d]).map((d) => {
    const a = pos[d], b = pos[t.task_id];
    const x1 = a.x + W, y1 = a.y + H / 2, x2 = b.x, y2 = b.y + H / 2, mx = (x1 + x2) / 2;
    return svg("path", { class: "edge", d: `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`, "marker-end": "url(#arrow)" });
  }));
  const cost = job.costs.by_task;
  const nodes = job.tasks.map((t) => {
    const p = pos[t.task_id];
    const select = () => { state.task = t.task_id; renderJob(); };
    return svg("g", { class: `node ${t.state}${state.task === t.task_id ? " selected" : ""}`, transform: `translate(${p.x},${p.y})`, tabindex: 0, "data-key": `task-${t.task_id}`,
                      role: "button", "aria-label": `${t.task_id} ${t.state}`, onclick: select,
                      onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(); } } },
      svg("rect", { width: W, height: H }),
      svg("text", { x: 10, y: 20 }, `${t.task_id} · ${t.state}`),
      svg("text", { x: 10, y: 38, class: "sub" }, `${t.difficulty || "?"} · attempts ${t.attempts} · ${usd(cost[t.task_id])}`),
      svg("title", {}, short(t.acceptance, 300)));
  });
  const defs = svg("defs", {}, svg("marker", { id: "arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" },
    svg("path", { d: "M0,0 L10,5 L0,10 z", class: "edge" })));
  return h("div", {}, h("div", { class: "graph" }, svg("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "img",
    "aria-label": "Task dependency graph" }, defs, edges, nodes)),
    h("div", { class: "legend" }, "Columns follow dependencies; a task starts when every task on its left that it depends on is accepted. Select a task for details."));
}

function taskDetail(job) {
  const t = job.tasks.find((x) => x.task_id === state.task);
  if (!t) return null;
  const reviews = job.reviews[t.task_id] || {};
  const findings = job.findings.filter((f) => f.task_id === t.task_id && f.status === "open");
  return h("div", { class: "card", "aria-live": "polite" },
    h("div", { class: "head" }, h("h2", {}, t.task_id), badge(t.state), h("span", { class: "badge" }, t.difficulty || "?"),
      h("span", { class: "small muted" }, `attempts ${t.attempts} · ${usd(job.costs.by_task[t.task_id])} · depends on ${t.depends_on.join(", ") || "nothing"}`)),
    h("div", { class: "chips" }, Object.entries(reviews).map(([kind, verdict]) => h("span", { class: badgeClass(verdict) }, `${kind}: ${verdict}`))),
    h("p", {}, t.acceptance),
    h("p", { class: "small muted" }, "Files: ", h("span", { class: "mono" }, t.file_scope.join(", "))),
    findings.length ? h("div", {}, h("div", { class: "section-label" }, "Open findings"),
      findings.map((f) => h("pre", {}, `${f.finding_id} [${f.kind}, ${f.severity}${f.blocking ? ", blocking" : ""}] ${f.problem}`))) : null);
}

// --- cards ------------------------------------------------------------------------------------

function costsCard(job) {
  const roles = job.costs.by_role;
  const max = Math.max(0.01, ...roles.map((r) => r.usd));
  return h("section", { class: "card" }, h("h2", {}, "Cost by role"),
    roles.length ? h("table", { class: "costs" }, h("tr", {}, h("th", {}, "role"), h("th", {}, "calls"), h("th", {}, "USD"), h("th", {}, "")),
      roles.map((r) => h("tr", {}, h("td", {}, r.role), h("td", {}, String(r.calls)), h("td", {}, usd(r.usd) + (r.unknown ? " *" : "")),
        h("td", {}, bar(r.usd / max, "money"))))) : h("p", { class: "muted" }, "No agent calls yet."),
    roles.some((r) => r.unknown) ? h("p", { class: "small muted" }, "* includes calls with unknown cost (booked at the reservation)") : null);
}

function runCard(job) {
  const run = job.run;
  return h("section", { class: "card" }, h("h2", {}, "Background run"),
    run ? h("div", {}, h("div", { class: "head" }, badge(run.alive ? "RUNNING" : "finished"), h("span", { class: "mono small" }, run.argv.join(" ")),
      h("span", { class: "small muted" }, `started ${run.started_at}`)), h("pre", {}, run.log_tail || "(no output yet)"))
      : h("p", { class: "muted" }, "Runs started from this dashboard appear here. Runs started in a terminal show up in the events."));
}

function shotsCard(job) {
  if (!job.screenshots.length) return null;
  return h("section", { class: "card wide" }, h("h2", {}, `Screenshots from browser reviews (${job.screenshots.length})`),
    h("div", { class: "shots" }, job.screenshots.map((s) => h("a", { href: `/api/artifact/${s.artifact_id}`, target: "_blank", rel: "noopener",
      title: `${s.role} · ${s.at}` }, h("img", { src: `/api/artifact/${s.artifact_id}`, alt: `screenshot by ${s.role} at ${time(s.at)}`, loading: "lazy" })))));
}

function findingsCard(job) {
  const filter = state.findingFilter;
  const list = job.findings.filter((f) => filter === "all" || (filter === "open" ? f.status === "open" : f.blocking));
  const radio = (value, label) => h("label", {}, h("input", { type: "radio", name: "ff", value, checked: filter === value,
    onchange: () => { state.findingFilter = value; renderJob(); } }), ` ${label}`);
  return h("section", { class: "card wide" }, h("h2", {}, `Findings (${job.findings.length})`),
    h("div", { class: "filters", role: "radiogroup", "aria-label": "Filter findings" }, radio("open", "open"), radio("blocking", "blocking"), radio("all", "all")),
    h("div", { class: "scroll" }, list.length ? h("table", {},
      h("tr", {}, h("th", {}, "id"), h("th", {}, "task"), h("th", {}, "review"), h("th", {}, "severity"), h("th", {}, "status"), h("th", {}, "problem")),
      list.map((f) => h("tr", {}, h("td", { class: "mono" }, f.finding_id), h("td", {}, f.task_id || "feature"), h("td", {}, f.kind),
        h("td", {}, badge(f.blocking ? "BLOCKING" : f.severity)), h("td", {}, f.status), h("td", {}, short(f.problem, 600)))))
      : h("p", { class: "muted" }, "None.")));
}

function eventsCard(job) {
  return h("section", { class: "card wide" }, h("h2", {}, "Timeline (latest 200 events)"),
    h("div", { class: "scroll" }, h("table", {}, job.events.map((e) => h("tr", {},
      h("td", { class: "mono" }, time(e.at)), h("td", {}, e.from_state || e.to_state ? `${e.from_state || ""} → ${e.to_state || ""}` : e.kind),
      h("td", { class: "mono small" }, short(e.payload, 300)))))));
}

// --- refresh loop and routing -----------------------------------------------------------------

function markLive(ok) {
  const el = document.getElementById("live");
  if (ok) state.lastOk = Date.now();
  const stale = Date.now() - state.lastOk > 15000;
  el.className = stale ? "live stale" : "live";
  el.textContent = stale ? "connection lost — retrying" : `live · updated ${new Date(state.lastOk).toLocaleTimeString()}`;
}

async function refreshJobs() {
  try { state.jobs = await getJSON("/api/jobs"); renderJobs(); markLive(true); } catch { markLive(false); }
}

async function refreshJob() {
  if (!state.jobId) return;
  try {
    const text = await (await fetch(`/api/job/${state.jobId}`, { cache: "no-store" })).text();
    markLive(true);
    if (text === state.jobText) return;  // unchanged: keep the DOM, focus and selection as they are
    const typing = document.activeElement && ["INPUT", "TEXTAREA"].includes(document.activeElement.tagName);
    state.jobText = text;
    state.job = JSON.parse(text);
    if (!typing) renderJob();  // never steal the field the operator is typing in
  } catch { markLive(false); }
}

function route() {
  const m = location.hash.match(/^#\/job\/(job-[0-9a-f]{12})$/);
  const id = m ? m[1] : null;
  if (id !== state.jobId) { state.jobId = id; state.job = null; state.jobText = ""; state.task = null; state.panelKey = ""; state.panelEl = null; }
  if (!id && state.jobs.length) {
    const first = state.jobs.find((j) => j.waiting) || state.jobs.find((j) => !j.terminal) || state.jobs[0];
    location.replace(`#/job/${first.job_id}`);
    return;
  }
  renderJobs();
  renderJob();
  refreshJob();
}

window.addEventListener("hashchange", route);
setInterval(() => { if (!document.hidden) refreshJobs(); }, JOBS_EVERY_MS);
setInterval(() => { if (!document.hidden) refreshJob(); }, JOB_EVERY_MS);
refreshJobs().then(route);
