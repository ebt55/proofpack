"""Generate synthetic pre-approval application forms (PDF) for testing.

Every participant, coordinator and broker below is fictional. The provider websites
are real public sites so the verification agent has genuine evidence to find.

    python tools/make_sample_forms.py            # writes samples/*.pdf

Layouts deliberately mimic the paper forms used in NY Self-Direction programs
(title, request fields, a YES/NO checklist, signatures) so the extraction step is
tested on realistic input, but the wording is our own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "samples"

BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=9.5, leading=12)
SMALL = ParagraphStyle("small", fontName="Helvetica", fontSize=8, leading=10, textColor=colors.grey)
H1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=15, leading=18, spaceAfter=2)
H2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=10.5, leading=13, spaceBefore=8, spaceAfter=3)
CELL_LABEL = ParagraphStyle("lbl", fontName="Helvetica-Bold", fontSize=9, leading=11)
CELL_VALUE = ParagraphStyle("val", fontName="Helvetica", fontSize=9.5, leading=12)

CHECKED, UNCHECKED = "[X]", "[  ]"


@dataclass
class Form:
    filename: str
    title: str
    subtitle: str
    fields: list[tuple[str, str]]
    checklist_heading: str
    checklist: list[tuple[str, str]]          # (question, "YES" | "NO" | "")
    notes: list[str] = field(default_factory=list)


def _yes_no(answer: str) -> str:
    y = CHECKED if answer == "YES" else UNCHECKED
    n = CHECKED if answer == "NO" else UNCHECKED
    return f"YES {y}    NO {n}"


def render(form: Form) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / form.filename
    doc = SimpleDocTemplate(
        str(path), pagesize=letter, leftMargin=0.8 * inch, rightMargin=0.8 * inch,
        topMargin=0.7 * inch, bottomMargin=0.7 * inch, title=form.title,
    )
    story = [
        Paragraph("Self-Direction Program — Fiscal Intermediary Pre-Approval Request", SMALL),
        Paragraph(form.title, H1),
        Paragraph(form.subtitle, BODY),
        Spacer(1, 8),
        Paragraph("Section A — Request details", H2),
    ]
    rows = [[Paragraph(k, CELL_LABEL), Paragraph(v, CELL_VALUE)] for k, v in form.fields]
    t = Table(rows, colWidths=[2.3 * inch, 4.5 * inch])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#9ca3af")),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f3f4f6")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story += [t, Paragraph(f"Section B — {form.checklist_heading}", H2)]
    story.append(Paragraph(
        "Reviewer to confirm each item. Mark YES or NO. Attach links, flyers or letters where indicated.",
        SMALL,
    ))
    story.append(Spacer(1, 4))
    crow = [[Paragraph(f"{i}. {q}", CELL_VALUE), Paragraph(_yes_no(a), CELL_VALUE)]
            for i, (q, a) in enumerate(form.checklist, 1)]
    ct = Table(crow, colWidths=[5.2 * inch, 1.6 * inch])
    ct.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#9ca3af")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(ct)
    if form.notes:
        story.append(Paragraph("Section C — Notes", H2))
        for n in form.notes:
            story.append(Paragraph(n, BODY))
    story += [
        Spacer(1, 14),
        Paragraph("Broker signature: ______________________     Date: ___________          "
                  "FI Coordinator signature: ______________________     Date: ___________", BODY),
        Spacer(1, 10),
        Paragraph("Synthetic sample for software testing — the participant, coordinator and broker are "
                  "fictional; the provider website is real.", SMALL),
    ]
    doc.build(story)
    return path


FORMS: list[Form] = [
    Form(
        filename="01-community-class-gallopnyc.pdf",
        title="Community Class Pre-approval Checklist",
        subtitle="Use this form to request pre-approval for a community class funded from the "
                 "participant's Self-Direction budget.",
        fields=[
            ("Participant Name", "Jordan Ellis"),
            ("Participant Age", "24"),
            ("FI Coordinator", "R. Patel"),
            ("Broker", "M. Okafor"),
            ("Provider / Organization", "GallopNYC (Sunrise Stables)"),
            ("Class Requested", "Recreational Riding — 30-Minute Group Lesson"),
            ("Subject Area", "Horseback riding (recreational, group instruction)"),
            ("Link to Webpage / Place of Publication", "https://www.gallopnyc.org/recreational-riding"),
            ("Fee per Class / Session", "$80 per session"),
            ("Duration per Session", "30 minutes"),
            ("Frequency", "1 session per week"),
            ("Valued Outcome", "Jordan wants to build core strength and balance and take part in a "
                               "community activity with peers outside the day program."),
            ("Justification for why the requested Item/Service is needed",
             "Riding supports Jordan's Life Plan goal of increasing physical stamina and community "
             "participation; the lessons are open group classes at a public stable."),
        ],
        checklist_heading="Community Class checklist",
        checklist=[
            ("Are community classes currently approved in the budget?", "YES"),
            ("Is the class open to and attended by the broader public?", "YES"),
            ("Does the class have published fees? (attach link / advertised flyer)", "YES"),
            ("Are the fees identical for both OPWDD and non-OPWDD individuals?", "YES"),
            ("Is the class subject based? (Art, Dance, Martial Arts, Cooking)", "YES"),
            ("Does the class provide college credits?", "NO"),
            ("Is the class clinical in nature? (therapy)", "NO"),
            ("Is there a published schedule of the class? (please attach)", "NO"),
            ("Does the class provide opportunity for community inclusion?", "YES"),
            ("Does the class accommodate the individual's health and/or safety needs?", "YES"),
            ("Does the class increase independence or substitute for human assistance?", "YES"),
            ("Is the class provided exclusively for the benefit of the participant?", "YES"),
            ("Is the class given in a setting accessed only by people with Developmental Disabilities?", "NO"),
            ("Is the class run by OPWDD or a provider-agency staff acting in their official capacities?", "NO"),
            ("Is the class located on grounds where OPWDD services are normally provided?", "NO"),
            ("Does the class duplicate any Medicaid state plan or HCBS waiver services?", "NO"),
            ("Does the class duplicate services given through Board of Education for school-aged children?", "NO"),
            ("Is the class being reimbursed directly? (if yes, attach a W9)", "NO"),
        ],
    ),
    Form(
        filename="02-membership-brooklyn-museum.pdf",
        title="Health Club / Organizational Memberships Pre-approval Checklist",
        subtitle="Use this form to request pre-approval for a health-club or organizational membership "
                 "funded from the participant's Self-Direction budget.",
        fields=[
            ("Participant Name", "Samira Haddad"),
            ("Participant Age", "31"),
            ("FI Coordinator", "R. Patel"),
            ("Broker", "T. Nguyen"),
            ("Organization", "Brooklyn Museum"),
            ("Membership Requested", "Individual Membership (one adult)"),
            ("Link to Webpage / Place of Publication", "https://www.brooklynmuseum.org/support/membership"),
            ("Membership Fee", "$85"),
            ("Billing Period", "Annual (12 months)"),
            ("Valued Outcome", "Samira wants to visit exhibitions regularly and attend member programs "
                               "to expand her interests in art and meet people in her borough."),
            ("Justification for why the requested Item/Service is needed",
             "An annual membership is cheaper than the per-visit tickets Samira currently pays for "
             "and supports her Life Plan goal of independent community outings."),
        ],
        checklist_heading="Membership checklist",
        checklist=[
            ("Is Health-club/Organizational Membership currently approved in the budget?", "YES"),
            ("Is the organization open to the public and not a private/invitation-only club?", "YES"),
            ("Is the membership fee published on the organization's website?", "YES"),
            ("Does the membership provide opportunity for community inclusion?", "YES"),
            ("Does the membership accommodate the individual's health and/or safety needs?", "YES"),
            ("Does the membership increase independence or substitute for human assistance?", "YES"),
            ("Is the membership provided exclusively toward the benefit of the participant? (not family)", "YES"),
        ],
    ),
    Form(
        filename="03-community-class-gracie-barra.pdf",
        title="Community Class Pre-approval Checklist",
        subtitle="Use this form to request pre-approval for a community class funded from the "
                 "participant's Self-Direction budget.",
        fields=[
            ("Participant Name", "Luis Ortega"),
            ("Participant Age", "19"),
            ("FI Coordinator", "A. Brennan"),
            ("Broker", "M. Okafor"),
            ("Provider / Organization", "Gracie Barra"),
            ("Class Requested", "GB1 Jiu-Jitsu Fundamentals (beginner adult class)"),
            ("Subject Area", "Martial arts — Brazilian Jiu-Jitsu"),
            ("Link to Webpage / Place of Publication", "https://graciebarra.com/classes/jiu-jitsu-fundamentals/"),
            ("Fee per Class / Session", "$189 per month (unlimited fundamentals classes)"),
            ("Duration per Session", "60 minutes"),
            ("Frequency", "2 sessions per week"),
            ("Valued Outcome", "Luis wants to improve fitness, self-confidence and self-defence skills "
                               "in a structured group setting."),
            ("Justification for why the requested Item/Service is needed",
             "Beginner-level group martial arts class open to the public; supports Luis's Life Plan "
             "goals for physical activity and social interaction with peers."),
        ],
        checklist_heading="Community Class checklist",
        checklist=[
            ("Are community classes currently approved in the budget?", "YES"),
            ("Is the class open to and attended by the broader public?", "YES"),
            ("Does the class have published fees? (attach link / advertised flyer)", "YES"),
            ("Are the fees identical for both OPWDD and non-OPWDD individuals?", "YES"),
            ("Is the class subject based? (Art, Dance, Martial Arts, Cooking)", "YES"),
            ("Does the class provide college credits?", "NO"),
            ("Is the class clinical in nature? (therapy)", "NO"),
            ("Is there a published schedule of the class? (please attach)", "YES"),
            ("Does the class provide opportunity for community inclusion?", "YES"),
            ("Does the class accommodate the individual's health and/or safety needs?", "YES"),
            ("Does the class increase independence or substitute for human assistance?", "YES"),
            ("Is the class provided exclusively for the benefit of the participant?", "YES"),
            ("Is the class given in a setting accessed only by people with Developmental Disabilities?", "NO"),
            ("Is the class run by OPWDD or a provider-agency staff acting in their official capacities?", "NO"),
            ("Is the class located on grounds where OPWDD services are normally provided?", "NO"),
            ("Does the class duplicate any Medicaid state plan or HCBS waiver services?", "NO"),
            ("Does the class duplicate services given through Board of Education for school-aged children?", "NO"),
            ("Is the class being reimbursed directly? (if yes, attach a W9)", "NO"),
        ],
    ),
    Form(
        filename="04-coaching-love-and-logic.pdf",
        title="Coaching for Parents/Spouse Pre-approval Form",
        subtitle="Use this form to request pre-approval for a parenting / spouse coaching course funded "
                 "from the participant's Self-Direction budget (adults 18+).",
        fields=[
            ("Participant Name", "Aisha Rahman"),
            ("Participant Age", "27"),
            ("FI Coordinator", "R. Patel"),
            ("Broker", "T. Nguyen"),
            ("Parent / Spouse attending", "Nadia Rahman (mother)"),
            ("Provider / Organization", "Love and Logic Institute"),
            ("Course / Class Requested", "Love and Logic Parenting Online (webinar course)"),
            ("Subject Area", "Parenting skills — communication, boundaries, de-escalation"),
            ("Link to Webpage / Place of Publication", "https://www.loveandlogic.com/products/love-and-logic-parenting-online"),
            ("Fee per Class", "n/a"),
            ("Fee per Private Class", "n/a"),
            ("Fee per Course", "$150"),
            ("Duration", "Self-paced online course, approx. 7 hours of video"),
            ("Valued Outcome", "Aisha's mother wants practical strategies to support Aisha's growing "
                               "independence at home without daily conflict."),
            ("Justification for why the requested Item/Service is needed",
             "The course teaches de-escalation and boundary-setting techniques that directly support "
             "Aisha's Life Plan goal of managing her own routine with less prompting."),
        ],
        checklist_heading="Coaching checklist",
        checklist=[
            ("Is coaching education currently approved in the budget?", "YES"),
            ("Is the individual over 18?", "YES"),
            ("Does the coaching address the individual's goals being worked on in the LP?", "YES"),
            ("Can the coaching program be funded by other public programs?", "NO"),
            ("Do published fees exist for the course/class?", "YES"),
            ("Is the content a genuine educational/coaching opportunity?", "YES"),
        ],
    ),
    Form(
        filename="05-hri-laptop-macbook-air.pdf",
        title="Household Related Items (HRI) Justification Form",
        subtitle="Use this form to request pre-approval for a household-related item funded from the "
                 "participant's Self-Direction budget (adults 18+; $1,500 per budget year).",
        fields=[
            ("Participant Name", "Marcus Bell"),
            ("Participant Age", "34"),
            ("FI Coordinator", "A. Brennan"),
            ("Broker", "M. Okafor"),
            ("Item Requested", "Apple MacBook Air 13-inch laptop"),
            ("Vendor", "Apple"),
            ("Link to the Item", "https://www.apple.com/shop/buy-mac/macbook-air"),
            ("Item Price", "$1,299 one-time"),
            ("Safety features for the item", "None — general-purpose laptop"),
            ("Valued Outcome", "Marcus wants to manage his own schedule, video-call family and apply "
                               "for jobs without staff help."),
            ("Justification for why the requested Item/Service is needed",
             "A laptop would let Marcus complete online job applications and telehealth check-ins "
             "independently from home."),
        ],
        checklist_heading="HRI checklist",
        checklist=[
            ("Is Household-Related Items approved in the budget?", "YES"),
            ("Is the individual over 18?", "YES"),
            ("Is the item related to a need or goal identified in the Life Plan?", "YES"),
            ("Will the item increase independence, or substitute for human assistance?", "YES"),
            ("Does the item increase safety of the participant in their home environment?", "NO"),
            ("Is the item exclusively needed for the participant?", "YES"),
            ("Does the item increase the participant's health and safety?", "NO"),
            ("Does the individual live in their own home? (for Household support only)", "YES"),
            ("Was the same item bought already in this budget year?", "NO"),
            ("Can the item be funded through other sources?", "NO"),
            ("Does the item exist at the linked page with a visible price?", "YES"),
            ("Is the item NOT on the IDGS/HRI exclusion list?", "YES"),
        ],
        notes=["Broker note: item requested to support employment goals; see attached Life Plan excerpt."],
    ),
    Form(
        filename="06-otps-weighted-blanket-gravity.pdf",
        title="Other Than Personal Services (OTPS) Pre-Approval Form",
        subtitle="Use this form to request pre-approval for goods or services that increase independence "
                 "or health and safety ($3,000 per budget year).",
        fields=[
            ("Participant Name", "Elena Vasquez"),
            ("Participant Age", "22"),
            ("FI Coordinator", "R. Patel"),
            ("Broker", "T. Nguyen"),
            ("Item Requested", "Gravity Weighted Blanket, 20 lb"),
            ("Vendor", "Gravity Blankets"),
            ("Link to the Item", "https://gravityblankets.com/products/gravity-blanket"),
            ("Item Price", "$199.00 one-time"),
            ("Safety features for the item", "Weight approx. 10% of body weight; breathable removable "
                                             "cover; evenly distributed glass beads"),
            ("Valued Outcome", "Elena wants to fall asleep without staff sitting with her and wake up "
                               "less anxious."),
            ("Justification for why the requested Item/Service is needed",
             "Sensory regulation and sleep are goals in Elena's Life Plan; a weighted blanket was "
             "recommended by her care manager as a non-clinical support."),
        ],
        checklist_heading="OTPS checklist",
        checklist=[
            ("Is 'Other goods & services that increase independence' approved in the budget?", "YES"),
            ("Is 'Other goods & services that increase health and safety' approved in the budget?", "YES"),
            ("Is the item related to a need or goal identified in the Life Plan?", "YES"),
            ("Will the item increase the independence and/or health and safety of the individual?", "YES"),
            ("Is the item exclusively needed for the participant?", "YES"),
            ("Can the item be funded through other sources?", "NO"),
            ("Was the same item bought already in this budget year?", "NO"),
            ("Does the item exist at the linked page with a visible price?", "YES"),
            ("Is the item on the OTPS exclusion list? (see guidelines)", "NO"),
            ("Are the safety features described on the form supported by the product page?", "YES"),
        ],
    ),
    Form(
        filename="07-appeal-gracie-barra.pdf",
        title="Pre-Approval Appeals Form — Community Class",
        subtitle="Use this form to appeal a denied pre-approval. Attach the original application and "
                 "any rate sheets or provider documents that support the appeal.",
        fields=[
            ("Participant Name", "Luis Ortega"),
            ("Participant Age", "19"),
            ("FI Coordinator", "A. Brennan"),
            ("Broker", "M. Okafor"),
            ("Provider / Organization", "Gracie Barra"),
            ("Class Requested", "GB1 Jiu-Jitsu Fundamentals (beginner adult class)"),
            ("Link to Webpage / Place of Publication", "https://graciebarra.com/classes/jiu-jitsu-fundamentals/"),
            ("Fee per Class / Session", "$189 per month (unlimited fundamentals classes)"),
            ("Date of Denial", "08-10-2026"),
            ("Reason for the denial", "Published fees could not be verified on the provider's public "
                                      "website; no rate sheet was attached to the application."),
            ("Justification for Appeal", "The school confirmed the $189/month fundamentals rate by "
                                         "phone. The class is open to the public and beginners; a "
                                         "weekly schedule is posted at the school."),
        ],
        checklist_heading="Appeal checklist (re-verify)",
        checklist=[
            ("Is the class open to and attended by the broader public?", "YES"),
            ("Does the class have published fees?", "YES"),
            ("Are the fees identical for both OPWDD and non-OPWDD individuals?", "YES"),
            ("Is the class subject based?", "YES"),
            ("Does the class provide college credits?", "NO"),
            ("Is the class clinical in nature? (therapy)", "NO"),
            ("Is there a published schedule of the class?", "YES"),
            ("Justification for Appeal (attached rate sheet / documents)", "YES"),
        ],
    ),
]


if __name__ == "__main__":
    for f in FORMS:
        print("wrote", render(f).relative_to(ROOT))
