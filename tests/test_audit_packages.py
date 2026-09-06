"""Audit every report package in output/ for evidence integrity.

Run directly:  python tests/test_audit_packages.py
Also collected by pytest (skips when output/ has no packages).

The checks themselves live in `preapproval.audit` so the workbench's "Verify
integrity" button and this test cannot drift apart — see that module for the
list of invariants.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from preapproval.audit import audit_package, packages  # noqa: E402


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
