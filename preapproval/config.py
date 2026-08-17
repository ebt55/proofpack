"""Configuration + checklist loading.

Checklists are pure data (YAML): adding a new form category means adding a new
YAML file to checklists/ — no code changes. See docs/ADDING-A-CHECKLIST.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class FeeCap:
    id: str
    label: str
    max_amount: float
    unit_keywords: list[str] = field(default_factory=list)


@dataclass
class ChecklistItem:
    id: str
    form_question: str
    kind: str                      # website | internal | document
    requirement: Optional[str] = None
    check_hint: Optional[str] = None
    internal_reason: Optional[str] = None


@dataclass
class Checklist:
    category: str
    display_name: str
    form_title_hints: list[str]
    adults_only: bool
    fee_caps: list[FeeCap]
    notes: list[str]
    items: list[ChecklistItem]
    exclusion_list: list[str] = field(default_factory=list)
    exclusion_keywords: list[str] = field(default_factory=list)
    appeal_framing: Optional[str] = None

    @property
    def website_items(self) -> list[ChecklistItem]:
        return [i for i in self.items if i.kind == "website"]

    @property
    def internal_items(self) -> list[ChecklistItem]:
        return [i for i in self.items if i.kind == "internal"]

    @property
    def document_items(self) -> list[ChecklistItem]:
        return [i for i in self.items if i.kind == "document"]


@dataclass
class ToolConfig:
    model: str = "claude-opus-4-8"
    max_agent_iterations: int = 40
    page_text_limit: int = 12000
    headless: bool = True
    viewport_width: int = 1280
    viewport_height: int = 1200
    navigation_timeout_ms: int = 30000
    output_dir: Path = REPO_ROOT / "output"
    checklist_dir: Path = REPO_ROOT / "checklists"
    # Used only to estimate the cost of a run; edit when prices or model change.
    price_input_per_mtok: float = 5.0
    price_output_per_mtok: float = 25.0


def load_config(path: Optional[Path] = None) -> ToolConfig:
    path = path or REPO_ROOT / "config.yaml"
    cfg = ToolConfig()
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        cfg.model = raw.get("model", cfg.model)
        cfg.max_agent_iterations = raw.get("max_agent_iterations", cfg.max_agent_iterations)
        cfg.page_text_limit = raw.get("page_text_limit", cfg.page_text_limit)
        browser = raw.get("browser", {}) or {}
        cfg.headless = browser.get("headless", cfg.headless)
        cfg.viewport_width = browser.get("viewport_width", cfg.viewport_width)
        cfg.viewport_height = browser.get("viewport_height", cfg.viewport_height)
        cfg.navigation_timeout_ms = browser.get(
            "navigation_timeout_ms", cfg.navigation_timeout_ms
        )
        if raw.get("output_dir"):
            cfg.output_dir = REPO_ROOT / raw["output_dir"]
        if raw.get("checklist_dir"):
            cfg.checklist_dir = REPO_ROOT / raw["checklist_dir"]
        pricing = raw.get("pricing", {}) or {}
        cfg.price_input_per_mtok = pricing.get("input_per_mtok", cfg.price_input_per_mtok)
        cfg.price_output_per_mtok = pricing.get("output_per_mtok", cfg.price_output_per_mtok)
    return cfg


def _parse_checklist(raw: dict) -> Checklist:
    items = [
        ChecklistItem(
            id=i["id"],
            form_question=i["form_question"],
            kind=i["kind"],
            requirement=i.get("requirement"),
            check_hint=(i.get("check_hint") or "").strip() or None,
            internal_reason=i.get("internal_reason"),
        )
        for i in raw.get("items", [])
    ]
    caps = [
        FeeCap(
            id=c["id"],
            label=c["label"],
            max_amount=float(c["max_amount"]),
            unit_keywords=c.get("unit_keywords", []) or [],
        )
        for c in raw.get("fee_caps", []) or []
    ]
    appeal = raw.get("appeal") or {}
    return Checklist(
        category=raw["category"],
        display_name=raw["display_name"],
        form_title_hints=raw.get("form_title_hints", []),
        adults_only=bool(raw.get("adults_only", False)),
        fee_caps=caps,
        notes=raw.get("notes", []) or [],
        items=items,
        exclusion_list=raw.get("exclusion_list", []) or [],
        exclusion_keywords=raw.get("exclusion_keywords", []) or [],
        appeal_framing=(appeal.get("framing") or "").strip() or None,
    )


def load_checklists(checklist_dir: Optional[Path] = None) -> dict[str, Checklist]:
    """Load every checklist YAML, keyed by category."""
    checklist_dir = checklist_dir or (REPO_ROOT / "checklists")
    checklists: dict[str, Checklist] = {}
    for path in sorted(checklist_dir.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        cl = _parse_checklist(raw)
        checklists[cl.category] = cl
    return checklists


def checklist_for(category: str, checklists: dict[str, Checklist]) -> Checklist:
    if category not in checklists:
        raise KeyError(
            f"No checklist found for category '{category}'. "
            f"Available: {', '.join(sorted(checklists))}. "
            "Add a YAML file to checklists/ to support a new category."
        )
    return checklists[category]
