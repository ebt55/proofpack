/* ProofPack reviewer workbench.
 *
 * One vanilla ES module, no build step, no framework, no CDN — the same
 * constraint the report lives under, for the same reason: a reviewer must be
 * able to run this on a locked-down machine, offline, and an auditor must be
 * able to read every line of it.
 *
 * Three views behind a hash router:
 *   #/                 queue      — active jobs, intake, finished packages
 *   #/run/<job_id>     run        — the live agent trail as it happens
 *   #/package/<slug>   package    — the report, integrity check and chat
 *
 * Server text (participant names, model notes, log lines, URLs) is only ever
 * put into the DOM as a text node — nothing here assigns innerHTML except the
 * icon() helper, whose markup is a module constant.
 */

const BOOT = document.body.dataset;
const API_KEY_PRESENT = BOOT.apiKey === "1";
const FAKE_RUN = BOOT.fake === "1";

const ACTIVE_STATES = new Set(["queued", "running", "needs_input"]);

const PHASES = [
  { key: "extract", label: "Extract", hint: "read the form" },
  { key: "research", label: "Research", hint: "visit the website" },
  { key: "checks", label: "Checks", hint: "record findings" },
  { key: "package", label: "Package", hint: "write the report" },
];

const STATUS_LABEL = {
  found: "Found",
  not_found: "Not Found",
  needs_review: "Needs Review",
  internal: "Internal",
  needs_document: "Needs Document",
  pass: "Pass",
  flag: "Flag",
};

const VERDICT_CLASS = {
  "matches application exactly": "found",
  "differs from application": "not-found",
  "not published": "not-found",
  "could not verify": "needs-review",
};

// ---------------------------------------------------------------------------
// Tiny DOM helper
// ---------------------------------------------------------------------------

/** h("div", {class: "card"}, "text", node, [nodes]) -> HTMLElement */
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [key, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") el.className = value;
      else if (key === "text") el.textContent = value;
      else if (key === "dataset") Object.assign(el.dataset, value);
      else if (key === "style") Object.assign(el.style, value);
      else if (key.startsWith("on") && typeof value === "function") {
        el.addEventListener(key.slice(2).toLowerCase(), value);
      } else if (value === true) el.setAttribute(key, "");
      else el.setAttribute(key, String(value));
    }
  }
  append(el, kids);
  return el;
}

function append(el, kids) {
  for (const kid of kids) {
    if (kid === null || kid === undefined || kid === false || kid === "") continue;
    if (Array.isArray(kid)) append(el, kid);
    else if (kid.nodeType) el.append(kid);
    else el.append(document.createTextNode(String(kid)));
  }
}

const SVG_NS = "http://www.w3.org/2000/svg";

// Stroke paths only, drawn in currentColor. This is the one place innerHTML is
// used and every string below is a constant in this file.
const ICONS = {
  doc: '<path d="M6 3h7l5 5v13H6z"/><path d="M13 3v5h5"/>',
  globe: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.4 2.6 2.4 14.4 0 17M12 3.5c-2.4 2.6-2.4 14.4 0 17"/>',
  book: '<path d="M4 4.5h5.5a3 3 0 0 1 2.5 1.4V20a2.6 2.6 0 0 0-2.5-1.6H4z"/><path d="M20 4.5h-5.5a3 3 0 0 0-2.5 1.4V20a2.6 2.6 0 0 1 2.5-1.6H20z"/>',
  search: '<circle cx="10.8" cy="10.8" r="6"/><path d="M15.2 15.2 20.5 20.5"/>',
  link: '<path d="M10.2 13.8a3.8 3.8 0 0 0 5.4 0l3-3a3.8 3.8 0 1 0-5.4-5.4l-1.5 1.5"/><path d="M13.8 10.2a3.8 3.8 0 0 0-5.4 0l-3 3a3.8 3.8 0 1 0 5.4 5.4l1.5-1.5"/>',
  camera: '<path d="M3.5 8.8A1.6 1.6 0 0 1 5.1 7.2h2.8l1.4-2.2h5.4l1.4 2.2h2.8a1.6 1.6 0 0 1 1.6 1.6v8.6a1.6 1.6 0 0 1-1.6 1.6H5.1a1.6 1.6 0 0 1-1.6-1.6z"/><circle cx="12" cy="13.1" r="3.3"/>',
  clipboard: '<path d="M9 4.4h6v3H9z"/><path d="M9 5.9H6.4v14.7h11.2V5.9H15"/><path d="m9.4 13.4 1.8 1.8 3.5-3.9"/>',
  scale: '<path d="M12 4.2v15.6M6.4 7.6h11.2M8.4 19.8h7.2"/><path d="m4 14 2.6-6.4L9.2 14a2.6 2.6 0 0 1-5.2 0z"/><path d="m14.8 14 2.6-6.4L20 14a2.6 2.6 0 0 1-5.2 0z"/>',
  shield: '<path d="M12 3.2 19 6v6c0 4.1-2.8 7.4-7 8.8C7.8 19.4 5 16.1 5 12V6z"/><path d="m9.7 14.3 4.6-4.6M14.3 14.3 9.7 9.7"/>',
  coin: '<circle cx="12" cy="12" r="8.4"/><path d="M12 6.9v10.2"/><path d="M14.6 9.4h-3.9a1.9 1.9 0 0 0 0 3.8h2.6a1.9 1.9 0 0 1 0 3.8H9.2"/>',
  box: '<path d="M3.6 7.6 12 3.2l8.4 4.4v8.8L12 20.8l-8.4-4.4z"/><path d="m3.6 7.6 8.4 4.4 8.4-4.4M12 12v8.8"/>',
  flag: '<path d="M6 21V3.8M6 4.2h11l-2.3 3.7L17 11.6H6"/>',
  dot: '<circle cx="12" cy="12" r="3.2"/>',
  back: '<path d="M14.5 5.5 8 12l6.5 6.5"/>',
};

function icon(name) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.6");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  svg.innerHTML = ICONS[name] || ICONS.dot;
  return svg;
}

// ---------------------------------------------------------------------------
// Fetch + toasts
// ---------------------------------------------------------------------------

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

/** Every request goes through here so failures always surface as one toast. */
async function api(path, options = {}) {
  let res;
  try {
    res = await fetch(path, options);
  } catch (err) {
    const message = `Cannot reach the workbench server. Is \`verify.py serve\` still running? (${path})`;
    toast(message, "bad");
    throw new ApiError(message, 0);
  }
  const type = res.headers.get("content-type") || "";
  const body = type.includes("json")
    ? await res.json().catch(() => null)
    : await res.text();
  if (!res.ok) {
    const message =
      (body && typeof body === "object" && body.error) ||
      (typeof body === "string" && body.trim()) ||
      `${res.status} ${res.statusText}`;
    toast(message, "bad");
    throw new ApiError(message, res.status);
  }
  return body;
}

function toast(message, kind = "info", title = null) {
  const host = document.getElementById("toasts");
  const el = h(
    "div",
    { class: `toast ${kind}`, role: "alert", title: "Click to dismiss" },
    title ? h("div", { class: "toast-title" }, title) : null,
    message
  );
  el.addEventListener("click", () => el.remove());
  host.append(el);
  setTimeout(() => el.remove(), 9000);
}

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

