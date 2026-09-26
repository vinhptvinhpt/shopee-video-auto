"""Product source: read the CSV you export by hand from Shopee Affiliate's
"Lấy link hàng loạt" (bulk get-link) feature, instead of automating the
portal's UI -- clicking through many "Lấy link" modals in a row got the
account captcha-challenged. This file has no dependency on Playwright or a
logged-in session at all.

Thumbnails aren't in that CSV, so we fetch them by plain HTTP GET on the
product's public page (the `og:image` meta tag Shopee renders server-side
for link previews) -- an ordinary, unauthenticated request, the same kind
any chat app's link-preview bot makes.
"""

from __future__ import annotations

import csv
import dataclasses
import re
import urllib.request
from pathlib import Path

from shopee_auto.config import ProductSourceConfig
from shopee_auto.logger import get_logger

log = get_logger("product_source")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_OG_IMAGE_RE = re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.I)


@dataclasses.dataclass
class Product:
    name: str
    link: str  # "Link ưu đãi" -- the tracked affiliate link
    product_url: str  # "Link sản phẩm" -- public page, used only to fetch the thumbnail
    thumbnail_url: str = ""
    sales_count: int | None = None


class NoCsvFoundError(RuntimeError):
    pass


def load_products(cfg: ProductSourceConfig) -> list[Product]:
    if not cfg.csv_path.exists():
        raise NoCsvFoundError(
            f"Không thấy file CSV tại {cfg.csv_path}. Xuất file mới bằng chức năng "
            "\"Lấy link hàng loạt\" trên Shopee Affiliate rồi đặt đúng đường dẫn này."
        )

    products: list[Product] = []
    with cfg.csv_path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            name = (row.get("Tên sản phẩm") or "").strip()
            link = (row.get("Link ưu đãi") or "").strip()
            product_url = (row.get("Link sản phẩm") or "").strip()
            if not name or not link:
                continue
            sales = _parse_sales_count(row.get("Doanh thu") or "")
            if cfg.min_sales and sales is not None and sales < cfg.min_sales:
                continue
            products.append(Product(name=name, link=link, product_url=product_url, sales_count=sales))

    log.info("Đọc %d sản phẩm đạt điều kiện từ %s", len(products), cfg.csv_path)
    return products[: cfg.max_candidates_per_run]


def fetch_thumbnail_url(product_url: str, timeout: float = 15) -> str:
    request = urllib.request.Request(product_url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        html = response.read().decode("utf-8", errors="ignore")
    match = _OG_IMAGE_RE.search(html)
    return match.group(1) if match else ""


def download_thumbnail(product: Product, dest_dir: Path, timeout: float = 15) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", product.name)[:60]
    dest_path = dest_dir / f"{safe_name}.jpg"

    thumbnail_url = product.thumbnail_url or fetch_thumbnail_url(product.product_url, timeout=timeout)
    if not thumbnail_url:
        raise RuntimeError(f"Không tìm thấy ảnh og:image cho {product.product_url}")

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
