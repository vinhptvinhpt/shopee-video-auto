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
@click.pass_obj
def run_daily(cfg) -> None:
    """Scan input_dir for new CSVs, then post up to `daily_target` videos
    pulling from the queue."""
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
    ok = True

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        click.echo("[OK] Playwright/Chromium hoạt động")
    except Exception as exc:  # noqa: BLE001
        ok = False
        click.echo(f"[FAIL] Playwright: {exc}")

    try:
        import uiautomator2 as u2

        d = u2.connect(cfg.phone.adb_serial) if cfg.phone.adb_serial else u2.connect()
        info = d.info
        click.echo(f"[OK] Điện thoại kết nối: {info.get('productName', 'unknown')}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        click.echo(f"[FAIL] ADB/uiautomator2: {exc}")

    try:
        import yt_dlp  # noqa: F401

        click.echo("[OK] yt-dlp import thành công")
    except Exception as exc:  # noqa: BLE001
        ok = False
        click.echo(f"[FAIL] yt-dlp: {exc}")

    try:
        from shopee_auto import product_source
        from shopee_auto.state import StateStore

        with StateStore(cfg.state.db_path) as s:
            pending = s.count_pending_queue()
        csv_files = product_source.list_csv_files(cfg.product_source.input_dir)
        if pending > 0:
            click.echo(f"[OK] Hàng đợi còn {pending} sản phẩm chưa đăng")
        elif csv_files:
            click.echo(
                f"[OK] Hàng đợi rỗng nhưng thấy {len(csv_files)} file CSV trong "
                f"{cfg.product_source.input_dir} — run-daily sẽ tự quét và nạp"
            )
        else:
            ok = False
            click.echo(
                f"[FAIL] Hàng đợi rỗng và không có file CSV nào trong {cfg.product_source.input_dir} "
                "— thả file xuất từ \"Lấy link hàng loạt\" vào thư mục này"
            )
    except Exception as exc:  # noqa: BLE001
        ok = False
        click.echo(f"[FAIL] Không đọc được state DB: {exc}")

    if not any(cfg.phone.ui.values()):
        ok = False
        click.echo("[FAIL] config.phone.ui chưa có selector nào được điền")

    if ok:
        click.echo("Tất cả kiểm tra cơ bản đều PASS. Vẫn nên chạy `run-once` trước khi bật vòng lặp hàng ngày.")
    else:
        raise SystemExit(1)


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
