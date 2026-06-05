"""轻度去重与防滥用（R6 cookie 去重 / KTD7 软性 per-IP 限速）。

voter_key 以 cookie 为主（KTD：cookie 为主、IP 参与限速而非 dedup 键）。
清 cookie / 换设备可绕过——已知的软约束（R-2）。限速为节流非永久封禁，
避免 NAT 误伤（KTD7）。进程内计数，多机时换 Redis（与 broker 同构）。
"""
from __future__ import annotations

import secrets
import time
from collections import defaultdict, deque

from starlette.requests import Request
from starlette.responses import Response

from .config import settings

VOTER_COOKIE = "voter_key"
# cookie 生命周期：180 天（实现时定，#5 已 defer）。
COOKIE_MAX_AGE = 60 * 60 * 24 * 180


def get_or_create_voter_key(request: Request) -> tuple[str, bool]:
    """返回 (voter_key, is_new)。无 cookie 的新访客分配一个新 key。"""
    existing = request.cookies.get(VOTER_COOKIE)
    if existing:
        return existing, False
    return secrets.token_urlsafe(16), True


def set_voter_cookie(response: Response, voter_key: str) -> None:
    response.set_cookie(
        VOTER_COOKIE,
        voter_key,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.force_https,
    )


def client_ip(request: Request) -> str | None:
    """取客户端 IP。代理后取 X-Forwarded-For 首段，否则取直连地址。"""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


class RateLimiter:
    """滑动窗口 per-IP 限速（KTD7）。进程内、单 worker 有效。"""

    def __init__(self, window_seconds: int, max_submits: int) -> None:
        self.window = window_seconds
        self.max_submits = max_submits
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check_and_record(self, key: str | None, *, now: float | None = None) -> bool:
        """记录一次提交。返回 True 表示允许，False 表示触发限速冷却。

        key 为 None（拿不到 IP）时不限速，放行。
        """
        if not key:
            return True
        t = time.monotonic() if now is None else now
        bucket = self._hits[key]
        cutoff = t - self.window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= self.max_submits:
            return False
        bucket.append(t)
        return True

    def reset(self) -> None:
        self._hits.clear()


rate_limiter = RateLimiter(
    settings.rate_limit_window_seconds, settings.rate_limit_max_submits
)
