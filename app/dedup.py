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
    """取客户端 IP。

    仅在显式配置 trust_proxy（部署在可信反代后）时才信任 X-Forwarded-For；
    否则一律用直连地址。X-Forwarded-For 是客户端可控头，直连暴露时信任它会让
    攻击者每次请求伪造不同 IP、绕过 per-IP 限速并撑爆限速表（安全/对抗评审）。
    """
    if settings.trust_proxy:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


class RateLimiter:
    """滑动窗口 per-IP 限速（KTD7）。进程内、单 worker 有效。

    _hits 字典按 key（IP）增长。空闲桶会被机会性驱逐，避免长时间运行后
    无界膨胀（即便有人用伪造 IP 刷，键数也被 _max_keys 上限约束）。
    """

    def __init__(self, window_seconds: int, max_submits: int, *, max_keys: int = 10000) -> None:
        self.window = window_seconds
        self.max_submits = max_submits
        self._max_keys = max_keys
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
        if len(self._hits) > self._max_keys:
            self._evict_idle(t)
        return True

    def _evict_idle(self, now: float) -> None:
        """删除窗口外（空或最后命中已过期）的键，回收内存。"""
        cutoff = now - self.window
        stale = [k for k, b in self._hits.items() if not b or b[-1] < cutoff]
        for k in stale:
            del self._hits[k]

    def reset(self) -> None:
        self._hits.clear()


rate_limiter = RateLimiter(
    settings.rate_limit_window_seconds, settings.rate_limit_max_submits
)
