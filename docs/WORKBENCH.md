# The reviewer workbench

`python verify.py serve` runs ProofPack as a local web page instead of a command line. Same
pipeline, same packages, same report — but the reviewer can drop a PDF in, watch the agent work,
and open the finished package without a terminal.

It is **localhost software**: one user, no login, no hosting. That is a deliberate stopping
point, not an oversight — the honest next step from a CLI, and the shape a hosted version would
grow from (see [KNOWN-GAPS.md](KNOWN-GAPS.md)).

---

## Starting it

```bash
.venv/bin/python verify.py serve                 # → http://127.0.0.1:8765
```

| Flag | Default | What it does |
|---|---|---|
| `--host` | `127.0.0.1` | Bind address. Anything other than loopback prints a loud warning: there is no authentication. |
| `--port` | `8765` | Port. |
| `--out DIR` | `output` (from `config.yaml`) | Which directory holds the packages. Use `--out output/_scratch` to experiment without touching the committed audit packages. |
| `--model ID` | from `config.yaml` | Override the Gemini model id for this session. |
| `--headed` | off | Show the browser window during reviews (also gets past some bot checks). |
| `--open` | off | Open the workbench in your default browser once it is up. |

The startup banner prints the URL, the model, the output directory and whether an API key was
found. **No key is needed to browse**: every committed package, its report, its `run.log` and
the integrity check all work offline. Only starting a review and the reviewer chat need one, and
they say so instead of failing obscurely.

### Demo mode (no key, no website, no cost)

```bash
PROOFPACK_FAKE_RUN=1 .venv/bin/python verify.py serve     # Windows: $env:PROOFPACK_FAKE_RUN=1
```

Reviews are then run by `jobs.fake_run_review`, which emits the same log lines in the same order
as a real run — including a clarification question, two `REJECTED` gate lines and a failed
capture — and writes a small but structurally real package. Two guardrails keep the demo
honest:

- output goes to **`<slug>-demo`**, never the real slug, so a fake run can never overwrite a
  committed audit package;
- a re-run of a non-demo package is **refused** while `PROOFPACK_FAKE_RUN` is set.

Every demo package carries a warning banner in its report and an `agent_summary` saying nothing
in it was verified.

---

## The three views

The whole app is one page with a hash router: `#/`, `#/run/<job_id>`, `#/package/<slug>`.

### 1 · Queue — `#/`

Drag an application PDF onto the drop zone (or use the file picker, with an optional URL
override), or pick one of the bundled samples — a sample that already has a package in the
output directory offers **Open package** and **Re-review** (a full review from the PDF again,
unlike the package view's **Re-run website check**, which keeps the reviewer's notes) instead of
**Review**. Above that sit the jobs currently queued,
running or waiting for an answer; below it, every finished package as a sortable table:
participant and age, requested item and provider, category, rate verdict, a found / not-found
bar with "N of M", warnings, cost and when it was reviewed. Clicking a row opens the package
view; clicking a live job opens its run view.

### 2 · Run — `#/run/<job_id>`

The live view of one review, streamed over server-sent events.

- **Phase stepper** — Extract → Research → Checks → Package, advanced by the log lines below,
  not by the job state.
- **Agent trail** — one row per log line, parsed into an icon, a kind and a detail: pages
  opened, page reads, on-page searches, captures (with a thumbnail as soon as the capture is
  registered), findings as they are recorded, and milestones (extraction, verification start,
  token usage, package written). A "Follow" toggle keeps it scrolled; a "Raw log" toggle shows
  the unparsed lines.
- **GATE rows** — when the tool layer refuses a claim (`REJECTED …`), the trail shows it in red
  with the reason. This is the point of the view: a reviewer can see the model being told *no*
  and correcting itself, which is the same behaviour `tests/test_agent_gates.py` asserts.
- **Findings board** — as soon as the category is known, the checklist's website items are
  listed as *pending* and flip to Found / Not Found / Needs Review as each `record_finding`
  arrives, with a row for the rate comparison.
- **Evidence strip** — thumbnails of the captures made so far, served from the package.
- **Questions** — if the pipeline raises `NeedsClarification` (no URL on the form, an ambiguous
  category, a missing requested item), the job parks in `needs_input` and the view asks. Answer
  it and the run continues from where it stopped; skip it and the job is cancelled.
- **Finish** — a success banner with tokens and cost and a button through to the package, or a
  red banner with the error. The trail stays either way.

A live run of sample 03 through the workbench on 2026-09-06 cost $0.10 of Gemini
(169,329 tokens in / 5,218 out across 23 requests).

### 3 · Package — `#/package/<slug>`

