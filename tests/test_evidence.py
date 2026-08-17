"""Evidence integrity: stamping, hashing, manifest, and the audit invariant."""

import json

from PIL import Image

from preapproval.evidence import EvidenceStore, sha256_of


def make_png(path, size=(400, 300)):
    Image.new("RGB", size, (200, 220, 240)).save(path, format="PNG")


def test_stamp_adds_visible_banner_and_manifest_hash(tmp_path):
    store = EvidenceStore(tmp_path)
    p = store.next_path("evidence", "published fees")
    make_png(p)
    original_height = Image.open(p).height

    record = store.register(
        p, kind="targeted", label="Evidence: published fees", url="https://example.org/fees"
    )

    stamped = Image.open(p)
    assert stamped.height > original_height, "banner must be burned into the image"
    assert record.sha256 == sha256_of(p), "manifest hash must match the stamped file"
    assert record.captured_at and record.url == "https://example.org/fees"
    assert record.file.startswith("evidence/")


def test_manifest_roundtrip_and_has_file(tmp_path):
    store = EvidenceStore(tmp_path)
    p = store.next_path("full", "homepage")
    make_png(p)
    record = store.register(p, kind="full_page", label="Full page", url="https://example.org")

    assert store.has_file(record.file)
    assert not store.has_file("evidence/nonexistent.png")

    store.write_manifest(tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["captures"][0]["sha256"] == record.sha256


def test_rerun_clears_stale_captures(tmp_path):
    """A package must never hold evidence files its manifest doesn't reference."""
    store = EvidenceStore(tmp_path)
    first = store.next_path("evidence", "old label")
    make_png(first)
    store.register(first, kind="targeted", label="Old", url="https://example.org")
    assert first.exists()

    rerun = EvidenceStore(tmp_path)  # fresh=True by default
    assert not first.exists(), "stale capture from the previous run should be removed"

    new = rerun.next_path("evidence", "new label")
    make_png(new)
    record = rerun.register(new, kind="targeted", label="New", url="https://example.org")
    rerun.write_manifest(tmp_path)

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    manifest_files = {c["file"] for c in manifest["captures"]}
    on_disk = {f"evidence/{p.name}" for p in (tmp_path / "evidence").glob("*.png")}
    assert on_disk == manifest_files == {record.file}


def test_append_mode_keeps_existing_captures(tmp_path):
    store = EvidenceStore(tmp_path)
    first = store.next_path("evidence", "keep me")
    make_png(first)
    store.register(first, kind="targeted", label="Keep", url="https://example.org")

    EvidenceStore(tmp_path, fresh=False)
    assert first.exists()


def test_tampering_is_detectable(tmp_path):
    """The audit story: altering a capture after the fact breaks the manifest hash."""
    store = EvidenceStore(tmp_path)
    p = store.next_path("evidence", "price")
    make_png(p)
    record = store.register(p, kind="targeted", label="Price", url="https://example.org")

    img = Image.open(p)
    img.putpixel((5, img.height - 5), (255, 0, 0))  # "edit" the evidence
    img.save(p, format="PNG")

    assert sha256_of(p) != record.sha256
