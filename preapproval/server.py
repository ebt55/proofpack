"""The reviewer workbench: a localhost HTTP app around the same pipeline the CLI runs.

Deliberately small: FastAPI for routing, one background worker thread for
reviews (see jobs.py), server-sent events for the live agent trail, and static
serving of the finished packages so the report and its evidence open in the
browser exactly as they do from disk.

Security posture: this binds to 127.0.0.1 and has NO authentication. Review
packages contain participant names — do not expose it on a network.
"""

from __future__ import annotations

import json
import os
import queue
import re
import threading
from pathlib import Path
from typing import Callable, Optional

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .audit import integrity_report
from .chat import ChatSession
from .config import REPO_ROOT, Checklist, ToolConfig
from .jobs import NO_KEY_MESSAGE, JobManager
from .llm import new_chat, run_tool_loop
from .pipeline import package_dir_for
from .report import plain_text

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
SSE_HEARTBEAT_SECONDS = 15
STATUS_KEYS = (
    "found",
    "not_found",
    "needs_review",
    "internal",
    "needs_document",
    "pass",
    "flag",
)


def _safe_pdf_name(name: str) -> str:
    """Filename an upload is stored under: basename only, conservative charset."""
    base = Path(name or "").name
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-._")
    if not base.lower().endswith(".pdf"):
        base = f"{base or 'upload'}.pdf"
    return base[:120]


