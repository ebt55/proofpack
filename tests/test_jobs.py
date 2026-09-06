"""The workbench's job manager: state machine, log fan-out, clarifications.

Offline: every run here uses the demo runner that ships in jobs.py (the one
PROOFPACK_FAKE_RUN=1 turns on), so no browser, no API key and no network are
involved — only its watch-me pauses are turned off.
"""

from __future__ import annotations

import json
import queue as queue_module
import time

import pytest

from preapproval import jobs as jobs_module
from preapproval.config import ToolConfig, load_checklists
from preapproval.jobs import JobManager

SAMPLE_URL = "https://example.org/classes"


@pytest.fixture
def cfg(tmp_path) -> ToolConfig:
    cfg = ToolConfig()
    cfg.output_dir = tmp_path / "out"
    cfg.output_dir.mkdir(parents=True)
    return cfg


@pytest.fixture
def no_delay(monkeypatch):
    monkeypatch.setattr(jobs_module, "FAKE_DELAY", 0)


def demo_manager(cfg) -> JobManager:
    """Manager in demo mode: runs `jobs.fake_run_review` instead of the pipeline."""
    return JobManager(cfg, load_checklists(), client_factory=None, fake=True)


def real_manager(cfg, client_factory=lambda: object()) -> JobManager:
    """Manager on the real code path (used with a monkeypatched run_review)."""
    return JobManager(cfg, load_checklists(), client_factory=client_factory, fake=False)


