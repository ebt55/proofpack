"""Audit every report package in output/ for evidence integrity.

Run directly:  python tests/audit_packages.py
Also collected by pytest (skips when output/ has no packages).

Invariants checked — the "no hallucinated findings" guarantees:
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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from preapproval.evidence import sha256_of  # noqa: E402

OUTPUT = Path(__file__).resolve().parent.parent / "output"


def audit_package(pkg: Path) -> list[str]:
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


def packages() -> list[Path]:
    if not OUTPUT.exists():
        return []
    return sorted(p for p in OUTPUT.iterdir() if (p / "report.json").exists())


def test_all_packages_pass_audit():
    import pytest

    pkgs = packages()
    if not pkgs:
        pytest.skip("no report packages in output/ yet")
    failures = {p.name: audit_package(p) for p in pkgs}
    failures = {k: v for k, v in failures.items() if v}
    assert not failures, f"integrity problems: {failures}"


if __name__ == "__main__":
    pkgs = packages()
    if not pkgs:
        print("No report packages in output/.")
        raise SystemExit(1)
    bad = 0
    for pkg in pkgs:
        problems = audit_package(pkg)
        report = json.loads((pkg / "report.json").read_text(encoding="utf-8"))
        counts: dict[str, int] = {}
        for f in report["findings"]:
            counts[f["status"]] = counts.get(f["status"], 0) + 1
        print(f"\n{pkg.name}")
        print(f"  statuses: {counts}")
        print(f"  rate: {report['rate_comparison']['verdict']}")
        if problems:
            bad += 1
            for p in problems:
                print(f"  PROBLEM: {p}")
        else:
            print("  integrity: OK")
    print(f"\n{len(pkgs)} package(s) audited, {bad} with problems.")
    raise SystemExit(1 if bad else 0)
