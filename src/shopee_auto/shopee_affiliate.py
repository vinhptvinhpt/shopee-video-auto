"""Browse the Shopee Affiliate Center with a persistent, already-logged-in
Playwright browser profile, and scrape the current best-selling products.

Selectors are read from config (`shopee_affiliate.selectors`) rather than
hardcoded, since Shopee's markup can only be confirmed against a real,
logged-in account. See README "Calibrating selectors".
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from playwright.sync_api import BrowserContext, sync_playwright

from shopee_auto.config import ShopeeAffiliateConfig
from shopee_auto.logger import get_logger

log = get_logger("shopee_affiliate")


@dataclasses.dataclass
class Product:
    name: str
    link: str
    thumbnail_url: str
    sales_count: int | None = None


class NotLoggedInError(RuntimeError):
    """Raised when the persistent profile has no valid Shopee affiliate session."""


class ShopeeAffiliateClient:
    def __init__(self, cfg: ShopeeAffiliateConfig):
        self._cfg = cfg
        self._playwright = None
        self._context: BrowserContext | None = None
        self._owns_context = True

    def __enter__(self) -> "ShopeeAffiliateClient":
        self._playwright = sync_playwright().start()

        if self._cfg.cdp_endpoint:
            # Attach to a real Chrome window you launched and logged into by
            # hand (see README "Google sign-in blocked"). This never triggers
            # Google's automation block, since Playwright only connects to it
            # after the fact instead of launching/controlling it from the start.
            browser = self._playwright.chromium.connect_over_cdp(self._cfg.cdp_endpoint)
            self._context = browser.contexts[0] if browser.contexts else browser.new_context()
            self._owns_context = False
            return self

        self._cfg.browser_profile_dir.mkdir(parents=True, exist_ok=True)
        launch_kwargs: dict = {"headless": self._cfg.headless}
        if self._cfg.channel:
            launch_kwargs["channel"] = self._cfg.channel
        self._context = self._playwright.chromium.launch_persistent_context(
            str(self._cfg.browser_profile_dir), **launch_kwargs
        )
        return self

    def __exit__(self, *exc: object) -> None:
        if self._context and self._owns_context:
            self._context.close()
        # else: it's your real Chrome window (connected via CDP) -- leave it running.
        if self._playwright:
            self._playwright.stop()

    def ensure_logged_in(self) -> None:
        """First run must be done with headless=false (or via cdp_endpoint,
        see README) so you can log into Shopee affiliate manually once; the
        session then persists for every future run."""
        nav = self._cfg.navigation
        page = self._context.new_page()
        try:
            page.goto(self._cfg.portal_url, wait_until="networkidle")
            if page.get_by_role("link", name=nav.category_link_text).count() == 0:
                raise NotLoggedInError(
                    f"Không thấy menu '{nav.category_link_text}' trên affiliate portal "
                    "— có thể chưa đăng nhập, hoặc Shopee đã đổi nhãn menu."
                )
        finally:
            page.close()

    def fetch_bestseller_products(self) -> list[Product]:
        """Navigate to the bestseller tab, then for each product card: read
        name/thumbnail/sales directly off the card, click its in-card "Lấy
        link" button, and pull the real affiliate link out of the Ant Design
        modal that opens (no popup/new tab involved).
        """
        nav = self._cfg.navigation
        sel = self._cfg.selectors
        cfg = self._cfg.bestseller_filter
        page = self._context.new_page()
        try:
            page.goto(self._cfg.portal_url, wait_until="networkidle")
            page.get_by_text(nav.bestseller_tab_text, exact=True).click()
            page.wait_for_load_state("networkidle")

            cards = page.locator(sel["product_card"])
            count = min(cards.count(), self._cfg.max_candidates_per_run)
            products: list[Product] = []
            for i in range(count):
                card = cards.nth(i)
                name = card.locator(sel["product_name"]).inner_text().strip()
                thumb = card.locator(sel["product_thumbnail"]).get_attribute("src") or ""
                sales = None
                if sel.get("sales_count"):
                    sales_el = card.locator(sel["sales_count"])
                    if sales_el.count() > 0:
                        sales = _parse_sales_count(sales_el.first.inner_text())
                if cfg.min_sales and sales is not None and sales < cfg.min_sales:
                    continue
                if not thumb:
                    log.warning("Bỏ qua sản phẩm thiếu thumbnail: %s", name)
                    continue

                link = self._get_affiliate_link(card)
                if not link:
                    log.warning("Bỏ qua sản phẩm không lấy được link: %s", name)
                    continue
                products.append(Product(name=name, link=link, thumbnail_url=thumb, sales_count=sales))

            log.info("Tìm thấy %d sản phẩm bán chạy đạt điều kiện", len(products))
            return products
        finally:
            page.close()

    def _get_affiliate_link(self, card) -> str:
        """Click the card's "Lấy link" button; the modal that opens shows
        the shortened link in a disabled <textarea> as soon as the backend
        generates it, so poll briefly instead of assuming it's there on the
        first frame."""
        nav = self._cfg.navigation
        sel = self._cfg.selectors
        page = card.page
        card.locator(sel["get_link_button"]).click()
        link_el = page.locator(sel["product_link_value"])
        try:
            link_el.wait_for(timeout=8000)
            link = ""
            for _ in range(20):
                link = (link_el.input_value() or "").strip()
                if link:
                    break
                page.wait_for_timeout(200)
            return link
        finally:
            try:
                page.get_by_role("button", name=nav.close_popup_button_text).click(timeout=3000)
            except Exception:  # noqa: BLE001 - best-effort cleanup, don't mask the real error
                pass

    def download_thumbnail(self, product: Product, dest_dir: Path) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", product.name)[:60]
        dest_path = dest_dir / f"{safe_name}.jpg"
        response = self._context.request.get(product.thumbnail_url)
        dest_path.write_bytes(response.body())
        return dest_path


def _parse_sales_count(text: str) -> int | None:
    """Parse strings like '1.2k đã bán' or '12,345 sold' into an int."""
    text = text.strip().lower()
    match = re.search(r"([\d.,]+)\s*(k|tr|m)?", text)
    if not match:
        return None
    number_str, unit = match.group(1), match.group(2)
    number_str = number_str.replace(",", "")
    try:
        number = float(number_str)
    except ValueError:
        return None
    multiplier = {"k": 1_000, "tr": 1_000_000, "m": 1_000_000}.get(unit or "", 1)
    return int(number * multiplier)
