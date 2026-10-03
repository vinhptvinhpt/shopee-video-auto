"""Environment checks shared by the CLI's `check-setup` and the dashboard's
"Kiểm tra môi trường" panel, so the two never drift out of sync."""

from __future__ import annotations

import dataclasses

from shopee_auto.config import AppConfig


@dataclasses.dataclass
class CheckResult:
    ok: bool
    message: str


def run_checks(cfg: AppConfig) -> list[CheckResult]:
    results: list[CheckResult] = []

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        results.append(CheckResult(True, "Playwright/Chromium hoạt động"))
    except Exception as exc:  # noqa: BLE001
        results.append(CheckResult(False, f"Playwright: {exc}"))

    try:
        import uiautomator2 as u2

        d = u2.connect(cfg.phone.adb_serial) if cfg.phone.adb_serial else u2.connect()
        info = d.info
        results.append(CheckResult(True, f"Điện thoại kết nối: {info.get('productName', 'unknown')}"))
    except Exception as exc:  # noqa: BLE001
        results.append(CheckResult(False, f"ADB/uiautomator2: {exc}"))

    try:
        import yt_dlp  # noqa: F401

        results.append(CheckResult(True, "yt-dlp import thành công"))
    except Exception as exc:  # noqa: BLE001
        results.append(CheckResult(False, f"yt-dlp: {exc}"))

    try:
        from shopee_auto import product_source
        from shopee_auto.state import StateStore

        with StateStore(cfg.state.db_path) as s:
            counts = s.count_by_status()
        pending = counts.get("pending", 0)
        ready = counts.get("video_ready", 0)
        csv_files = product_source.list_csv_files(cfg.product_source.input_dir)
        if pending or ready:
            results.append(
                CheckResult(True, f"Hàng đợi: {pending} chờ chuẩn bị, {ready} đã có video sẵn sàng đăng")
            )
        elif csv_files:
            results.append(
                CheckResult(
                    True,
                    f"Hàng đợi rỗng nhưng thấy {len(csv_files)} file CSV trong "
                    f"{cfg.product_source.input_dir}",
                )
            )
        else:
            results.append(
                CheckResult(
                    False,
                    f"Hàng đợi rỗng và không có file CSV nào trong {cfg.product_source.input_dir}",
                )
            )
    except Exception as exc:  # noqa: BLE001
        results.append(CheckResult(False, f"Không đọc được state DB: {exc}"))

    if cfg.phone.ui and any(cfg.phone.ui.values()):
        results.append(CheckResult(True, "config.phone.ui đã điền selector"))
    else:
        results.append(CheckResult(False, "config.phone.ui chưa có selector nào được điền"))

    if cfg.image_search.selectors.get("result_link"):
        results.append(CheckResult(True, "config.image_search.selectors.result_link đã điền"))
    else:
        results.append(CheckResult(False, "config.image_search.selectors.result_link chưa được điền"))

    return results
