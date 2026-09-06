"""Background review jobs for the workbench.

One plain worker thread, FIFO. Deliberately *not* asyncio: `pipeline.run_review`
drives Playwright's **sync** API, which refuses to run inside a thread with a
running asyncio event loop. So the HTTP layer stays async and every review runs
here, on a thread that never touches a loop.

Each job owns a `RunLog` whose sink appends `{t, text}` to the job and fans the
line out to any subscribed `queue.Queue` (the SSE streams). A
`NeedsClarification` from the pipeline parks the job in `needs_input` and blocks
the worker on an Event until the reviewer answers or skips — the same
conversation the CLI has, moved into the browser.

Setting PROOFPACK_FAKE_RUN=1 swaps the real pipeline for `fake_run_review`,
which logs the same line shapes and writes a small demo package. That lets the
workbench (and the tests) be exercised with no API key, no browser and no money.
"""

from __future__ import annotations

import os
import queue
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from PIL import Image, ImageDraw

from . import __version__
from .config import Checklist, ToolConfig
from .evidence import EvidenceStore
from .models import (
    ApplicationData,
    FeeAmount,
    Finding,
    RateComparison,
    ReviewReport,
    Status,
    TokenUsage,
)
from .pipeline import (
    NeedsClarification,
    ReviewOverrides,
    RunLog,
    eligibility_findings,
    fee_cap_findings,
    internal_and_document_findings,
    package_dir_for,
    rerun_review,
    run_review,
)

ACTIVE_STATES = ("queued", "running", "needs_input")
TERMINAL_STATES = ("done", "failed", "cancelled")

NO_KEY_MESSAGE = (
    "No Gemini API key found. Put GEMINI_API_KEY=... in the .env file at the "
    "repo root (get a key at https://aistudio.google.com/apikey) and restart "
    "the server."
)

# Demo mode: how long each fake step pauses, so the live log is watchable.
# Tests set this to 0.
FAKE_DELAY = 0.12
FAKE_SUFFIX = "-demo"

_EVIDENCE_LINE = re.compile(r"^\s*->\s*(evidence/[^\s]+\.png)\s*$")


def fake_mode_enabled() -> bool:
    return os.getenv("PROOFPACK_FAKE_RUN", "").strip() not in ("", "0", "false", "no")


def fake_package_dir(pdf_path: Path, out_root: Path) -> Path:
    """Demo runs never write to a real package slug — see fake_run_review."""
    real = package_dir_for(pdf_path, out_root)
    return real.with_name(real.name + FAKE_SUFFIX)


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------


