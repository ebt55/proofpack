"""Evidence store: date-stamped, URL-stamped captures + integrity manifest.

Every screenshot gets a visible banner burned into the image (capture time,
URL, what the capture proves) and a SHA-256 recorded in manifest.json, so the
package holds up in an audit. Stamping is deterministic code — the model never
controls timestamps or hashes.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .models import EvidenceRecord

BANNER_BG = (17, 24, 39)      # dark slate
BANNER_FG = (255, 255, 255)
BANNER_ACCENT = (110, 231, 183)  # mint — the label line


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 15] + "..." + text[-12:]


def stamp_image(path: Path, *, captured_at: str, url: str, label: str) -> None:
    """Burn a visible banner (timestamp | label / URL) onto the top of a PNG."""
    img = Image.open(path).convert("RGB")
    font = _font(16)
    line1 = f"CAPTURED {captured_at}   |   {label}"
    line2 = f"URL: {_shorten(url, 150)}"
    pad, gap = 10, 6
    line_h = 20
    banner_h = pad * 2 + line_h * 2 + gap

    stamped = Image.new("RGB", (img.width, img.height + banner_h), BANNER_BG)
    stamped.paste(img, (0, banner_h))
    draw = ImageDraw.Draw(stamped)
    draw.text((pad, pad), line1, fill=BANNER_ACCENT, font=font)
    draw.text((pad, pad + line_h + gap), line2, fill=BANNER_FG, font=font)
    stamped.save(path, format="PNG")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _slug(text: str, limit: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:limit] or "capture"


class EvidenceStore:
    """Manages the evidence/ folder of one report package.

    One run == one manifest == one set of captures. Re-reviewing an application
    therefore clears captures from the previous run (`fresh=True`), so the folder
    can never contain files the manifest doesn't reference. Pass `fresh=False`
    to add captures to an existing package (e.g. a future re-check of a single
    item from chat mode).
    """

    def __init__(self, package_dir: Path, *, fresh: bool = True):
        self.dir = package_dir / "evidence"
        self.dir.mkdir(parents=True, exist_ok=True)
        if fresh:
            for stale in self.dir.glob("*.png"):
                stale.unlink()
        self.records: list[EvidenceRecord] = []
        self._counter = 0

    def next_path(self, kind: str, label: str) -> Path:
        self._counter += 1
        return self.dir / f"{self._counter:02d}-{kind}-{_slug(label)}.png"

    def register(self, path: Path, *, kind: str, label: str, url: str) -> EvidenceRecord:
        """Stamp a freshly captured PNG and add it to the manifest."""
        captured_at = datetime.now(timezone.utc).astimezone().strftime(
            "%Y-%m-%d %H:%M:%S %Z"
        )
        stamp_image(path, captured_at=captured_at, url=url, label=label)
        record = EvidenceRecord(
            file=f"evidence/{path.name}",
            kind=kind,  # type: ignore[arg-type]
            label=label,
            url=url,
            captured_at=captured_at,
            sha256=sha256_of(path),
        )
        self.records.append(record)
        return record

    def has_file(self, relative_file: str) -> bool:
        return any(r.file == relative_file for r in self.records)

    def write_manifest(self, package_dir: Path) -> None:
        manifest = {
            "note": (
                "Every capture below is stamped in-image with its capture time and URL. "
                "SHA-256 hashes are computed over the stamped file; recompute to verify "
                "the evidence has not been altered."
            ),
            "captures": [r.model_dump() for r in self.records],
        }
        (package_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
