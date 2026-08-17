"""The website-verification agent.

Claude drives a small set of browser tools through the Anthropic SDK tool
runner. The critical property: the model can only *decide*; all evidence is
produced and validated by deterministic code:

  - `record_finding(status="found")` is REJECTED unless it references a capture
    that actually exists in the evidence store;
  - quotes are REJECTED unless they appear verbatim in the text of a page the
    agent actually visited this session (anti-fabrication);
  - timestamps, URLs and hashes are burned in by the evidence store, never
    written by the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from anthropic import Anthropic, beta_tool

from .browser import Browser
from .config import Checklist, ToolConfig
from .evidence import EvidenceStore
from .models import ApplicationData, Finding, RateComparison, Status, TokenUsage

VALID_AGENT_STATUSES = {"found", "not_found", "needs_review"}
VALID_VERDICTS = {
    "matches application exactly",
    "differs from application",
    "not published",
    "could not verify",
}


@dataclass
class VerifySession:
    app: ApplicationData
    checklist: Checklist
    browser: Browser
    store: EvidenceStore
    page_text_limit: int
    log: Callable[[str], None]
    visited: dict[str, str] = field(default_factory=dict)  # url -> full page text
    findings: dict[str, Finding] = field(default_factory=dict)
    rate_comparison: Optional[RateComparison] = None

    def remember_page(self) -> None:
        text = self.browser.page_text()
        if text:
            self.visited[self.browser.page.url] = text

    def quote_appears_on_a_visited_page(self, quote: str) -> bool:
        needle = re.sub(r"\s+", " ", quote).strip().lower()
        if not needle:
            return False
        for text in self.visited.values():
            if needle in re.sub(r"\s+", " ", text).lower():
                return True
        return False

    # -- integrity gates ----------------------------------------------------
    #
    # These are the guarantees behind "no hallucinated findings". They live
    # here (not inside the tool closures) so they can be unit-tested directly
    # without the SDK or a browser: see tests/test_agent_gates.py.

    def apply_finding(
        self,
        item_id: str,
        status: str,
        note: str,
        quote: str = "",
        evidence_file: str = "",
    ) -> str:
        """Validate and record one finding. Returns the message shown to the model."""
        website_ids = [i.id for i in self.checklist.website_items]
        if item_id not in website_ids:
            return f"REJECTED: unknown item_id {item_id!r}. Valid ids: {', '.join(website_ids)}"
        if status not in VALID_AGENT_STATUSES:
            return f"REJECTED: status must be one of {sorted(VALID_AGENT_STATUSES)}"

        if status == "found":
            if not evidence_file:
                return (
                    "REJECTED: status 'found' requires evidence_file. Capture the evidence "
                    "first (capture_evidence / capture_page), then record the finding."
                )
            if not self.store.has_file(evidence_file):
                return (
                    f"REJECTED: {evidence_file!r} is not in the evidence manifest. Use the "
                    "exact filename a capture tool returned."
                )
            if not quote:
                return "REJECTED: status 'found' requires a verbatim quote from the page."
        if quote and not self.quote_appears_on_a_visited_page(quote):
            return (
                "REJECTED: that quote does not appear on any page visited this session. "
                "Quotes must be verbatim page text — re-read the page and copy exactly."
            )

        item = next(i for i in self.checklist.website_items if i.id == item_id)
        evidence_url = None
        if evidence_file:
            evidence_url = next(
                (r.url for r in self.store.records if r.file == evidence_file), None
            )
        self.findings[item_id] = Finding(
            item_id=item_id,
            form_question=item.form_question,
            requirement=item.requirement,
            status=Status(status),
            note=note,
            quote=quote or None,
            evidence_url=evidence_url or self.current_url,
            evidence_files=[evidence_file] if evidence_file else [],
            source="agent",
        )
        self.log(f"record_finding: {item_id} = {status}")
        remaining = [i for i in website_ids if i not in self.findings]
        return (
            f"Recorded {item_id} = {status}. "
            + (f"Remaining items: {', '.join(remaining)}" if remaining else "All items recorded.")
        )

    def apply_rate_comparison(
        self,
        published_fee: str,
        verdict: str,
        detail: str,
        evidence_file: str = "",
    ) -> str:
        """Validate and record the rate comparison. Returns the model-facing message."""
        if verdict not in VALID_VERDICTS:
            return f"REJECTED: verdict must be one of {sorted(VALID_VERDICTS)}"
        if verdict in {"matches application exactly", "differs from application"}:
            if not evidence_file or not self.store.has_file(evidence_file):
                return (
                    "REJECTED: that verdict requires an existing evidence_file from a "
                    "capture tool."
                )
        evidence_url = next(
            (r.url for r in self.store.records if r.file == evidence_file), None
        )
        self.rate_comparison = RateComparison(
            form_fee=self.app.fee_text,
            published_fee=published_fee or None,
            verdict=verdict,  # type: ignore[arg-type]
            detail=detail,
            evidence_url=evidence_url,
            evidence_files=[evidence_file] if evidence_file else [],
        )
        self.log(f"record_rate_comparison: {verdict}")
        return f"Rate comparison recorded: {verdict}"

    @property
    def current_url(self) -> str:
        try:
            return self.browser.page.url
        except Exception:
            return ""


def _fmt_nav(session: VerifySession, result) -> str:
    if not result.ok:
        return f"NAVIGATION FAILED for {result.url}: {result.error}"
    session.remember_page()
    text = session.visited.get(session.browser.page.url, "")
    preview = re.sub(r"\s+", " ", text)[:1500]
    lines = [
        f"Loaded: {result.final_url}",
        f"Title: {result.title}",
    ]
    if result.blocked:
        lines.append(
            "WARNING: this page looks like a bot-check/CAPTCHA or access-denied page, "
            "not real content. Do not treat its text as provider content."
        )
    lines.append(f"Page text preview ({len(text)} chars total): {preview}")
    return "\n".join(lines)


def build_tools(session: VerifySession) -> list:
    """Create the agent's tools, closed over this verification session."""

    @beta_tool
    def open_url(url: str) -> str:
        """Navigate the browser to a URL and get the page title plus a text preview.

        Args:
            url: Absolute URL to open (e.g. https://provider.org/classes).
        """
        session.log(f"open_url: {url}")
        return _fmt_nav(session, session.browser.navigate(url))

    @beta_tool
    def read_page(offset: int = 0) -> str:
        """Read the visible text of the current page, starting at a character offset.

        Args:
            offset: Character position to start from (use to page through long content).
        """
        session.log(f"read_page: offset={offset}")
        session.remember_page()
        text = session.visited.get(session.browser.page.url, "")
        chunk = text[offset : offset + session.page_text_limit]
        suffix = ""
        if offset + session.page_text_limit < len(text):
            suffix = (
                f"\n[... truncated — total {len(text)} chars; call read_page with "
                f"offset={offset + session.page_text_limit} for more]"
            )
        return (chunk or "(page has no visible text)") + suffix

    @beta_tool
    def find_on_page(query: str) -> str:
        """Search the current page's text for a phrase (case-insensitive) and get
        the surrounding context of each match.

        Args:
            query: The text to look for, e.g. a price like "$80" or a word like "schedule".
        """
        session.log(f"find_on_page: {query!r}")
        session.remember_page()
        matches = session.browser.find_text(query)
        if not matches:
            return f"No occurrences of {query!r} on the current page."
        out = [f"{len(matches)} match(es) for {query!r}:"]
        out += [f"  {i+1}. ...{m.snippet}..." for i, m in enumerate(matches)]
        return "\n".join(out)

    @beta_tool
    def list_links(filter: str = "") -> str:
        """List links on the current page (text -> URL), optionally filtered.

        Args:
            filter: Only return links whose text or URL contains this (case-insensitive).
        """
        session.log(f"list_links: filter={filter!r}")
        links = session.browser.links(contains=filter)
        if not links:
            return "No matching links on the current page."
        return "\n".join(f"- {text} -> {href}" for text, href in links)

    @beta_tool
    def capture_page(label: str) -> str:
        """Capture a date-stamped FULL-PAGE screenshot of the current page for the
        audit file. Use for the 'here is the website we reviewed' record and as a
        fallback when a targeted capture can't locate its text.

        Args:
            label: What this capture shows, e.g. "Full page: recreational riding program".
        """
        session.log(f"capture_page: {label!r}")
        path = session.store.next_path("full", label)
        session.browser.capture_full_page(path)
        record = session.store.register(
            path, kind="full_page", label=label, url=session.browser.page.url
        )
        return f"Captured full page -> {record.file} (stamped {record.captured_at})"

    @beta_tool
    def capture_evidence(locate_text: str, label: str) -> str:
        """Capture a date-stamped, labeled screenshot of the page region around a
        piece of text — one targeted capture per confirmed requirement.

        Args:
            locate_text: Short verbatim text visible on the page to center the capture on
                (e.g. "$80 per session"). Must actually appear on the page.
            label: The requirement this proves, e.g. "Evidence: published fees".
        """
        session.log(f"capture_evidence: {locate_text!r} ({label!r})")
        path = session.store.next_path("evidence", label)
        ok = session.browser.capture_region_around_text(locate_text, path)
        if not ok:
            path.unlink(missing_ok=True)
            return (
                f"Could not locate {locate_text!r} as on-page text. Try a shorter, exact "
                "fragment from find_on_page, or use capture_page for a full-page capture."
            )
        record = session.store.register(
            path, kind="targeted", label=label, url=session.browser.page.url
        )
        return f"Captured -> {record.file} (stamped {record.captured_at})"

    @beta_tool
    def record_finding(
        item_id: str,
        status: str,
        note: str,
        quote: str = "",
        evidence_file: str = "",
    ) -> str:
        """Record your conclusion for ONE checklist item. Call once per item (a
        second call for the same item overwrites the first).

        Args:
            item_id: The checklist item id, exactly as listed in your instructions.
            status: "found" (the REQUIREMENT is satisfied and evidenced), "not_found"
                (checked — the requirement FAILS or its proof is absent), or "needs_review"
                (could not be conclusively verified; a human should look). Judge the
                requirement, not the literal words: for a requirement phrased "does NOT
                provide X", a page with no X-language at all SATISFIES it — record "found"
                with a quote of what the offering is and a note explaining the absence.
            note: 1-3 plain-language sentences saying what the page shows and why that
                supports the status. For appeals: state whether this supports or refutes
                the denial reason.
            quote: Verbatim sentence/fragment from a page you visited that backs the
                finding. Required for "found". Must be exact page text.
            evidence_file: A capture filename returned by capture_evidence/capture_page
                (e.g. "evidence/03-evidence-published-fees.png"). Required for "found".
        """
        return session.apply_finding(item_id, status, note, quote, evidence_file)

    @beta_tool
    def record_rate_comparison(
        published_fee: str,
        verdict: str,
        detail: str,
        evidence_file: str = "",
    ) -> str:
        """Record the rate comparison: the fee on the application vs the fee published
        on the website. Call exactly once, after you have checked pricing.

        Args:
            published_fee: The fee as published on the website, verbatim (e.g.
                "$80 per 30-minute session"). Empty string if no fee is published.
            verdict: One of: "matches application exactly", "differs from application",
                "not published", "could not verify".
            detail: 1-3 sentences of plain-language explanation.
            evidence_file: Capture filename backing this. Required for "matches
                application exactly" and "differs from application".
        """
        return session.apply_rate_comparison(published_fee, verdict, detail, evidence_file)

    return [
        open_url,
        read_page,
        find_on_page,
        list_links,
        capture_page,
        capture_evidence,
        record_finding,
        record_rate_comparison,
    ]