function fmtBytes(n) {
  if (!Number.isFinite(n)) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function fmtMoney(x) {
  return typeof x === "number" ? `$${x.toFixed(2)}` : "—";
}

function relTime(iso) {
  if (!iso) return "—";
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return iso;
  const secs = (Date.now() - then.getTime()) / 1000;
  if (secs < 45) return "just now";
  if (secs < 3600) return `${Math.round(secs / 60)} min ago`;
  if (secs < 86400) return `${Math.round(secs / 3600)} h ago`;
  const days = Math.round(secs / 86400);
  if (days === 1) return "yesterday";
  if (days < 30) return `${days} days ago`;
  return then.toLocaleDateString();
}

/** Undo Python's repr() quoting on the values inside a log line. */
function unrepr(text) {
  const s = (text || "").trim();
  if (s.length >= 2 && ((s[0] === "'" && s.endsWith("'")) || (s[0] === '"' && s.endsWith('"')))) {
    return s.slice(1, -1).replace(/\\'/g, "'").replace(/\\"/g, '"').replace(/\\\\/g, "\\");
  }
  return s;
}

function statusChip(status, extraClass = "") {
  const label = STATUS_LABEL[status] || status || "—";
  const cls = String(status || "pending").replace(/_/g, "-");
  return h("span", { class: `chip ${cls} ${extraClass}`.trim() }, label);
}

function verdictChip(verdict) {
  return h("span", { class: `chip ${VERDICT_CLASS[verdict] || "needs-review"}` }, verdict || "—");
}

function stateChip(state) {
  const map = {
    queued: ["pending", "Queued"],
    running: ["needs-document", "Running"],
    needs_input: ["needs-review", "Needs your answer"],
    done: ["found", "Done"],
    failed: ["not-found", "Failed"],
    cancelled: ["internal", "Skipped"],
  };
  const [cls, label] = map[state] || ["internal", state];
  return h("span", { class: `chip ${cls}` }, label);
}

/** The proportional found / not-found / needs-review bar from theme.css. */
function segBar(counts, total) {
  const parts = [
    ["s-found", counts.found || 0],
    ["s-not-found", counts.not_found || 0],
    ["s-needs-review", counts.needs_review || 0],
  ];
  const label =
    total > 0
      ? `${counts.found || 0} found, ${counts.not_found || 0} not found, ` +
        `${counts.needs_review || 0} needs review, out of ${total} checks`
      : "no website checks";
  const bar = h("div", { class: "seg", role: "img", "aria-label": label });
  if (!total) {
    bar.append(h("span", { class: "s-pending", style: { width: "100%" } }));
    return bar;
  }
  for (const [cls, n] of parts) {
    if (n > 0) {
      bar.append(h("span", { class: cls, style: { width: `${(100 * n) / total}%` } }));
    }
  }
  return bar;
}

function segLegend(counts) {
  return h(
    "div",
    { class: "seg-legend" },
    h("span", {}, h("i", { class: "s-found" }), `${counts.found || 0} found`),
    h("span", {}, h("i", { class: "s-not-found" }), `${counts.not_found || 0} not found`),
    h("span", {}, h("i", { class: "s-needs-review" }), `${counts.needs_review || 0} needs review`)
  );
}

/** URL for a file inside a package, path segments escaped individually. */
function pkgUrl(slug, path) {
  const parts = String(path).split("/").map(encodeURIComponent).join("/");
  return `/packages/${encodeURIComponent(slug)}/${parts}`;
}

function sectionHead(title, ...rest) {
  return h("div", { class: "sec-head" }, h("h2", {}, title), ...rest);
}

function backLink(href, label) {
  return h("a", { class: "crumb", href }, icon("back"), label);
}

function spinner() {
  return h("span", { class: "spin", "aria-hidden": "true" });
}

// ---------------------------------------------------------------------------
// Log-line parsing (the formats in §3.3 of the UI plan)
// ---------------------------------------------------------------------------

const RE = {
  evidence: /^\s*->\s*(evidence\/\S+\.png)\s*$/,
  noCapture: /^\s*->\s*could not locate text; no capture\s*$/,
  extracted: /^\s*->\s*([^|]*)\|([^|]*)\|([^|]*)\|(.*)$/,
  extracting: /^Extracting application from (.+?) \.\.\.$/,
  rerunning: /^Re-running website verification for (.+?) \.\.\.$/,
  verifying: /^Verifying on the provider's website \((.*)\) \.\.\.$/,
  openUrl: /^open_url: (.+)$/,
  readPage: /^read_page: offset=(\d+)$/,
  findOnPage: /^find_on_page: (.+)$/,
  listLinks: /^list_links: filter=(.+)$/,
  capturePage: /^capture_page: (.+)$/,
  captureEvidence: /^capture_evidence: (.+) \((.+)\)$/,
  recordFinding: /^record_finding: (\S+) = (\S+)$/,
  recordRate: /^record_rate_comparison: (.+)$/,
  rejectedFinding: /^REJECTED record_finding (\S+): (.+)$/,
  rejectedRate: /^REJECTED record_rate_comparison: (.+)$/,
  tokens: /^Tokens: (.+)$/,
  written: /^Report package written to (.+)$/,
};

/** Which of the four phases a line moves the run into (null = no change). */
function phaseOf(text) {
  if (RE.extracting.test(text)) return 0;
  if (RE.rerunning.test(text)) return 1;
  if (RE.verifying.test(text)) return 1;
  if (RE.tokens.test(text)) return 2;
  if (RE.written.test(text)) return 3;
  return null;
}

/**
 * Turn one raw log line into the pieces a ledger row needs.
 * Returns { kind, icon, cls, primary, detail, detailMono, chip }.
 */
function parseLine(text) {
  let m;

  // The two "-> …" continuation lines attach to the capture row above them;
  // the icon/primary here are only used if that row is somehow missing.
  if ((m = text.match(RE.evidence))) {
    return { kind: "evidence", file: m[1], icon: "camera", cls: "capture", label: "saved", primary: m[1], primaryMono: true };
  }
  if (RE.noCapture.test(text)) {
    return { kind: "no-capture", icon: "camera", primary: "could not locate that text — nothing captured" };
  }
  if ((m = text.match(RE.extracting))) {
    return { kind: "milestone", icon: "doc", cls: "milestone", primary: "Reading the application form", detail: m[1] };
  }
  if ((m = text.match(RE.rerunning))) {
    return { kind: "milestone", icon: "flag", cls: "milestone", primary: "Re-running the website verification", detail: m[1] };
  }
  if ((m = text.match(RE.verifying))) {
    return { kind: "milestone", icon: "globe", cls: "milestone", primary: "Verifying on the provider's website", detail: m[1], detailMono: true };
  }
  if ((m = text.match(RE.tokens))) {
    return { kind: "tokens", icon: "coin", cls: "milestone", primary: "Model usage", detail: m[1] };
  }
  if ((m = text.match(RE.written))) {
    return { kind: "written", icon: "box", cls: "milestone", primary: "Report package written", detail: m[1], detailMono: true };
  }
  if ((m = text.match(RE.rejectedFinding))) {
    return { kind: "gate", icon: "shield", cls: "gate", gate: true, primary: `record_finding ${m[1]} refused`, detail: m[2] };
  }
  if ((m = text.match(RE.rejectedRate))) {
    return { kind: "gate", icon: "shield", cls: "gate", gate: true, primary: "record_rate_comparison refused", detail: m[1] };
  }
  if ((m = text.match(RE.openUrl))) {
    return { kind: "open", icon: "globe", label: "open_url", primary: m[1], primaryMono: true };
  }
  if ((m = text.match(RE.readPage))) {
    return { kind: "read", icon: "book", label: "read_page", primary: `read the page from character ${Number(m[1]).toLocaleString()}` };
  }
  if ((m = text.match(RE.findOnPage))) {
    return { kind: "find", icon: "search", label: "find_on_page", primary: unrepr(m[1]) };
  }
  if ((m = text.match(RE.listLinks))) {
    const filter = unrepr(m[1]);
    return { kind: "links", icon: "link", label: "list_links", primary: filter ? `filter “${filter}”` : "all links on the page" };
  }
  if ((m = text.match(RE.captureEvidence))) {
    return { kind: "capture", icon: "camera", cls: "capture", label: "capture_evidence", primary: unrepr(m[2]), detail: `located “${unrepr(m[1])}”` };
  }
  if ((m = text.match(RE.capturePage))) {
    return { kind: "capture", icon: "camera", cls: "capture", label: "capture_page", primary: unrepr(m[1]) };
  }
  if ((m = text.match(RE.recordFinding))) {
    return { kind: "finding", icon: "clipboard", cls: "finding", label: "record_finding", primary: m[1], primaryMono: true, chip: m[2], itemId: m[1], status: m[2] };
  }
  if ((m = text.match(RE.recordRate))) {
    return { kind: "rate", icon: "scale", cls: "finding", label: "record_rate_comparison", primary: "", verdict: m[1] };
  }
  if ((m = text.match(RE.extracted))) {
    return {
      kind: "extracted",
      icon: "doc",
      label: "read from the form",
      primary: m[2].trim() || "(no item read)",
      detail: [m[1].trim(), m[3].trim(), m[4].trim()].filter(Boolean).join(" · "),
      category: m[1].trim(),
      url: m[4].trim(),
    };
  }
  return { kind: "plain", icon: "dot", primary: text };
}

// ---------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------

const APP = document.getElementById("app");
let disposeView = null;

async function render() {
  if (disposeView) {
    try {
      disposeView();
    } catch (err) {
      console.error("view cleanup failed", err);
    }
    disposeView = null;
  }
  APP.replaceChildren(h("p", { class: "muted", style: { padding: "24px 0" } }, "Loading…"));
  window.scrollTo(0, 0);

  const parts = (location.hash.replace(/^#/, "") || "/").split("/").filter(Boolean);
  try {
    if (parts.length === 0) disposeView = await viewQueue();
    else if (parts[0] === "run" && parts[1]) disposeView = await viewRun(decodeURIComponent(parts[1]));
    else if (parts[0] === "package" && parts[1]) disposeView = await viewPackage(decodeURIComponent(parts[1]));
    else viewNotFound();
  } catch (err) {
    if (!(err instanceof ApiError)) console.error(err);
    APP.replaceChildren(
      h("div", { class: "view-head" }, h("div", {}, h("h1", {}, "That did not work"))),
      h("div", { class: "banner bad" }, String(err && err.message ? err.message : err)),
      h("p", { style: { marginTop: "14px" } }, h("a", { class: "btn", href: "#/" }, "Back to the queue"))
    );
  }
}

function viewNotFound() {
  document.title = "ProofPack workbench — not found";
  APP.replaceChildren(
    h("div", { class: "view-head" }, h("div", {}, h("h1", {}, "No such page"))),
    h(
      "div",
      { class: "card" },
      h("p", { class: "muted", style: { margin: "0 0 12px" } }, `The workbench has no view for ${location.hash || "#/"}.`),
      h("a", { class: "btn btn-primary", href: "#/" }, "Back to the queue")
    )
  );
}

// ---------------------------------------------------------------------------
// View: queue
// ---------------------------------------------------------------------------

async function viewQueue() {
  document.title = "ProofPack workbench";
  let state = await api("/api/state");

  const activeHost = h("section", { class: "sec" });
  const packagesHost = h("section", { class: "sec" });
  const root = h("div", {});

  root.append(
    h(
      "div",
      { class: "view-head" },
      h(
        "div",
        {},
        h("h1", {}, "Reviewer workbench"),
        h(
          "p",
          { class: "lede" },
          "Drop a pre-approval application in, watch the agent check it against the provider's website, " +
            "then read the evidence package it leaves behind. Everything runs on this machine."
        )
      )
    )
  );

  if (FAKE_RUN) {
    root.append(
      h(
        "div",
        { class: "banner warn" },
        h("strong", {}, "Demo mode "),
        "(PROOFPACK_FAKE_RUN=1) — new reviews are simulated: no website is visited, no model is called, " +
          "and every finding is fabricated. Demo packages are written to a separate “-demo” directory."
      )
    );
  } else if (!API_KEY_PRESENT) {
    root.append(
      h(
        "div",
        { class: "banner warn" },
        h("strong", {}, "No Gemini API key. "),
        "Finished packages below still open, but new reviews cannot start. Put GEMINI_API_KEY=… in the " +
          ".env file at the repo root and restart the server."
      )
    );
  }

  root.append(activeHost, buildIntake(state), packagesHost);
  APP.replaceChildren(root);

  // Table sort state lives across repaints of the packages section.
  const sort = { key: "reviewed_at", dir: -1 };

  function paintPackages() {
    packagesHost.replaceChildren(
      sectionHead(
        "Review packages",
        h("span", { class: "count" }, `${state.packages.length} on disk`),
        h("span", { class: "grow" }),
        h("span", { class: "mono muted" }, state.output_dir)
      ),
      state.packages.length
        ? packagesTable(state.packages, sort, paintPackages)
        : h(
            "div",
            { class: "empty" },
            "No review packages yet. Start one above — the finished package (report, evidence and hashes) lands in ",
            h("span", { class: "mono" }, state.output_dir),
            "."
          )
    );
  }

  async function paintActive() {
    const active = state.jobs.filter((job) => ACTIVE_STATES.has(job.state));
    if (!active.length) {
      activeHost.replaceChildren();
      return;
    }
    // /api/state omits lines, so pull the full snapshot for the latest one.
    const detailed = await Promise.all(
      active.map((job) => api(`/api/jobs/${encodeURIComponent(job.id)}`).catch(() => job))
    );
    activeHost.replaceChildren(
      sectionHead("In progress", h("span", { class: "count" }, `${detailed.length} running`)),
      h("div", { class: "jobs-strip" }, detailed.map(jobCard))
    );
  }

  paintPackages();
  await paintActive();

  // Poll only while something is moving; the queue is otherwise static.
  let timer = null;
  const tick = async () => {
    try {
      const next = await api("/api/state");
      const wasActive = state.jobs.filter((j) => ACTIVE_STATES.has(j.state)).length;
      const packagesChanged = next.packages.length !== state.packages.length;
      state = next;
      await paintActive();
      if (packagesChanged || (wasActive && !next.jobs.filter((j) => ACTIVE_STATES.has(j.state)).length)) {
        paintPackages();
      }
    } catch (err) {
      /* the toast already told the reviewer; keep polling */
    }
    schedule();
  };
  const schedule = () => {
    clearTimeout(timer);
    if (state.jobs.some((job) => ACTIVE_STATES.has(job.state))) timer = setTimeout(tick, 2000);
    else timer = setTimeout(tick, 10000);
  };
  schedule();

  const stopWindowDrop = preventWindowDrop();
  return () => {
    clearTimeout(timer);
    stopWindowDrop();
  };
}

/** Dropping a PDF outside the drop zone must not navigate away from the app. */
function preventWindowDrop() {
  const swallow = (e) => e.preventDefault();
  window.addEventListener("dragover", swallow);
  window.addEventListener("drop", swallow);
  return () => {
    window.removeEventListener("dragover", swallow);
    window.removeEventListener("drop", swallow);
  };
}

function jobCard(job) {
  const last = job.lines && job.lines.length ? job.lines[job.lines.length - 1] : null;
  let phase = job.kind === "rerun" ? 1 : 0;
  for (const line of job.lines || []) {
    const p = phaseOf(line.text);
    if (p !== null) phase = p;
  }
  const attn = job.state === "needs_input";
  return h(
    "a",
    { class: `card job-card ${attn ? "attn" : ""}`, href: `#/run/${encodeURIComponent(job.id)}` },
    h("div", { class: "shimmer" }, h("i")),
    h(
      "div",
      { class: "job-body" },
      h(
        "div",
        { class: "job-top" },
        h("span", { class: "job-title" }, job.source_pdf || job.slug),
        stateChip(job.state)
      ),
      h(
        "div",
        { class: "job-phase" },
        attn ? "Waiting for your answer" : `${PHASES[phase].label} — ${PHASES[phase].hint}`
      ),
      h(
        "span",
        { class: "job-line mono" },
        attn && job.question ? job.question.text : last ? last.text : "starting…"
      )
    )
  );
}

function buildIntake(state) {
  const sec = h("section", { class: "sec" });
  const canStart = API_KEY_PRESENT || FAKE_RUN;

  // -- left: upload ---------------------------------------------------------
  let picked = null;
  const pickedHost = h("div", {});
  const fileInput = h("input", {
    type: "file",
    accept: "application/pdf,.pdf",
    class: "visually-hidden",
    tabindex: "-1",
  });
  const urlInput = h("input", {
    type: "url",
    placeholder: "https://provider.example.org/classes",
  });
  const startBtn = h("button", { class: "btn btn-primary", type: "button", disabled: true }, "Start review");

  function setFile(file) {
    if (!file) return;
    const isPdf = file.type === "application/pdf" || /\.pdf$/i.test(file.name);
    if (!isPdf) {
      toast(`${file.name} is not a PDF. The workbench reads the OPWDD application form as a PDF.`, "bad");
      return;
    }
    picked = file;
    pickedHost.replaceChildren(
      h(
        "div",
        { class: "picked" },
        icon("doc"),
        h("span", { class: "name" }, file.name),
        h("span", { class: "muted mono" }, fmtBytes(file.size)),
        h(
          "button",
          {
            class: "btn btn-ghost btn-sm",
            type: "button",
            "aria-label": "Remove the chosen file",
            onclick: () => {
              picked = null;
              fileInput.value = "";
              pickedHost.replaceChildren();
              startBtn.disabled = true;
            },
          },
          "×"
        )
      )
    );
    startBtn.disabled = !canStart;
  }

  fileInput.addEventListener("change", () => setFile(fileInput.files[0]));

  const drop = h(
    "div",
    { class: "drop" },
    h("div", { class: "drop-icon" }, icon("doc")),
    h("div", { class: "drop-title" }, "Drop an application PDF here"),
    h("div", { class: "drop-sub" }, "the file stays on this machine — nothing is uploaded anywhere"),
    pickedHost,
    h(
      "button",
      { class: "btn", type: "button", onclick: () => fileInput.click() },
      "Choose a PDF…"
    ),
    h(
      "p",
      { class: "drop-foot" },
      "The agent reads the form, opens the provider's website, screenshots what it finds and hashes " +
        "every capture into the package."
    ),
    fileInput
  );
  drop.addEventListener("dragover", (e) => {
    e.preventDefault();
    drop.classList.add("over");
  });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    drop.classList.remove("over");
    setFile(e.dataTransfer && e.dataTransfer.files ? e.dataTransfer.files[0] : null);
  });

  startBtn.addEventListener("click", async () => {
    if (!picked) return;
    const body = new FormData();
    body.append("file", picked);
    if (urlInput.value.trim()) body.append("url", urlInput.value.trim());
    startBtn.disabled = true;
    startBtn.replaceChildren(spinner(), "Starting…");
    try {
      const job = await api("/api/reviews", { method: "POST", body });
      location.hash = `#/run/${encodeURIComponent(job.job_id)}`;
    } catch (err) {
      startBtn.disabled = false;
      startBtn.replaceChildren(document.createTextNode("Start review"));
    }
  });

  const uploadCard = h(
    "div",
    { class: "card" },
    drop,
    h(
      "label",
      { class: "field" },
      h("span", {}, "Provider website (optional override)"),
      urlInput
    ),
    h(
      "div",
      { class: "row", style: { marginTop: "12px" } },
      startBtn,
      h(
        "span",
        { class: "muted small" },
        canStart
          ? "Leave the URL blank to use the link on the form; the agent asks if it cannot find one."
          : "Disabled — no API key configured."
      )
    )
  );

  // -- right: samples -------------------------------------------------------
  const sampleList = h("ul", { class: "samples" });
  for (const sample of state.samples) {
    const actions = h("div", { class: "s-actions" });
    if (sample.package_slug) {
      actions.append(
        h(
          "a",
          { class: "btn btn-sm", href: `#/package/${encodeURIComponent(sample.package_slug)}` },
          "Open package"
        )
      );
    }
    const runBtn = h(
      "button",
      { class: `btn btn-sm ${sample.package_slug ? "" : "btn-primary"}`.trim(), type: "button", disabled: !canStart },
      sample.package_slug ? "Re-review" : "Review"
    );
    runBtn.addEventListener("click", async () => {
      runBtn.disabled = true;
      runBtn.replaceChildren(spinner());
      try {
        const job = await api("/api/reviews", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sample: sample.name }),
        });
        location.hash = `#/run/${encodeURIComponent(job.job_id)}`;
      } catch (err) {
        runBtn.disabled = false;
        runBtn.replaceChildren(document.createTextNode(sample.package_slug ? "Re-review" : "Review"));
      }
    });
    actions.append(runBtn);
    sampleList.append(
      h(
        "li",
        {},
        h(
          "div",
          { style: { minWidth: "0" } },
          h("div", { class: "s-name" }, sample.name),
          h(
            "div",
            { class: "s-meta" },
            fmtBytes(sample.size_bytes),
            sample.package_slug ? " · reviewed" : " · not reviewed yet"
          )
        ),
        actions
      )
    );
  }

  const samplesCard = h(
    "div",
    { class: "card" },
    h("h3", { style: { fontSize: "15px" } }, "Or pick a sample application"),
    h(
      "p",
      { class: "muted small", style: { margin: "0 0 6px" } },
      "Seven synthetic OPWDD forms ship with the tool — no real participant data."
    ),
    sampleList
  );

  sec.append(
    sectionHead("Review an application"),
    h("div", { class: "intake" }, uploadCard, samplesCard)
  );
  return sec;
}

