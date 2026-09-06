"""The workbench HTTP API.

Offline: no key, no browser, no network. Job submission runs the demo runner
from jobs.py; the chat route is exercised against a stubbed ChatSession.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from preapproval import jobs as jobs_module
from preapproval import server as server_module
from preapproval.config import ToolConfig, load_checklists, load_config
from preapproval.jobs import JobManager
from preapproval.server import create_app

REPO = Path(__file__).resolve().parent.parent
SAMPLES = REPO / "samples"
STATE_KEYS = {"api_key_present", "model", "output_dir", "samples", "packages", "jobs"}
PACKAGE_KEYS = {
    "slug", "source_pdf", "category", "category_display", "participant_name",
    "participant_age", "requested_item", "provider_name", "url", "reviewed_at",
    "review_count", "model", "rate_verdict", "published_fee", "form_fee", "counts",
    "website_total", "warnings", "estimated_cost_usd", "evidence_count", "has_full_page",
}
COUNT_KEYS = {
    "found", "not_found", "needs_review", "internal", "needs_document", "pass", "flag"
}
JOB_KEYS = {
    "id", "kind", "slug", "source_pdf", "state", "started_at", "finished_at", "lines",
    "question", "error", "report_ready", "evidence",
}


def build(cfg, *, client_factory=None, fake=False, uploads=None, checklists=None):
    checklists = checklists or load_checklists()
    manager = JobManager(cfg, checklists, client_factory, fake=fake)
    app = create_app(
        cfg,
        checklists,
        client_factory,
        samples_dir=SAMPLES,
        uploads_dir=uploads or (REPO / "uploads"),
        job_manager=manager,
    )
    return TestClient(app), manager


@pytest.fixture
def repo_client():
    """An app over the committed packages in output/ (read-only routes)."""
    client, _manager = build(load_config())
    with client:
        yield client


@pytest.fixture
def demo_client(tmp_path, monkeypatch):
    """An app whose reviews run the demo runner into a temp output dir."""
    monkeypatch.setattr(jobs_module, "FAKE_DELAY", 0)
    cfg = ToolConfig()
    cfg.output_dir = tmp_path / "out"
    cfg.output_dir.mkdir(parents=True)
    client, manager = build(cfg, fake=True, uploads=tmp_path / "uploads")
    with client:
        yield client, manager, cfg


def committed_packages(client) -> list[dict]:
    return client.get("/api/state").json()["packages"]


def first_package(client) -> dict:
    pkgs = committed_packages(client)
    if not pkgs:
        pytest.skip("no report packages in output/ yet")
    return pkgs[0]


def wait_for_state(client, job_id, *states, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = client.get(f"/api/jobs/{job_id}").json()
        if snap["state"] in states:
            return snap
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} never reached {states}")


# --- shell + state ----------------------------------------------------------


def test_shell_renders(repo_client):
    resp = repo_client.get("/")
    assert resp.status_code == 200
    assert "ProofPack" in resp.text
    assert repo_client.get("/static/app.js").status_code == 200


def test_state_shape(repo_client):
    state = repo_client.get("/api/state").json()
    assert set(state) == STATE_KEYS
    assert isinstance(state["api_key_present"], bool)
    assert state["model"]
    assert state["jobs"] == []

    assert state["samples"], "samples/ should hold the seven sample PDFs"
    for sample in state["samples"]:
        assert set(sample) == {"name", "size_bytes", "package_slug"}
        assert sample["name"].endswith(".pdf")
        assert sample["size_bytes"] > 0

    for pkg in state["packages"]:
        assert set(pkg) == PACKAGE_KEYS
        assert set(pkg["counts"]) == COUNT_KEYS
        assert pkg["website_total"] >= 0
        assert pkg["slug"] and pkg["reviewed_at"]


def test_reviewed_samples_link_to_their_package(repo_client):
    state = repo_client.get("/api/state").json()
    slugs = {p["slug"] for p in state["packages"]}
    if not slugs:
        pytest.skip("no report packages in output/ yet")
    linked = [s for s in state["samples"] if s["package_slug"]]
    assert linked, "committed packages exist, so some sample should link to one"
    assert all(s["package_slug"] in slugs for s in linked)


def test_checklists(repo_client):
    data = repo_client.get("/api/checklists").json()
    assert "community_class" in data
    cl = data["community_class"]
    assert set(cl) == {
        "display_name",
        "adults_only",
        "website_items",
        "internal_count",
        "document_count",
        "fee_caps",
    }
    assert cl["website_items"]
    for item in cl["website_items"]:
        assert set(item) == {"id", "form_question", "requirement"}
    assert isinstance(cl["internal_count"], int)
    for cap in cl["fee_caps"]:
        assert set(cap) == {"id", "label", "max_amount"}


# --- packages ---------------------------------------------------------------


def test_package_json_is_verbatim_plus_slug(repo_client):
    slug = first_package(repo_client)["slug"]
    body = repo_client.get(f"/api/packages/{slug}").json()
    on_disk = json.loads(
        (load_config().output_dir / slug / "report.json").read_text(encoding="utf-8")
    )
    assert body["slug"] == slug
    body.pop("slug")
    assert body == on_disk


def test_integrity_of_a_committed_package(repo_client):
    slug = first_package(repo_client)["slug"]
    body = repo_client.get(f"/api/packages/{slug}/integrity").json()
    assert set(body) == {"ok", "checked_at", "problems", "captures"}
    assert body["ok"] is True, body["problems"]
    assert body["problems"] == []
    assert body["captures"]
    for cap in body["captures"]:
        assert set(cap) == {"file", "expected", "actual", "ok"}
        assert cap["ok"] is True
        assert cap["actual"] == cap["expected"]


def test_run_log_is_plain_text(repo_client):
    slug = first_package(repo_client)["slug"]
    resp = repo_client.get(f"/api/packages/{slug}/log")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "Extracting application from" in resp.text


def test_unknown_package_is_a_json_error(repo_client):
    resp = repo_client.get("/api/packages/no-such-package")
    assert resp.status_code == 404
    assert "error" in resp.json()


def test_package_files_are_served(repo_client):
    slug = first_package(repo_client)["slug"]
    resp = repo_client.get(f"/packages/{slug}/report.html")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"

    manifest = repo_client.get(f"/packages/{slug}/manifest.json").json()
    capture = manifest["captures"][0]["file"]
    png = repo_client.get(f"/packages/{slug}/{capture}")
    assert png.status_code == 200
    assert png.content[:4] == b"\x89PNG"


@pytest.mark.parametrize(
    "path",
    [
        "../../config.yaml",
        "..%2f..%2fconfig.yaml",
        "evidence/../../../config.yaml",
        "nope.txt",
    ],
)
def test_package_files_refuse_to_escape_the_package(repo_client, path):
    slug = first_package(repo_client)["slug"]
    resp = repo_client.get(f"/packages/{slug}/{path}")
    assert resp.status_code == 404
    assert "error" in resp.json()          # one error shape for every route
    assert "checklist_dir" not in resp.text  # nothing from config.yaml leaked


# --- reviews ----------------------------------------------------------------


def test_upload_must_be_a_pdf(demo_client):
    client, _manager, _cfg = demo_client
    resp = client.post(
        "/api/reviews",
        files={"file": ("notes.pdf", b"this is not a pdf at all", "application/pdf")},
    )
    assert resp.status_code == 400
    assert "not a PDF" in resp.json()["error"]


def test_upload_review_runs_and_streams(demo_client):
    client, manager, cfg = demo_client
    pdf = (SAMPLES / "01-community-class-gallopnyc.pdf").read_bytes()
    resp = client.post(
        "/api/reviews",
        files={"file": ("01-community-class-gallopnyc.pdf", pdf, "application/pdf")},
        data={"url": "https://example.org/classes"},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert set(body) == {"job_id", "slug"}
    assert body["slug"] == "01-community-class-gallopnyc-demo"

    snap = wait_for_state(client, body["job_id"], "done")
    assert set(snap) == JOB_KEYS
    assert snap["kind"] == "review"
    assert snap["report_ready"] is True
    assert snap["evidence"]
    assert all(set(line) == {"t", "text"} for line in snap["lines"])

    # the package it wrote is now served and listed
    assert client.get(f"/packages/{body['slug']}/report.html").status_code == 200
    assert body["slug"] in {p["slug"] for p in client.get("/api/state").json()["packages"]}

    # SSE replays the finished run and closes
    with client.stream("GET", f"/api/jobs/{body['job_id']}/events") as stream:
        assert stream.headers["content-type"].startswith("text/event-stream")
        raw = "".join(chunk for chunk in stream.iter_text())
    assert raw.startswith("event: state")
    assert "event: line" in raw
    assert raw.rstrip().endswith('data: {"state": "done"}')
    lines = [
        json.loads(part.split("data: ", 1)[1])
        for part in raw.split("\n\n")
        if part.startswith("event: line")
    ]
    assert any(line["text"].startswith("open_url: ") for line in lines)


def test_sample_review_answers_a_clarification_then_finishes(demo_client):
    client, _manager, _cfg = demo_client
    resp = client.post("/api/reviews", json={"sample": "02-membership-brooklyn-museum.pdf"})
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    snap = wait_for_state(client, job_id, "needs_input")
    assert snap["question"]["field"] == "url"

    answered = client.post(
        f"/api/jobs/{job_id}/answer", json={"value": "https://example.org/membership"}
    )
    assert answered.status_code == 200
    assert wait_for_state(client, job_id, "done")["state"] == "done"


def test_answering_with_skip_cancels(demo_client):
    client, _manager, _cfg = demo_client
    job_id = client.post(
        "/api/reviews", json={"sample": "03-community-class-gracie-barra.pdf"}
    ).json()["job_id"]
    wait_for_state(client, job_id, "needs_input")
    assert client.post(f"/api/jobs/{job_id}/answer", json={"skip": True}).status_code == 200
    assert wait_for_state(client, job_id, "cancelled")["state"] == "cancelled"


def test_second_review_of_the_same_slug_conflicts(demo_client):
    client, _manager, _cfg = demo_client
    payload = {"sample": "04-coaching-love-and-logic.pdf"}
    first = client.post("/api/reviews", json=payload)
    assert first.status_code == 202
    again = client.post("/api/reviews", json=payload)
    assert again.status_code == 409
    assert "already" in again.json()["error"]

    client.post(f"/api/jobs/{first.json()['job_id']}/answer", json={"skip": True})
    wait_for_state(client, first.json()["job_id"], "cancelled")


def test_rerun_of_a_demo_package(demo_client):
    client, _manager, _cfg = demo_client
    slug = client.post(
        "/api/reviews",
        json={"sample": "05-hri-laptop-macbook-air.pdf", "url": "https://example.org/x"},
    ).json()["slug"]
    wait_for_state(client, client.get("/api/state").json()["jobs"][0]["id"], "done")

    resp = client.post("/api/reviews", json={"package": slug})
    assert resp.status_code == 202
    snap = wait_for_state(client, resp.json()["job_id"], "done")
    assert snap["kind"] == "rerun"


def test_bad_review_requests(demo_client):
    client, _manager, _cfg = demo_client
    assert client.post("/api/reviews", json={}).status_code == 400
    assert client.post("/api/reviews", json={"sample": "nope.pdf"}).status_code == 404
    assert client.post("/api/reviews", json={"package": "nope"}).status_code == 404
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.get("/api/jobs/nope/events").status_code == 404
    assert client.post("/api/jobs/nope/answer", json={"value": "x"}).status_code == 404


def test_reviews_need_a_key_when_not_in_demo_mode(tmp_path):
    cfg = ToolConfig()
    cfg.output_dir = tmp_path / "out"
    cfg.output_dir.mkdir(parents=True)
    client, _manager = build(cfg, client_factory=None, fake=False, uploads=tmp_path / "up")
    with client:
        resp = client.post("/api/reviews", json={"sample": "01-community-class-gallopnyc.pdf"})
    assert resp.status_code == 503
    assert "GEMINI_API_KEY" in resp.json()["error"]


# --- reviewer chat -----------------------------------------------------------


class StubChatSession:
    """Stands in for ChatSession — the route's job is plumbing, not the model."""

    instances: list["StubChatSession"] = []

    def __init__(self, package_dir, client, cfg, checklists):
        self.package_dir = package_dir
        self.client = client
        self.model = cfg.model
        self.dirty = False
        self.saved = 0
        self.report = SimpleNamespace(review_count=1)
        StubChatSession.instances.append(self)

    def build_tools(self):
        return []

    def system_prompt(self):
        return "stub"

    def save(self):
        self.saved += 1
        self.dirty = False