def wait_for(manager, job_id, *states, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = manager.snapshot(job_id, lines=False)
        if snap["state"] in states:
            return snap
        time.sleep(0.02)
    raise AssertionError(
        f"job {job_id} never reached {states} "
        f"(state={manager.snapshot(job_id, lines=False)['state']})"
    )


def texts(manager, job_id) -> list[str]:
    return [line["text"] for line in manager.snapshot(job_id)["lines"]]


# --- the happy path, including one clarification ---------------------------


def test_review_asks_then_completes(cfg, no_delay, tmp_path):
    manager = demo_manager(cfg)
    pdf = tmp_path / "01-community-class-gallopnyc.pdf"
    pdf.write_bytes(b"%PDF-1.4 not really a pdf")

    job = manager.submit_review(pdf)
    assert job.state in ("queued", "running")
    # A demo run must never be able to overwrite a real audit package.
    assert job.slug == "01-community-class-gallopnyc-demo"

    snap = wait_for(manager, job.id, "needs_input")
    assert snap["question"]["field"] == "url"
    assert snap["question"]["text"]
    assert snap["started_at"]

    manager.answer(job.id, value=SAMPLE_URL)
    snap = wait_for(manager, job.id, "done")

    assert snap["error"] is None
    assert snap["finished_at"]
    assert snap["report_ready"] is True

    lines = texts(manager, job.id)
    assert any(t.startswith("Extracting application from 01-community") for t in lines)
    assert any(t.startswith(f"Verifying on the provider's website ({SAMPLE_URL})") for t in lines)
    assert any(t.startswith("record_finding: ") for t in lines)
    assert any(t.startswith("REJECTED record_finding ") for t in lines)
    assert any(t.startswith("Tokens: ") for t in lines)
    assert any(t.startswith("Report package written to") for t in lines)

    # evidence is parsed out of the "  -> evidence/..." lines
    assert snap["evidence"], "no evidence parsed from the log"
    assert all(f.startswith("evidence/") and f.endswith(".png") for f in snap["evidence"])

    package_dir = cfg.output_dir / job.slug
    for name in ("report.json", "manifest.json", "report.html", "run.log"):
        assert (package_dir / name).exists(), f"{name} missing from the package"
    for rel in snap["evidence"]:
        assert (package_dir / rel).exists()


def test_completed_package_passes_the_integrity_audit(cfg, no_delay, tmp_path):
    """The demo package must be structurally honest, or it teaches nothing."""
    from preapproval.audit import audit_package

    manager = demo_manager(cfg)
    pdf = tmp_path / "demo-application.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    job = manager.submit_review(pdf, url=SAMPLE_URL)
    wait_for(manager, job.id, "done")

    package_dir = cfg.output_dir / job.slug
    assert audit_package(package_dir) == []
    report = json.loads((package_dir / "report.json").read_text(encoding="utf-8"))
    assert any("DEMO RUN" in w for w in report["warnings"]), "a fake package must say so"


# --- skipping and failing ---------------------------------------------------


def test_skip_cancels_the_job(cfg, no_delay, tmp_path):
    manager = demo_manager(cfg)
    pdf = tmp_path / "02-membership-brooklyn-museum.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    job = manager.submit_review(pdf)
    wait_for(manager, job.id, "needs_input")
    manager.answer(job.id, skip=True)
    snap = wait_for(manager, job.id, "cancelled")

    assert snap["question"] is None
    assert snap["finished_at"]
    assert not (cfg.output_dir / job.slug).exists()


def test_exception_fails_the_job_with_its_type(cfg, monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise RuntimeError("the browser fell over")

    monkeypatch.setattr(jobs_module, "run_review", boom)
    manager = real_manager(cfg)
    pdf = tmp_path / "03-community-class-gracie-barra.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    job = manager.submit_review(pdf, url=SAMPLE_URL)
    snap = wait_for(manager, job.id, "failed")
    assert snap["error"] == "RuntimeError: the browser fell over"
    assert snap["report_ready"] is False
    assert snap["slug"] == "03-community-class-gracie-barra"  # no -demo off the fake path


def test_missing_api_key_fails_with_the_friendly_message(cfg, no_delay, monkeypatch, tmp_path):
    monkeypatch.setattr(jobs_module, "run_review", jobs_module.fake_run_review)
    manager = real_manager(cfg, client_factory=None)
    pdf = tmp_path / "04-coaching-love-and-logic.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    job = manager.submit_review(pdf, url=SAMPLE_URL)
    snap = wait_for(manager, job.id, "failed")
    assert "GEMINI_API_KEY" in snap["error"]
    assert "Traceback" not in snap["error"]


# --- re-runs, queueing and subscriptions ------------------------------------


def test_rerun_reuses_the_package_and_bumps_the_review_count(cfg, no_delay, tmp_path):
    manager = demo_manager(cfg)
    pdf = tmp_path / "05-hri-laptop-macbook-air.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    first = manager.submit_review(pdf, url=SAMPLE_URL)
    wait_for(manager, first.id, "done")

    package_dir = cfg.output_dir / first.slug
    again = manager.submit_rerun(package_dir)
    assert again.kind == "rerun"
    assert again.source_pdf == pdf.name
    snap = wait_for(manager, again.id, "done")
    assert snap["report_ready"] is True

    report = json.loads((package_dir / "report.json").read_text(encoding="utf-8"))
    assert report["review_count"] == 2
    assert texts(manager, again.id)[0].startswith("Re-running website verification for")


def test_demo_mode_refuses_to_rerun_a_real_package(cfg, no_delay):
    manager = demo_manager(cfg)
    real = cfg.output_dir / "01-community-class-gallopnyc"
    real.mkdir()
    (real / "report.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="demo packages"):
        manager.submit_rerun(real)


def test_active_job_for_a_slug_is_reported(cfg, no_delay, tmp_path):
    manager = demo_manager(cfg)
    pdf = tmp_path / "06-otps-weighted-blanket-gravity.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    job = manager.submit_review(pdf)
    wait_for(manager, job.id, "needs_input")
    assert manager.active_for_slug(job.slug) is job
    assert manager.active_for_slug("nothing-here") is None

    manager.answer(job.id, skip=True)
    wait_for(manager, job.id, "cancelled")
    assert manager.active_for_slug(job.slug) is None


def test_subscribers_get_lines_then_an_end_event(cfg, no_delay, tmp_path):
    manager = demo_manager(cfg)
    pdf = tmp_path / "07-appeal-gracie-barra.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    job = manager.submit_review(pdf, url=SAMPLE_URL)

    _replay, q = manager.subscribe(job.id)
    events = []
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            events.append(q.get(timeout=1))
        except queue_module.Empty:
            continue
        if events[-1][0] == "end":
            break
    manager.unsubscribe(job.id, q)

    assert events[-1] == ("end", {"state": "done"})
    line_texts = [payload["text"] for kind, payload in events if kind == "line"]
    assert any(t.startswith("open_url: ") for t in line_texts)
    assert all(set(payload) == {"t", "text"} for kind, payload in events if kind == "line")


def test_late_subscriber_to_a_finished_job_still_ends(cfg, no_delay, tmp_path):
    manager = demo_manager(cfg)
    pdf = tmp_path / "late.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    job = manager.submit_review(pdf, url=SAMPLE_URL)
    wait_for(manager, job.id, "done")

    replay, q = manager.subscribe(job.id)
    assert replay and replay[0]["text"].startswith("Extracting application from late.pdf")
    assert q.get(timeout=1)[0] == "state"
    assert q.get(timeout=1) == ("end", {"state": "done"})
    manager.unsubscribe(job.id, q)
