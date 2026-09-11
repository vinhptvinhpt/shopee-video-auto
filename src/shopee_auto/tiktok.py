"""Rank candidate TikTok video URLs by view count and download the winner.

yt-dlp pulls TikTok's own play-address API rather than scraping the web
player, so the file it saves has no watermark burned in -- there is no
separate "remove watermark" step needed.
"""

from __future__ import annotations

import dataclasses
import time
from pathlib import Path

import yt_dlp

from shopee_auto.config import TikTokConfig
from shopee_auto.logger import get_logger

log = get_logger("tiktok")


@dataclasses.dataclass
class TikTokVideo:
    url: str
    views: int


def get_video_stats(urls: list[str], request_delay_seconds: float) -> list[TikTokVideo]:
    videos: list[TikTokVideo] = []
    ydl_opts = {"quiet": True, "skip_download": True, "no_warnings": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for url in urls:
            try:
                info = ydl.extract_info(url, download=False)
                views = int(info.get("view_count") or 0)
                videos.append(TikTokVideo(url=url, views=views))
            except Exception as exc:  # noqa: BLE001 - a single bad URL shouldn't abort the batch
                log.warning("Không lấy được thông tin video %s: %s", url, exc)
            time.sleep(request_delay_seconds)
    return videos


def pick_best(videos: list[TikTokVideo], cfg: TikTokConfig) -> TikTokVideo | None:
    eligible = [v for v in videos[: cfg.max_candidates] if v.views >= cfg.min_views]
    if not eligible:
        log.info("Không có video nào đạt ngưỡng %d lượt xem", cfg.min_views)
        return None
    best = max(eligible, key=lambda v: v.views)
    log.info("Chọn video %s (%d lượt xem)", best.url, best.views)
    return best


def download(video: TikTokVideo, download_dir: Path) -> Path:
    download_dir.mkdir(parents=True, exist_ok=True)
    ydl_opts = {
        "outtmpl": str(download_dir / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "format": "best",
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video.url, download=True)
        path = Path(ydl.prepare_filename(info))
    log.info("Đã tải video về %s", path)
    return path
