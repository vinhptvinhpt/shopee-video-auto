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


def test_new_product_not_posted(store: StateStore) -> None:
    assert store.is_product_posted("https://shopee.vn/product/1") is False


def test_mark_and_check_posted(store: StateStore) -> None:
    store.mark_product_posted("https://shopee.vn/p/1", "Áo thun", "https://tiktok.com/x", "success")
    assert store.is_product_posted("https://shopee.vn/p/1") is True


def test_failed_status_does_not_count_as_posted(store: StateStore) -> None:
    store.mark_product_posted("https://shopee.vn/p/2", "Quần jean", None, "failed")
    assert store.is_product_posted("https://shopee.vn/p/2") is False


def test_count_posted_today_only_counts_success(store: StateStore) -> None:
    store.mark_product_posted("https://shopee.vn/p/3", "A", "u1", "success")
    store.mark_product_posted("https://shopee.vn/p/4", "B", "u2", "failed")
    store.mark_product_posted("https://shopee.vn/p/5", "C", "u3", "success")
    assert store.count_posted_today() == 2


def test_mark_product_posted_upserts(store: StateStore) -> None:
    store.mark_product_posted("https://shopee.vn/p/6", "D", None, "failed")
    assert store.is_product_posted("https://shopee.vn/p/6") is False
    store.mark_product_posted("https://shopee.vn/p/6", "D", "u4", "success")
    assert store.is_product_posted("https://shopee.vn/p/6") is True
    assert store.count_posted_today() == 1


def test_log_stage_does_not_raise(store: StateStore) -> None:
    store.log_stage("https://shopee.vn/p/7", "thumbnail", "success")
    store.log_stage(None, "startup", "success", "pipeline booted")


def _products(n: int) -> list[Product]:
    return [
        Product(name=f"Product {i}", link=f"https://s.shopee.vn/p{i}", product_url=f"https://shopee.vn/product/{i}")
        for i in range(n)
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

    day1 = store.get_pending_queue(5)
    assert len(day1) == 5
    for p in day1:
        store.mark_queue_status(p.link, "success")

    day2 = store.get_pending_queue(5)
    assert len(day2) == 5
    assert {p.link for p in day1}.isdisjoint({p.link for p in day2})
    for p in day2:
        store.mark_queue_status(p.link, "success")
    assert store.count_pending_queue() == 2


def test_failed_queue_item_leaves_pending_pool(store: StateStore) -> None:
    store.enqueue_products(_products(1))
    [p] = store.get_pending_queue(1)
    store.mark_queue_status(p.link, "failed")
    assert store.count_pending_queue() == 0