def build_system_prompt(app: ApplicationData, checklist: Checklist) -> str:
    items_block = "\n".join(
        f"- id: {i.id}\n  requirement: {i.requirement}\n  how to check: {i.check_hint}"
        for i in checklist.website_items
    )
    exclusion_block = ""
    if checklist.exclusion_list:
        exclusion_block = (
            "\nEXCLUSION LIST for this category (an item in any of these categories "
            "must be flagged — record not_found for the 'not on exclusion list' item "
            "and name the match):\n"
            + "\n".join(f"- {e}" for e in checklist.exclusion_list)
            + "\n"
        )
    appeal_block = ""
    if checklist.appeal_framing and app.denial_reason:
        appeal_block = (
            f"\nAPPEAL CONTEXT:\n{checklist.appeal_framing}\n"
            f"Date of denial: {app.denial_date or 'not stated'}\n"
            f"STATED DENIAL REASON: {app.denial_reason}\n"
            f"Applicant's justification: {app.appeal_justification or 'not stated'}\n"
        )

    fees = "; ".join(f"${f.amount:g} {f.unit}" for f in app.fees) or "none parsed"
    return f"""You are the website-verification assistant for a Pre-Approvals reviewer at a \
NY human-services agency (OPWDD Self-Direction). Before a purchase is approved, the reviewer \
must confirm on the provider's PUBLIC website that the requested item is real, open to the \
public, at a published price — with screenshot evidence that will be kept for Medicaid audits.

Your job: verify ONLY the website-verifiable checklist items below for this application, \
capture evidence, and record honest findings. You never approve or deny anything.

THE APPLICATION
- Category: {checklist.display_name}
- Requested: {app.requested_item}
- Provider/Vendor: {app.provider_name or "not stated"}
- URL on the form: {app.url}
- Fee stated on the form: {app.fee_text} (parsed: {fees})
- Duration/billing: {app.duration_text or "not stated"}
- Safety features claimed on the form: {app.safety_features or "n/a"}
{appeal_block}
WEBSITE-VERIFIABLE CHECKLIST ITEMS (record a finding for EVERY one of these ids):
{items_block}
{exclusion_block}
NON-NEGOTIABLE RULES
1. Never fabricate. "not_found" and "needs_review" are correct, expected answers when \
evidence is absent, gated, or ambiguous. A wrong "found" is the worst possible outcome.
   Statuses judge the REQUIREMENT as written: "found" = requirement satisfied, "not_found" \
= requirement fails (e.g. for "class does NOT give college credits", a page with no credit \
language satisfies the requirement -> "found"; for "has published fees", no visible price \
-> "not_found").
2. Evidence before conclusions: capture the proof (capture_evidence, or capture_page) \
BEFORE recording a "found" finding. Quotes must be verbatim page text — they are checked.
3. Capture at least ONE full-page screenshot of the most relevant page (the page carrying \
the pricing/schedule/product proof) as the "here is the website we reviewed" record — do \
this even when other items fail.
4. If a page is bot-blocked, dead, or requires login/location before showing content, say \
exactly that in the note and use needs_review — do not guess around it.
5. Stay on the provider's own website (follow its internal links; a linked PDF price sheet \
on their domain counts). Do not use other websites or your own knowledge of the provider.
6. Be efficient: ~10 navigations max. Start from the URL on the form.
7. After all items: record_rate_comparison (form fee vs published fee), exactly once.
8. Finish with a short plain-language summary (3-6 sentences) for the reviewer: what you \
verified, what you could not, and anything that needs their attention. Do not repeat every \
finding — highlight what matters. Plain prose only — no markdown headings, no ** bold, no \
bullet lists (the summary is displayed as plain text).

Work item by item. Think about which page would prove each requirement, look there, capture, \
then record."""


