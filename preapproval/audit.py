"""Package integrity audit — the checks behind "no hallucinated findings".

This lives in the library (not in tests/) because two callers need exactly the
same answer: the offline audit test that gates every commit, and the
workbench's "Verify integrity" button. A reviewer clicking that button sees the
same verdict CI does.

Invariants checked:
  1. every 'found' finding cites >= 1 evidence file;
  2. every cited evidence file is in manifest.json AND exists on disk;
  3. every manifest hash matches the file on disk (tamper check);
  4. a rate verdict of matches/differs cites evidence too;
  5. no evidence file on disk is missing from the manifest (no unexplained
     captures — e.g. leftovers from an earlier run);
  6. the package holds at least one whole-page capture (the "here is the site
     we reviewed" record).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import REPO_ROOT
from .evidence import sha256_of


def audit_package(pkg: Path) -> list[str]:
    """Return a list of integrity problems for one package (empty == clean)."""
    problems: list[str] = []
    report = json.loads((pkg / "report.json").read_text(encoding="utf-8"))
    manifest = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))["captures"]
    manifest_files = {c["file"] for c in manifest}

    for c in manifest:
        f = pkg / c["file"]
        if not f.exists():
            problems.append(f"manifest lists missing file {c['file']}")
        elif sha256_of(f) != c["sha256"]:
            problems.append(f"HASH MISMATCH (tampered?): {c['file']}")

    on_disk = {f"evidence/{p.name}" for p in (pkg / "evidence").glob("*.png")}
    for orphan in sorted(on_disk - manifest_files):
        problems.append(f"evidence file not in manifest (stale re-run leftover?): {orphan}")

    if not any(c["kind"] == "full_page" for c in manifest):
        problems.append("no whole-page capture in the package")

    for finding in report["findings"]:
        if finding["status"] == "found":
            if not finding["evidence_files"]:
                problems.append(f"'found' without evidence: {finding['item_id']}")
            for ef in finding["evidence_files"]:
                if ef not in manifest_files:
                    problems.append(f"{finding['item_id']} cites unmanifested {ef}")
                elif not (pkg / ef).exists():
                    problems.append(f"{finding['item_id']} cites missing file {ef}")

    rate = report["rate_comparison"]
    if rate["verdict"] in ("matches application exactly", "differs from application"):
        if not rate["evidence_files"]:
            problems.append(f"rate verdict '{rate['verdict']}' without evidence")
    return problems


def integrity_report(pkg: Path) -> dict:
    """Per-capture hash re-computation plus the full audit, for the UI.

    `captures[].actual` is recomputed from the file on disk right now — that is
    the whole point of the check, so it is never read from the manifest.
    """
    checked_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    manifest_path = pkg / "manifest.json"
    if not manifest_path.exists():
        return {
            "ok": False,
            "checked_at": checked_at,
            "problems": ["no manifest.json in this package"],
            "captures": [],
        }

    captures = []
    for c in json.loads(manifest_path.read_text(encoding="utf-8"))["captures"]:
        path = pkg / c["file"]
        actual = sha256_of(path) if path.exists() else None
        captures.append(
            {
                "file": c["file"],
                "expected": c["sha256"],
                "actual": actual,
                "ok": actual == c["sha256"],
            }
        )

    problems = audit_package(pkg)
    return {
        "ok": not problems,
        "checked_at": checked_at,
        "problems": problems,
        "captures": captures,
    }


def packages(output_dir: Optional[Path] = None) -> list[Path]:
    """Every report package directly under `output_dir` (default: output/)."""
    root = output_dir or (REPO_ROOT / "output")
    if not root.exists():
        return []
    return sorted(p for p in root.iterdir() if (p / "report.json").exists())
