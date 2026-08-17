"""Data models for the pre-approval verification pipeline."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Extraction — what the tool reads off a completed application form
# ---------------------------------------------------------------------------

CategoryName = Literal[
    "community_class",
    "coaching",
    "membership",
    "hri",
    "otps",
    "transition_program",
    "appeal",
]


class FeeAmount(BaseModel):
    """A single parsed fee from the form, e.g. $80 'per session'."""

    amount: float = Field(description="Numeric dollar amount, e.g. 80.0")
    unit: str = Field(
        description=(
            "What the amount is per, lowercase, e.g. 'per session', 'per month', "
            "'per class', 'per course', 'one-time item price'"
        )
    )


class FormAnswer(BaseModel):
    """One YES/NO checklist row as filled in on the form."""

    question: str = Field(description="The question text exactly as printed on the form")
    answer: Literal["YES", "NO", "UNANSWERED"]


class ApplicationData(BaseModel):
    """Everything the tool extracts from a completed pre-approval form PDF."""

    category: CategoryName = Field(
        description=(
            "Form category, inferred from the form's title: community_class, coaching, "
            "membership, hri, otps, transition_program, or appeal"
        )
    )
    form_title: str = Field(description="The form's printed title")
    participant_name: str
    participant_age: Optional[int] = None
    fi_coordinator: Optional[str] = None
    broker: Optional[str] = None
    requested_item: str = Field(
        description="The specific class / membership / item / program requested"
    )
    provider_name: Optional[str] = Field(
        default=None, description="Provider or vendor name as stated on the form"
    )
    url: Optional[str] = Field(
        default=None,
        description="The 'Link to Webpage / place of publication / item link' URL. None if absent.",
    )
    fee_text: str = Field(
        description="The fee exactly as written on the form, verbatim (all fee fields joined)"
    )
    fees: list[FeeAmount] = Field(
        default_factory=list, description="Each dollar amount on the form, parsed"
    )
    duration_text: Optional[str] = Field(
        default=None, description="Duration per session/billing period if stated"
    )
    subject_area: Optional[str] = None
    safety_features: Optional[str] = Field(
        default=None, description="'Safety features for the item' text (HRI/OTPS forms)"
    )
    valued_outcome: Optional[str] = None
    justification: Optional[str] = Field(
        default=None, description="'Justification for why the requested Item/Service is needed'"
    )
    # Appeal-only fields
    denial_date: Optional[str] = None
    denial_reason: Optional[str] = Field(
        default=None, description="'Reason for the denial' on an appeal form"
    )
    appeal_justification: Optional[str] = Field(
        default=None, description="'Justification for Appeal' on an appeal form"
    )
    form_answers: list[FormAnswer] = Field(
        default_factory=list,
        description="Every YES/NO checklist row on the form with the answer as filled in",
    )


# ---------------------------------------------------------------------------
# Verification — what the tool concludes, per checklist item
# ---------------------------------------------------------------------------


class Status(str, Enum):
    FOUND = "found"                    # requirement verified on the website, evidence captured
    NOT_FOUND = "not_found"            # checked; evidence absent or contradicts the requirement
    NEEDS_REVIEW = "needs_review"      # could not be conclusively verified — human should look
    INTERNAL = "internal"              # not website-verifiable; left for the reviewer
    NEEDS_DOCUMENT = "needs_document"  # proven by a document (e.g. screening letter), not the web
    PASS = "pass"                      # deterministic form-level check passed (caps, age)
    FLAG = "flag"                      # deterministic form-level check failed / needs attention


class Finding(BaseModel):
    """The tool's conclusion for one checklist item."""

    item_id: str
    form_question: str
    requirement: Optional[str] = None
    status: Status
    note: str = ""
    quote: Optional[str] = None            # verbatim text from the page supporting the finding
    evidence_url: Optional[str] = None     # page where the evidence was seen
    evidence_files: list[str] = Field(default_factory=list)  # relative capture filenames
    source: Literal["agent", "deterministic", "reviewer"] = "agent"


class RateComparison(BaseModel):
    form_fee: str
    published_fee: Optional[str] = None
    verdict: Literal[
        "matches application exactly",
        "differs from application",
        "not published",
        "could not verify",
    ] = "could not verify"
    detail: str = ""
    evidence_url: Optional[str] = None
    evidence_files: list[str] = Field(default_factory=list)


class EvidenceRecord(BaseModel):
    """One stamped capture in the evidence package."""

    file: str                      # relative path within the output package
    kind: Literal["full_page", "targeted"]
    label: str                     # e.g. "Evidence: published fees"
    url: str
    captured_at: str               # ISO timestamp burned into the image
    sha256: str


class TokenUsage(BaseModel):
    """Token spend for one review, accumulated across every API call."""

    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def add(self, usage: object) -> None:
        """Accumulate an SDK usage object (fields absent on some responses)."""
        if usage is None:
            return
        self.requests += 1
        for field in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        ):
            value = getattr(usage, field, None)
            if isinstance(value, int):
                setattr(self, field, getattr(self, field) + value)

    def estimated_cost_usd(self, input_per_mtok: float, output_per_mtok: float) -> float:
        """Rough cost estimate. Cached reads are billed at ~10% of input price."""
        billed_input = self.input_tokens + self.cache_creation_input_tokens
        cost = (billed_input / 1_000_000) * input_per_mtok
        cost += (self.cache_read_input_tokens / 1_000_000) * input_per_mtok * 0.1
        cost += (self.output_tokens / 1_000_000) * output_per_mtok
        return round(cost, 4)


class ReviewReport(BaseModel):
    """The full review-ready report for one application."""

    tool_version: str
    model: str
    source_pdf: str
    reviewed_at: str
    category: CategoryName
    category_display: str
    application: ApplicationData
    rate_comparison: RateComparison
    findings: list[Finding]
    evidence: list[EvidenceRecord]
    agent_summary: str = ""
    checklist_notes: list[str] = Field(default_factory=list)
    reviewer_notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    estimated_cost_usd: Optional[float] = None
    review_count: int = Field(
        default=1, description="How many times this application has been reviewed/re-run"
    )

    @property
    def reviewed_at_dt(self) -> datetime:
        return datetime.fromisoformat(self.reviewed_at)