function packagesTable(packages, sort, repaint) {
  // Widths are declared so the long category chips cannot starve the request
  // column; the table scrolls horizontally below its min-width instead.
  const columns = [
    {
      key: "participant_name",
      label: "Participant",
      width: "13%",
      cell: (p) =>
        h(
          "div",
          {},
          h(
            "a",
            { class: "t-strong", href: `#/package/${encodeURIComponent(p.slug)}` },
            p.participant_name || "(no name)"
          ),
          h("div", { class: "t-sub" }, p.participant_age != null ? `age ${p.participant_age}` : "age not stated")
        ),
    },
    {
      key: "requested_item",
      label: "Request",
      width: "24%",
      cell: (p) =>
        h(
          "div",
          {},
          h("div", {}, p.requested_item || "—"),
          h("div", { class: "t-sub" }, p.provider_name || "provider not stated")
        ),
    },
    {
      key: "category_display",
      label: "Category",
      width: "17%",
      cell: (p) => h("span", { class: "chip internal chip-wrap" }, p.category_display || p.category || "—"),
    },
    {
      key: "rate_verdict",
      label: "Rate",
      width: "14%",
      cell: (p) => h("span", { class: `chip ${VERDICT_CLASS[p.rate_verdict] || "needs-review"} chip-wrap` }, p.rate_verdict || "—"),
    },
    {
      key: "checks",
      label: "Website checks",
      width: "13%",
      value: (p) => (p.website_total ? (p.counts.found || 0) / p.website_total : -1),
      cell: (p) =>
        h(
          "div",
          { class: "cell-checks" },
          segBar(p.counts, p.website_total),
          h("div", { class: "n mono" }, `${p.counts.found || 0}/${p.website_total} found`)
        ),
    },
    {
      key: "attention",
      label: "Attention",
      width: "11%",
      value: (p) => (p.counts.not_found || 0) * 10 + (p.warnings || 0),
      cell: (p) => {
        const bits = [];
        if (p.counts.not_found) bits.push(h("span", { class: "badge-count bad" }, `${p.counts.not_found} not found`));
        if (p.counts.needs_review) bits.push(h("span", { class: "badge-count warn" }, `${p.counts.needs_review} to review`));
        if (p.warnings) bits.push(h("span", { class: "badge-count warn" }, `${p.warnings} warning${p.warnings > 1 ? "s" : ""}`));
        if (!bits.length) bits.push(h("span", { class: "muted" }, "—"));
        return h("div", { class: "badge-stack" }, bits);
      },
    },
    {
      key: "estimated_cost_usd",
      label: "Cost",
      width: "6%",
      cell: (p) => h("span", { class: "mono cell-nowrap" }, fmtMoney(p.estimated_cost_usd)),
    },
    {
      key: "reviewed_at",
      label: "Reviewed",
      width: "9%",
      cell: (p) =>
        h(
          "div",
          { class: "cell-nowrap" },
          relTime(p.reviewed_at),
          p.review_count > 1 ? h("div", { class: "t-sub" }, `re-run #${p.review_count}`) : null
        ),
    },
  ];

  const rows = packages.slice().sort((a, b) => {
    const col = columns.find((c) => c.key === sort.key) || columns[columns.length - 1];
    const va = col.value ? col.value(a) : a[sort.key];
    const vb = col.value ? col.value(b) : b[sort.key];
    if (va === vb) return 0;
    if (va === null || va === undefined) return 1;
    if (vb === null || vb === undefined) return -1;
    return (va > vb ? 1 : -1) * sort.dir;
  });

  const thead = h(
    "tr",
    {},
    columns.map((col) =>
      h(
        "th",
        { scope: "col", "aria-sort": sort.key === col.key ? (sort.dir === 1 ? "ascending" : "descending") : "none" },
        h(
          "button",
          {
            type: "button",
            onclick: () => {
              if (sort.key === col.key) sort.dir = -sort.dir;
              else {
                sort.key = col.key;
                sort.dir = col.key === "reviewed_at" ? -1 : 1;
              }
              repaint();
            },
          },
          col.label,
          sort.key === col.key ? h("span", { class: "arrow" }, sort.dir === 1 ? " ▲" : " ▼") : null
        )
      )
    )
  );

  const tbody = h(
    "tbody",
    {},
    rows.map((p) => {
      const tr = h(
        "tr",
        {},
        columns.map((col) => h("td", {}, col.cell(p)))
      );
      tr.addEventListener("click", (e) => {
        if (e.target.closest("a")) return;
        location.hash = `#/package/${encodeURIComponent(p.slug)}`;
      });
      return tr;
    })
  );

  const cols = h(
    "colgroup",
    {},
    columns.map((col) => h("col", col.width ? { style: { width: col.width } } : {}))
  );
  return h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" }, cols, h("thead", {}, thead), tbody));
}

