"""Browse the Shopee Affiliate Center with a persistent, already-logged-in
Playwright browser profile, and scrape the current best-selling products.

The affiliate portal's markup is not something we can verify from this
environment, so every selector is read from config (`shopee_affiliate.selectors`)
instead of being hardcoded. See README "Calibrating selectors" for how to
fill them in with Playwright codegen against your own account.
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

    def __enter__(self) -> "ShopeeAffiliateClient":
        self._cfg.browser_profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        launch_kwargs: dict = {"headless": self._cfg.headless}
        if self._cfg.channel:
            launch_kwargs["channel"] = self._cfg.channel
        self._context = self._playwright.chromium.launch_persistent_context(
            str(self._cfg.browser_profile_dir), **launch_kwargs
        )
        return self

    def __exit__(self, *exc: object) -> None:
        if self._context:
            self._context.close()
        if self._playwright:
            self._playwright.stop()

    def ensure_logged_in(self) -> None:
        """First run must be done with headless=false so you can log into
        Shopee affiliate manually once; the session then persists in
        browser_profile_dir for every future run."""
        page = self._context.new_page()
        page.goto(self._cfg.portal_url, wait_until="networkidle")
        sel = self._cfg.selectors
        if sel.get("product_card") and page.locator(sel["product_card"]).count() == 0:
            page.close()
            raise NotLoggedInError(
                "Không thấy danh sách sản phẩm trên affiliate portal. "
                "Chạy lại với headless=false và đăng nhập Shopee thủ công một lần."
            )
        page.close()

    def fetch_bestseller_products(self) -> list[Product]:
        sel = self._cfg.selectors
        cfg = self._cfg.bestseller_filter
        page = self._context.new_page()
        try:
            page.goto(self._cfg.portal_url, wait_until="networkidle")

            if sel.get("sort_dropdown"):
                page.locator(sel["sort_dropdown"]).click()
                page.get_by_text(cfg.sort_by, exact=False).click()
                page.wait_for_load_state("networkidle")

            cards = page.locator(sel["product_card"])
            count = min(cards.count(), self._cfg.max_candidates_per_run)
            products: list[Product] = []
            for i in range(count):
                card = cards.nth(i)
                name = card.locator(sel["product_name"]).inner_text().strip()
                link = card.locator(sel["product_link"]).get_attribute("href") or ""
                thumb = card.locator(sel["product_thumbnail"]).get_attribute("src") or ""
                sales = None
                if sel.get("sales_count"):
                    sales_text = card.locator(sel["sales_count"]).inner_text()
                    sales = _parse_sales_count(sales_text)

                if cfg.min_sales and sales is not None and sales < cfg.min_sales:
                    continue
                if not link or not thumb:
                    log.warning("Bỏ qua sản phẩm thiếu link/thumbnail: %s", name)
                    continue
                products.append(Product(name=name, link=link, thumbnail_url=thumb, sales_count=sales))

            log.info("Tìm thấy %d sản phẩm bán chạy đạt điều kiện", len(products))
            return products
        finally:
            page.close()

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
