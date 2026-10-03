"""Two independent phases, matching the dashboard's two buttons:

- `prepare_videos`: pull pending products off the queue, find a matching
  TikTok clip, download it (no watermark). Never touches the phone.
- `post_ready`: pull products with a video already prepared, drive the
  phone to publish + attach the cart, verify. Never touches TikTok/Lens.

`run_daily`/`run_once` (used by the CLI/cron) are a thin convenience
wrapper that runs both phases back to back for unattended use; the
dashboard calls them separately so video prep (no phone needed) and
posting (phone needed, physically watched) can happen at different times.

Every product is isolated in a try/except so one bad product (captcha,
missing UI element, no matching clip) never aborts the rest of a batch.
Every run first scans `product_source.input_dir` for CSV exports it hasn't
seen before (by content hash) and enqueues whatever's in them -- you just
drop exported files in that folder, nothing else to run by hand.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from shopee_auto.config import AppConfig
from shopee_auto.image_search import CaptchaEncounteredError, GoogleLensSearch
from shopee_auto.logger import get_logger
from shopee_auto.phone_control import PhoneAutomationError, PhoneController
from shopee_auto.product_source import Product
from shopee_auto import product_source
from shopee_auto.state import QueueItem, StateStore
from shopee_auto import tiktok

log = get_logger("pipeline")


@dataclasses.dataclass
class PrepareResult:
    product: Product
    status: str  # "ready" | "failed"
    detail: str = ""


@dataclasses.dataclass
class CycleResult:
    product: Product
    status: str  # "success" | "failed"
    detail: str = ""


class Pipeline:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self.state = StateStore(cfg.state.db_path)

    def close(self) -> None:
        self.state.close()

    def import_new_csvs(self) -> tuple[int, int, int]:
        """Scan product_source.input_dir for CSV files not yet imported (by
        content hash), enqueue their products (deduped against the queue and
        against each other), and record each file as imported so it's never
        re-read. Returns (files_imported, products_added, products_duplicate).
        """
        files_imported = products_added = products_duplicate = 0
        for path in product_source.list_csv_files(self.cfg.product_source.input_dir):
            fingerprint = product_source.file_fingerprint(path)
            if self.state.is_file_imported(fingerprint):
                continue
            products = product_source.load_products_from_file(path, self.cfg.product_source.min_sales)
            added, duplicates = self.state.enqueue_products(products)
            self.state.mark_file_imported(fingerprint, path.name, added)
            files_imported += 1
            products_added += added
            products_duplicate += duplicates
            log.info("Đã nạp %s: %d sản phẩm mới, %d trùng", path.name, added, duplicates)
        return files_imported, products_added, products_duplicate

    # -- phase 1: find + download a TikTok clip for each pending product --

    def prepare_videos(self, limit: int | None = None) -> list[PrepareResult]:
        self.import_new_csvs()
        candidates = self.state.get_pending_for_prepare(limit)
        if not candidates:
            log.info("Không có sản phẩm nào đang chờ chuẩn bị video.")
            return []
        log.info("Chuẩn bị video cho %d sản phẩm", len(candidates))

        results: list[PrepareResult] = []
        with GoogleLensSearch(self.cfg.image_search) as lens:
            for product in candidates:
                results.append(self._prepare_one(product, lens))

        ready = sum(1 for r in results if r.status == "ready")
        log.info("Hoàn tất chuẩn bị: %d/%d sản phẩm có video sẵn sàng", ready, len(results))
        return results

    def _prepare_one(self, product: Product, lens: GoogleLensSearch) -> PrepareResult:
        log.info("=== Chuẩn bị video cho: %s ===", product.name)
        try:
            thumb_path = product_source.download_thumbnail(
                product, self.cfg.tiktok.download_dir / "thumbnails"
            )
            self.state.log_stage(product.link, "thumbnail", "success")

            search_urls = lens.search_by_image(thumb_path)
            tiktok_urls = lens.filter_tiktok_video_links(search_urls)
            if not tiktok_urls:
                self.state.log_stage(product.link, "image_search", "failed", "no tiktok links found")
                self.state.mark_prepare_failed(product.link)
                return PrepareResult(product, "failed", "Không tìm được video TikTok trùng ảnh")

            videos = tiktok.get_video_stats(tiktok_urls, self.cfg.tiktok.request_delay_seconds)
            best = tiktok.pick_best(videos, self.cfg.tiktok)
            if best is None:
                self.state.log_stage(product.link, "tiktok_pick", "failed", "no video above min_views")
                self.state.mark_prepare_failed(product.link)
                return PrepareResult(product, "failed", "Không có video đạt ngưỡng lượt xem")

            video_path = tiktok.download(best, self.cfg.tiktok.download_dir)
            self.state.log_stage(product.link, "tiktok_download", "success", best.url)
            self.state.mark_video_ready(product.link, str(video_path), best.url)
            return PrepareResult(product, "ready", best.url)

        except CaptchaEncounteredError as exc:
            log.warning("Bị chặn captcha khi tìm ảnh cho %s: %s", product.name, exc)
            self.state.log_stage(product.link, "image_search", "captcha", str(exc))
            self.state.mark_prepare_failed(product.link)
            return PrepareResult(product, "failed", str(exc))
        except Exception as exc:  # noqa: BLE001 - isolate failure to this product, keep the batch going
            log.exception("Lỗi không xác định khi chuẩn bị %s", product.name)
            self.state.log_stage(product.link, "unknown", "failed", str(exc))
            self.state.mark_prepare_failed(product.link)
            return PrepareResult(product, "failed", str(exc))

    # -- phase 2: publish already-prepared videos on the phone -----------

    def post_ready(self, max_videos: int | None = None) -> list[CycleResult]:
        remaining = self.cfg.daily_target - self.state.count_posted_today()
        if max_videos is not None:
            remaining = min(remaining, max_videos)
        if remaining <= 0:
            log.info("Đã đạt chỉ tiêu %d video hôm nay, dừng.", self.cfg.daily_target)
            return []

        candidates = self.state.get_ready_to_post(remaining)
        if not candidates:
            log.info("Không có video nào sẵn sàng để đăng -- chạy \"Tìm & tải video\" trước.")
            return []
        log.info("Đăng %d video đã chuẩn bị (cần đăng thêm %d video)", len(candidates), remaining)

        results: list[CycleResult] = []
        with PhoneController(self.cfg.phone) as phone:
            phone.check_ready()
            for item in candidates:
                results.append(self._post_one(item, phone))

        posted = sum(1 for r in results if r.status == "success")
        log.info("Hoàn tất đợt đăng: %d/%d video đăng thành công", posted, len(results))
        return results

    def _post_one(self, item: QueueItem, phone: PhoneController) -> CycleResult:
        product = item.product
        log.info("=== Đăng bài: %s ===", product.name)
        try:
            phone.push_video(Path(item.video_path))
            phone.open_shopee_app()
            phone.publish_video(caption=product.name, product_query=product.name)
            posted_ok = phone.verify_last_post()

            status = "success" if posted_ok else "failed"
            detail = item.tiktok_source_url if posted_ok else "verify_last_post trả về False (chưa thấy giỏ hàng)"
            self.state.mark_posted(product.link, "posted" if posted_ok else "post_failed")
            self.state.log_stage(product.link, "publish", status, detail or "")
            return CycleResult(product, status, detail or "")

        except PhoneAutomationError as exc:
            log.error("Lỗi tự động hóa điện thoại cho %s: %s", product.name, exc)
            self.state.mark_posted(product.link, "post_failed")
            self.state.log_stage(product.link, "publish", "failed", str(exc))
            return CycleResult(product, "failed", str(exc))
        except Exception as exc:  # noqa: BLE001 - isolate failure to this product, keep the batch going
            log.exception("Lỗi không xác định khi đăng %s", product.name)
            self.state.mark_posted(product.link, "post_failed")
            self.state.log_stage(product.link, "unknown", "failed", str(exc))
            return CycleResult(product, "failed", str(exc))

    # -- CLI/cron convenience: both phases back to back -------------------

    def run_daily(self, max_videos: int | None = None) -> list[CycleResult]:
        remaining = self.cfg.daily_target - self.state.count_posted_today()
        if max_videos is not None:
            remaining = min(remaining, max_videos)
        if remaining <= 0:
            log.info("Đã đạt chỉ tiêu %d video hôm nay, dừng.", self.cfg.daily_target)
            return []
        self.prepare_videos(limit=remaining)
        return self.post_ready(max_videos=remaining)
