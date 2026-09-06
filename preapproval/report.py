"""Render the review-ready report (HTML for humans, JSON already saved by the pipeline).

The HTML is deliberately one self-contained file: the shared stylesheet is
inlined at render time so a package can be zipped, e-mailed or opened from
file:// years later and still look right. Nothing is fetched from the network.
"""

from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import REPO_ROOT
from .models import EvidenceRecord, Finding, ReviewReport, Status

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

# Order matters: the workbench (and §3.4 of the UI plan) expects these keys.
COUNT_KEYS = (
    "found",
    "not_found",
    "needs_review",
    "internal",
    "needs_document",
    "pass",
    "flag",
)


def plain_text(summary: str) -> str:
    """Strip stray markdown the model may emit.

    Both surfaces that show model prose render it as plain text: the report's
    agent summary and the workbench chat replies (server.py). Sharing one
    definition keeps `**bold**` from leaking literal asterisks into either.
    """
    text = re.sub(r"^#{1,6}\s*", "", summary, flags=re.M)   # headings
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.S)  # bold
    text = re.sub(r"^\s*[-*]\s+", "- ", text, flags=re.M)     # normalize bullets
    return text.strip()


_plain_text = plain_text  # kept for callers that used the private name


def status_counts(findings: list[Finding]) -> dict[str, int]:
    """Count findings by status, always returning every key (zeros included)."""
    counts = {key: 0 for key in COUNT_KEYS}
    for finding in findings:
        counts[finding.status.value] = counts.get(finding.status.value, 0) + 1
    return counts


def _environment() -> Environment:
    """Jinja environment with templates/ *and* static/ on the search path.

    The extra entry lets the report inline the shared stylesheet with
    `{% include "theme.css" %}`; the workbench serves the very same file from
    /static/theme.css, so the two surfaces can never drift apart.

    Autoescape covers ".html" and ".html.j2" but not ".css": model-written text
    (notes, quotes, labels) is escaped before it reaches an attribute, while the
    stylesheet's `>` selectors survive the include untouched.
    """
    env = Environment(
        loader=FileSystemLoader([REPO_ROOT / "templates", REPO_ROOT / "static"]),
        autoescape=select_autoescape(["html", "html.j2"]),
    )
    env.filters["status_label"] = lambda s: STATUS_META[Status(s)][0]
    env.filters["status_class"] = lambda s: STATUS_META[Status(s)][1]
    return env


def render_report(report: ReviewReport, package_dir: Path) -> Path:
    env = _environment()

    website = [f for f in report.findings if f.source == "agent"]
    form_checks = [
        f
        for f in report.findings
        if f.source == "deterministic" and f.status in (Status.PASS, Status.FLAG, Status.NEEDS_REVIEW)
    ]
    documents = [f for f in report.findings if f.status == Status.NEEDS_DOCUMENT]
    internal = [f for f in report.findings if f.status == Status.INTERNAL]

    # Findings link to captures by filename; the appendix owns the metadata.
    # Handing the template the lookup lets a finding card show the label and
    # capture time next to its thumbnail without re-scanning the evidence list.
    evidence_by_file: dict[str, EvidenceRecord] = {e.file: e for e in report.evidence}

    html = env.get_template("report.html.j2").render(
        r=report,
        agent_summary=plain_text(report.agent_summary),
        website_findings=website,
        form_checks=form_checks,
        document_findings=documents,
        internal_findings=internal,
        verdict_class=VERDICT_CLASS.get(report.rate_comparison.verdict, "needs-review"),
        counts=status_counts(report.findings),
        website_counts=status_counts(website),
        website_total=len(website),
        evidence_by_file=evidence_by_file,
    )
    out = package_dir / "report.html"
    # newline="\n" so a Windows checkout writes the same bytes as a POSIX one —
    # otherwise every regenerated report shows up as a whole-file diff (CRLF).
    out.write_text(html, encoding="utf-8", newline="\n")
    return out


def render_package(package_dir: Path) -> Path:
    """Re-render report.html from a finished package's saved report.json.

    Used by `verify.py render` and by the workbench, which never hold a
    ReviewReport in memory — the package on disk is the source of truth.
    """
    package_dir = Path(package_dir)
    report_json = package_dir / "report.json"
    if not report_json.exists():
        raise FileNotFoundError(f"No report.json in {package_dir} — run a review first.")
    report = ReviewReport.model_validate_json(report_json.read_text(encoding="utf-8"))
    return render_report(report, package_dir)
