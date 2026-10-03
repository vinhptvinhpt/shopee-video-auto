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

from playwright.sync_api import Browser, Playwright

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
    for ThumbnailFetcher in the same thread.

    cdp_endpoint, when set, attaches to the same real, manually-launched
    Chrome window used for thumbnails (see ThumbnailFetcher) instead of
    launching a separate Playwright-controlled browser -- this rules out
    Google's automation-fingerprint check (confirmed elsewhere in this
    project, e.g. Shopee's /verify/traffic/error) as a cause, independent
    of whatever else is going on with a given result.

    Observed in production logs (real runs, 2 different product images):
    the page landed on had zero <a href> elements that were actual result
    content -- every link was Google's own search-results-page chrome
    (nav/footer). That is consistent with the visual-match grid being
    rendered inside a child <iframe> that a main-frame-only query would
    completely miss, which is why search_by_image scans every frame via
    page.frames rather than just page.locator(). This was verified against
    a local HTML fixture reproducing that exact shape (outer chrome +
    iframe with the real result links) -- not yet confirmed against the
    live google.com page, since this project's sandbox can't reach it
    (network policy blocks google.com/lens.google.com here)."""

    def __init__(
        self,
        playwright: Playwright,
        cfg: ImageSearchConfig,
        debug_dir: Path | None = None,
        cdp_endpoint: str | None = None,
    ):
        self._playwright = playwright
        self._cfg = cfg
        self._debug_dir = debug_dir
        self._cdp_endpoint = cdp_endpoint
        self._browser: Browser | None = None
        self._owns_browser = True

    def __enter__(self) -> "GoogleLensSearch":
        if self._cdp_endpoint:
            self._browser = self._playwright.chromium.connect_over_cdp(self._cdp_endpoint)
            self._owns_browser = False
        else:
            self._browser = self._playwright.chromium.launch(headless=self._cfg.headless)
            self._owns_browser = True
        return self

    def __exit__(self, *exc: object) -> None:
        if self._browser and self._owns_browser:
            self._browser.close()
        # else: it's your real Chrome window (connected via CDP) -- leave it running.

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
        page = self._browser.new_page()
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

            if self._cfg.keyword:
                self._add_keyword_to_search(page, sel.get("search_box"))
                if page.get_by_text(re.compile("unusual traffic|captcha", re.I)).count() > 0:
                    raise CaptchaEncounteredError("Google Lens hiện captcha sau khi thêm từ khoá.")

            # The real result grid can be rendered inside an <iframe> (seen
            # in practice: the top-level page is just the normal Google
            # search chrome -- header/footer nav links only, zero actual
            # results -- while the visual-match tiles live in a child
            # frame). page.locator() only sees the main frame, so scan every
            # frame on the page and merge their links instead of assuming
            # everything is in the top-level document.
            frame_urls = [f.url for f in page.frames]
            log.info("Trang hiện tại có %d frame: %s", len(frame_urls), frame_urls)

            urls: list[str] = []
            for frame in page.frames:
                try:
                    frame_links = frame.locator(selector)
                    count = frame_links.count()
                except Exception:  # noqa: BLE001 - a detached/cross-origin frame can throw; skip it
                    continue
                for i in range(count):
                    try:
                        href = frame_links.nth(i).get_attribute("href")
                    except Exception:  # noqa: BLE001
                        href = None
                    if href:
                        urls.append(href)
            log.info("Google Lens trả về %d link trên trang kết quả (quét %d frame)", len(urls), len(page.frames))

            if not any(_TIKTOK_VIDEO_RE.search(u) for u in urls):
                # Could be a genuine "no matching TikTok video" -- or the
                # selector/timing missed the real result grid entirely (e.g.
                # the result is a <div onclick> with no href, or lives in a
                # shadow root that plain HTML serialization skips). A raw
                # text scan for the literal word "tiktok" across every
                # frame's rendered HTML is a much lower bar than "found a
                # clean <a href>" -- if THIS finds mentions but the
                # structured scan above found none, that proves it's a
                # markup-extraction problem, not a "no match" result.
                mentions = self._raw_tiktok_mentions(page)
                sample = urls[:40]
                log.warning(
                    "Không có link TikTok (link thường) nào trong %d link thu được (URL hiện tại: %s). "
                    "Quét thô chữ 'tiktok' trong HTML: tìm thấy %d lần. Mẫu link: %s",
                    len(urls),
                    page.url,
                    len(mentions),
                    sample,
                )
                for snippet in mentions[:8]:
                    log.warning("  -> %s", snippet)

                self._dump_debug(page, image_path)

            return urls
        finally:
            page.close()

    def _add_keyword_to_search(self, page, custom_selector: str | None) -> None:
        """Types `self._cfg.keyword` into Lens's "Add to your search" box
        and submits it, so results must visually match *and* mention that
        word -- far more likely to be an actual tiktok.com page than a
        generic visually-similar product listing. Best-effort: Google's
        markup for this box isn't documented/stable, so a list of likely
        selectors is tried in order, and failing to find any of them just
        means we fall back to the plain image search instead of raising."""
        candidates = [custom_selector] if custom_selector else []
        candidates += [
            "textarea[aria-label='Search']",
            "input[aria-label='Search']",
            "textarea[aria-label*='search' i]",
            "input[aria-label*='search' i]",
            "textarea[placeholder*='search' i]",
            "input[placeholder*='search' i]",
            "[role='combobox']",
        ]
        # Same reasoning as the result grid: the search box can live inside
        # an <iframe>, not the main document, so try every frame.
        for frame in page.frames:
            for candidate in candidates:
                if not candidate:
                    continue
                try:
                    box = frame.locator(candidate).first
                    if box.count() == 0:
                        continue
                    box.click()
                    box.fill(self._cfg.keyword)
                    box.press("Enter")
                    page.wait_for_load_state("networkidle")
                    page.wait_for_timeout(1500)
                    log.info("Đã thêm từ khoá '%s' vào tìm kiếm Lens (selector: %s)", self._cfg.keyword, candidate)
                    return
                except Exception:  # noqa: BLE001 - try the next candidate selector/frame
                    log.debug("Selector '%s' không gõ được từ khoá, thử selector khác", candidate, exc_info=True)
                    continue
        log.warning(
            "Không tìm được ô nhập từ khoá trên trang Lens để gõ '%s' -- tiếp tục chỉ tìm bằng ảnh.",
            self._cfg.keyword,
        )

    @staticmethod
    def _raw_tiktok_mentions(page) -> list[str]:
        """Every occurrence of the literal text "tiktok" across every
        frame's rendered HTML, each with ~120 chars of surrounding context.
        Deliberately not limited to <a href> values -- this is a sanity
        check for whether the word appears ANYWHERE in the DOM at all, to
        tell a genuine "no match" apart from a markup-extraction miss."""
        mentions: list[str] = []
        for frame in page.frames:
            try:
                content = frame.content()
            except Exception:  # noqa: BLE001 - detached/cross-origin frame
                continue
            for m in re.finditer("tiktok", content, re.I):
                start = max(0, m.start() - 60)
                end = min(len(content), m.end() + 60)
                snippet = " ".join(content[start:end].split())
                mentions.append(f"[{frame.url}] ...{snippet}...")
        return mentions

    def _dump_debug(self, page, image_path: Path) -> None:
        """Best-effort: full-page screenshot plus each frame's HTML saved
        separately (frame content isn't included in the main page's
        page.content(), so a single dump would miss iframe content)."""
        if not self._debug_dir:
            return
        self._debug_dir.mkdir(parents=True, exist_ok=True)
        stem = image_path.stem
        try:
            page.screenshot(path=str(self._debug_dir / f"{stem}_lens.png"), full_page=True)
        except Exception:  # noqa: BLE001
            log.exception("Không lưu được debug screenshot cho Google Lens")
        for i, frame in enumerate(page.frames):
            try:
                content = frame.content()
            except Exception:  # noqa: BLE001
                continue
            try:
                (self._debug_dir / f"{stem}_lens_frame{i}.html").write_text(content, encoding="utf-8")
            except Exception:  # noqa: BLE001
                log.exception("Không lưu được debug HTML frame %d cho Google Lens", i)
        log.info("Đã lưu debug: %s_lens.png + %d file HTML frame trong %s", stem, len(page.frames), self._debug_dir)

    @staticmethod
    def filter_tiktok_video_links(urls: list[str]) -> list[str]:
        matches = [u for u in urls if _TIKTOK_VIDEO_RE.search(u)]
        log.info("Lọc còn %d link TikTok video trong %d kết quả", len(matches), len(urls))
        return matches