@dataclass
class Job:
    id: str
    kind: str                       # "review" | "rerun"
    slug: str
    source_pdf: str
    package_dir: Path
    pdf_path: Optional[Path] = None
    overrides: ReviewOverrides = field(default_factory=ReviewOverrides)
    state: str = "queued"
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    lines: list[dict] = field(default_factory=list)
    question: Optional[dict] = None
    error: Optional[str] = None
    report_ready: bool = False
    evidence: list[str] = field(default_factory=list)
    # internals
    answer_value: Optional[str] = None
    answered: threading.Event = field(default_factory=threading.Event)

    def snapshot(self, *, lines: bool = True) -> dict:
        snap = {
            "id": self.id,
            "kind": self.kind,
            "slug": self.slug,
            "source_pdf": self.source_pdf,
            "state": self.state,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "question": dict(self.question) if self.question else None,
            "error": self.error,
            "report_ready": self.report_ready,
            "evidence": list(self.evidence),
        }
        if lines:
            snap["lines"] = [dict(line) for line in self.lines]
        return snap


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class JobManager:
    """Queue + single worker thread + subscriber fan-out."""

    def __init__(
        self,
        cfg: ToolConfig,
        checklists: dict[str, Checklist],
        client_factory: Optional[Callable[[], object]] = None,
        *,
        fake: Optional[bool] = None,
    ):
        self.cfg = cfg
        self.checklists = checklists
        self.client_factory = client_factory
        self.fake = fake_mode_enabled() if fake is None else fake
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._subs: dict[str, list[queue.Queue]] = {}
        self._pending: queue.Queue = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._client = None

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(
                    target=self._worker_loop, name="proofpack-jobs", daemon=True
                )
                self._worker.start()

    def _worker_loop(self) -> None:
        while True:
            job_id = self._pending.get()
            if job_id is None:  # shutdown sentinel
                return
            job = self._jobs.get(job_id)
            if job is None or job.state != "queued":
                continue
            try:
                self._run(job)
            except BaseException as e:  # noqa: BLE001 — a worker must never die
                self._finish(job, "failed", error=f"{type(e).__name__}: {e}")

    # -- submission ---------------------------------------------------------

    def active_for_slug(self, slug: str) -> Optional[Job]:
        with self._lock:
            for job_id in reversed(self._order):
                job = self._jobs[job_id]
                if job.slug == slug and job.state in ACTIVE_STATES:
                    return job
        return None

    def submit_review(self, pdf_path: Path, url: Optional[str] = None) -> Job:
        out = self.cfg.output_dir
        package_dir = fake_package_dir(pdf_path, out) if self.fake else package_dir_for(pdf_path, out)
        job = Job(
            id=uuid.uuid4().hex[:12],
            kind="review",
            slug=package_dir.name,
            source_pdf=pdf_path.name,
            package_dir=package_dir,
            pdf_path=pdf_path,
            overrides=ReviewOverrides(url=url or None),
        )
        return self._enqueue(job)

    def submit_rerun(self, package_dir: Path) -> Job:
        if not (package_dir / "report.json").exists():
            raise FileNotFoundError(f"No report.json in {package_dir} — run a review first.")
        if self.fake and not package_dir.name.endswith(FAKE_SUFFIX):
            raise ValueError(
                "PROOFPACK_FAKE_RUN is set, so re-runs are limited to demo packages "
                f"(…{FAKE_SUFFIX}) — a fake run must never overwrite a real audit package."
            )
        import json as _json

        source_pdf = ""
        try:
            source_pdf = _json.loads(
                (package_dir / "report.json").read_text(encoding="utf-8")
            ).get("source_pdf", "")
        except Exception:  # noqa: BLE001 — a broken report.json fails later, with detail
            pass
        job = Job(
            id=uuid.uuid4().hex[:12],
            kind="rerun",
            slug=package_dir.name,
            source_pdf=source_pdf,
            package_dir=package_dir,
        )
        return self._enqueue(job)

    def _enqueue(self, job: Job) -> Job:
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
        self.start()
        self._pending.put(job.id)
        return job

    # -- reads --------------------------------------------------------------

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def snapshot(self, job_id: str, *, lines: bool = True) -> Optional[dict]:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.snapshot(lines=lines) if job else None

    def snapshots(self, *, lines: bool = False) -> list[dict]:
        with self._lock:
            return [self._jobs[i].snapshot(lines=lines) for i in self._order]

    # -- subscriptions ------------------------------------------------------

    def subscribe(self, job_id: str) -> tuple[list[dict], queue.Queue]:
        """Register an SSE listener. Returns the lines so far + its live queue.

        Both happen under the lock so a line can never slip through the gap
        between "replay what exists" and "listen for the rest".
        """
        q: queue.Queue = queue.Queue()
        with self._lock:
            job = self._jobs[job_id]
            lines = [dict(line) for line in job.lines]
            self._subs.setdefault(job_id, []).append(q)
            if job.state in TERMINAL_STATES:
                q.put(("state", job.snapshot(lines=False)))
                q.put(("end", {"state": job.state}))
        return lines, q

    def unsubscribe(self, job_id: str, q: queue.Queue) -> None:
        with self._lock:
            subs = self._subs.get(job_id, [])
            if q in subs:
                subs.remove(q)

    def _publish(self, job_id: str, event: str, payload: dict) -> None:
        with self._lock:
            subs = list(self._subs.get(job_id, []))
        for q in subs:
            try:
                q.put_nowait((event, payload))
            except Exception:  # noqa: BLE001 — a dead listener must not stop the run
                pass

    # -- state --------------------------------------------------------------

    def _append_line(self, job: Job, text: str) -> None:
        entry = {"t": datetime.now().strftime("%H:%M:%S"), "text": text}
        match = _EVIDENCE_LINE.match(text)
        with self._lock:
            job.lines.append(entry)
            if match and match.group(1) not in job.evidence:
                job.evidence.append(match.group(1))
        self._publish(job.id, "line", entry)

    def _set_state(self, job: Job, state: str, **fields) -> None:
        with self._lock:
            job.state = state
            for key, value in fields.items():
                setattr(job, key, value)
            snap = job.snapshot(lines=False)
        self._publish(job.id, "state", snap)

    def _finish(self, job: Job, state: str, *, error: Optional[str] = None) -> None:
        report_ready = (job.package_dir / "report.html").exists()
        self._set_state(
            job,
            state,
            finished_at=_now(),
            error=error,
            question=None,
            report_ready=report_ready,
        )
        self._publish(job.id, "end", {"state": state})

    def answer(self, job_id: str, value: Optional[str] = None, *, skip: bool = False) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if job.state != "needs_input":
                raise ValueError(f"job {job_id} is not waiting for an answer (state={job.state})")
            job.answer_value = None if skip else (value or None)
        if not skip:
            # Flip out of needs_input here rather than waiting for the worker to
            # wake, so the HTTP response never reports a state we have left.
            self._set_state(job, "running", question=None)
        job.answered.set()
        return job

    # -- running ------------------------------------------------------------

    def _make_client(self):
        if self.fake:
            return None
        if self._client is None:
            if self.client_factory is None:
                raise _MissingKey(NO_KEY_MESSAGE)
            self._client = self.client_factory()
        return self._client

    def _ask(self, job: Job, exc: NeedsClarification) -> Optional[str]:
        """Park the job until the reviewer answers. None means 'skip'."""
        job.answered.clear()
        self._set_state(
            job,
            "needs_input",
            question={"field": exc.field, "text": exc.question, "options": list(exc.options)},
        )
        job.answered.wait()
        with self._lock:
            value = job.answer_value
            job.answer_value = None
        if value is None:
            return None
        self._set_state(job, "running", question=None)
        return value

    def _run(self, job: Job) -> None:
        self._set_state(job, "running", started_at=_now())
        run_log = RunLog(sink=lambda text: self._append_line(job, text))

        review = fake_run_review if self.fake else run_review
        rerun = fake_rerun_review if self.fake else rerun_review

        try:
            client = self._make_client()
        except _MissingKey as e:
            self._finish(job, "failed", error=str(e))
            return

        try:
            if job.kind == "rerun":
                rerun(job.package_dir, self.cfg, self.checklists, client, log=run_log)
            else:
                while True:
                    try:
                        review(
                            job.pdf_path,
                            self.cfg,
                            self.checklists,
                            client,
                            log=run_log,
                            overrides=job.overrides,
                        )
                        break
                    except NeedsClarification as e:
                        answer = self._ask(job, e)
                        if answer is None:
                            self._finish(job, "cancelled")
                            return
                        job.overrides.set(e.field, answer)
        except Exception as e:  # noqa: BLE001 — surfaced to the reviewer, not swallowed
            self._finish(job, "failed", error=f"{type(e).__name__}: {e}")
            return

        self._finish(job, "done")


