"""End-to-end review pipeline for one application PDF.

Deterministic work (extraction routing, internal/document items, fee caps, age
eligibility, exclusion-keyword backstop, report rendering) happens here in
plain Python. Only the website research is delegated to the agent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from google import genai

from . import __version__
from .agent import run_verification
from .browser import Browser
from .config import REPO_ROOT, Checklist, ToolConfig, checklist_for
from .evidence import EvidenceStore
from .extraction import UnknownFormCategory, extract_application
from .models import ApplicationData, Finding, ReviewReport, Status, TokenUsage
from .report import render_report


class NeedsClarification(Exception):
    """Key information is missing or ambiguous — a human must supply it.

    `field` tells the caller which override to collect ("url", "category",
    "requested_item"); `options` lists valid answers where there is a fixed set.
    """

    def __init__(self, question: str, *, field: str, options: Optional[list[str]] = None):
        self.question = question
        self.field = field
        self.options = options or []
        super().__init__(question)


@dataclass
class ReviewOverrides:
    """Answers a reviewer supplied in response to a clarification request."""

    url: Optional[str] = None
    category: Optional[str] = None
    requested_item: Optional[str] = None

    def set(self, field: str, value: str) -> None:
        if field not in {"url", "category", "requested_item"}:
            raise ValueError(f"unknown clarification field {field!r}")
        setattr(self, field, value)


class RunLog:
    """Logs to the console and keeps the lines for run.log in the package."""

    def __init__(self, sink: Callable[[str], None] = print):
        self.lines: list[str] = []
        self.sink = sink

    def __call__(self, message: str) -> None:
        self.lines.append(f"{datetime.now().strftime('%H:%M:%S')}  {message}")
        self.sink(message)

    def write(self, path: Path) -> None:
        path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")


def package_dir_for(pdf_path: Path, out_root: Path) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", pdf_path.stem.lower()).strip("-")
    return out_root / slug


# ---------------------------------------------------------------------------
# Deterministic findings
# ---------------------------------------------------------------------------


def internal_and_document_findings(checklist: Checklist) -> list[Finding]:
    findings = []
    for item in checklist.internal_items:
        findings.append(
            Finding(
                item_id=item.id,
                form_question=item.form_question,
                status=Status.INTERNAL,
                note=f"Internal — not website-verifiable. {item.internal_reason or ''}".strip(),
                source="deterministic",
            )
        )
    for item in checklist.document_items:
        findings.append(
            Finding(
                item_id=item.id,
                form_question=item.form_question,
                status=Status.NEEDS_DOCUMENT,
                note=f"Needs document. {item.internal_reason or ''}".strip(),
                source="deterministic",
            )
        )
    return findings


def eligibility_findings(app: ApplicationData, checklist: Checklist) -> list[Finding]:
    findings = []
    if checklist.adults_only:
        if app.participant_age is None:
            status, note = Status.FLAG, "Adults-only category but no age is stated on the form."
        elif app.participant_age >= 18:
            status, note = (
                Status.PASS,
                f"Adults-only category; form states age {app.participant_age} (≥ 18).",
            )
        else:
            status, note = (
                Status.FLAG,
                f"Adults-only category but form states age {app.participant_age} (< 18).",
            )
        findings.append(
            Finding(
                item_id="eligibility_age",
                form_question="Program guideline: adults (18+) only",
                requirement="Participant is 18 or older (checked against the age stated on the form)",
                status=status,
                note=note,
                source="deterministic",
            )
        )
    return findings


def fee_cap_findings(app: ApplicationData, checklist: Checklist) -> list[Finding]:
    findings = []
    for cap in checklist.fee_caps:
        matched = None
        for fee in app.fees:
            unit = fee.unit.lower()
            if not cap.unit_keywords or any(k in unit for k in cap.unit_keywords):
                matched = fee
                break
        if matched is None:
            findings.append(
                Finding(
                    item_id=cap.id,
                    form_question=f"Program cap: {cap.label}",
                    requirement=cap.label,
                    status=Status.NEEDS_REVIEW,
                    note="No fee on the form could be matched to this cap — reviewer should check.",
                    source="deterministic",
                )
            )
            continue
        within = matched.amount <= cap.max_amount
        findings.append(
            Finding(
                item_id=cap.id,
                form_question=f"Program cap: {cap.label}",
                requirement=cap.label,
                status=Status.PASS if within else Status.FLAG,
                note=(
                    f"Form states ${matched.amount:g} {matched.unit} — "
                    + ("within" if within else "EXCEEDS")
                    + f" the {cap.label} cap."
                ),
                source="deterministic",
            )
        )
    return findings


def apply_exclusion_backstop(
    app: ApplicationData, checklist: Checklist, findings: list[Finding]
) -> list[str]:
    """Keyword backstop: if the requested item matches an exclusion keyword but the
    agent verified 'not on the exclusion list', downgrade to needs_review."""
    warnings: list[str] = []
    if not checklist.exclusion_keywords:
        return warnings
    text = f"{app.requested_item} {app.justification or ''}".lower()
    hits = [k.strip() for k in checklist.exclusion_keywords if k.lower() in text]
    if not hits:
        return warnings
    for f in findings:
        if f.item_id == "not_excluded" and f.status == Status.FOUND:
            f.status = Status.NEEDS_REVIEW
            f.note += (
                f" [Deterministic backstop: the requested item text matches exclusion "
                f"keyword(s) {', '.join(repr(h) for h in hits)} — downgraded to Needs "
                f"Review for human confirmation.]"
            )
            warnings.append(
                f"Exclusion keyword match ({', '.join(hits)}) conflicted with the agent's "
                "'not excluded' finding; downgraded to Needs Review."
            )
    return warnings


# ---------------------------------------------------------------------------
# Clarification checks — ask rather than guess
# ---------------------------------------------------------------------------


def categories_matching_title(form_title: str, checklists: dict[str, Checklist]) -> list[str]:
    """Categories whose configured form_title_hints appear in the form's title."""
    title = (form_title or "").lower()
    if not title:
        return []
    return sorted(
        cl.category
        for cl in checklists.values()
        if any(hint.lower() in title for hint in cl.form_title_hints)
    )


