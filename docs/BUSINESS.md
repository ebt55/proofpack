# ProofPack — business model, impact and operations

*Written for the Build with Gemini XPRIZE submission (August 2026). Numbers marked "measured"
come from the committed runs in [`output/`](../output/); everything else is a stated
hypothesis to be tested in pilots.*

## The one-paragraph pitch

Every US state runs Medicaid "self-direction" programs where a person with a disability
controls their own service budget. Before that budget can pay for a community class, a
membership or a household item, an agency reviewer must prove the purchase is *publicly
available at a published price* — by visiting the provider's website and saving date-stamped
screenshots for the audit file. It is slow, manual, and inconsistent, and a mistake means the
agency repays the money. ProofPack is an AI agent that does that research and produces an
audit-grade evidence package in minutes, for cents, while a human keeps the approve/deny
decision.

## Category: Professional Services Access

Compliance review is professional-services labor that small human-services non-profits can't
staff at the level the audits demand. ProofPack makes reviewer-grade verification available
per application, on demand, at a price a five-person agency can afford — the same way payroll
or e-signature software made once-specialist work routine.

## Customer, value, model

| | |
|---|---|
| **Who buys** | Fiscal Intermediaries (FIs) and support-brokerage agencies that administer self-directed budgets. New York alone has dozens of FIs serving ~30,000 self-directing participants; nationally, Medicaid HCBS self-direction covers well over a million people across nearly every state (Applied Self-Direction / NCI figures). |
| **Who uses** | The pre-approvals reviewer (5–50 per agency). |
| **Value created** | Reviewer time: 20–40 minutes of manual browsing and screenshotting → ~3 minutes of reading a finished report. Consistency: every reviewer gets the same checklist applied the same way. Audit defence: every "Found" carries a stamped, hashed capture. |
| **Pricing (hypothesis)** | **$3 per reviewed application** (usage-based, no seat minimum) *or* **$149 per reviewer seat / month** for high-volume agencies. Both are 15–100× the measured model cost, leaving room for hosting, support and a browser-capture service. |
| **Cost to serve** | Measured $0.02–$0.19 of Gemini per review (seven committed runs); browser compute is negligible; the marginal cost of a review is well under $0.50 all-in. |
| **B2B / B2C** | B2B, sold to agencies. Participants and providers never pay. |
| **Acquisition** | Pilot-first: 20 anonymized forms reviewed free, agency grades the packages against their own reviewer's work. Channels: state FI associations, self-direction conferences, and the brokers/care managers who submit the forms and feel the delay. |
| **Retention** | The evidence package becomes part of the agency's audit file — once the auditors have seen it, going back to manual screenshots is a step down. Per-agency checklists (YAML) and caps make the product sticky without lock-in. |

## Impact — how we measure it

**Theory of change:** if reviewers stop spending their time gathering evidence, approvals get
faster and more defensible, participants get access to their own budgets sooner, and small
agencies can take on more self-directing participants without hiring.

| Output (we count) | Outcome (we expect) | Evidence |
|---|---|---|
| Applications reviewed; % of website items evidenced automatically (target ≥ 70%; committed runs: 25 of 30 website items concluded, 5 honest Not-Found/Needs-Review) | Reviewer minutes per application: 20–40 → < 5 | Pilot time studies; the tool's own `run.log` and cost lines |
| Fabricated findings (target: **zero**, enforced by code) | Audit findings avoided; agency clawbacks avoided | Package integrity audit (`tests/test_audit_packages.py`); pilot audit outcomes |
| Days from submission to decision | Faster access for participants to classes, memberships, equipment | Agency queue data before/after |
| Cost per review | Agencies serve more participants per reviewer | Billing data |

**Hypotheses to prove in pilots:** (1) reviewers accept ≥ 90% of agent findings without
change (we log every override in chat mode, so acceptance is measurable); (2) turnaround drops
by half; (3) at least one agency's auditor accepts the package format as the audit record.

## AI-native operations — what the AI decides, and what it doesn't

ProofPack is not a chatbot bolted onto a form. Gemini runs the operation:

- **Reads every application** (Gemini PDF understanding with a JSON schema) — decides the
  category, the requested item, the fees, every YES/NO answer.
- **Runs the investigation** — the agent chooses which pages to open, what to read, what to
  search for, what to capture, and whether each requirement is met (`found` / `not_found` /
  `needs_review`), then writes the reviewer summary. Roughly 20–40 model decisions per review,
  each logged.
- **Handles the reviewer's follow-ups** in plain language (chat mode: change a status, add a
  note, re-run the check).

What the AI is *not allowed* to do is decided in code: it cannot record a "Found" without a
capture that exists, cannot quote text that wasn't on a page it visited, cannot write a
timestamp or a hash, and never sees the internal-only questions. That split — model decides,
Python enforces — is the reason the output can go into a Medicaid audit file.

**Evidence of AI in production:** every committed package in [`output/`](../output/) contains
`run.log` (the agent's decision trail), `report.json` (model id, token usage, cost) and hashed
captures; the Google Cloud console for the project shows the Generative Language API traffic
behind them.

## Google Cloud usage

- **Gemini API** (`gemini-3.7-flash`) via a Google AI Studio key on a billed Google Cloud
  project — three call sites: form extraction, the browsing agent, reviewer chat.
- Roadmap: **Cloud Run** for the hosted intake queue and **Google Drive API** for
  a watched-folder intake, because a shared Drive folder is how FI staff already move forms.

## Revenue, expenses, users (hackathon period, May 19 – Aug 17 2026)

- **Revenue:** $0 (May $0 · June $0 · July $0 · August $0). Related-party revenue: $0.
- **Users:** 0 external users; 0 paying. The product was built and validated in the period; the
  first pilot conversations start after submission.
- **Expenses:** Gemini API usage only (a few dollars for development and the committed runs) — 100%
  COGS. Marketing/sales: $0. R&D: founder time, unpaid. G&A: $0.

## Sustaining operations

- **Resources:** one founder-engineer; the product runs on pay-as-you-go Gemini + a small
  Cloud Run footprint, so fixed costs are near zero until pilots convert.
- **Threats:** provider websites that block automated browsers (mitigation: headed capture
  service / licensed product-data APIs); PHI handling requirements (mitigation: redact
  participant identity before any API call, paid-tier no-training terms, retention rules);
  model changes (mitigation: pinned model id + the golden sample set as a regression suite).
- **After the hackathon:** ship the watched-folder intake, run three free pilots with NY FIs,
  convert one to paid, then use per-agency YAML checklists to expand to other states' forms.

## Five-year view (hypothesis)

Serviceable market: ~1,500 agencies administering self-directed budgets in the US, averaging
a few thousand pre-approval decisions a year → roughly 5–10 million reviewable applications a
year, i.e. a $15–30M/yr opportunity at $3 per review before adjacent workflows (invoice
verification, provider re-verification, appeals). Path to profitability: gross margin > 85% at
the measured cost to serve; break-even at ~30 paying agencies on seat pricing, which is a
single-state footprint.

## Pilot offer

Send 20 anonymized, completed pre-approval forms (participant names removed). We return the
review packages within two business days. Your reviewer grades them; if the packages don't save
real time or find something your reviewer would have missed, you owe nothing and keep the
packages.
