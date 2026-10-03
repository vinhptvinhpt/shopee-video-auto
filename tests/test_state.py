"""Unit tests for state.py -- the only module that needs no browser, phone,
or network access, so it's the part we can actually verify in CI/sandbox.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shopee_auto.product_source import Product
from shopee_auto.state import StateStore


@pytest.fixture
def store(tmp_path: Path) -> StateStore:
    s = StateStore(tmp_path / "state.db")
    yield s
    s.close()


def _products(n: int, start: int = 0) -> list[Product]:
    return [
        Product(name=f"Product {i}", link=f"https://s.shopee.vn/p{i}", product_url=f"https://shopee.vn/product/{i}")
        for i in range(start, start + n)
    ]


def test_enqueue_products_dedupes(store: StateStore) -> None:
    added, dup = store.enqueue_products(_products(5))
    assert (added, dup) == (5, 0)
    assert store.count_pending_queue() == 5

    # importing the same (overlapping) batch again adds nothing
    added2, dup2 = store.enqueue_products(_products(5))
    assert (added2, dup2) == (0, 5)
    assert store.count_pending_queue() == 5


def test_queue_overflow_carries_to_next_pull(store: StateStore) -> None:
    store.enqueue_products(_products(12))

    day1 = store.get_pending_for_prepare(5)
    assert len(day1) == 5
    for p in day1:
        store.mark_video_ready(p.link, f"/tmp/{p.link}.mp4", "https://tiktok.com/x")
        store.mark_posted(p.link, "posted")

    day2 = store.get_pending_for_prepare(5)
    assert len(day2) == 5
    assert {p.link for p in day1}.isdisjoint({p.link for p in day2})
    for p in day2:
        store.mark_video_ready(p.link, f"/tmp/{p.link}.mp4", "https://tiktok.com/x")
        store.mark_posted(p.link, "posted")
    assert store.count_pending_queue() == 2


def test_get_pending_for_prepare_no_limit_returns_all(store: StateStore) -> None:
    store.enqueue_products(_products(7))
    assert len(store.get_pending_for_prepare()) == 7


def test_prepare_then_post_lifecycle(store: StateStore) -> None:
    store.enqueue_products(_products(1))
    [p] = store.get_pending_for_prepare(1)

    store.mark_video_ready(p.link, "/tmp/video.mp4", "https://tiktok.com/@x/video/1")
    assert store.count_by_status() == {"video_ready": 1}

    ready = store.get_ready_to_post(5)
    assert len(ready) == 1
    assert ready[0].video_path == "/tmp/video.mp4"
    assert ready[0].tiktok_source_url == "https://tiktok.com/@x/video/1"

    store.mark_posted(p.link, "posted")
    assert store.count_by_status() == {"posted": 1}
    assert store.count_posted_today() == 1


def test_mark_preparing_is_visible_before_outcome(store: StateStore) -> None:
    store.enqueue_products(_products(1))
    [p] = store.get_pending_for_prepare(1)
    store.mark_preparing(p.link)
    assert store.count_by_status() == {"preparing": 1}
    store.mark_video_ready(p.link, "/tmp/v.mp4", "https://tiktok.com/x")
    assert store.count_by_status() == {"video_ready": 1}


def test_mark_posting_is_visible_before_outcome(store: StateStore) -> None:
    store.enqueue_products(_products(1))
    [p] = store.get_pending_for_prepare(1)
    store.mark_video_ready(p.link, "/tmp/v.mp4", "https://tiktok.com/x")
    store.mark_posting(p.link)
    assert store.count_by_status() == {"posting": 1}
    store.mark_posted(p.link, "posted")
    assert store.count_by_status() == {"posted": 1}


def test_get_queue_items_by_links_filters_by_status_and_selection(store: StateStore) -> None:
    store.enqueue_products(_products(5))
    items = store.get_pending_for_prepare()
    picked_links = [items[0].link, items[2].link]

    selected = store.get_queue_items_by_links(picked_links, "pending")
    assert {i.product.link for i in selected} == set(picked_links)

    # an item not in the required status is silently excluded
    store.mark_prepare_failed(items[0].link)
    selected2 = store.get_queue_items_by_links(picked_links, "pending")
    assert {i.product.link for i in selected2} == {items[2].link}

    # empty selection -> empty result, no error
    assert store.get_queue_items_by_links([], "pending") == []


def test_prepare_failed_item_does_not_become_ready(store: StateStore) -> None:
    store.enqueue_products(_products(1))
    [p] = store.get_pending_for_prepare(1)
    store.mark_prepare_failed(p.link)
    assert store.count_by_status() == {"prepare_failed": 1}
    assert store.get_ready_to_post(5) == []


def test_skip_queue_item(store: StateStore) -> None:
    store.enqueue_products(_products(2))
    [a, b] = store.get_pending_for_prepare(2)
    assert store.skip_queue_item(a.link) is True
    assert store.count_by_status() == {"pending": 1, "skipped": 1}
    # already-skipped items can be skipped again harmlessly (still matches)
    assert store.skip_queue_item(a.link) is True
    # can't skip a nonexistent link
    assert store.skip_queue_item("https://s.shopee.vn/does-not-exist") is False


def test_skip_cannot_undo_posted(store: StateStore) -> None:
    store.enqueue_products(_products(1))
    [p] = store.get_pending_for_prepare(1)
    store.mark_video_ready(p.link, "/tmp/v.mp4", "https://tiktok.com/x")
    store.mark_posted(p.link, "posted")
    assert store.skip_queue_item(p.link) is False
    assert store.count_by_status() == {"posted": 1}


def test_requeue_item_resets_to_pending(store: StateStore) -> None:
    store.enqueue_products(_products(1))
    [p] = store.get_pending_for_prepare(1)
    store.mark_prepare_failed(p.link)
    assert store.requeue_item(p.link) is True
    assert store.count_by_status() == {"pending": 1}
    assert len(store.get_pending_for_prepare()) == 1


def test_list_queue_filters_by_status(store: StateStore) -> None:
    store.enqueue_products(_products(3))
    [a, b, c] = store.get_pending_for_prepare(3)
    store.mark_prepare_failed(a.link)

    pending_rows = store.list_queue(status="pending")
    assert {r.product.link for r in pending_rows} == {b.link, c.link}

    failed_rows = store.list_queue(status="prepare_failed")
    assert len(failed_rows) == 1
    assert failed_rows[0].product.link == a.link


def test_log_stage_and_recent_log(store: StateStore) -> None:
    store.log_stage("https://shopee.vn/p/7", "thumbnail", "success")
    store.log_stage(None, "startup", "success", "pipeline booted")
    entries = store.recent_log(10)
    assert len(entries) == 2
    assert entries[0]["stage"] == "startup"  # most recent first