def check_for_ambiguity(
    app: ApplicationData, checklists: dict[str, Checklist], overrides: ReviewOverrides
) -> None:
    """Raise NeedsClarification when the tool shouldn't proceed on a guess."""
    if not app.url:
        raise NeedsClarification(
            "The form has no provider URL ('Link to Webpage / place of publication'). "
            "Which website should be reviewed?",
            field="url",
        )
    if not (app.requested_item or "").strip():
        raise NeedsClarification(
            "The form doesn't state what is being requested (class / item / membership). "
            "What should be verified on the website?",
            field="requested_item",
        )

    # Cross-check the model's category against the checklist title hints.
    if overrides.category is None:
        matches = categories_matching_title(app.form_title, checklists)
        if matches and app.category not in matches:
            raise NeedsClarification(
                f"The form is titled {app.form_title!r}, which matches the "
                f"{' / '.join(matches)} checklist, but it was read as '{app.category}'. "
                "Which checklist should be applied?",
                field="category",
                options=sorted(set(matches) | {app.category}),
            )


# ---------------------------------------------------------------------------
# Review + re-review
# ---------------------------------------------------------------------------


def run_review(
    pdf_path: Path,
    cfg: ToolConfig,
    checklists: dict[str, Checklist],
    client: genai.Client,
    log: Callable[[str], None] = print,
    overrides: Optional[ReviewOverrides] = None,
) -> ReviewReport:
    """Read an application PDF and produce a full report package."""
    overrides = overrides or ReviewOverrides()
    run_log = log if isinstance(log, RunLog) else RunLog(log)

    run_log(f"Extracting application from {pdf_path.name} ...")
    try:
        app, usage = extract_application(pdf_path, client, cfg.model)
    except UnknownFormCategory as e:
        if not overrides.category:
            raise NeedsClarification(
                f"This form ({e.form_title!r}) was read as category '{e.category}', which has "
                "no checklist. Which category should be applied?",
                field="category",
                options=sorted(checklists),
            ) from e
        raise

    if overrides.url:
        app.url = overrides.url
    if overrides.requested_item:
        app.requested_item = overrides.requested_item
    if overrides.category:
        app.category = overrides.category  # type: ignore[assignment]

    run_log(
        f"  -> {app.category} | {app.requested_item} | {app.provider_name} | "
        f"{app.url or 'NO URL'}"
    )
    check_for_ambiguity(app, checklists, overrides)

    package_dir = package_dir_for(pdf_path, cfg.output_dir)
    return _verify_and_report(
        app=app,
        source_pdf=pdf_path.name,
        package_dir=package_dir,
        cfg=cfg,
        checklists=checklists,
        client=client,
        run_log=run_log,
        base_usage=usage,
    )


