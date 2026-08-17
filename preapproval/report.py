"""Render the review-ready report (HTML for humans, JSON already saved by the pipeline)."""

from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import REPO_ROOT
from .models import ReviewReport, Status

STATUS_META = {
    Status.FOUND: ("Found", "found"),
    Status.NOT_FOUND: ("Not Found", "not-found"),
    Status.NEEDS_REVIEW: ("Needs Review", "needs-review"),
    Status.INTERNAL: ("Internal — not website-verifiable", "internal"),
    Status.NEEDS_DOCUMENT: ("Needs Document", "needs-document"),
    Status.PASS: ("Pass", "found"),
    Status.FLAG: ("Flag", "not-found"),
}

VERDICT_CLASS = {
    "matches application exactly": "found",
    "differs from application": "not-found",
    "not published": "not-found",
    "could not verify": "needs-review",
}


def _plain_text(summary: str) -> str:
    """Strip stray markdown the model may emit (the summary is shown as plain text)."""
    text = re.sub(r"^#{1,6}\s*", "", summary, flags=re.M)   # headings
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.S)  # bold
    text = re.sub(r"^\s*[-*]\s+", "- ", text, flags=re.M)     # normalize bullets
    return text.strip()


def render_report(report: ReviewReport, package_dir: Path) -> Path:
    env = Environment(
        loader=FileSystemLoader(REPO_ROOT / "templates"),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["status_label"] = lambda s: STATUS_META[Status(s)][0]
    env.filters["status_class"] = lambda s: STATUS_META[Status(s)][1]

    website = [f for f in report.findings if f.source == "agent"]
    form_checks = [
        f
        for f in report.findings
        if f.source == "deterministic" and f.status in (Status.PASS, Status.FLAG, Status.NEEDS_REVIEW)
    ]
    documents = [f for f in report.findings if f.status == Status.NEEDS_DOCUMENT]
    internal = [f for f in report.findings if f.status == Status.INTERNAL]

    html = env.get_template("report.html.j2").render(
        r=report,
        agent_summary=_plain_text(report.agent_summary),
        website_findings=website,
        form_checks=form_checks,
        document_findings=documents,
        internal_findings=internal,
        verdict_class=VERDICT_CLASS.get(report.rate_comparison.verdict, "needs-review"),
    )
    out = package_dir / "report.html"
    out.write_text(html, encoding="utf-8")
    return out