The finished report in a frame, with the tools a reviewer needs around it:

- **Verify integrity** — recomputes the SHA-256 of every capture on disk and compares it to
  `manifest.json`, then runs the full package audit (a "Found" with no evidence, a capture not
  in the manifest, an orphan file, a missing whole-page capture). This is the same
  `preapproval.audit` code that gates CI, so the button and the build agree by construction.
- **Open report in a new tab** — the same self-contained `report.html` that ships in the package.
- **run.log** — the agent's full action trail for that review, as plain text.
- **Re-run website check** — re-visits the site, refreshes findings and evidence, keeps reviewer
  notes, and stamps the report as a re-run.
- **Reviewer chat** — the CLI's `chat` mode in a panel beside the report: *"change
  published_fees to needs review"*, *"add a note that I called the provider"*, *"what did you
  not find?"*. Those three sit under the thread as suggestion chips; clicking one drops it into
  the message box so it can be edited before sending. Edits are recorded as reviewer overrides,
  and when the report changes the frame reloads. Disabled with an explanation when there is no
  API key.

**Verify integrity**, **run.log** and **Reviewer chat** are toggles: the button that opened a
panel closes it again.

---

## How live progress works

The run view uses no websocket and no polling (the queue view is the only part that polls
`/api/state`, every 2 s while a job is moving and every 10 s otherwise). The chain is four links
long:

```
pipeline.RunLog(sink=…)  →  JobManager._append_line  →  queue.Queue per listener  →  SSE stream
```

1. `pipeline.run_review` already logs every step through a `RunLog`. The job manager passes it a
   sink instead of `print`.
2. The sink appends `{t: "HH:MM:SS", text}` to the job's line list (that same list becomes
   `run.log` in the package) and fans the line out to every subscribed queue.
3. Each open SSE stream is a subscriber. On connect it gets the current job snapshot, then every
   line so far, then live lines as they happen — subscription and replay happen under one lock,
   so a line can never fall through the gap.
4. The browser parses the line text. **The log lines are the API**: their prefixes are stable, and
   changing one changes the UI.

| Line | Means |
|---|---|
| `Extracting application from <pdf> ...` | Phase: extract |
| `  -> <category> \| <item> \| <provider> \| <url or NO URL>` | What the form said; the findings board loads this category's checklist |
| `Verifying on the provider's website (<url>) ...` | Phase: research |
| `Re-running website verification for <pdf> ...` | Same, for a re-run |
| `open_url: <url>` | Agent navigated |
| `read_page: offset=<n>` | Agent read page text |
| `find_on_page: '<query>'` | Agent searched the page |
| `list_links: filter='<f>'` | Agent listed links |
| `capture_page: '<label>'` | Whole-page capture requested |
| `capture_evidence: '<locate_text>' ('<label>')` | Targeted capture requested |
| `  -> evidence/<file>.png` | A capture was stored (this is where the thumbnail comes from) |
| `  -> could not locate text; no capture` | The locate text wasn't on the page — nothing was captured |
| `record_finding: <id> = <found\|not_found\|needs_review>` | A finding was accepted |
| `record_rate_comparison: <verdict>` | The rate verdict was accepted |
| `REJECTED record_finding <id>: <reason>` | **Gate:** the claim was refused |
| `REJECTED record_rate_comparison: <reason>` | **Gate:** the verdict was refused |
| `Tokens: <n> in / <n> out across <n> request(s) — estimated $<x>` | Phase: checks / packaging |
| `Report package written to <path>` | Done |

Reviews run on **one background worker thread**, FIFO. Deliberately not asyncio: the pipeline
drives Playwright's *sync* API, which refuses to run on a thread with a running event loop. So
the HTTP layer stays async and every review runs on a thread that never touches a loop.

### Job states

| State | Meaning |
|---|---|
| `queued` | Accepted, waiting for the worker |
| `running` | The pipeline is executing |
| `needs_input` | Parked on a `NeedsClarification`; `question` holds `{field, text, options}` |
| `done` | Package written; `report_ready` says whether `report.html` exists |
| `failed` | `error` holds `"<ExceptionType>: <message>"` (or the friendly no-API-key message) |
| `cancelled` | The reviewer skipped the question |

`done`, `failed` and `cancelled` are terminal; the stream sends an `end` event and closes.

---

## HTTP API

All responses are JSON unless noted. Errors are always `{"error": "<message>"}` with a 4xx/5xx
status — including 404s for unknown routes — so the frontend only ever reads `.error`.

