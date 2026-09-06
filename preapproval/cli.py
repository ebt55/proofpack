"""Command-line interface.

  python verify.py serve                                  # reviewer workbench (localhost)
  python verify.py review samples/Sample-01---....pdf     # review one application
  python verify.py review-all                             # review every sample
  python verify.py chat output/sample-01---...            # adjust a finished report
  python verify.py render --all                           # re-render report.html
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from .config import REPO_ROOT, load_checklists, load_config

MISSING_KEY_MESSAGE = """\
No Gemini API key found.

This tool calls the Gemini API to read the form and verify the website, so it needs a key:

  1. Get one at https://aistudio.google.com/apikey
  2. Copy .env.example to .env
  3. Paste the key into .env as:  GEMINI_API_KEY=...

(You can also export GEMINI_API_KEY in your shell instead of using .env.)"""


def _client():
    from .llm import make_client

    load_dotenv(REPO_ROOT / ".env")
    if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
        print(MISSING_KEY_MESSAGE, file=sys.stderr)
        raise SystemExit(2)
    return make_client()


def _ask_for(field: str, question: str, options: list[str]) -> str | None:
    """Prompt the reviewer for a clarification. None means 'skip this application'."""
    print(f"\nCLARIFICATION NEEDED: {question}")
    if options:
        print("  Options: " + ", ".join(options))
    if not sys.stdin.isatty():
        print("(non-interactive run — skipping this application)")
        return None
    prompt = {
        "url": "URL",
        "category": "Category",
        "requested_item": "Requested item",
    }.get(field, field)
    answer = input(f"{prompt} (or press Enter to skip): ").strip()
    return answer or None


def cmd_review(args: argparse.Namespace) -> int:
    from .pipeline import NeedsClarification, ReviewOverrides, run_review

    cfg = load_config()
    if args.model:
        cfg.model = args.model
    if args.headed:
        cfg.headless = False
    if args.out:
        cfg.output_dir = Path(args.out)
    checklists = load_checklists(cfg.checklist_dir)
    client = _client()

    pdfs = [Path(p) for p in args.pdf]
    failures = 0
    for pdf in pdfs:
        if not pdf.exists():
            print(f"ERROR: {pdf} does not exist", file=sys.stderr)
            failures += 1
            continue
        print(f"\n=== {pdf.name} ===")
        overrides = ReviewOverrides(url=args.url)
        while True:
            try:
                run_review(pdf, cfg, checklists, client, overrides=overrides)
                break
            except NeedsClarification as e:
                answer = _ask_for(e.field, e.question, e.options)
                if answer is None:
                    failures += 1
                    break
                overrides.set(e.field, answer)
            except Exception as e:  # noqa: BLE001 — keep batch runs going
                print(f"ERROR reviewing {pdf.name}: {type(e).__name__}: {e}", file=sys.stderr)
                failures += 1
                break
    return 1 if failures else 0


def cmd_review_all(args: argparse.Namespace) -> int:
    samples = sorted((REPO_ROOT / "samples").glob("*.pdf"))
    if not samples:
        print("No PDFs found in samples/", file=sys.stderr)
        return 1
    args.pdf = [str(p) for p in samples]
    args.url = None
    return cmd_review(args)


def cmd_chat(args: argparse.Namespace) -> int:
    from .chat import ChatSession

    cfg = load_config()
    if args.model:
        cfg.model = args.model
    package_dir = Path(args.package)
    if not package_dir.exists():
        print(f"ERROR: {package_dir} does not exist", file=sys.stderr)
        return 1
    checklists = load_checklists(cfg.checklist_dir)
    ChatSession(package_dir, _client(), cfg, checklists).run()
    return 0


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"}


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .config import REPO_ROOT as _root
    from .server import create_app

    cfg = load_config()
    if args.model:
        cfg.model = args.model
    if args.headed:
        cfg.headless = False
    if args.out:
        cfg.output_dir = Path(args.out)

    checklists = load_checklists(cfg.checklist_dir)
    load_dotenv(_root / ".env")
    client_factory = None
    if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
        from .llm import make_client

        client_factory = make_client

    app = create_app(
        cfg,
        checklists,
        client_factory,
        samples_dir=_root / "samples",
        uploads_dir=_root / "uploads",
    )

    url = f"http://{args.host}:{args.port}/"
    print(f"\nProofPack reviewer workbench: {url}")
    print(f"  model:   {cfg.model}")
    print(f"  output:  {cfg.output_dir}")
    if client_factory is None:
        print("  API key: NOT FOUND — reviews and chat are disabled until you set "
              "GEMINI_API_KEY in .env")
    else:
        print("  API key: found")
    if os.getenv("PROOFPACK_FAKE_RUN", "").strip() not in ("", "0", "false", "no"):
        print("  PROOFPACK_FAKE_RUN=1 — reviews are FAKE: no website, no model, no cost.")
    if args.host not in LOOPBACK_HOSTS:
        print(
            "\n  *** WARNING: binding to a non-loopback address. The workbench has NO "
            f"\n  *** authentication and serves report packages containing participant "
            f"\n  *** names. Anyone who can reach {args.host}:{args.port} can read them."
        )
    print("\nPress Ctrl+C to stop.\n")

    if args.open:
        import threading
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    try:
        from .report import render_package
    except ImportError:
        print(
            "ERROR: preapproval.report.render_package is not available in this build.\n"
            "It is provided by the report/design chunk; update preapproval/report.py.",
            file=sys.stderr,
        )
        return 2

    cfg = load_config()
    if args.out:
        cfg.output_dir = Path(args.out)
    if args.all:
        dirs = [p for p in sorted(cfg.output_dir.glob("*")) if (p / "report.json").exists()]
        if not dirs:
            print(f"No report packages in {cfg.output_dir}", file=sys.stderr)
            return 1
    elif args.package:
        dirs = [Path(p) for p in args.package]
    else:
        print("Give one or more package directories, or --all.", file=sys.stderr)
        return 2

    failures = 0
    for package_dir in dirs:
        try:
            print(render_package(package_dir))
        except Exception as e:  # noqa: BLE001 — keep going through the rest
            print(f"ERROR rendering {package_dir}: {type(e).__name__}: {e}", file=sys.stderr)
            failures += 1
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to cp1252; page text and reports are UTF-8.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        prog="verify.py",
        description="Pre-Approval Website-Verification Tool — reads a completed "
        "application form (PDF), verifies the website-checkable items on the provider's "
        "public site, captures date-stamped evidence, and produces a review-ready report.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_review = sub.add_parser("review", help="review one or more application PDFs")
    p_review.add_argument("pdf", nargs="+", help="path(s) to completed application PDF(s)")
    p_review.add_argument("--headed", action="store_true", help="show the browser window")
    p_review.add_argument("--model", help="override the Gemini model id")
    p_review.add_argument("--out", help="override the output directory")
    p_review.add_argument("--url", help="override/supply the provider URL")
    p_review.set_defaults(func=cmd_review)

    p_all = sub.add_parser("review-all", help="review every PDF in samples/")
    p_all.add_argument("--headed", action="store_true", help="show the browser window")
    p_all.add_argument("--model", help="override the Gemini model id")
    p_all.add_argument("--out", help="override the output directory")
    p_all.set_defaults(func=cmd_review_all)

    p_chat = sub.add_parser("chat", help="adjust a finished report in plain language")
    p_chat.add_argument("package", help="path to a report package (e.g. output/sample-01---...)")
    p_chat.add_argument("--model", help="override the Gemini model id")
    p_chat.set_defaults(func=cmd_chat)

    p_serve = sub.add_parser(
        "serve", help="run the reviewer workbench in a browser (localhost only)"
    )
    p_serve.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    p_serve.add_argument("--port", type=int, default=8765, help="port (default 8765)")
    p_serve.add_argument("--out", help="override the output directory")
    p_serve.add_argument("--model", help="override the Gemini model id")
    p_serve.add_argument("--headed", action="store_true", help="show the browser window")
    p_serve.add_argument("--open", action="store_true", help="open the workbench in a browser")
    p_serve.set_defaults(func=cmd_serve)

    p_render = sub.add_parser("render", help="re-render report.html for report packages")
    p_render.add_argument("package", nargs="*", help="package director(ies) to re-render")
    p_render.add_argument("--all", action="store_true", help="every package in the output dir")
    p_render.add_argument("--out", help="override the output directory (with --all)")
    p_render.set_defaults(func=cmd_render)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