@dataclass
class AgentResult:
    findings: list[Finding]
    rate_comparison: RateComparison
    summary: str
    iterations: int
    usage: TokenUsage


def run_verification(
    app: ApplicationData,
    checklist: Checklist,
    browser: Browser,
    store: EvidenceStore,
    client: Anthropic,
    cfg: ToolConfig,
    log: Callable[[str], None] = print,
) -> AgentResult:
    session = VerifySession(
        app=app,
        checklist=checklist,
        browser=browser,
        store=store,
        page_text_limit=cfg.page_text_limit,
        log=log,
    )
    tools = build_tools(session)
    runner = client.beta.messages.tool_runner(
        model=cfg.model,
        max_tokens=6000,
        system=build_system_prompt(app, checklist),
        tools=tools,
        messages=[
            {
                "role": "user",
                "content": (
                    "Review this application now. Open the URL from the form, verify every "
                    "listed checklist item, capture the evidence, record the rate "
                    "comparison, then give your summary."
                ),
            }
        ],
    )

    summary = ""
    iterations = 0
    usage = TokenUsage()
    for message in runner:
        iterations += 1
        usage.add(getattr(message, "usage", None))
        for block in message.content:
            if block.type == "text" and block.text.strip():
                summary = block.text.strip()
        if iterations >= cfg.max_agent_iterations:
            log(f"agent: iteration cap ({cfg.max_agent_iterations}) reached, stopping")
            break

    # Anything the agent failed to record becomes an honest needs_review.
    for item in checklist.website_items:
        if item.id not in session.findings:
            session.findings[item.id] = Finding(
                item_id=item.id,
                form_question=item.form_question,
                requirement=item.requirement,
                status=Status.NEEDS_REVIEW,
                note="The verification agent did not reach a conclusion for this item.",
                source="deterministic",
            )
    rate = session.rate_comparison or RateComparison(
        form_fee=app.fee_text,
        verdict="could not verify",
        detail="The verification agent did not record a rate comparison.",
    )
    ordered = [session.findings[i.id] for i in checklist.website_items]
    return AgentResult(
        findings=ordered,
        rate_comparison=rate,
        summary=summary,
        iterations=iterations,
        usage=usage,
    )