def rerun_review(
    package_dir: Path,
    cfg: ToolConfig,
    checklists: dict[str, Checklist],
    client: genai.Client,
    log: Callable[[str], None] = print,
) -> ReviewReport:
    """Re-run the website verification for an existing package.

    The application details already extracted from the form are reused (no second
    extraction call), reviewer notes are preserved, and the evidence is captured
    fresh — so the package always reflects one coherent, current review.
    """
    report_path = package_dir / "report.json"
    if not report_path.exists():
        raise FileNotFoundError(f"No report.json in {package_dir} — run a review first.")
    previous = ReviewReport.model_validate_json(report_path.read_text(encoding="utf-8"))

    run_log = log if isinstance(log, RunLog) else RunLog(log)
    run_log(f"Re-running website verification for {previous.source_pdf} ...")
    return _verify_and_report(
        app=previous.application,
        source_pdf=previous.source_pdf,
        package_dir=package_dir,
        cfg=cfg,
        checklists=checklists,
        client=client,
        run_log=run_log,
        base_usage=TokenUsage(),
        previous=previous,
    )


def _verify_and_report(
    *,
    app: ApplicationData,
    source_pdf: str,
    package_dir: Path,
    cfg: ToolConfig,
    checklists: dict[str, Checklist],
    client: genai.Client,
    run_log: RunLog,
    base_usage: TokenUsage,
    previous: Optional[ReviewReport] = None,
) -> ReviewReport:
    """Website research + deterministic checks + package assembly."""
    checklist = checklist_for(app.category, checklists)
    package_dir.mkdir(parents=True, exist_ok=True)
    store = EvidenceStore(package_dir)

    run_log(f"Verifying on the provider's website ({app.url}) ...")
    browser = Browser(
        headless=cfg.headless,
        viewport_width=cfg.viewport_width,
        viewport_height=cfg.viewport_height,
        navigation_timeout_ms=cfg.navigation_timeout_ms,
    )
    with browser:
        result = run_verification(app, checklist, browser, store, client, cfg, log=run_log)

    findings: list[Finding] = list(result.findings)
    findings += fee_cap_findings(app, checklist)
    findings += eligibility_findings(app, checklist)
    findings += internal_and_document_findings(checklist)

    warnings = apply_exclusion_backstop(app, checklist, findings)
    if not any(r.kind == "full_page" for r in store.records):
        warnings.append(
            "No whole-page capture was taken for this review — the audit file should include "
            "one. Re-run the review, or capture the page manually."
        )

    usage = base_usage.model_copy(deep=True)
    for field in (
        "requests",
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    ):
        setattr(usage, field, getattr(usage, field) + getattr(result.usage, field))
    cost = usage.estimated_cost_usd(cfg.price_input_per_mtok, cfg.price_output_per_mtok)

    report = ReviewReport(
        tool_version=__version__,
        model=cfg.model,
        source_pdf=source_pdf,
        reviewed_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        category=app.category,
        category_display=checklist.display_name,
        application=app,
        rate_comparison=result.rate_comparison,
        findings=findings,
        evidence=store.records,
        agent_summary=result.summary,
        checklist_notes=checklist.notes,
        reviewer_notes=list(previous.reviewer_notes) if previous else [],
        warnings=warnings,
        usage=usage,
        estimated_cost_usd=cost,
        review_count=(previous.review_count + 1) if previous else 1,
    )

    store.write_manifest(package_dir)
    (package_dir / "report.json").write_text(
        report.model_dump_json(indent=2), encoding="utf-8"
    )
    render_report(report, package_dir)
    run_log(
        f"Tokens: {usage.input_tokens:,} in / {usage.output_tokens:,} out "
        f"across {usage.requests} request(s) — estimated ${cost:.2f}"
    )
    try:  # keep run.log portable: no machine-specific absolute paths
        shown = package_dir.relative_to(REPO_ROOT)
    except ValueError:
        shown = package_dir
    run_log(f"Report package written to {shown}")
    run_log.write(package_dir / "run.log")
    return report