// ---------------------------------------------------------------------------
// View: run
// ---------------------------------------------------------------------------

let checklistCache = null;
async function loadChecklists() {
  if (!checklistCache) checklistCache = await api("/api/checklists");
  return checklistCache;
}

async function viewRun(jobId) {
  let job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
  document.title = `ProofPack — reviewing ${job.source_pdf || job.slug}`;

  // -- header ---------------------------------------------------------------
  const chipHost = h("span", {});
  const stepHost = h("div", { class: "stepper" });
  const bannerHost = h("div", {});

  const head = h(
    "div",
    { class: "card run-head" },
    h(
      "div",
      { class: "run-title" },
      h(
        "div",
        { style: { minWidth: "0" } },
        h("h1", {}, job.kind === "rerun" ? "Re-running the website check" : "Reviewing an application"),
        h(
          "div",
          { class: "meta" },
          h("span", { class: "mono" }, job.source_pdf || "(no source file)"),
          " → package ",
          h("span", { class: "mono" }, job.slug)
        )
      ),
      chipHost
    ),
    stepHost
  );

  // -- trail ----------------------------------------------------------------
  const trail = h("div", { class: "trail", role: "log", "aria-label": "Agent trail" });
  const raw = h("pre", { class: "raw", hidden: true });
  const followBox = h("input", { type: "checkbox", checked: true });
  const rawBtn = h("button", { class: "btn btn-ghost btn-sm", type: "button" }, "Raw log");
  rawBtn.addEventListener("click", () => {
    const showRaw = raw.hidden;
    raw.hidden = !showRaw;
    trail.hidden = showRaw;
    rawBtn.textContent = showRaw ? "Ledger" : "Raw log";
  });

  const trailPanel = h(
    "div",
    { class: "panel" },
    h(
      "div",
      { class: "panel-head" },
      h("h2", {}, "Agent trail"),
      h(
        "div",
        { class: "tools" },
        h("label", { class: "toggle" }, followBox, "Follow"),
        rawBtn
      )
    ),
    trail,
    raw
  );

  // -- findings board -------------------------------------------------------
  const boardBody = h(
    "div",
    { class: "panel-body" },
    h("p", { class: "muted small", style: { margin: 0 } }, "Waiting for the form to be read…")
  );
  const boardPanel = h(
    "div",
    { class: "panel" },
    h("div", { class: "panel-head" }, h("h2", {}, "Findings board")),
    boardBody
  );

  const evStrip = h("div", { class: "ev-strip" });
  const evBody = h(
    "div",
    { class: "panel-body" },
    h("p", { class: "muted small", style: { margin: "0 0 8px" } }, "Screenshots the agent captured, in order."),
    evStrip
  );
  const evPanel = h(
    "div",
    { class: "panel", style: { marginTop: "16px" } },
    h("div", { class: "panel-head" }, h("h2", {}, "Evidence")),
    evBody
  );

  const ask = buildAskDialog(job.id);

  APP.replaceChildren(
    h(
      "div",
      {},
      backLink("#/", "Queue"),
      head,
      bannerHost,
      h("div", { class: "run-cols" }, trailPanel, h("div", {}, boardPanel, evPanel)),
      ask.dialog
    )
  );

  // -- live state -----------------------------------------------------------
  let phase = job.kind === "rerun" ? 1 : 0;
  let rendered = 0;
  let lastCaptureSlot = null;
  let tokensLine = null;
  const seenEvidence = new Set();
  const boardRows = new Map();
  // The board is built asynchronously (it awaits /api/checklists), but a run
  // that is already finished has its whole log replayed synchronously by the
  // SSE stream — so findings arrive before any row exists. Every finding is
  // recorded here regardless of the board, and replayed once the rows land.
  const findingStatus = new Map();
  let rateVerdict = null;
  let rateRow = null;
  let boardReady = false;
  let boardRendered = false;

  function paintSteps(state) {
    const finished = state === "done";
    stepHost.replaceChildren(
      ...PHASES.map((p, i) => {
        const done = finished || i < phase;
        const active = !finished && i === phase && ACTIVE_STATES.has(state);
        return h(
          "div",
          { class: `step ${done ? "done" : ""} ${active ? "active" : ""}`.trim() },
          h("div", { class: "n" }, `Step ${i + 1}`),
          h("div", { class: "l" }, p.label)
        );
      })
    );
  }

  function addEvidence(file) {
    if (seenEvidence.has(file)) return;
    seenEvidence.add(file);
    evStrip.append(
      h(
        "a",
        { href: pkgUrl(job.slug, file), target: "_blank", rel: "noopener", title: file },
        h("img", { src: pkgUrl(job.slug, file), alt: `Evidence capture ${file}`, loading: "lazy" })
      )
    );
  }

  async function buildBoard(category) {
    if (boardReady) return;
    boardReady = true;
    let checklist = null;
    try {
      const all = await loadChecklists();
      checklist = all[category] || null;
    } catch (err) {
      /* the toast covered it — fall through to the id-only board */
    }
    const list = h("ul", { class: "fb" });
    if (checklist) {
      for (const item of checklist.website_items) {
        const chip = statusChip("pending");
        chip.textContent = "Pending";
        const li = h(
          "li",
          {},
          h("div", { style: { minWidth: "0" } }, h("div", { class: "req" }, item.requirement), h("div", { class: "id mono" }, item.id)),
          chip
        );
        boardRows.set(item.id, { li, chip });
        list.append(li);
      }
    }
    const rateChip = statusChip("pending");
    rateChip.textContent = "Pending";
    rateRow = { chip: rateChip };
    boardBody.replaceChildren(
      checklist
        ? h(
            "p",
            { class: "muted small", style: { margin: "0 0 8px" } },
            `${checklist.display_name} — ${checklist.website_items.length} website checks, `,
            `${checklist.internal_count} internal, ${checklist.document_count} need a document.`
          )
        : h("p", { class: "muted small", style: { margin: "0 0 8px" } }, "Findings as they are recorded."),
      list,
      h(
        "div",
        { class: "fb-rate row", style: { justifyContent: "space-between" } },
        h("div", {}, h("div", { class: "req" }, "Rate comparison"), h("div", { class: "id mono" }, "published fee vs the form")),
        rateChip
      )
    );
    boardRendered = true;
    // Catch up on everything that was recorded while the checklist loaded.
    for (const [itemId, status] of findingStatus) markFinding(itemId, status);
    if (rateVerdict !== null) markRate(rateVerdict);
  }

  function markFinding(itemId, status) {
    findingStatus.set(itemId, status);
    if (!boardRendered) return;
    const row = boardRows.get(itemId);
    const chip = statusChip(status);
    if (row) {
      row.li.replaceChild(chip, row.chip);
      row.chip = chip;
      row.li.classList.add("settled");
    } else {
      const li = h(
        "li",
        { class: "settled" },
        h("div", { style: { minWidth: "0" } }, h("div", { class: "req" }, itemId), h("div", { class: "id mono" }, "not on the checklist")),
        chip
      );
      boardRows.set(itemId, { li, chip });
      const list = boardBody.querySelector("ul.fb");
      if (list) list.append(li);
    }
  }

  function markRate(verdict) {
    rateVerdict = verdict;
    if (!rateRow) return;
    const chip = verdictChip(verdict);
    rateRow.chip.replaceWith(chip);
    rateRow.chip = chip;
  }

  function appendLine(entry) {
    raw.append(document.createTextNode(`${entry.t}  ${entry.text}\n`));

    const p = phaseOf(entry.text);
    if (p !== null && p > phase) {
      phase = p;
      // Phases advance with the log lines, not with the job state, so the
      // stepper has to be repainted here — `applyState` only fires on the
      // (rare) state events.
      paintSteps(job.state);
    }
    const info = parseLine(entry.text);

    if (info.kind === "evidence") {
      addEvidence(info.file);
      if (lastCaptureSlot) {
        lastCaptureSlot.append(
          h("img", {
            class: "shot fresh",
            src: pkgUrl(job.slug, info.file),
            alt: `Capture ${info.file}`,
            title: `${info.file} — click to open`,
            loading: "lazy",
            onclick: () => window.open(pkgUrl(job.slug, info.file), "_blank", "noopener"),
          }),
          h("div", { class: "detail mono" }, info.file)
        );
        lastCaptureSlot = null;
        return;
      }
    } else if (info.kind === "no-capture") {
      if (lastCaptureSlot) {
        lastCaptureSlot.append(h("div", { class: "detail" }, "could not locate that text — nothing captured"));
        lastCaptureSlot = null;
        return;
      }
    }

    if (info.kind === "extracted" && info.category) buildBoard(info.category);
    if (info.kind === "finding") markFinding(info.itemId, info.status);
    if (info.kind === "rate") markRate(info.verdict);
    if (info.kind === "tokens") tokensLine = info.detail;

    const primary = h("div", { class: `primary ${info.primaryMono ? "mono break-all" : ""}`.trim() });
    if (info.label) primary.append(h("span", { class: "k mono" }, info.label));
    if (info.gate) primary.append(h("span", { class: "gate-badge" }, "GATE"));
    if (info.primary) primary.append(document.createTextNode(info.primary));
    if (info.kind === "finding" && info.chip) primary.append(" ", statusChip(info.chip));
    if (info.kind === "rate" && info.verdict) primary.append(verdictChip(info.verdict));
    if (info.detail) {
      primary.append(h("div", { class: `detail ${info.detailMono ? "mono break-all" : ""}`.trim() }, info.detail));
    }

    const row = h(
      "div",
      { class: `tr ${info.cls || ""}`.trim() },
      h("div", { class: "t" }, entry.t),
      h("div", { class: "ic" }, icon(info.icon || "dot")),
      primary
    );
    trail.append(row);
    lastCaptureSlot = info.kind === "capture" ? primary : null;

    if (followBox.checked) trail.scrollTop = trail.scrollHeight;
  }

  function paintBanner(snap) {
    bannerHost.replaceChildren();
    if (snap.state === "done") {
      bannerHost.append(
        h(
          "div",
          { class: "banner ok", style: { marginTop: "14px" } },
          h("strong", {}, "Review finished. "),
          tokensLine ? `${tokensLine}. ` : "",
          "The package is written and hashed.",
          h(
            "div",
            { class: "row", style: { marginTop: "10px" } },
            h("a", { class: "btn btn-primary", href: `#/package/${encodeURIComponent(snap.slug)}` }, "Open report"),
            h(
              "a",
              { class: "btn", href: pkgUrl(snap.slug, "report.html"), target: "_blank", rel: "noopener" },
              "Open in a new tab"
            )
          )
        )
      );
    } else if (snap.state === "failed") {
      bannerHost.append(
        h(
          "div",
          { class: "banner bad", style: { marginTop: "14px" } },
          h("strong", {}, "The review failed. "),
          snap.error || "No detail was reported.",
          h("div", { class: "row", style: { marginTop: "10px" } }, h("a", { class: "btn", href: "#/" }, "Back to the queue"))
        )
      );
    } else if (snap.state === "cancelled") {
      bannerHost.append(
        h(
          "div",
          { class: "banner warn", style: { marginTop: "14px" } },
          h("strong", {}, "Skipped. "),
          "The review stopped because the question was skipped; nothing was written.",
          h("div", { class: "row", style: { marginTop: "10px" } }, h("a", { class: "btn", href: "#/" }, "Back to the queue"))
        )
      );
    }
  }

  function applyState(snap) {
    job = { ...job, ...snap };
    chipHost.replaceChildren(stateChip(snap.state));
    paintSteps(snap.state);
    paintBanner(snap);
    document.title =
      snap.state === "done"
        ? `ProofPack — ${snap.slug} ready`
        : `ProofPack — reviewing ${snap.source_pdf || snap.slug}`;
    if (snap.state === "needs_input" && snap.question) ask.open(snap.question);
    else ask.close();
    for (const file of snap.evidence || []) addEvidence(file);
  }

  applyState(job);

  // -- transport: SSE, falling back to polling ------------------------------
  let ended = false;
  let poller = null;
  let source = null;

  const startPolling = () => {
    if (poller || ended) return;
    const tick = async () => {
      try {
        const snap = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
        for (const entry of snap.lines.slice(rendered)) {
          appendLine(entry);
          rendered += 1;
        }
        applyState(snap);
        if (!ACTIVE_STATES.has(snap.state)) {
          ended = true;
          clearInterval(poller);
          poller = null;
        }
      } catch (err) {
        /* toast shown; keep trying */
      }
    };
    poller = setInterval(tick, 2000);
    tick();
  };

  try {
    source = new EventSource(`/api/jobs/${encodeURIComponent(jobId)}/events`);
    source.addEventListener("line", (e) => {
      appendLine(JSON.parse(e.data));
      rendered += 1;
    });
    source.addEventListener("state", (e) => applyState(JSON.parse(e.data)));
    source.addEventListener("end", () => {
      ended = true;
      if (source) source.close();
    });
    source.addEventListener("error", () => {
      if (ended) return;
      // A dropped stream must not silently stall the view.
      if (source) source.close();
      source = null;
      toast("Live stream interrupted — falling back to polling every 2 seconds.", "info");
      startPolling();
    });
  } catch (err) {
    startPolling();
  }

  return () => {
    if (source) source.close();
    if (poller) clearInterval(poller);
    if (ask.dialog.open) ask.dialog.close();
  };
}

