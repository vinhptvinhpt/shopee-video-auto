"""Drive the Shopee Android app over ADB (via uiautomator2) to publish a
video and attach a product's cart link, then verify the post went live.

Every screen-specific locator comes from `config.phone.ui` as a dict of
uiautomator2 selector kwargs (e.g. `{"resourceId": "com.shopee.vn:id/foo"}`
or `{"text": "Đăng"}`). Shopee's app changes its resource IDs across
releases and we have no device attached in this environment to verify them,
so an empty/unfilled selector raises `PhoneAutomationError` immediately
instead of silently mis-clicking. See README "Calibrating selectors" for how
to capture real ones with `python -m uiautomator2` / weditor against your
own phone.
"""

from __future__ import annotations

import time
from pathlib import Path

import uiautomator2 as u2

from shopee_auto.config import PhoneConfig
from shopee_auto.logger import get_logger

log = get_logger("phone_control")

DEFAULT_TIMEOUT = 15


class PhoneAutomationError(RuntimeError):
    pass


class PhoneController:
    def __init__(self, cfg: PhoneConfig):
        self._cfg = cfg
        self._d: u2.Device | None = None

    def __enter__(self) -> "PhoneController":
        self._d = u2.connect(self._cfg.adb_serial) if self._cfg.adb_serial else u2.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        pass  # uiautomator2 keeps no persistent connection to close

    def check_ready(self) -> None:
        info = self._d.info
        log.info("Đã kết nối điện thoại: %s", info.get("productName", "unknown"))

    def push_video(self, local_path: Path) -> str:
        remote_path = f"{self._cfg.push_dir_on_device}/{local_path.name}"
        self._d.push(str(local_path), remote_path)
        log.info("Đã copy %s sang điện thoại tại %s", local_path, remote_path)
        return remote_path

    def open_shopee_app(self) -> None:
        self._d.app_start(self._cfg.shopee_app_package)
        time.sleep(3)

    def publish_video(self, caption: str, product_query: str) -> None:
        self._tap("open_shop_video_entry")
        self._tap("select_video_from_gallery")
        self._set_text("caption_input", caption)
        self._tap("attach_product_button")
        self._set_text("product_search_input", product_query)
        self._tap("product_search_result_item")
        self._tap("publish_button")
        time.sleep(5)
        log.info("Đã bấm đăng bài với caption: %s", caption)

    def verify_last_post(self) -> bool:
        self._tap("my_videos_tab")
        if not self._wait_exists("latest_video_item"):
            log.warning("Không thấy video mới nhất trong danh sách của tôi")
            return False
        has_cart = self._exists("cart_icon_on_video")
        log.info("Video mới nhất đã đăng, gắn giỏ hàng: %s", has_cart)
        return has_cart

    # -- selector helpers ------------------------------------------------

    def _spec(self, name: str) -> dict:
        spec = self._cfg.ui.get(name) or {}
        if not spec:
            raise PhoneAutomationError(
                f"Selector '{name}' chưa được cấu hình trong config.phone.ui. "
                "Xem README mục Calibrating selectors."
            )
        return spec

    def _element(self, name: str):
        return self._d(**self._spec(name))

    def _wait_exists(self, name: str, timeout: float = DEFAULT_TIMEOUT) -> bool:
        return self._element(name).wait(timeout=timeout)

    def _exists(self, name: str) -> bool:
        return self._element(name).exists

    def _tap(self, name: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        el = self._element(name)
        if not el.wait(timeout=timeout):
            raise PhoneAutomationError(f"Không tìm thấy phần tử '{name}' sau {timeout}s")
        el.click()

    def _set_text(self, name: str, text: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        el = self._element(name)
        if not el.wait(timeout=timeout):
            raise PhoneAutomationError(f"Không tìm thấy phần tử '{name}' sau {timeout}s")
        el.set_text(text)
