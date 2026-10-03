"""Command-line entry point: `shopee-auto <command>`."""

from __future__ import annotations

from pathlib import Path

import click

from shopee_auto.config import DEFAULT_CONFIG_PATH, load_config
from shopee_auto.logger import get_logger, setup_logging
from shopee_auto.pipeline import Pipeline

log = get_logger("cli")


@click.group()
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    default=DEFAULT_CONFIG_PATH,
    show_default=True,
    help="Path to config.yaml",
)
@click.pass_context
def main(ctx: click.Context, config_path: Path) -> None:
    cfg = load_config(config_path)
    setup_logging(cfg.logging)
    ctx.obj = cfg


@main.command("import-csv")
@click.pass_obj
def import_csv(cfg) -> None:
    """Scan product_source.input_dir for CSV files not yet imported and add
    their products to the durable queue (dedup against anything already
    queued/posted before). run-daily/run-once already do this automatically
    -- use this command to check what a newly-dropped file did, on its own."""
    pipeline = Pipeline(cfg)
    try:
        files, added, duplicates = pipeline.import_new_csvs()
    finally:
        pipeline.close()
    if files == 0:
        click.echo("Không có file CSV mới trong thư mục input_dir.")
    else:
        click.echo(
            f"Đã quét {files} file mới: nạp {added} sản phẩm ({duplicates} trùng/đã có, bỏ qua)."
        )


@main.command()
@click.option("--limit", type=int, default=None, help="Chỉ chuẩn bị tối đa N sản phẩm (mặc định: hết hàng đợi)")
@click.pass_obj
def prepare(cfg, limit: int | None) -> None:
    """Giai đoạn 1: quét CSV mới, tìm clip TikTok khớp ảnh và tải về cho
    từng sản phẩm đang chờ -- không đụng tới điện thoại."""
    pipeline = Pipeline(cfg)
    try:
        results = pipeline.prepare_videos(limit=limit)
    finally:
        pipeline.close()
    if not results:
        click.echo("Không có sản phẩm nào để chuẩn bị.")
        return
    for r in results:
        click.echo(f"[{r.status.upper():6}] {r.product.name} — {r.detail}")
    ready = sum(1 for r in results if r.status == "ready")
    click.echo(f"\nTổng kết: {ready}/{len(results)} video đã sẵn sàng để đăng.")


@main.command()
@click.option("--limit", type=int, default=None, help="Chỉ đăng tối đa N video (mặc định: phần còn lại của daily_target)")
@click.pass_obj
def post(cfg, limit: int | None) -> None:
    """Giai đoạn 2: đăng các video đã chuẩn bị sẵn lên điện thoại, gắn giỏ
    hàng, xác nhận -- không tìm/tải clip mới."""
    pipeline = Pipeline(cfg)
    try:
        results = pipeline.post_ready(max_videos=limit)
    finally:
        pipeline.close()
    _print_summary(results)


@main.command()
@click.pass_obj
def run_daily(cfg) -> None:
    """Chạy cả 2 giai đoạn liên tiếp (dùng cho cron/chạy không giám sát):
    quét input_dir cho CSV mới, chuẩn bị video, rồi đăng tối đa
    `daily_target` video."""
    pipeline = Pipeline(cfg)
    try:
        results = pipeline.run_daily()
    finally:
        pipeline.close()
    _print_summary(results)


@main.command()
@click.pass_obj
def run_once(cfg) -> None:
    """Post exactly one video, ignoring the rest of the daily target."""
    pipeline = Pipeline(cfg)
    try:
        results = pipeline.run_daily(max_videos=1)
    finally:
        pipeline.close()
    _print_summary(results)


@main.command()
@click.pass_obj
def check_setup(cfg) -> None:
    """Verify Playwright, ADB/uiautomator2, and yt-dlp are all reachable
    before you rely on the unattended daily loop."""
    from shopee_auto.checks import run_checks

    results = run_checks(cfg)
    for r in results:
        click.echo(f"[{'OK' if r.ok else 'FAIL'}] {r.message}")

    if all(r.ok for r in results):
        click.echo("Tất cả kiểm tra cơ bản đều PASS. Vẫn nên chạy `run-once` trước khi bật vòng lặp hàng ngày.")
    else:
        raise SystemExit(1)


@main.command()
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8787, show_default=True, type=int)
@click.pass_obj
def web(cfg, host: str, port: int) -> None:
    """Mở dashboard điều khiển pipeline tại http://host:port (mặc định
    http://127.0.0.1:8787) -- xem README "Dashboard"."""
    from shopee_auto.web import create_app

    app = create_app(cfg)
    click.echo(f"Dashboard: http://{host}:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)


def _print_summary(results) -> None:
    if not results:
        click.echo("Không có sản phẩm nào được xử lý trong lượt chạy này.")
        return
    for r in results:
        click.echo(f"[{r.status.upper():8}] {r.product.name} — {r.detail}")
    success = sum(1 for r in results if r.status == "success")
    click.echo(f"\nTổng kết: {success}/{len(results)} video đăng thành công.")


if __name__ == "__main__":
    main()