@pytest.fixture
def chat_client(monkeypatch):
    StubChatSession.instances = []
    monkeypatch.setattr(server_module, "ChatSession", StubChatSession)
    monkeypatch.setattr(server_module, "new_chat", lambda *a, **k: object())
    client, _manager = build(load_config(), client_factory=lambda: object())
    with client:
        yield client


def test_chat_reply(chat_client, monkeypatch):
    """The reply is stripped of markdown — the panel renders it as plain text."""
    monkeypatch.setattr(
        server_module,
        "run_tool_loop",
        lambda *a, **k: SimpleNamespace(text="# Answer\nNothing **changed**."),
    )
    slug = first_package(chat_client)["slug"]
    body = chat_client.post(
        f"/api/packages/{slug}/chat", json={"message": "what did you not find?"}
    ).json()
    assert body == {"reply": "Answer\nNothing changed.", "changed": False}
    assert "**" not in body["reply"] and "#" not in body["reply"]
    assert StubChatSession.instances[0].saved == 0


def test_chat_edit_saves_and_reports_the_change(chat_client, monkeypatch):
    def edit(chat, tools, message, **kwargs):
        StubChatSession.instances[-1].dirty = True
        return SimpleNamespace(text="Marked it needs review.")

    monkeypatch.setattr(server_module, "run_tool_loop", edit)
    slug = first_package(chat_client)["slug"]
    body = chat_client.post(
        f"/api/packages/{slug}/chat", json={"message": "change published_fees"}
    ).json()
    assert body["changed"] is True
    assert StubChatSession.instances[-1].saved == 1


