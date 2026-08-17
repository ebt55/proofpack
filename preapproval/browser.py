"""Playwright wrapper: navigation, page reading, and raw screenshot capture.

Everything here is deterministic code. The verification agent decides WHERE to
look; this module is the only thing that actually touches the web page, and the
evidence module is the only thing that produces stamped captures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout, sync_playwright

# Markers that indicate a bot-block / CAPTCHA page rather than real content.
BLOCK_MARKERS = [
    "type the characters you see",
    "enter the characters you see",
    "robot check",
    "are you a robot",
    "verify you are a human",
    "unusual traffic",
    "access denied",
    "captcha",
    "pardon our interruption",
]

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


@dataclass
class NavResult:
    ok: bool
    url: str
    final_url: str = ""
    title: str = ""
    error: Optional[str] = None
    blocked: bool = False


@dataclass
class TextMatch:
    snippet: str
    index: int


@dataclass
class Browser:
    headless: bool = True
    viewport_width: int = 1280
    viewport_height: int = 1200
    navigation_timeout_ms: int = 30000
    _pw: object = field(default=None, repr=False)
    _browser: object = field(default=None, repr=False)
    _page: Optional[Page] = field(default=None, repr=False)

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        self._pw = sync_playwright().start()
        launch_kwargs = dict(headless=self.headless)
        try:
            self._browser = self._pw.chromium.launch(**launch_kwargs)
        except Exception:
            # Bundled Chromium missing — fall back to an installed Google Chrome.
            self._browser = self._pw.chromium.launch(channel="chrome", **launch_kwargs)
        context = self._browser.new_context(
            user_agent=UA,
            viewport={"width": self.viewport_width, "height": self.viewport_height},
            locale="en-US",
        )
        context.set_default_timeout(self.navigation_timeout_ms)
        self._page = context.new_page()

    def stop(self) -> None:
        try:
            if self._browser:
                self._browser.close()
        finally:
            if self._pw:
                self._pw.stop()

    def __enter__(self) -> "Browser":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    @property
    def page(self) -> Page:
        assert self._page is not None, "Browser not started"
        return self._page

    # -- navigation & reading ------------------------------------------------

    def navigate(self, url: str) -> NavResult:
        if not re.match(r"^https?://", url, re.I):
            url = "https://" + url
        try:
            self.page.goto(url, wait_until="domcontentloaded")
            # Give JS-rendered pages a moment to settle.
            try:
                self.page.wait_for_load_state("networkidle", timeout=6000)
            except PlaywrightTimeout:
                pass
        except Exception as e:  # noqa: BLE001 — report any nav failure to the agent
            return NavResult(ok=False, url=url, error=f"{type(e).__name__}: {e}")

        title = self.page.title() or ""
        text_probe = (self.page_text()[:4000] + " " + title).lower()
        blocked = any(m in text_probe for m in BLOCK_MARKERS)
        return NavResult(
            ok=True,
            url=url,
            final_url=self.page.url,
            title=title,
            blocked=blocked,
        )

    def page_text(self) -> str:
        """Visible text of the current page (DOM innerText)."""
        try:
            return self.page.evaluate("() => document.body ? document.body.innerText : ''")
        except Exception:
            return ""

    def links(self, contains: str = "", limit: int = 40) -> list[tuple[str, str]]:
        """(text, href) pairs of links on the page, optionally filtered."""
        try:
            raw = self.page.evaluate(
                """() => Array.from(document.querySelectorAll('a[href]'))
                        .map(a => [a.innerText.trim().replace(/\\s+/g, ' '), a.href])
                        .filter(([t, h]) => t && h && !h.startsWith('javascript:'))"""
            )
        except Exception:
            return []
        seen, out = set(), []
        needle = contains.lower()
        for text, href in raw:
            if needle and needle not in text.lower() and needle not in href.lower():
                continue
            key = (text[:80], href)
            if key in seen:
                continue
            seen.add(key)
            out.append((text[:120], href))
            if len(out) >= limit:
                break
        return out

    def find_text(self, query: str, context_chars: int = 200, limit: int = 6) -> list[TextMatch]:
        """Case-insensitive occurrences of `query` in the page text, with context."""
        text = self.page_text()
        matches: list[TextMatch] = []
        for m in re.finditer(re.escape(query), text, re.I):
            start = max(0, m.start() - context_chars)
            end = min(len(text), m.end() + context_chars)
            snippet = re.sub(r"\s+", " ", text[start:end]).strip()
            matches.append(TextMatch(snippet=snippet, index=m.start()))
            if len(matches) >= limit:
                break
        return matches

    # -- raw capture (stamping happens in evidence.py) -----------------------

    def capture_full_page(self, path: Path) -> None:
        self.page.screenshot(path=str(path), full_page=True)

    def capture_region_around_text(self, text: str, path: Path, pad: int = 220) -> bool:
        """Screenshot a region around the first occurrence of `text`.

        Returns False if the text can't be located as an on-screen element —
        callers should then fall back to a full-page capture.
        """
        try:
            locator = self.page.get_by_text(text, exact=False).first
            locator.scroll_into_view_if_needed(timeout=5000)
            box = locator.bounding_box()
        except Exception:
            box = None
        if not box:
            return False
        page_height = self.page.evaluate("() => document.body.scrollHeight") or 10**6
        y = max(0, box["y"] - pad)
        height = min(box["height"] + 2 * pad, page_height - y)
        self.page.screenshot(
            path=str(path),
            clip={"x": 0, "y": y, "width": self.viewport_width, "height": max(height, 80)},
        )
        return True