class _MissingKey(RuntimeError):
    """No API key configured — reported with the friendly message, not a traceback."""


# ---------------------------------------------------------------------------
# Demo runner (PROOFPACK_FAKE_RUN=1)
# ---------------------------------------------------------------------------


def _pause(multiplier: float = 1.0) -> None:
    if FAKE_DELAY:
        time.sleep(FAKE_DELAY * multiplier)


def _demo_png(path: Path, caption: str) -> None:
    img = Image.new("RGB", (900, 460), (245, 247, 250))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, 899, 70), fill=(11, 31, 58))
    draw.text((24, 28), "DEMO CAPTURE — no website was visited", fill=(110, 231, 183))
    draw.text((24, 120), caption, fill=(17, 24, 39))
    draw.text((24, 160), "PROOFPACK_FAKE_RUN=1", fill=(91, 107, 127))
    img.save(path, format="PNG")


def _demo_application(category: str, checklist: Checklist, url: str) -> ApplicationData:
    return ApplicationData(
        category=category,  # type: ignore[arg-type]
        form_title=checklist.display_name,
        participant_name="Demo Participant",
        participant_age=27,
        requested_item="Demo requested item",
        provider_name="Demo Provider",
        url=url,
        fee_text="Fee per Session: $80 per 30-minute session",
        fees=[FeeAmount(amount=80, unit="per session")],
    )


