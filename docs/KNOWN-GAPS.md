# Known gaps, and what to do next

An honest engineering audit of this prototype, written after building and validating the
tool, then worked from the top — so it doubles as a record of what the audit produced.
Nothing here is hidden in the code: a team inheriting this repo knows exactly where the edges
are.

Priority key: **P1** = fix before anyone relies on this · **P2** = needed for production ·
**P3** = polish.

---

## Closed by this audit

| Was | What shipped |
|---|---|
| **The integrity gates had no unit tests** — the tool's core safety claim rested on code covered only indirectly | Validation moved out of the tool closures onto `VerifySession` (`apply_finding` / `apply_rate_comparison`), and [`tests/test_agent_gates.py`](../tests/test_agent_gates.py) now asserts every rejection path: found-without-evidence, evidence-not-in-manifest, fabricated quote, missing quote, internal item, unknown id, bad status, bad verdict — plus that honest negatives are never harder to record than positives |
| **Chat mode couldn't re-run the research** | `rerun_website_verification` tool + `pipeline.rerun_review()`. Reuses the already-extracted application (no second extraction call), keeps reviewer notes, captures evidence fresh, and stamps the report as a re-run |
| **Clarification only covered a missing URL** | `NeedsClarification` now carries a `field` and `options`; the CLI prompts for whichever is needed. Covered: missing URL, missing requested item, unknown category, and a category that contradicts the form's title |
| **`form_title_hints` was dead config** — loaded from every YAML, never used | Wired up as a category cross-check: if the form's printed title matches a different checklist than the model chose, the tool asks instead of proceeding. Hints stay advisory — an unfamiliar title is not an error |
| **The agent's action trail wasn't kept** | Every run writes `run.log` into the package — each navigation, capture and finding, timestamped |
| **No token/cost accounting** | Usage is accumulated across extraction and every agent turn, stored in `report.json`, shown in the report footer and printed at the end of a run. Prices live in `config.yaml` |
| **Whole-page capture was prompt-only** | The pipeline now warns in the report if a review produced no `full_page` capture, and the package auditor fails without one |
| **Re-running left stale evidence behind** | `EvidenceStore` starts each run clean; the auditor checks disk→manifest as well as manifest→disk |
| **Unfriendly failure with no API key** | Clear message pointing at `.env.example` instead of an SDK traceback |
| **No CI** | [`.github/workflows/tests.yml`](../.github/workflows/tests.yml) runs the offline suite on 3.11/3.12/3.13 plus the package audit |

---

## Remaining gaps

### A. Against the spec

| # | Gap | Priority | Notes |
|---|---|---|---|
| A3 | **Whole-page evidence is PNG only.** Agencies accept "screenshot _or_ PDF". The HTML report prints cleanly by hand, but nothing automates a PDF capture. | P3 | `page.pdf()` in headless Chromium; the manifest/hash flow already generalises. |
| A4 | **"Unclear provider/vendor" doesn't trigger a clarification.** Deliberate: the HRI and OTPS forms have no vendor field at all, so requiring one would produce false prompts on valid applications. The requested item and URL are the fields that actually gate the research. | By design | Revisit if a category appears where the vendor is load-bearing. |

### B. Test coverage

| # | Gap | Priority | Notes |
|---|---|---|---|
| B2 | **No golden-set extraction test.** Sample 01 must extract to *Jordan Ellis / 24 / $80 per session*, but nothing asserts it — an extraction regression after a model or prompt change would be silent. | **P2** | Needs an API key, so it belongs behind a `live` marker or should run against cached API responses. |
| B4 | **No end-to-end test with a stubbed website.** The gates and the deterministic checks are unit-tested; the assembly of a full package isn't. | P2 | A local fixture site served by `http.server` plus a recorded extraction would make the whole pipeline testable offline. |

### C. Robustness & operations

| # | Gap | Priority | Notes |
|---|---|---|---|
| C4 | **No resume/retry of a partially-completed run.** A crash mid-review loses the run (evidence files survive, but the package isn't assembled). | P3 | Checkpoint findings as they're recorded. |
| C5 | **`review-all` is sequential** — seven samples take ~20 minutes of wall clock. | P3 | One browser per worker process; mind API rate limits. |
| C6 | **Provider fee sheets published as PDFs can't be read.** Playwright returns no text for a PDF URL, so a linked price list is invisible to the agent — a genuinely common provider pattern. | **P2** | Detect `.pdf` / `content-type: application/pdf` on navigation and send the bytes to Gemini as a PDF part, exactly like the application form. Evidence capture would need the same treatment. |
| C8 | **One shared browser context per run, no proxy/pool.** Fine for a prototype; retail sites will block a datacentre IP at volume. | P2 | Pairs with the anti-bot strategy in the README's production section. |
| C9 | **No explicit context caching on the agent loop.** A measured run of sample 01 used **333k input tokens across 37 requests** for ~6,200 output tokens: the loop resends the whole conversation each turn. Gemini's implicit caching recovered ~24k of that automatically; the rest is re-billed. | **P2** | Create an explicit cached content for the system prompt + checklist (identical across every turn of a run, and across runs of the same category) and cap the agent's page reads. Cached reads bill at ~25% of input, so this is the single biggest cost lever — a run like the one above should drop well under $0.10. |

### D. Unused data

| # | Gap | Priority | Notes |
|---|---|---|---|
| D2 | **`subject_area` and `valued_outcome` are extracted but never surfaced** — they land in `report.json` and are otherwise unused. | P3 | `subject_area` would genuinely help the agent's "is it subject-based?" check; `valued_outcome` is internal by nature and could be dropped from extraction. |

### E. Product & domain limits

| # | Gap | Priority | Notes |
|---|---|---|---|
| E1 | **Fee caps are checked per form, not per budget year.** The tool can say "$400 is within the $500/year coaching cap", but not "…and $300 of it is already spent." | P2 (blocked) | Needs budget-system integration on the agency side. |
| E2 | **The rate comparison is a model-authored string,** not a structured numeric comparison — no automatic tolerance ("$80" vs "$80.00 + tax") or period normalisation. | **P2** | Have the agent emit the published amount and unit as structured fields, compare numerically in Python, and let the model write only the explanation. Moves one more judgment from the model into code. |
| E3 | **One review = one provider site** — no cross-referencing third-party sources. | By design | An audit file should show the provider's *own* published claims. Revisit only with a reviewer in the loop. |
| E4 | **No multi-item applications.** One form → one requested item is assumed. | P3 | Not present in the sample set; would need a findings-per-item report structure. |
| E5 | **No hosted intake.** Reviewers run a CLI; agencies want to drop PDFs in a shared folder or post to an API and get the package back. | **P1 (product)** | A watched Google Drive folder is the smallest step that matches how FI staff already work; a Cloud Run service with a queue is the production shape. |

---

## Next

1. **E5** — hosted intake (watched Drive folder → Cloud Run queue), so a pilot agency can
   use this without a terminal.
2. **C9** — cache the agent's system prompt with explicit context caching. Now that runs are
   measured, this is the clearest cost win available.
3. **C6** — read PDF-hosted price sheets. The most common real-world evidence source the tool
   currently misses, and it reuses the document-handling path that already exists for forms.
4. **E2** — make the rate comparison structured, so the match/differ verdict is arithmetic
   rather than judgment.
5. **B2 + B4** — a golden extraction test and an offline end-to-end test against a fixture
   site, so the whole pipeline is regression-protected without an API key.
6. **C5** — parallelise `review-all`, which is what makes a golden-set regression run practical
   as a routine check rather than a ten-minute event.