def _package_summary(pkg: Path) -> Optional[dict]:
    """The row the queue view shows for one finished package."""
    try:
        report = json.loads((pkg / "report.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a half-written package must not break the list
        return None
    counts = {key: 0 for key in STATUS_KEYS}
    website_total = 0
    for finding in report.get("findings", []):
        status = finding.get("status")
        if status in counts:
            counts[status] += 1
        if finding.get("source") == "agent":
            website_total += 1
    app_data = report.get("application", {})
    rate = report.get("rate_comparison", {})
    evidence = report.get("evidence", [])
    return {
        "slug": pkg.name,
        "source_pdf": report.get("source_pdf", ""),
        "category": report.get("category", ""),
        "category_display": report.get("category_display", ""),
        "participant_name": app_data.get("participant_name", ""),
        "participant_age": app_data.get("participant_age"),
        "requested_item": app_data.get("requested_item", ""),
        "provider_name": app_data.get("provider_name"),
        "url": app_data.get("url"),
        "reviewed_at": report.get("reviewed_at", ""),
        "review_count": report.get("review_count", 1),
        "model": report.get("model", ""),
        "rate_verdict": rate.get("verdict", ""),
        "published_fee": rate.get("published_fee"),
        "form_fee": rate.get("form_fee", ""),
        "counts": counts,
        "website_total": website_total,
        "warnings": len(report.get("warnings", [])),
        "estimated_cost_usd": report.get("estimated_cost_usd"),
        "evidence_count": len(evidence),
        "has_full_page": any(e.get("kind") == "full_page" for e in evidence),
    }


def _checklist_summary(checklist: Checklist) -> dict:
    return {
        "display_name": checklist.display_name,
        "adults_only": checklist.adults_only,
        "website_items": [
            {"id": i.id, "form_question": i.form_question, "requirement": i.requirement}
            for i in checklist.website_items
        ],
        "internal_count": len(checklist.internal_items),
        "document_count": len(checklist.document_items),
        "fee_caps": [
            {"id": c.id, "label": c.label, "max_amount": c.max_amount}
            for c in checklist.fee_caps
        ],
    }


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def create_app(
    cfg: ToolConfig,
    checklists: dict[str, Checklist],
    client_factory: Optional[Callable[[], object]] = None,
    *,
    samples_dir: Path,
    uploads_dir: Path,
    job_manager: Optional[JobManager] = None,
) -> FastAPI:
    """Build the workbench app.

    `client_factory` is None when no API key is configured — the UI stays usable
    (packages, integrity, reports) and only the model-backed routes refuse.
    `job_manager` is an injection point for tests; production passes None.
    """
    manager = job_manager or JobManager(cfg, checklists, client_factory)
    app = FastAPI(title="ProofPack workbench", version=__version__, docs_url=None, redoc_url=None)
    app.state.cfg = cfg
    app.state.jobs = manager

    static_dir = REPO_ROOT / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    templates_dir = REPO_ROOT / "templates"
    # Same search path as the report renderer (§3.2) so the shell can inline
    # the shared stylesheet with {% include "theme.css" %}.
    env = Environment(
        loader=FileSystemLoader([str(templates_dir), str(static_dir)]),
        autoescape=select_autoescape(["html"]),
    )
    chat_sessions: dict[str, tuple] = {}
    chat_lock = threading.Lock()
    # A Gemini chat holds one linear history, and `session.save()` rewrites the
    # package on disk. Two messages for the same package must therefore take
    # turns: `chat_lock` guards the session registry (fast), while the per-slug
    # lock below is held across the whole tool loop + save (slow).
    chat_turn_locks: dict[str, threading.Lock] = {}

    # -- errors -------------------------------------------------------------

    # One error shape for the whole API — including "no such route" — so the
    # frontend only ever has to read `.error`.
    @app.exception_handler(StarletteHTTPException)
    def _http_error(request: Request, exc: StarletteHTTPException):
        return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    def _validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse({"error": f"Invalid request body: {exc.errors()}"}, status_code=400)

    # -- helpers ------------------------------------------------------------

    def _package_dir(slug: str, *, require_report: bool = True) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", slug or "") or slug in {".", ".."}:
            raise HTTPException(404, f"No package named {slug!r}.")
        pkg = cfg.output_dir / slug
        if not pkg.is_dir() or (require_report and not (pkg / "report.json").exists()):
            raise HTTPException(404, f"No package named {slug!r}.")
        return pkg

    def _require_client():
        if manager.fake:
            return None
        if client_factory is None:
            raise HTTPException(503, NO_KEY_MESSAGE)
        return client_factory

    def _submit(job) -> JSONResponse:
        return JSONResponse({"job_id": job.id, "slug": job.slug}, status_code=202)

    def _guard_slug_free(slug: str) -> None:
        active = manager.active_for_slug(slug)
        if active is not None:
            raise HTTPException(
                409,
                f"A {active.kind} for '{slug}' is already {active.state} "
                f"(job {active.id}). Wait for it to finish first.",
            )

    def _state_payload() -> dict:
        packages = []
        if cfg.output_dir.exists():
            for pkg in sorted(cfg.output_dir.iterdir()):
                if not (pkg / "report.json").exists():
                    continue
                summary = _package_summary(pkg)
                if summary:
                    packages.append(summary)
        packages.sort(key=lambda p: p["reviewed_at"], reverse=True)
        known = {p["slug"] for p in packages}

        samples = []
        if samples_dir.exists():
            for pdf in sorted(samples_dir.glob("*.pdf")):
                slug = package_dir_for(pdf, cfg.output_dir).name
                if slug not in known and f"{slug}-demo" in known:
                    slug = f"{slug}-demo"
                samples.append(
                    {
                        "name": pdf.name,
                        "size_bytes": pdf.stat().st_size,
                        "package_slug": slug if slug in known else None,
                    }
                )
        return {
            "api_key_present": client_factory is not None,
            "model": cfg.model,
            "output_dir": str(cfg.output_dir),
            "samples": samples,
            "packages": packages,
            "jobs": manager.snapshots(lines=False),
        }

    # -- shell --------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def workbench() -> HTMLResponse:
        state = _state_payload()
        html = env.get_template("workbench.html.j2").render(
            state=state,
            version=__version__,
            model=state["model"],
            api_key_present=state["api_key_present"],
            output_dir=state["output_dir"],
            fake_run=manager.fake,
        )
        return HTMLResponse(html)

    # -- state --------------------------------------------------------------

    @app.get("/api/state")
    def api_state() -> dict:
        return _state_payload()

    @app.get("/api/checklists")
    def api_checklists() -> dict:
        return {name: _checklist_summary(cl) for name, cl in sorted(checklists.items())}

    # -- reviews ------------------------------------------------------------

    def _start_review(pdf_path: Path, url: Optional[str]):
        from .jobs import fake_package_dir

        slug = (
            fake_package_dir(pdf_path, cfg.output_dir)
            if manager.fake
            else package_dir_for(pdf_path, cfg.output_dir)
        ).name
        _guard_slug_free(slug)
        return manager.submit_review(pdf_path, url)

    @app.post("/api/reviews")
    async def api_reviews(request: Request):
        content_type = request.headers.get("content-type", "")

        if content_type.startswith("multipart/form-data"):
            _require_client()
            form = await request.form()
            upload = form.get("file")
            if upload is None or not hasattr(upload, "read"):
                raise HTTPException(400, "Attach the application PDF as the 'file' field.")
            data = b""
            while True:
                chunk = await upload.read(1 << 20)
                if not chunk:
                    break
                data += chunk
                if len(data) > MAX_UPLOAD_BYTES:
                    raise HTTPException(400, "That file is larger than 25 MB.")
            if not data.startswith(b"%PDF"):
                raise HTTPException(
                    400, "That file is not a PDF (it does not start with %PDF)."
                )
            name = _safe_pdf_name(getattr(upload, "filename", "") or "upload.pdf")
            uploads_dir.mkdir(parents=True, exist_ok=True)
            pdf_path = uploads_dir / name
            pdf_path.write_bytes(data)
            url = str(form.get("url") or "").strip() or None
            return _submit(_start_review(pdf_path, url))

        try:
            body = await request.json()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"Could not read the request body as JSON: {e}") from e
        if not isinstance(body, dict):
            raise HTTPException(400, "Expected a JSON object.")

        if body.get("package"):
            slug = str(body["package"])
            pkg = _package_dir(slug)
            _require_client()
            _guard_slug_free(slug)
            try:
                job = manager.submit_rerun(pkg)
            except (FileNotFoundError, ValueError) as e:
                raise HTTPException(400, str(e)) from e
            return _submit(job)

        if body.get("sample"):
            name = Path(str(body["sample"])).name
            pdf_path = samples_dir / name
            if not pdf_path.exists():
                raise HTTPException(404, f"No sample named {name!r}.")
            _require_client()
            url = str(body.get("url") or "").strip() or None
            return _submit(_start_review(pdf_path, url))

        raise HTTPException(
            400, "Send a PDF upload, {\"sample\": \"...\"} or {\"package\": \"...\"}."
        )

    # -- jobs ---------------------------------------------------------------

    @app.get("/api/jobs/{job_id}")
    def api_job(job_id: str) -> dict:
        snap = manager.snapshot(job_id)
        if snap is None:
            raise HTTPException(404, f"No job {job_id!r}.")
        return snap

    @app.get("/api/jobs/{job_id}/events")
    def api_job_events(job_id: str) -> StreamingResponse:
        if manager.get(job_id) is None:
            raise HTTPException(404, f"No job {job_id!r}.")

        def stream():
            # A plain sync generator: Starlette iterates it in a threadpool, so
            # blocking on the queue here never blocks the event loop.
            lines, q = manager.subscribe(job_id)
            try:
                snap = manager.snapshot(job_id, lines=False)
                if snap:
                    yield _sse("state", snap)
                for entry in lines:
                    yield _sse("line", entry)
                while True:
                    try:
                        event, payload = q.get(timeout=SSE_HEARTBEAT_SECONDS)
                    except queue.Empty:
                        yield ": ping\n\n"
                        continue
                    yield _sse(event, payload)
                    if event == "end":
                        break
            finally:
                manager.unsubscribe(job_id, q)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/jobs/{job_id}/answer")
    def api_job_answer(job_id: str, body: dict = Body(default_factory=dict)) -> dict:
        if manager.get(job_id) is None:
            raise HTTPException(404, f"No job {job_id!r}.")
        try:
            if body.get("skip"):
                manager.answer(job_id, skip=True)
            else:
                value = str(body.get("value") or "").strip()
                if not value:
                    raise HTTPException(400, "Send {\"value\": \"...\"} or {\"skip\": true}.")
                manager.answer(job_id, value=value)
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        snap = manager.snapshot(job_id, lines=False)
        return snap or {}

    # -- packages -----------------------------------------------------------

    @app.get("/api/packages/{slug}")
    def api_package(slug: str) -> dict:
        pkg = _package_dir(slug)
        report = json.loads((pkg / "report.json").read_text(encoding="utf-8"))
        report["slug"] = slug
        return report

    @app.get("/api/packages/{slug}/integrity")
    def api_package_integrity(slug: str) -> dict:
        return integrity_report(_package_dir(slug))

    @app.get("/api/packages/{slug}/log", response_class=PlainTextResponse)
    def api_package_log(slug: str) -> PlainTextResponse:
        log_path = _package_dir(slug) / "run.log"
        if not log_path.exists():
            raise HTTPException(404, "This package has no run.log.")
        return PlainTextResponse(
            log_path.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"}
        )

    @app.post("/api/packages/{slug}/chat")
    def api_package_chat(slug: str, body: dict = Body(default_factory=dict)) -> dict:
        pkg = _package_dir(slug)
        message = str(body.get("message") or "").strip()
        if not message:
            raise HTTPException(400, "Send {\"message\": \"...\"}.")
        if client_factory is None:
            raise HTTPException(503, NO_KEY_MESSAGE)

        with chat_lock:
            entry = chat_sessions.get(slug)
            if entry is None:
                session = ChatSession(pkg, client_factory(), cfg, checklists)
                tools = session.build_tools()
                chat = new_chat(
                    session.client,
                    session.model,
                    session.system_prompt(),
                    tools,
                    max_output_tokens=4096,
                )
                entry = (session, chat, tools)
                chat_sessions[slug] = entry
            turn_lock = chat_turn_locks.setdefault(slug, threading.Lock())
        session, chat, tools = entry

        with turn_lock:
            before = session.report.review_count
            result = run_tool_loop(chat, tools, message, max_iterations=12, log=lambda m: None)
            changed = session.dirty
            if session.dirty:
                session.save()
            changed = changed or session.report.review_count != before
        # The chat drawer renders the reply as text, not markdown, so strip the
        # markdown the model slips in — otherwise "**published fees**" reaches
        # the panel with its asterisks showing.
        return {"reply": plain_text(result.text), "changed": changed}

    # -- package files ------------------------------------------------------

    @app.get("/packages/{slug}/{path:path}")
    def package_file(slug: str, path: str) -> FileResponse:
        pkg = _package_dir(slug, require_report=False)
        root = pkg.resolve()
        target = (root / path).resolve()
        if root != target and root not in target.parents:
            raise HTTPException(404, "Not found.")
        if not target.is_file():
            raise HTTPException(404, "Not found.")
        return FileResponse(target, headers={"Cache-Control": "no-store"})

    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    return app


def build_app() -> FastAPI:
    """Convenience factory for `uvicorn preapproval.server:build_app --factory`."""
    from dotenv import load_dotenv

    from .config import load_checklists, load_config

    load_dotenv(REPO_ROOT / ".env")
    cfg = load_config()
    checklists = load_checklists(cfg.checklist_dir)
    factory = None
    if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
        from .llm import make_client

        factory = make_client
    return create_app(
        cfg,
        checklists,
        factory,
        samples_dir=REPO_ROOT / "samples",
        uploads_dir=REPO_ROOT / "uploads",
    )