def _demo_category(checklists: dict[str, Checklist]) -> str:
    return "community_class" if "community_class" in checklists else sorted(checklists)[0]


def _fake_verify_and_report(
    *,
    app: ApplicationData,
    category: str,
    checklist: Checklist,
    source_pdf: str,
    package_dir: Path,
    cfg: ToolConfig,
    run_log: RunLog,
    previous: Optional[ReviewReport] = None,
) -> ReviewReport:
    """Write a small but *structurally real* package, logging §3.3 line shapes."""
    package_dir.mkdir(parents=True, exist_ok=True)
    store = EvidenceStore(package_dir)

    run_log(f"Verifying on the provider's website ({app.url}) ...")
    _pause()
    run_log(f"open_url: {app.url}")
    _pause()
    run_log("read_page: offset=0")
    _pause()
    run_log("find_on_page: '$80'")
    _pause()

    full_label = "Full page: demo provider page"
    full_path = store.next_path("full", full_label)
    _demo_png(full_path, full_label)
    run_log(f"capture_page: {full_label!r}")
    full = store.register(full_path, kind="full_page", label=full_label, url=app.url or "")
    run_log(f"  -> {full.file}")
    _pause()

    run_log("capture_evidence: 'a price that is not on the page' ('Evidence: missing')")
    run_log("  -> could not locate text; no capture")
    _pause()

    ev_label = "Evidence: published fees"
    ev_path = store.next_path("evidence", ev_label)
    _demo_png(ev_path, ev_label)
    run_log(f"capture_evidence: '30-Minute Group - $80' ({ev_label!r})")
    targeted = store.register(ev_path, kind="targeted", label=ev_label, url=app.url or "")
    run_log(f"  -> {targeted.file}")
    _pause()

    # The gate that makes this tool trustworthy, shown in the trail: a "found"
    # without evidence is refused before it is retried with the capture.
    website_items = checklist.website_items
    findings: list[Finding] = []
    for index, item in enumerate(website_items):
        if index == 0:
            run_log(
                f"REJECTED record_finding {item.id}: status 'found' requires evidence_file. "
                "Capture the evidence first (capture_evidence / capture_page), then record "
                "the finding."
            )
            _pause(0.5)
        if index % 4 == 3:
            status, note, quote, files = (
                Status.NEEDS_REVIEW,
                "Demo run — nothing was checked, so this item is left for the reviewer.",
                None,
                [],
            )
        else:
            status, note, quote, files = (
                Status.FOUND,
                "Demo run — this finding is fabricated for the interface walkthrough.",
                "30-Minute Group - $80",
                [targeted.file],
            )
        findings.append(
            Finding(
                item_id=item.id,
                form_question=item.form_question,
                requirement=item.requirement,
                status=status,
                note=note,
                quote=quote,
                evidence_url=app.url,
                evidence_files=files,
                source="agent",
            )
        )
        run_log(f"record_finding: {item.id} = {status.value}")
        _pause(0.5)

    run_log("REJECTED record_rate_comparison: verdict must be one of ['could not verify', ...]")
    _pause(0.5)
    rate = RateComparison(
        form_fee=app.fee_text,
        published_fee="30-Minute Group - $80",
        verdict="matches application exactly",
        detail="Demo run — no page was read; this verdict is placeholder text.",
        evidence_url=app.url,
        evidence_files=[targeted.file],
    )
    run_log(f"record_rate_comparison: {rate.verdict}")
    _pause()

    findings += fee_cap_findings(app, checklist)
    findings += eligibility_findings(app, checklist)
    findings += internal_and_document_findings(checklist)

    usage = TokenUsage(requests=7, input_tokens=41_500, output_tokens=3_100)
    cost = usage.estimated_cost_usd(cfg.price_input_per_mtok, cfg.price_output_per_mtok)
    report = ReviewReport(
        tool_version=__version__,
        model=f"{cfg.model} (demo — not called)",
        source_pdf=source_pdf,
        reviewed_at=_now(),
        category=category,  # type: ignore[arg-type]
        category_display=checklist.display_name,
        application=app,
        rate_comparison=rate,
        findings=findings,
        evidence=store.records,
        agent_summary=(
            "This is a demo package produced with PROOFPACK_FAKE_RUN=1. No website was "
            "visited, no model was called and no finding here reflects a real check — it "
            "exists so the workbench can be exercised without an API key."
        ),
        checklist_notes=checklist.notes,
        reviewer_notes=list(previous.reviewer_notes) if previous else [],
        warnings=[
            "DEMO RUN (PROOFPACK_FAKE_RUN=1): no website was visited and no model was "
            "called. This package is not a real verification."
        ],
        usage=usage,
        estimated_cost_usd=cost,
        review_count=(previous.review_count + 1) if previous else 1,
    )

    store.write_manifest(package_dir)
    (package_dir / "report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    _write_html(report, package_dir)
    run_log(
        f"Tokens: {usage.input_tokens:,} in / {usage.output_tokens:,} out "
        f"across {usage.requests} request(s) — estimated ${cost:.2f}"
    )
    run_log(f"Report package written to {package_dir.name}")
    run_log.write(package_dir / "run.log")
    return report


def _write_html(report: ReviewReport, package_dir: Path) -> None:
    """Render report.html, falling back to a stub if the template is unavailable."""
    try:
        from .report import render_report

        render_report(report, package_dir)
    except Exception as e:  # noqa: BLE001 — a demo package must still open
        (package_dir / "report.html").write_text(
            "<!doctype html><meta charset='utf-8'><title>Demo package</title>"
            f"<h1>Demo package</h1><p>report.html could not be rendered: {e}</p>",
            encoding="utf-8",
        )


def fake_run_review(
    pdf_path: Path,
    cfg: ToolConfig,
    checklists: dict[str, Checklist],
    client=None,
    log: Callable[[str], None] = print,
    overrides: Optional[ReviewOverrides] = None,
) -> ReviewReport:
    """Stand-in for `pipeline.run_review`: same signature, same log line shapes.

    Asks one clarification (the missing URL) so the needs_input path is
    exercised, then writes a demo package next to the real slug (…-demo).
    """
    overrides = overrides or ReviewOverrides()
    run_log = log if isinstance(log, RunLog) else RunLog(log)
    category = _demo_category(checklists)
    checklist = checklists[category]

    run_log(f"Extracting application from {pdf_path.name} ...")
    _pause(2)
    url = (overrides.url or "").strip()
    run_log(
        f"  -> {category} | Demo requested item | Demo Provider | {url or 'NO URL'}"
    )
    if not url:
        raise NeedsClarification(
            "Demo run: no provider URL was read from the form. Which website should be "
            "reviewed?",
            field="url",
        )

    return _fake_verify_and_report(
        app=_demo_application(category, checklist, url),
        category=category,
        checklist=checklist,
        source_pdf=pdf_path.name,
        package_dir=fake_package_dir(pdf_path, cfg.output_dir),
        cfg=cfg,
        run_log=run_log,
    )


def fake_rerun_review(
    package_dir: Path,
    cfg: ToolConfig,
    checklists: dict[str, Checklist],
    client=None,
    log: Callable[[str], None] = print,
) -> ReviewReport:
    """Stand-in for `pipeline.rerun_review` (demo mode)."""
    report_path = package_dir / "report.json"
    if not report_path.exists():
        raise FileNotFoundError(f"No report.json in {package_dir} — run a review first.")
    previous = ReviewReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    run_log = log if isinstance(log, RunLog) else RunLog(log)
    run_log(f"Re-running website verification for {previous.source_pdf} ...")
    _pause(2)
    checklist = checklists[previous.category]
    return _fake_verify_and_report(
        app=previous.application,
        category=previous.category,
        checklist=checklist,
        source_pdf=previous.source_pdf,
        package_dir=package_dir,
        cfg=cfg,
        run_log=run_log,
        previous=previous,
    )
