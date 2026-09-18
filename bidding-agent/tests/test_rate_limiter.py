"""滑动窗口限流（见 docs/开发文档.md §5.4）。"""

from __future__ import annotations

import threading

from src.rate_limiter import MAX_REQUESTS, WINDOW_SECONDS, SlidingWindowRateLimiter


def test_quota_is_exhausted_exactly_at_the_limit():
    limiter = SlidingWindowRateLimiter()
    assert all(limiter.allow("ip", now=0.0) for _ in range(MAX_REQUESTS))
    assert limiter.allow("ip", now=0.0) is False, "第 31 次必须被拒"


def test_window_slides_so_old_hits_expire():
    limiter = SlidingWindowRateLimiter()
    for _ in range(MAX_REQUESTS):
        limiter.allow("ip", now=0.0)

    assert limiter.allow("ip", now=WINDOW_SECONDS - 1) is False, "窗口内仍应受限"
    assert limiter.allow("ip", now=WINDOW_SECONDS + 0.1) is True, "滑出窗口后应恢复"


def test_sliding_window_is_not_a_fixed_window():
    """固定窗口实现的经典漏洞：窗口边界两侧可连续打满 2 倍配额。
    滑动窗口下，只要之前 60 秒内用过就不该放行。"""
    limiter = SlidingWindowRateLimiter()
    for _ in range(MAX_REQUESTS):
        limiter.allow("ip", now=WINDOW_SECONDS - 0.1)  # 全部塞在第一个窗口末尾
    assert limiter.allow("ip", now=WINDOW_SECONDS + 0.1) is False


def test_clients_are_isolated():
    limiter = SlidingWindowRateLimiter()
    for _ in range(MAX_REQUESTS):
        limiter.allow("a", now=0.0)
    assert limiter.allow("a", now=0.0) is False
    assert limiter.allow("b", now=0.0) is True, "一个 IP 超限不能影响其他 IP"


def test_retry_after_counts_down_to_window_exit():
    limiter = SlidingWindowRateLimiter()
    for _ in range(MAX_REQUESTS):
        limiter.allow("ip", now=0.0)
    assert limiter.retry_after("ip", now=0.0) == WINDOW_SECONDS + 1
    assert limiter.retry_after("ip", now=WINDOW_SECONDS - 5) <= 6


def test_reset_clears_state():
    limiter = SlidingWindowRateLimiter()
    for _ in range(MAX_REQUESTS):
        limiter.allow("ip", now=0.0)
    limiter.reset()
    assert limiter.allow("ip", now=0.0) is True


def test_concurrent_requests_never_exceed_quota():
    """并发下配额必须精确：计数与判空在同一把锁内，否则会出现
    "检查时还没满、写入时已经满了"的超发。"""
    limiter = SlidingWindowRateLimiter()
    results: list[bool] = []
    lock = threading.Lock()
    barrier = threading.Barrier(40)

    def worker() -> None:
        barrier.wait()
        allowed = limiter.allow("ip", now=0.0)
        with lock:
            results.append(allowed)

    threads = [threading.Thread(target=worker) for _ in range(40)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(results) == MAX_REQUESTS, "放行数必须恰好等于配额"
    assert len(results) == 40
