"""Load and validate config/config.yaml into typed dataclasses."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"


@dataclasses.dataclass
class ProductSourceConfig:
    """Products come from CSVs you export by hand via Shopee Affiliate's own
    "Lấy link hàng loạt" (bulk get-link) feature and drop into input_dir --
    not from scraping the portal, which got the account captcha-challenged.
    See README "Nguồn sản phẩm (CSV) và hàng đợi"."""

    input_dir: Path
    min_sales: int


@dataclasses.dataclass
class ImageSearchConfig:
    engine: str
    search_url: str
    max_results: int
    headless: bool
    selectors: dict[str, str]


@dataclasses.dataclass
class TikTokConfig:
    min_views: int
    max_candidates: int
    download_dir: Path
    request_delay_seconds: float


@dataclasses.dataclass
class PhoneConfig:
    adb_serial: str | None
    shopee_app_package: str
    push_dir_on_device: str
    ui: dict[str, dict[str, str]]


@dataclasses.dataclass
class StateConfig:
    db_path: Path


@dataclasses.dataclass
class LoggingConfig:
    level: str
    log_dir: Path


@dataclasses.dataclass
class AppConfig:
    daily_target: int
    product_source: ProductSourceConfig
    image_search: ImageSearchConfig
    tiktok: TikTokConfig
    phone: PhoneConfig
    state: StateConfig
    logging: LoggingConfig
    root_dir: Path


def _resolve(root_dir: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (root_dir / path).resolve()


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> AppConfig:
    path = Path(path)
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    root_dir = path.resolve().parents[1]

    ps = raw["product_source"]
    ims = raw["image_search"]
    tk = raw["tiktok"]
    ph = raw["phone"]
    st = raw["state"]
    lg = raw["logging"]

    return AppConfig(
        daily_target=raw["daily_target"],
        root_dir=root_dir,
        product_source=ProductSourceConfig(
            input_dir=_resolve(root_dir, ps["input_dir"]),
            min_sales=ps["min_sales"],
        ),
        image_search=ImageSearchConfig(
            engine=ims["engine"],
            search_url=ims["search_url"],
            max_results=ims["max_results"],
            headless=ims["headless"],
            selectors=ims["selectors"],
        ),
        tiktok=TikTokConfig(
            min_views=tk["min_views"],
            max_candidates=tk["max_candidates"],
            download_dir=_resolve(root_dir, tk["download_dir"]),
            request_delay_seconds=tk["request_delay_seconds"],
        ),
        phone=PhoneConfig(
            adb_serial=ph["adb_serial"],
            shopee_app_package=ph["shopee_app_package"],
            push_dir_on_device=ph["push_dir_on_device"],
            ui=ph["ui"],
        ),
        state=StateConfig(db_path=_resolve(root_dir, st["db_path"])),
        logging=LoggingConfig(level=lg["level"], log_dir=_resolve(root_dir, lg["log_dir"])),
    )
