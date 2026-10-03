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
import hashlib
import re
import urllib.request
from pathlib import Path

from shopee_auto.logger import get_logger

log = get_logger("product_source")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_META_TAG_RE = re.compile(r"<meta\b([^>]+)>", re.I)
_ATTR_RE = re.compile(r'([\w:-]+)\s*=\s*"([^"]*)"|([\w:-]+)\s*=\s*\'([^\']*)\'', re.I)
# Checked in this order -- og:image is standard, but some pages only carry
# the twitter/itemprop variants.
_IMAGE_META_KEYS = ("og:image:secure_url", "og:image", "twitter:image", "image")


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


def _extract_meta_image(html: str) -> str:
    """Pull an image URL out of the page's <meta> tags regardless of
    attribute order (some pages emit content="..." before property="...",
    which a property-then-content regex silently misses)."""
    candidates: dict[str, str] = {}
    for tag_match in _META_TAG_RE.finditer(html):
        attrs: dict[str, str] = {}
        for m in _ATTR_RE.finditer(tag_match.group(1)):
            key = (m.group(1) or m.group(3)).lower()
            value = m.group(2) if m.group(1) else m.group(4)
            attrs[key] = value
        key = (attrs.get("property") or attrs.get("name") or attrs.get("itemprop") or "").lower()
        if key and attrs.get("content"):
            candidates[key] = attrs["content"]
    for key in _IMAGE_META_KEYS:
        if candidates.get(key):
            return candidates[key]
    return ""


def fetch_thumbnail_url(product_url: str, timeout: float = 15) -> str:
    request = urllib.request.Request(
        product_url, headers={"User-Agent": _USER_AGENT, "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        status = response.status
        html = response.read().decode("utf-8", errors="ignore")
    image = _extract_meta_image(html)
    if not image:
        log.warning(
            "Không tìm thấy ảnh trong <meta> của %s (HTTP %s, HTML dài %d ký tự). Đoạn đầu trang: %r",
            product_url,
            status,
            len(html),
            html[:500],
        )
    return image


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
