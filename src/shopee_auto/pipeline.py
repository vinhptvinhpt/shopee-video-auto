"""Orchestrate one full product cycle (read product from CSV -> match TikTok
clip -> publish on Shopee -> verify) and the daily loop over the configured
target. Every product is isolated in a try/except so one bad product
(captcha, missing UI element, no matching clip) never aborts the rest of the
day's run.
"""

from __future__ import annotations

import dataclasses

from shopee_auto.config import AppConfig
from shopee_auto.image_search import CaptchaEncounteredError, GoogleLensSearch
from shopee_auto.logger import get_logger
from shopee_auto.phone_control import PhoneAutomationError, PhoneController
from shopee_auto.product_source import Product
from shopee_auto import product_source
from shopee_auto.state import StateStore
from shopee_auto import tiktok

log = get_logger("pipeline")


@dataclasses.dataclass
class CycleResult:
    product: Product
    status: str  # "success" | "skipped" | "failed"
    detail: str = ""


class Pipeline:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self.state = StateStore(cfg.state.db_path)

    def close(self) -> None:
        self.state.close()

    def run_daily(self, max_videos: int | None = None) -> list[CycleResult]:
        remaining = self.cfg.daily_target - self.state.count_posted_today()
        if max_videos is not None:
            remaining = min(remaining, max_videos)
        if remaining <= 0:
            log.info("Đã đạt chỉ tiêu %d video hôm nay, dừng.", self.cfg.daily_target)
            return []

        products = product_source.load_products(self.cfg.product_source)
        candidates = [p for p in products if not self.state.is_product_posted(p.link)]
        log.info(
            "%d/%d sản phẩm chưa đăng, cần đăng thêm %d video",
            len(candidates),
            len(products),
            remaining,
        )

        results: list[CycleResult] = []
        with GoogleLensSearch(self.cfg.image_search) as lens, PhoneController(self.cfg.phone) as phone:
            phone.check_ready()
            for product in candidates:
                if len(results) >= remaining:
                    break
                results.append(self._run_one(product, lens, phone))

        posted = sum(1 for r in results if r.status == "success")
        log.info("Hoàn tất đợt chạy: %d/%d video đăng thành công", posted, len(results))
        return results

    def _run_one(
        self,
        product: Product,
        lens: GoogleLensSearch,
        phone: PhoneController,
    ) -> CycleResult:
        log.info("=== Xử lý sản phẩm: %s ===", product.name)
        try:
            thumb_path = product_source.download_thumbnail(
                product, self.cfg.tiktok.download_dir / "thumbnails"
            )
            self.state.log_stage(product.link, "thumbnail", "success")

            search_urls = lens.search_by_image(thumb_path)
            tiktok_urls = lens.filter_tiktok_video_links(search_urls)
            if not tiktok_urls:
                self.state.log_stage(product.link, "image_search", "failed", "no tiktok links found")
                return CycleResult(product, "skipped", "Không tìm được video TikTok trùng ảnh")

            videos = tiktok.get_video_stats(tiktok_urls, self.cfg.tiktok.request_delay_seconds)
            best = tiktok.pick_best(videos, self.cfg.tiktok)
            if best is None:
                self.state.log_stage(product.link, "tiktok_pick", "failed", "no video above min_views")
                return CycleResult(product, "skipped", "Không có video đạt ngưỡng lượt xem")

            video_path = tiktok.download(best, self.cfg.tiktok.download_dir)
            self.state.log_stage(product.link, "tiktok_download", "success", best.url)

            phone.push_video(video_path)
            phone.open_shopee_app()
            phone.publish_video(caption=product.name, product_query=product.name)
            posted_ok = phone.verify_last_post()

            status = "success" if posted_ok else "failed"
            detail = best.url if posted_ok else "verify_last_post trả về False (chưa thấy giỏ hàng)"
            self.state.mark_product_posted(product.link, product.name, best.url, status)
            self.state.log_stage(product.link, "publish", status, detail)
            return CycleResult(product, status, detail)

        except CaptchaEncounteredError as exc:
            log.warning("Bị chặn captcha khi tìm ảnh cho %s: %s", product.name, exc)
            self.state.log_stage(product.link, "image_search", "captcha", str(exc))
            return CycleResult(product, "skipped", str(exc))
        except PhoneAutomationError as exc:
            log.error("Lỗi tự động hóa điện thoại cho %s: %s", product.name, exc)
            self.state.mark_product_posted(product.link, product.name, None, "failed")
            self.state.log_stage(product.link, "publish", "failed", str(exc))
            return CycleResult(product, "failed", str(exc))
        except Exception as exc:  # noqa: BLE001 - isolate failure to this product, keep the run going
            log.exception("Lỗi không xác định khi xử lý %s", product.name)
            self.state.log_stage(product.link, "unknown", "failed", str(exc))
            return CycleResult(product, "failed", str(exc))
