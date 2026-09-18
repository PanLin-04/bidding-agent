"""滑动窗口限流（进程内，见 docs/开发文档.md §5.4）。

按客户端 IP 限流 **30 次 / 60 秒**，超限由 API 层返回 429。

两点实现约束：

- 状态在**进程内**，因此生产部署必须单进程（`--workers 1`）。多 worker 各自
  持有一份计数，配额会被放大 N 倍；要水平扩展得先换成 Redis 集中计数。
- 「滑动窗口」而非固定窗口：固定窗口在边界处可被瞬间打满 2 倍配额。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field

WINDOW_SECONDS = 60
MAX_REQUESTS = 30


@dataclass
class SlidingWindowRateLimiter:
    max_requests: int = MAX_REQUESTS
    window_seconds: float = WINDOW_SECONDS
    _hits: dict[str, deque[float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def allow(self, key: str, now: float | None = None) -> bool:
        """记录一次访问并判定是否放行。

        时间戳可由调用方注入（`now`），使并发配额测试不必真的 sleep 60 秒。
        """
        ts = time.monotonic() if now is None else now
        cutoff = ts - self.window_seconds
        with self._lock:
            bucket = self._hits.setdefault(key, deque())
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.max_requests:
                return False
            bucket.append(ts)
            return True

    def retry_after(self, key: str, now: float | None = None) -> int:
        """建议的等待秒数（用于 Retry-After 响应头）。"""
        ts = time.monotonic() if now is None else now
        with self._lock:
            bucket = self._hits.get(key)
            if not bucket:
                return 0
            # 最早的一次访问滑出窗口时即可重试
            return max(1, int(self.window_seconds - (ts - bucket[0])) + 1)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


rate_limiter = SlidingWindowRateLimiter()
