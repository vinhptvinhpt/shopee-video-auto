"""Local dashboard: a small Flask app you run on your own laptop
(`shopee-auto web`) to drive the pipeline from a browser instead of the
CLI. It has no auth and binds to 127.0.0.1 by default -- it's a control
panel for the machine it runs on (your phone over ADB, your browser
sessions), not a hosted service.

Only one pipeline job (prepare/post/scan) runs at a time, tracked in
`_JOB` (in-process, single-worker state -- fine for a single-user local
tool). Each job attaches a logging handler to the `shopee_auto` logger
for its duration so the dashboard can show a live-ish log by polling
/api/job, without changing how any pipeline module logs.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from shopee_auto.checks import run_checks
from shopee_auto.config import AppConfig
from shopee_auto.pipeline import Pipeline

_STATIC_DIR = Path(__file__).resolve().parent / "static"


@dataclasses.dataclass
class JobState:
    kind: str | None = None  # "prepare" | "post" | "scan"
    running: bool = False
    started_at: float | None = None
    finished_at: float | None = None
    log_lines: list[str] = dataclasses.field(default_factory=list)
    summary: dict | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "running": self.running,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "log_lines": self.log_lines[-500:],
            "summary": self.summary,
            "error": self.error,
        }


class _LogCapture(logging.Handler):
    def __init__(self, sink: list[str]):
        super().__init__()
        self._sink = sink
        self.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        self._sink.append(self.format(record))


class JobRunner:
    """Runs at most one pipeline job at a time in a background thread."""

    def __init__(self, cfg: AppConfig):
        self._cfg = cfg
        self._lock = threading.Lock()
        self._job = JobState()

    def status(self) -> dict:
        return self._job.to_dict()

    def start(self, kind: str, target) -> bool:
        """Returns False without starting anything if a job is already
        running."""
        if self._job.running:
            return False
        self._job = JobState(kind=kind, running=True, started_at=time.time())
        thread = threading.Thread(target=self._run, args=(target,), daemon=True)
        thread.start()
        return True

    def _run(self, target) -> None:
        job = self._job
        pipeline_logger = logging.getLogger("shopee_auto")
        handler = _LogCapture(job.log_lines)
        pipeline_logger.addHandler(handler)
        try:
            job.summary = target()
        except Exception as exc:  # noqa: BLE001 - surface to the dashboard instead of crashing the thread
            job.error = str(exc)
        finally:
            pipeline_logger.removeHandler(handler)
            job.running = False
            job.finished_at = time.time()


def create_app(cfg: AppConfig) -> Flask:
    app = Flask(__name__, static_folder=None)
    runner = JobRunner(cfg)

    def pipeline() -> Pipeline:
        return Pipeline(cfg)

    @app.get("/")
    def index():
        return send_from_directory(_STATIC_DIR, "index.html")

    @app.get("/api/status")
    def status():
        p = pipeline()
        try:
            counts = p.state.count_by_status()
            posted_today = p.state.count_posted_today()
        finally:
            p.close()
        return jsonify(
            {
                "counts": counts,
                "daily_target": cfg.daily_target,
                "posted_today": posted_today,
                "remaining_today": max(0, cfg.daily_target - posted_today),
                "job": runner.status(),
            }
        )

    @app.get("/api/job")
    def job():
        return jsonify(runner.status())

    @app.post("/api/scan")
    def scan():
        def task():
            p = pipeline()
            try:
                files, added, dup = p.import_new_csvs()
            finally:
                p.close()
            return {"files_imported": files, "products_added": added, "products_duplicate": dup}

        if not runner.start("scan", task):
            return jsonify({"error": "Đang có tác vụ khác chạy, đợi xong đã."}), 409
        return jsonify({"started": True})

    @app.post("/api/prepare")
    def prepare():
        body = request.get_json(silent=True) or {}
        limit = body.get("limit")
        links = body.get("links")  # dashboard checkbox selection, overrides limit when given

        def task():
            p = pipeline()
            try:
                results = p.prepare_videos(limit=limit, links=links)
            finally:
                p.close()
            ready = sum(1 for r in results if r.status == "ready")
            return {
                "total": len(results),
                "ready": ready,
                "failed": len(results) - ready,
                "items": [{"name": r.product.name, "status": r.status, "detail": r.detail} for r in results],
            }

        if not runner.start("prepare", task):
            return jsonify({"error": "Đang có tác vụ khác chạy, đợi xong đã."}), 409
        return jsonify({"started": True})

    @app.post("/api/post")
    def post():
        body = request.get_json(silent=True) or {}
        limit = body.get("limit")
        links = body.get("links")  # dashboard checkbox selection, overrides limit when given

        def task():
            p = pipeline()
            try:
                results = p.post_ready(max_videos=limit, links=links)
            finally:
                p.close()
            success = sum(1 for r in results if r.status == "success")
            return {
                "total": len(results),
                "success": success,
                "failed": len(results) - success,
                "items": [{"name": r.product.name, "status": r.status, "detail": r.detail} for r in results],
            }

        if not runner.start("post", task):
            return jsonify({"error": "Đang có tác vụ khác chạy, đợi xong đã."}), 409
        return jsonify({"started": True})

    @app.get("/api/queue")
    def queue():
        status_filter = request.args.get("status") or None
        limit = int(request.args.get("limit", 100))
        p = pipeline()
        try:
            items = p.state.list_queue(status=status_filter, limit=limit)
        finally:
            p.close()
        return jsonify(
            [
                {
                    "name": it.product.name,
                    "link": it.product.link,
                    "product_url": it.product.product_url,
                    "sales_count": it.product.sales_count,
                    "status": it.status,
                    "video_path": it.video_path,
                    "tiktok_source_url": it.tiktok_source_url,
                    "queued_at": it.queued_at,
                }
                for it in items
            ]
        )

    @app.post("/api/queue/skip")
    def skip_item():
        link = (request.get_json(silent=True) or {}).get("link")
        if not link:
            return jsonify({"error": "thiếu 'link'"}), 400
        p = pipeline()
        try:
            ok = p.state.skip_queue_item(link)
        finally:
            p.close()
        return jsonify({"ok": ok})

    @app.post("/api/queue/requeue")
    def requeue_item():
        link = (request.get_json(silent=True) or {}).get("link")
        if not link:
            return jsonify({"error": "thiếu 'link'"}), 400
        p = pipeline()
        try:
            ok = p.state.requeue_item(link)
        finally:
            p.close()
        return jsonify({"ok": ok})

    @app.get("/api/logs")
    def logs():
        limit = int(request.args.get("limit", 200))
        p = pipeline()
        try:
            entries = p.state.recent_log(limit)
        finally:
            p.close()
        return jsonify(entries)

    @app.get("/api/config")
    def config_view():
        return jsonify(
            {
                "daily_target": cfg.daily_target,
                "product_source": {
                    "input_dir": str(cfg.product_source.input_dir),
                    "min_sales": cfg.product_source.min_sales,
                },
                "tiktok": {
                    "min_views": cfg.tiktok.min_views,
                    "max_candidates": cfg.tiktok.max_candidates,
                    "download_dir": str(cfg.tiktok.download_dir),
                },
                "phone": {
                    "adb_serial": cfg.phone.adb_serial,
                    "shopee_app_package": cfg.phone.shopee_app_package,
                    "ui_selectors_filled": sum(1 for v in cfg.phone.ui.values() if v),
                    "ui_selectors_total": len(cfg.phone.ui),
                },
            }
        )

    @app.get("/api/check")
    def check():
        results = run_checks(cfg)
        return jsonify([{"ok": r.ok, "message": r.message} for r in results])

    return app
