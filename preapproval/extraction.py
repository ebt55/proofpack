"""Read a completed application form (PDF) into structured data.

Uses Gemini's native PDF understanding (handles both digital and scanned forms)
with a JSON response schema. The wire schema (`ExtractedForm`) is deliberately
flat — all strings, no optionals — so any model fills every field explicitly;
it is converted into the richer ApplicationData model here.
"""

from __future__ import annotations

from pathlib import Path

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from .models import ApplicationData, FeeAmount, FormAnswer, TokenUsage


class UnknownFormCategory(Exception):
    """The form's category isn't one this tool has a checklist for."""

    def __init__(self, category: str, form_title: str):
        self.category = category
        self.form_title = form_title
        super().__init__(
            f"Extracted category {category!r} (from form title {form_title!r}) has no checklist."
        )


KNOWN_CATEGORIES = {
    "community_class",
    "coaching",
    "membership",
    "hri",
    "otps",
    "transition_program",
    "appeal",
}


class XFee(BaseModel):
    amount: float = Field(description="Numeric dollar amount, e.g. 80.0")
    unit: str = Field(
        description="What the amount is per, lowercase: 'per session', 'per month', "
        "'per class', 'per course', 'one-time item price', ..."
    )


class XAnswer(BaseModel):
    question: str = Field(description="Question text exactly as printed on the form")
    answer: str = Field(description="YES, NO, or UNANSWERED (if neither box is checked)")


class ExtractedForm(BaseModel):
    """Flat wire schema. Use an empty string for any field the form does not have."""

    category: str = Field(
        description="One of: community_class, coaching, membership, hri, otps, "
        "transition_program, appeal — inferred from the form's printed title"
    )
    form_title: str = Field(description="The form's printed title")
    participant_name: str
    participant_age: str = Field(description="Age as printed, e.g. '26'. Empty if absent.")
    fi_coordinator: str
    broker: str
    requested_item: str = Field(
        description="The specific class / membership / item / program requested. If the form "
        "has no explicit name field for it (e.g. the coaching form), describe it briefly from "
        "the provider and category, e.g. 'Parenting & Family coaching classes'."
    )
    provider_name: str = Field(description="Provider or vendor name. Empty if absent.")
    url: str = Field(
        description="The 'Link to Webpage / Place of Publication / Link to the Item' URL "
        "exactly as printed. Empty if the form genuinely has none — never invent one."
    )
    fee_text: str = Field(
        description="Every fee field verbatim, joined with ' | ', e.g. "
        "'Fee per Class: $50 | Fee per Course: $400'"
    )
    fees: list[XFee] = Field(description="Each dollar amount on the form, parsed")
    duration_text: str = Field(description="Duration per session / billing period. Empty if absent.")
    subject_area: str
    safety_features: str = Field(description="'Safety features for the item' text (HRI/OTPS forms)")
    valued_outcome: str
    justification: str = Field(
        description="'Justification for why the requested Item/Service is needed'. Empty if absent."
    )
    denial_date: str = Field(description="Appeal forms only: 'Date of Denial'. Empty otherwise.")
    denial_reason: str = Field(
        description="Appeal forms only: 'Reason for the denial' verbatim. Empty otherwise."
    )
    appeal_justification: str = Field(
        description="Appeal forms only: 'Justification for Appeal' verbatim. Empty otherwise."
    )
    form_answers: list[XAnswer] = Field(
        description="EVERY row of the YES/NO checklist with the box that is checked"
    )


EXTRACTION_PROMPT = """\
You are reading a completed OPWDD Self-Direction pre-approval application form.

Extract the request exactly as written on the form. Rules:
- Copy values verbatim; do not correct, embellish, or infer missing values.
- Use an empty string for any field this form does not have.
- If the form shows a fee amount plus a separate billing period field (e.g. $15.00 +
  Monthly), combine them into one parsed fee ("per month").
- form_answers must include EVERY YES/NO checklist row on the form.
"""


def _none_if_empty(s: str) -> str | None:
    s = (s or "").strip()
    return s or None


def _to_application(x: ExtractedForm) -> ApplicationData:
    category = x.category.strip().lower().replace(" ", "_").replace("-", "_")
    if category not in KNOWN_CATEGORIES:
        raise UnknownFormCategory(category, x.form_title)
    age: int | None = None
    digits = "".join(ch for ch in x.participant_age if ch.isdigit())
    if digits:
        age = int(digits)
    answers = []
    for a in x.form_answers:
        norm = a.answer.strip().upper()
        if norm not in {"YES", "NO", "UNANSWERED"}:
            norm = "UNANSWERED"
        answers.append(FormAnswer(question=a.question, answer=norm))  # type: ignore[arg-type]
    return ApplicationData(
        category=category,  # type: ignore[arg-type]
        form_title=x.form_title,
        participant_name=x.participant_name,
        participant_age=age,
        fi_coordinator=_none_if_empty(x.fi_coordinator),
        broker=_none_if_empty(x.broker),
        requested_item=x.requested_item,
        provider_name=_none_if_empty(x.provider_name),
        url=_none_if_empty(x.url),
        fee_text=x.fee_text,
        fees=[FeeAmount(amount=f.amount, unit=f.unit) for f in x.fees],
        duration_text=_none_if_empty(x.duration_text),
        subject_area=_none_if_empty(x.subject_area),
        safety_features=_none_if_empty(x.safety_features),
        valued_outcome=_none_if_empty(x.valued_outcome),
        justification=_none_if_empty(x.justification),
        denial_date=_none_if_empty(x.denial_date),
        denial_reason=_none_if_empty(x.denial_reason),
        appeal_justification=_none_if_empty(x.appeal_justification),
        form_answers=answers,
    )


def extract_application(
    pdf_path: Path, client: genai.Client, model: str
) -> tuple[ApplicationData, TokenUsage]:
    """Read the form. Returns the application plus the tokens the call consumed."""
    response = client.models.generate_content(
        model=model,
        contents=[
            types.Part.from_bytes(data=pdf_path.read_bytes(), mime_type="application/pdf"),
            EXTRACTION_PROMPT,
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ExtractedForm,
        ),
    )
    parsed = response.parsed
    if not isinstance(parsed, ExtractedForm):
        # The SDK returns None if its own validation failed; validate the raw JSON ourselves
        # so the real pydantic error surfaces instead of a silent None.
        raw = response.text or ""
        if not raw.strip():
            raise RuntimeError(f"Extraction returned no structured output for {pdf_path.name}")
        parsed = ExtractedForm.model_validate_json(raw)
    usage = TokenUsage()
    usage.add(response.usage_metadata)
    return _to_application(parsed), usage
