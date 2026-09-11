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

from playwright.sync_api import BrowserContext, sync_playwright

from shopee_auto.config import ImageSearchConfig
from shopee_auto.logger import get_logger

log = get_logger("image_search")

_TIKTOK_VIDEO_RE = re.compile(r"tiktok\.com/@[\w.\-]+/video/\d+")


class CaptchaEncounteredError(RuntimeError):
    pass


class GoogleLensSearch:
    def __init__(self, cfg: ImageSearchConfig):
        self._cfg = cfg
        self._playwright = None
        self._context: BrowserContext | None = None

    def __enter__(self) -> "GoogleLensSearch":
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch(headless=self._cfg.headless).new_context()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._context:
            self._context.close()
        if self._playwright:
            self._playwright.stop()

    def search_by_image(self, image_path: Path) -> list[str]:
        sel = self._cfg.selectors
        page = self._context.new_page()
        try:
            page.goto(self._cfg.search_url, wait_until="networkidle")

            if page.get_by_text(re.compile("unusual traffic|captcha", re.I)).count() > 0:
                raise CaptchaEncounteredError("Google Lens hiện captcha, cần chờ hoặc đổi IP.")

            page.set_input_files(sel["file_input"], str(image_path))
            page.wait_for_load_state("networkidle")

            if page.get_by_text(re.compile("unusual traffic|captcha", re.I)).count() > 0:
                raise CaptchaEncounteredError("Google Lens hiện captcha sau khi upload ảnh.")

            links = page.locator(sel["result_link"])
            count = min(links.count(), self._cfg.max_results)
            urls = []
            for i in range(count):
                href = links.nth(i).get_attribute("href")
                if href:
                    urls.append(href)
            log.info("Google Lens trả về %d kết quả", len(urls))
            return urls
        finally:
            page.close()

    @staticmethod
    def filter_tiktok_video_links(urls: list[str]) -> list[str]:
        matches = [u for u in urls if _TIKTOK_VIDEO_RE.search(u)]
        log.info("Lọc còn %d link TikTok video trong %d kết quả", len(matches), len(urls))
        return matches