| Method & path | Returns |
|---|---|
| `GET /` | The workbench HTML shell |
| `GET /static/<file>` | `static/` — `theme.css`, `app.css`, `app.js` |
| `GET /api/state` | `{api_key_present, model, output_dir, samples[], packages[], jobs[]}` — samples list their package slug if one exists; packages carry the counts, rate verdict, cost and evidence totals the queue table shows; jobs are snapshots without their lines |
| `GET /api/checklists` | Every category: `{display_name, adults_only, website_items[], internal_count, document_count, fee_caps[]}` |
| `POST /api/reviews` | Start a review. `multipart/form-data` with `file=<pdf>` (+ optional `url`), or JSON `{"sample": "<name>.pdf", "url"?}` or `{"package": "<slug>"}` (re-run). → `202 {job_id, slug}` |
| `GET /api/jobs/<id>` | The full job snapshot, including `lines[]`, `question`, `error`, `report_ready`, `evidence[]` |
| `GET /api/jobs/<id>/events` | `text/event-stream`: a `state` event, then a `line` event per existing line, then live `line` / `state` events, a `: ping` comment every 15 s, and a final `end` event |
| `POST /api/jobs/<id>/answer` | `{"value": "..."}` resumes a parked job; `{"skip": true}` cancels it |
| `GET /api/packages/<slug>` | That package's `report.json` verbatim, plus `slug` |
| `GET /api/packages/<slug>/integrity` | `{ok, checked_at, problems[], captures: [{file, expected, actual, ok}]}` — hashes recomputed from disk, never read back from the manifest |
| `GET /api/packages/<slug>/log` | `text/plain` `run.log` |
| `POST /api/packages/<slug>/chat` | `{"message": "..."}` → `{reply, changed}`; `changed` is true when the report was edited or re-run |
| `GET /packages/<slug>/<path>` | A file from that package (`report.html`, `report.json`, `manifest.json`, `run.log`, `evidence/*.png`), `Cache-Control: no-store`. Paths that resolve outside the package directory get a 404 |

Details worth knowing before writing a client:

- **`POST /api/jobs/<id>/answer` returns the job snapshot** (without lines), not an empty body,
  so the caller sees the new state without a follow-up request. `409` if the job isn't waiting.
- **The SSE stream emits one `state` event before replaying lines**, so a client that connects
  late knows what it is looking at before the backlog arrives.
- **`POST /api/reviews`** answers `503` when no API key is configured (with the message telling
  you to set `GEMINI_API_KEY`), `404` for an unknown sample or package, `400` for a non-PDF, a
  file over 25 MB or an unrecognised body, and `409` when a job for the same slug is already
  queued, running or waiting for an answer. `POST /api/packages/<slug>/chat` answers `503` with
  the same message.
- **In demo mode** the review slug gets a `-demo` suffix, and a re-run of a package without that
  suffix is refused with `400`.
- Uploads are written to `uploads/` at the repo root under a sanitised filename, must start with
  `%PDF`, and are capped at 25 MB. `uploads/` is gitignored.

---

## Security notes

Read this before showing the workbench to anyone but yourself.

- **Loopback only, no authentication.** `serve` binds `127.0.0.1` by default. There are no
  users, no sessions and no permissions: anyone who can reach the port can read every package,
  start reviews (spending API credit) and edit reports through the chat. Binding to another
  address prints a warning; it does not add a login.
- **Packages contain PHI in production.** The samples are synthetic, but a real application form
  carries the participant's name and age, and the uploaded PDF is kept in `uploads/`. Hosting
  this means auth, per-agency isolation, encrypted storage and retention rules first — see
  [KNOWN-GAPS.md](KNOWN-GAPS.md).
- **One worker thread.** Reviews are strictly serial; a second submission queues behind the first.
- **Each open SSE stream holds one threadpool thread** for as long as it is connected. That is
  fine for one reviewer with a couple of tabs, and is one of the reasons this is a single-user
  tool rather than a service.
- **The reviewer chat runs inline in the request**, so a chat message that triggers a re-run
  blocks that HTTP request until the run finishes. One chat session (one history, one
  `report.json` on disk) is kept per package, and a per-package lock makes messages for the same
  package take turns rather than interleave.
- Package files are served with `Cache-Control: no-store`, and any path that resolves outside
  the package directory is a 404 (asserted in `tests/test_server.py`).

---

## Re-rendering reports

`report.html` is generated from `report.json`, so a template or stylesheet change doesn't
require re-running any review — or any API call:

```bash
.venv/bin/python verify.py render --all                       # every package in the output dir
.venv/bin/python verify.py render output/01-community-class-gallopnyc
```

Only `report.html` is rewritten. `report.json`, `manifest.json`, `run.log` and `evidence/*.png`
are the audit record and are never touched, which is also why the package audit still passes
after a re-render.