def test_chat_notices_a_rerun(chat_client, monkeypatch):
    def rerun(chat, tools, message, **kwargs):
        StubChatSession.instances[-1].report.review_count = 2
        return SimpleNamespace(text="Re-ran the verification.")

    monkeypatch.setattr(server_module, "run_tool_loop", rerun)
    slug = first_package(chat_client)["slug"]
    body = chat_client.post(
        f"/api/packages/{slug}/chat", json={"message": "re-run the check"}
    ).json()
    assert body["changed"] is True


def test_chat_reuses_one_session_per_package(chat_client, monkeypatch):
    """Two messages for the same slug share a session (and so one chat history)."""
    seen: list[object] = []

    def remember(chat, tools, message, **kwargs):
        seen.append(chat)
        return SimpleNamespace(text="ok")

    monkeypatch.setattr(server_module, "run_tool_loop", remember)
    slug = first_package(chat_client)["slug"]
    for message in ("what did you not find?", "and why?"):
        assert (
            chat_client.post(f"/api/packages/{slug}/chat", json={"message": message}).status_code
            == 200
        )
    assert len(StubChatSession.instances) == 1
    assert len(seen) == 2 and seen[0] is seen[1]


def test_chat_without_a_key_is_503(repo_client):
    slug = first_package(repo_client)["slug"]
    resp = repo_client.post(f"/api/packages/{slug}/chat", json={"message": "hi"})
    assert resp.status_code == 503
    assert "GEMINI_API_KEY" in resp.json()["error"]


def test_chat_needs_a_message(chat_client):
    slug = first_package(chat_client)["slug"]
    assert chat_client.post(f"/api/packages/{slug}/chat", json={}).status_code == 400
