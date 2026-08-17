"""Plain-language chat mode: let the reviewer adjust a finished report.

Supports the reviewer interactions: "Change this item to Needs Review",
"Add a note to the report", "Regenerate the report", "Re-run the check", and
questions about what was found. Edits are marked as reviewer edits and the
HTML is re-rendered after every change.
"""

from __future__ import annotations

from pathlib import Path

from google import genai

from .config import Checklist, ToolConfig
from .llm import new_chat, run_tool_loop
from .models import ReviewReport, Status
from .report import render_report

REVIEWER_STATUSES = [s.value for s in Status]


class ChatSession:
    def __init__(
        self,
        package_dir: Path,
        client: genai.Client,
        cfg: ToolConfig,
        checklists: dict[str, Checklist],
    ):
        self.package_dir = package_dir
        self.client = client
        self.cfg = cfg
        self.model = cfg.model
        self.checklists = checklists
        report_path = package_dir / "report.json"
        if not report_path.exists():
            raise FileNotFoundError(f"No report.json in {package_dir} — run a review first.")
        self.report = ReviewReport.model_validate_json(report_path.read_text(encoding="utf-8"))
        self.dirty = False

    # -- persistence ---------------------------------------------------------

    def save(self) -> None:
        (self.package_dir / "report.json").write_text(
            self.report.model_dump_json(indent=2), encoding="utf-8"
        )
        render_report(self.report, self.package_dir)
        self.dirty = False

    # -- tools ----------------------------------------------------------------

    def build_tools(self) -> list:
        session = self

        def list_findings() -> str:
            """List every finding in the report with its current status."""
            lines = []
            for f in session.report.findings:
                lines.append(f"- {f.item_id}: {f.status.value} — {f.form_question}")
            lines.append(f"- rate comparison: {session.report.rate_comparison.verdict}")
            return "\n".join(lines)

        def set_finding_status(item_id: str, status: str, note: str) -> str:
            """Change the status of a finding (reviewer override).

            Args:
                item_id: The finding's item id (see list_findings).
                status: New status: found, not_found, needs_review, internal,
                    needs_document, pass, or flag.
                note: Why the reviewer changed it (appended to the finding's note).
            """
            if status not in REVIEWER_STATUSES:
                return f"REJECTED: status must be one of {REVIEWER_STATUSES}"
            for f in session.report.findings:
                if f.item_id == item_id:
                    old = f.status.value
                    f.status = Status(status)
                    f.note = (f.note + f" [Reviewer override: {note}]").strip()
                    f.source = "reviewer"
                    session.dirty = True
                    return f"{item_id}: {old} -> {status}. Report will be regenerated."
            return f"REJECTED: no finding with item_id {item_id!r}. Use list_findings."

        def add_reviewer_note(text: str) -> str:
            """Add a free-text reviewer note to the report.

            Args:
                text: The note to add.
            """
            session.report.reviewer_notes.append(text)
            session.dirty = True
            return "Note added."

        def set_rate_verdict(verdict: str, detail: str) -> str:
            """Override the rate-comparison verdict (reviewer decision).

            Args:
                verdict: One of: "matches application exactly", "differs from application",
                    "not published", "could not verify".
                detail: Why.
            """
            valid = [
                "matches application exactly",
                "differs from application",
                "not published",
                "could not verify",
            ]
            if verdict not in valid:
                return f"REJECTED: verdict must be one of {valid}"
            session.report.rate_comparison.verdict = verdict  # type: ignore[assignment]
            session.report.rate_comparison.detail += f" [Reviewer override: {detail}]"
            session.dirty = True
            return "Rate verdict updated."

        def regenerate_report() -> str:
            """Re-render report.html and save report.json with the current state."""
            session.save()
            return f"Regenerated {session.package_dir / 'report.html'}"

        def rerun_website_verification() -> str:
            """Visit the provider's website again and redo the whole website check.

            Use when the reviewer asks to re-run / re-check / look again — for example
            after the provider updated their site, or a page was temporarily blocked.
            Takes a minute or two. Reviewer notes are kept; website findings, the rate
            comparison and the evidence captures are all replaced with the fresh review,
            so the package always reflects one coherent, current check.
            """
            from .pipeline import rerun_review

            if session.dirty:
                session.save()
            print("\n[re-running the website verification — this takes a minute]\n")
            session.report = rerun_review(
                session.package_dir, session.cfg, session.checklists, session.client
            )
            session.dirty = False
            statuses: dict[str, int] = {}
            for f in session.report.findings:
                if f.source == "agent":
                    statuses[f.status.value] = statuses.get(f.status.value, 0) + 1
            summary = ", ".join(f"{v} {k}" for k, v in sorted(statuses.items()))
            return (
                f"Re-ran the verification (review #{session.report.review_count}). "
                f"Website findings now: {summary}. "
                f"Rate verdict: {session.report.rate_comparison.verdict}. "
                "report.html and the evidence have been refreshed."
            )

        return [
            list_findings,
            set_finding_status,
            add_reviewer_note,
            set_rate_verdict,
            regenerate_report,
            rerun_website_verification,
        ]

    def system_prompt(self) -> str:
        r = self.report
        return (
            "You help a Pre-Approvals reviewer adjust a completed website-verification "
            "report in plain language. Use the tools to make the changes they ask for "
            "(change a finding's status, add notes, adjust the rate verdict, regenerate "
            "the report, or re-run the website check) and answer questions about what "
            "the report contains. Keep replies to a couple of sentences. Never invent "
            "new evidence — reviewer changes are recorded as overrides, and only "
            "rerun_website_verification may produce new findings. Re-running takes a "
            "minute or two and replaces the website findings and evidence, so confirm "
            "with the reviewer before doing it unless they clearly asked.\n\n"
            f"Report: {r.category_display} — {r.application.requested_item} "
            f"({r.application.provider_name}); participant {r.application.participant_name}; "
            f"reviewed {r.reviewed_at}.\n"
            f"Agent summary: {r.agent_summary}"
        )

    # -- loop ------------------------------------------------------------------

    def run(self) -> None:
        tools = self.build_tools()
        # One chat for the whole session: it keeps the conversation history.
        chat = new_chat(self.client, self.model, self.system_prompt(), tools, max_output_tokens=4096)
        print(
            f"\nChat mode for {self.package_dir.name} — e.g. \"change published_fees to "
            'needs review", "add a note ...", "re-run the website check", "regenerate '
            'the report". Type "quit" to exit.\n'
        )
        while True:
            try:
                user = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not user:
                continue
            if user.lower() in {"quit", "exit", "q"}:
                break

            result = run_tool_loop(chat, tools, user, max_iterations=12, log=lambda m: None)
            print(f"tool> {result.text}\n")
            if self.dirty:
                self.save()
                print("tool> (report.json and report.html updated)\n")
        print("Chat ended.")
