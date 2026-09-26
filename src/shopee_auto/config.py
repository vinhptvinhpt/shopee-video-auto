"""Load and validate config/config.yaml into typed dataclasses."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"


@dataclasses.dataclass
class BestsellerFilter:
    sort_by: str
    min_sales: int
    category: str | None


@dataclasses.dataclass
class ShopeeAffiliateConfig:
    portal_url: str
    browser_profile_dir: Path
    headless: bool
    channel: str | None
    bestseller_filter: BestsellerFilter
    max_candidates_per_run: int
    selectors: dict[str, str]


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
    shopee_affiliate: ShopeeAffiliateConfig
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

    sa = raw["shopee_affiliate"]
    ims = raw["image_search"]
    tk = raw["tiktok"]
    ph = raw["phone"]
    st = raw["state"]
    lg = raw["logging"]

    return AppConfig(
        daily_target=raw["daily_target"],
        root_dir=root_dir,
        shopee_affiliate=ShopeeAffiliateConfig(
            portal_url=sa["portal_url"],
            browser_profile_dir=_resolve(root_dir, sa["browser_profile_dir"]),
            headless=sa["headless"],
            channel=sa.get("channel"),
            bestseller_filter=BestsellerFilter(**sa["bestseller_filter"]),
            max_candidates_per_run=sa["max_candidates_per_run"],
            selectors=sa["selectors"],
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
