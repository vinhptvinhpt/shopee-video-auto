"""Reverse image search on Google Lens: upload a product thumbnail, read back
the visually-similar result links, and keep only the ones pointing at a
TikTok video page.

This drives the public Google Lens web UI with Playwright rather than an
API, per your choice of the free/no-API-key approach. Google may show a
captcha or change its DOM at any time -- `search_by_image` raises
`CaptchaEncounteredError` when it detects a captcha so the pipeline can skip
this product and move on instead of hanging.
"""

from __future__ import annotations

import re
from pathlib import Path

from playwright.sync_api import BrowserContext, Playwright

from shopee_auto.config import ImageSearchConfig
from shopee_auto.logger import get_logger

log = get_logger("image_search")

_TIKTOK_VIDEO_RE = re.compile(r"tiktok\.com/@[\w.\-]+/video/\d+")


class CaptchaEncounteredError(RuntimeError):
    pass


class GoogleLensSearch:
    """Takes an already-started Playwright driver (see pipeline.py) rather
    than starting its own -- Playwright's sync API only tolerates one
    sync_playwright() driver per thread, and prepare_videos also needs one
    for ThumbnailFetcher in the same thread."""

    def __init__(self, playwright: Playwright, cfg: ImageSearchConfig, debug_dir: Path | None = None):
        self._playwright = playwright
        self._cfg = cfg
        self._debug_dir = debug_dir
        self._context: BrowserContext | None = None

    def __enter__(self) -> "GoogleLensSearch":
        self._context = self._playwright.chromium.launch(headless=self._cfg.headless).new_context()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._context:
            self._context.close()

    def search_by_image(self, image_path: Path) -> list[str]:
        sel = self._cfg.selectors
        # No configured selector (the common case -- Google's result-card
        # markup/classes change often and aren't worth hand-calibrating) ->
        # grab every link on the page. We don't know in advance which of
        # them are real results vs. Google chrome/ads, so filtering down to
        # TikTok video links happens afterwards in filter_tiktok_video_links,
        # on the *unfiltered* list -- truncating here by max_results first
        # would almost always discard the handful of TikTok links before
        # they're even checked.
        selector = sel.get("result_link") or "a[href]"
        page = self._context.new_page()
        try:
            page.goto(self._cfg.search_url, wait_until="networkidle")

            if page.get_by_text(re.compile("unusual traffic|captcha", re.I)).count() > 0:
                raise CaptchaEncounteredError("Google Lens hiện captcha, cần chờ hoặc đổi IP.")

            page.set_input_files(sel["file_input"], str(image_path))
            page.wait_for_load_state("networkidle")
            # The visual-match grid is a client-rendered component that can
            # still be populating after "networkidle" fires (that event just
            # means the network went quiet, not that rendering is done) --
            # give it a moment before reading the DOM.
            page.wait_for_timeout(2000)

            if page.get_by_text(re.compile("unusual traffic|captcha", re.I)).count() > 0:
                raise CaptchaEncounteredError("Google Lens hiện captcha sau khi upload ảnh.")

            links = page.locator(selector)
            count = links.count()
            urls = []
            for i in range(count):
                href = links.nth(i).get_attribute("href")
                if href:
                    urls.append(href)
            log.info("Google Lens trả về %d link trên trang kết quả", len(urls))

            if not any(_TIKTOK_VIDEO_RE.search(u) for u in urls):
                # Could be a genuine "no matching TikTok video" -- or the
                # selector/timing missed the real result grid entirely.
                # Dump everything needed to tell the two apart without
                # having to reproduce the run.
                sample = urls[:40]
                log.warning(
                    "Không có link TikTok nào trong %d link thu được. Mẫu link: %s",
                    len(urls),
                    sample,
                )
                if self._debug_dir:
                    self._debug_dir.mkdir(parents=True, exist_ok=True)
                    stem = image_path.stem
                    try:
                        page.screenshot(path=str(self._debug_dir / f"{stem}_lens.png"), full_page=True)
                        (self._debug_dir / f"{stem}_lens.html").write_text(page.content(), encoding="utf-8")
                        log.info("Đã lưu debug: %s_lens.png / .html trong %s", stem, self._debug_dir)
                    except Exception:  # noqa: BLE001 - debug capture is best-effort
                        log.exception("Không lưu được debug screenshot/html cho Google Lens")

            return urls
        finally:
            page.close()

    @staticmethod
    def filter_tiktok_video_links(urls: list[str]) -> list[str]:
        matches = [u for u in urls if _TIKTOK_VIDEO_RE.search(u)]
        log.info("Lọc còn %d link TikTok video trong %d kết quả", len(matches), len(urls))
        return matches