/** The needs_input modal: the same question the CLI asks, in the browser. */
function buildAskDialog(jobId) {
  const qHost = h("p", { class: "ask-q" });
  const optsHost = h("div", { class: "ask-opts" });
  const input = h("input", { type: "text", placeholder: "Type your answer" });
  const inputField = h("label", { class: "field" }, h("span", {}, "Your answer"), input);
  const continueBtn = h("button", { class: "btn btn-primary", type: "button" }, "Continue");
  const skipBtn = h("button", { class: "btn", type: "button" }, "Skip this review");
  const dialog = h(
    "dialog",
    { class: "ask" },
    h("div", { class: "ask-head" }, "The agent needs your answer"),
    h("div", { class: "ask-body" }, qHost, optsHost, inputField),
    h("div", { class: "ask-foot" }, skipBtn, continueBtn)
  );

  let busy = false;
  async function send(payload) {
    if (busy) return;
    busy = true;
    continueBtn.disabled = skipBtn.disabled = true;
    try {
      await api(`/api/jobs/${encodeURIComponent(jobId)}/answer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (dialog.open) dialog.close();
    } finally {
      busy = false;
      continueBtn.disabled = skipBtn.disabled = false;
    }
  }

  continueBtn.addEventListener("click", () => {
    const value = input.value.trim();
    if (!value) {
      input.focus();
      toast("Type an answer, or choose Skip to stop this review.", "bad");
      return;
    }
    send({ value });
  });
  skipBtn.addEventListener("click", () => send({ skip: true }));
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      continueBtn.click();
    }
  });
  // Esc would abandon the question silently; keep the modal until it is answered.
  dialog.addEventListener("cancel", (e) => e.preventDefault());

  let shownFor = null;
  return {
    dialog,
    open(question) {
      const key = `${question.field}:${question.text}`;
      if (shownFor === key && dialog.open) return;
      shownFor = key;
      qHost.textContent = question.text;
      optsHost.replaceChildren(
        ...(question.options || []).map((option) =>
          h(
            "button",
            { class: "btn", type: "button", onclick: () => send({ value: option }) },
            option
          )
        )
      );
      input.value = "";
      if (!dialog.open) dialog.showModal();
      input.focus();
    },
    close() {
      if (dialog.open) dialog.close();
      shownFor = null;
    },
  };
}

// ---------------------------------------------------------------------------
// View: package
// ---------------------------------------------------------------------------

async function viewPackage(slug) {
  const report = await api(`/api/packages/${encodeURIComponent(slug)}`);
  const app = report.application || {};
  document.title = `ProofPack — ${app.participant_name || slug}`;

  const counts = { found: 0, not_found: 0, needs_review: 0, internal: 0, needs_document: 0, pass: 0, flag: 0 };
  let websiteTotal = 0;
  for (const finding of report.findings || []) {
    if (finding.status in counts) counts[finding.status] += 1;
    if (finding.source === "agent") websiteTotal += 1;
  }
  const websiteCounts = { found: 0, not_found: 0, needs_review: 0 };
  for (const finding of report.findings || []) {
    if (finding.source === "agent" && finding.status in websiteCounts) websiteCounts[finding.status] += 1;
  }

  const panelHost = h("div", {});
  const body = h("div", { class: "pkg-body" });
  const iframe = h("iframe", {
    class: "report",
    src: pkgUrl(slug, "report.html"),
    title: `Verification report for ${app.participant_name || slug}`,
  });
  body.append(h("div", { style: { minWidth: "0" } }, iframe));

  const actions = h("div", { class: "pkg-actions" });

  const head = h(
    "div",
    { class: "card pkg-head" },
    h(
      "div",
      { class: "pkg-title" },
      h(
        "div",
        { style: { minWidth: "0" } },
        h(
          "h1",
          {},
          app.participant_name || "(no name on the form)",
          app.participant_age != null ? h("span", { class: "muted", style: { fontWeight: "400" } }, ` · age ${app.participant_age}`) : null
        ),
        h("div", { class: "meta" }, app.requested_item || "—", app.provider_name ? ` · ${app.provider_name}` : "")
      ),
      h(
        "div",
        { class: "mono muted", style: { textAlign: "right" } },
        h("div", {}, slug),
        h("div", {}, `reviewed ${relTime(report.reviewed_at)}`)
      )
    ),
    h(
      "div",
      { class: "pkg-chips" },
      h("span", { class: "chip internal" }, report.category_display || report.category || "—"),
      verdictChip(report.rate_comparison ? report.rate_comparison.verdict : ""),
      report.review_count > 1 ? h("span", { class: "chip needs-document" }, `re-run #${report.review_count}`) : null,
      h(
        "span",
        { class: "mono muted" },
        `${report.model || ""} · ${fmtMoney(report.estimated_cost_usd)} · ${(report.evidence || []).length} captures`
      )
    ),
    h(
      "div",
      { class: "pkg-checks" },
      segBar(websiteCounts, websiteTotal),
      segLegend(websiteCounts)
    ),
    actions
  );

  const banners = h("div", {});
  for (const warning of report.warnings || []) {
    banners.append(h("div", { class: "banner warn", style: { marginTop: "12px" } }, h("strong", {}, "Attention: "), warning));
  }
  if (app.denial_reason) {
    banners.append(
      h(
        "div",
        { class: "banner appeal", style: { marginTop: "12px" } },
        h("strong", {}, "APPEAL"),
        ` — original denial ${app.denial_date || ""}: “${app.denial_reason}”`
      )
    );
  }

  APP.replaceChildren(h("div", {}, backLink("#/", "Queue"), head, banners, panelHost, body));

  // -- integrity ------------------------------------------------------------
  const integrityBtn = h("button", { class: "btn", type: "button" }, "Verify integrity");
  let integrityPanel = null;
  integrityBtn.addEventListener("click", async () => {
    if (integrityPanel) {
      integrityPanel.remove();
      integrityPanel = null;
      integrityBtn.textContent = "Verify integrity";
      return;
    }
    integrityBtn.disabled = true;
    integrityBtn.replaceChildren(spinner(), "Hashing…");
    try {
      const data = await api(`/api/packages/${encodeURIComponent(slug)}/integrity`);
      integrityPanel = buildIntegrityPanel(data, () => {
        integrityPanel.remove();
        integrityPanel = null;
        integrityBtn.textContent = "Verify integrity";
      });
      panelHost.append(integrityPanel);
      integrityPanel.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } finally {
      integrityBtn.disabled = false;
      integrityBtn.replaceChildren(document.createTextNode(integrityPanel ? "Hide integrity check" : "Verify integrity"));
    }
  });

  // -- run.log --------------------------------------------------------------
  const logBtn = h("button", { class: "btn", type: "button" }, "run.log");
  let logPanel = null;
  logBtn.addEventListener("click", async () => {
    if (logPanel) {
      logPanel.remove();
      logPanel = null;
      return;
    }
    logBtn.disabled = true;
    try {
      const text = await api(`/api/packages/${encodeURIComponent(slug)}/log`);
      const pre = h("pre", { class: "raw" }, String(text));
      logPanel = h(
        "div",
        { class: "panel", style: { marginTop: "14px" } },
        h(
          "div",
          { class: "panel-head" },
          h("h2", {}, "run.log — every step, as it was logged"),
          h(
            "button",
            {
              class: "btn btn-ghost btn-sm",
              type: "button",
              onclick: () => {
                logPanel.remove();
                logPanel = null;
              },
            },
            "Hide"
          )
        ),
        pre
      );
      panelHost.append(logPanel);
    } finally {
      logBtn.disabled = false;
    }
  });

  // -- re-run ---------------------------------------------------------------
  const rerunBtn = h("button", { class: "btn", type: "button", disabled: !(API_KEY_PRESENT || FAKE_RUN) }, "Re-run website check");
  rerunBtn.addEventListener("click", async () => {
    const ok = window.confirm(
      "Re-run the website verification for this package?\n\n" +
        "The agent visits the provider's site again and REPLACES the website findings, the rate " +
        "comparison and the evidence captures. Your reviewer notes are kept."
    );
    if (!ok) return;
    rerunBtn.disabled = true;
    rerunBtn.replaceChildren(spinner(), "Starting…");
    try {
      const job = await api("/api/reviews", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ package: slug }),
      });
      location.hash = `#/run/${encodeURIComponent(job.job_id)}`;
    } catch (err) {
      rerunBtn.disabled = false;
      rerunBtn.replaceChildren(document.createTextNode("Re-run website check"));
    }
  });

  // -- chat -----------------------------------------------------------------
  const chatBtn = h("button", { class: "btn", type: "button" }, "Reviewer chat");
  let chatPanel = null;
  chatBtn.addEventListener("click", () => {
    if (chatPanel) {
      chatPanel.remove();
      chatPanel = null;
      body.classList.remove("with-chat");
      chatBtn.textContent = "Reviewer chat";
      return;
    }
    chatPanel = buildChat(slug, iframe);
    body.append(chatPanel);
    body.classList.add("with-chat");
    chatBtn.textContent = "Hide chat";
  });

  actions.append(
    integrityBtn,
    rerunBtn,
    h("a", { class: "btn", href: pkgUrl(slug, "report.html"), target: "_blank", rel: "noopener" }, "Open report in a new tab"),
    logBtn,
    chatBtn
  );

  return () => {};
}

