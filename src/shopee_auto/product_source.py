"""Product source: read the CSV you export by hand from Shopee Affiliate's
"Lấy link hàng loạt" (bulk get-link) feature, instead of automating the
portal's UI -- clicking through many "Lấy link" modals in a row got the
account captcha-challenged.

Thumbnails aren't in that CSV. A plain HTTP GET on the product's public
page doesn't work either -- confirmed against a real product page: Shopee
serves a client-rendered SPA shell (160KB+ of app bootstrap HTML) with no
`og:image` or any other image meta tag anywhere in it; the image only
exists in the DOM after the page's JS runs. So ThumbnailFetcher renders
the page with a headless browser (same Playwright already used for the
Google Lens step) and reads the image back out of the DOM, same as a human
browser would see it.
"""

from __future__ import annotations

import csv
import dataclasses
import hashlib
import re
import urllib.request
from pathlib import Path

from playwright.sync_api import Browser, sync_playwright

from shopee_auto.logger import get_logger

log = get_logger("product_source")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclasses.dataclass
class Product:
    name: str
    link: str  # "Link ưu đãi" -- the tracked affiliate link
    product_url: str  # "Link sản phẩm" -- public page, used only to fetch the thumbnail
    thumbnail_url: str = ""
    sales_count: int | None = None


def list_csv_files(input_dir: Path) -> list[Path]:
    """All *.csv files currently sitting in the watched folder, in a stable
    order. Doesn't distinguish new vs already-imported -- that's tracked
    separately by content hash (see file_fingerprint / StateStore)."""
    input_dir.mkdir(parents=True, exist_ok=True)
    return sorted(input_dir.glob("*.csv"))


def file_fingerprint(path: Path) -> str:
    """Content hash, not mtime/size -- a file copied or re-saved with a new
    timestamp but identical rows must still be recognized as "already
    imported" so it's never re-enqueued."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_products_from_file(path: Path, min_sales: int) -> list[Product]:
    products: list[Product] = []
    seen_links: set[str] = set()
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            name = (row.get("Tên sản phẩm") or "").strip()
            link = (row.get("Link ưu đãi") or "").strip()
            product_url = (row.get("Link sản phẩm") or "").strip()
            if not name or not link:
                continue
            if link in seen_links:
                log.warning("Bỏ qua dòng trùng link trong CSV: %s", name)
                continue
            sales = _parse_sales_count(row.get("Doanh thu") or "")
            if min_sales and sales is not None and sales < min_sales:
                continue
            seen_links.add(link)
            products.append(Product(name=name, link=link, product_url=product_url, sales_count=sales))

    log.info("Đọc %d sản phẩm đạt điều kiện từ %s", len(products), path)
    return products


class ThumbnailFetcher:
    """Open once per batch (like GoogleLensSearch) and reuse across every
    product in the run, instead of launching a fresh browser per item."""

    def __init__(self, headless: bool = True):
        self._headless = headless
        self._playwright = None
        self._browser: Browser | None = None

    def __enter__(self) -> "ThumbnailFetcher":
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self._headless)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    def fetch_url(self, product_url: str, timeout: float = 20) -> str:
        page = self._browser.new_page()
        try:
            page.goto(product_url, wait_until="domcontentloaded", timeout=timeout * 1000)
            try:
                page.wait_for_selector('meta[property="og:image"]', timeout=timeout * 1000, state="attached")
            except Exception:  # noqa: BLE001 - fall through to the <img> fallback below
                pass

            meta = page.locator('meta[property="og:image"]')
            if meta.count() > 0:
                content = meta.first.get_attribute("content")
                if content:
                    return content

            img = page.locator("img[src*='susercontent.com']").first
            if img.count() > 0:
                src = img.get_attribute("src")
                if src:
                    return src

            log.warning("Không tìm thấy ảnh sản phẩm trên trang (sau khi render) cho %s", product_url)
            return ""
        finally:
            page.close()


def download_thumbnail(
    product: Product, dest_dir: Path, fetcher: ThumbnailFetcher, timeout: float = 20
) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", product.name)[:60]
    dest_path = dest_dir / f"{safe_name}.jpg"

    thumbnail_url = product.thumbnail_url or fetcher.fetch_url(product.product_url, timeout=timeout)
    if not thumbnail_url:
        raise RuntimeError(f"Không tìm thấy ảnh sản phẩm cho {product.product_url}")

    request = urllib.request.Request(thumbnail_url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        dest_path.write_bytes(response.read())
    return dest_path


def _parse_sales_count(text: str) -> int | None:
    """Parse strings like '1tr+', '300k+', '90k+' into an int."""
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