function buildIntegrityPanel(data, onClose) {
  const list = h("ul", { class: "int-list" });
  for (const capture of data.captures || []) {
    list.append(
      h(
        "li",
        {},
        h(
          "div",
          { class: "int-file" },
          h("span", { class: capture.ok ? "mark-ok" : "mark-bad" }, capture.ok ? "✓" : "✗"),
          h("span", { class: "name mono" }, capture.file)
        ),
        capture.ok
          ? h("div", { class: "int-hash mono break-all" }, h("span", { class: "lbl" }, "sha-256"), capture.actual)
          : h(
              "div",
              {},
              h("div", { class: "int-hash mono break-all" }, h("span", { class: "lbl" }, "expected"), capture.expected || "—"),
              h("div", { class: "int-hash mono break-all" }, h("span", { class: "lbl" }, "actual"), capture.actual || "(missing)")
            )
      )
    );
  }

  const banner = data.ok
    ? h(
        "div",
        { class: "banner ok" },
        h("strong", {}, "Package intact. "),
        `Every capture still hashes to the value recorded in manifest.json (checked ${relTime(data.checked_at)}).`
      )
    : h(
        "div",
        { class: "banner bad" },
        h("strong", {}, "Problems found. "),
        h("ul", { style: { margin: "6px 0 0", paddingLeft: "20px" } }, (data.problems || []).map((p) => h("li", {}, p)))
      );

  return h(
    "div",
    { class: "panel", style: { marginTop: "14px" } },
    h(
      "div",
      { class: "panel-head" },
      h("h2", {}, "Integrity check"),
      h("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: onClose }, "Hide")
    ),
    h(
      "div",
      { class: "panel-body" },
      banner,
      h(
        "p",
        { class: "muted small" },
        "Each screenshot is re-read from disk and hashed with SHA-256, then compared with the hash written " +
          "into the manifest when the capture was taken."
      ),
      list
    )
  );
}

function buildChat(slug, iframe) {
  const thread = h("div", { class: "thread" });
  const input = h("textarea", {
    rows: "2",
    placeholder: API_KEY_PRESENT ? "Ask about this package, or ask for a change…" : "Chat needs a Gemini API key",
    disabled: !API_KEY_PRESENT,
  });
  const sendBtn = h("button", { class: "btn btn-primary", type: "button", disabled: !API_KEY_PRESENT }, "Send");

  thread.append(
    h(
      "div",
      { class: "msg bot" },
      "Ask about this package in plain language — “what did you not find?”, “change published_fees to needs " +
        "review”, “add a note that I called the provider”. Changes are written into report.json and the report " +
        "is re-rendered."
    )
  );
  if (!API_KEY_PRESENT) {
    thread.append(
      h(
        "div",
        { class: "msg err" },
        "No Gemini API key found. Put GEMINI_API_KEY=… in the .env file at the repo root and restart the server."
      )
    );
  }

  const suggestions = [
    "what did you not find?",
    "change published_fees to needs review",
    "add a note that I called the provider",
  ];

  async function send(message) {
    if (!message.trim() || sendBtn.disabled) return;
    thread.append(h("div", { class: "msg user" }, message));
    input.value = "";
    input.disabled = sendBtn.disabled = true;
    const pending = h("div", { class: "msg bot" }, spinner(), " thinking…");
    thread.append(pending);
    thread.scrollTop = thread.scrollHeight;
    try {
      const result = await api(`/api/packages/${encodeURIComponent(slug)}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      });
      pending.replaceWith(h("div", { class: "msg bot" }, result.reply || "(no reply)"));
      if (result.changed) {
        thread.append(h("div", { class: "msg note" }, "report updated — reloading the view"));
        iframe.src = `${pkgUrl(slug, "report.html")}?t=${Date.now()}`;
      }
    } catch (err) {
      pending.replaceWith(h("div", { class: "msg err" }, String(err && err.message ? err.message : err)));
    } finally {
      input.disabled = sendBtn.disabled = !API_KEY_PRESENT;
      thread.scrollTop = thread.scrollHeight;
      input.focus();
    }
  }

  sendBtn.addEventListener("click", () => send(input.value));
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input.value);
    }
  });

  return h(
    "div",
    { class: "panel chat" },
    h("div", { class: "panel-head" }, h("h2", {}, "Reviewer chat")),
    thread,
    h(
      "div",
      { class: "sugg" },
      suggestions.map((text) =>
        h(
          "button",
          {
            type: "button",
            disabled: !API_KEY_PRESENT,
            onclick: () => {
              input.value = text;
              input.focus();
            },
          },
          text
        )
      )
    ),
    h("div", { class: "composer" }, input, sendBtn)
  );
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

window.addEventListener("hashchange", render);
render();
