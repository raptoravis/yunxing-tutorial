"""U4/U8：去重 voter_key 与软性限速验证。"""
from __future__ import annotations


def test_rate_limiter_allows_within_limit():
    from app.dedup import RateLimiter

    rl = RateLimiter(window_seconds=10, max_submits=3)
    now = 1000.0
    assert rl.check_and_record("ip1", now=now) is True
    assert rl.check_and_record("ip1", now=now) is True
    assert rl.check_and_record("ip1", now=now) is True
    # 第 4 次在窗口内 → 限速
    assert rl.check_and_record("ip1", now=now) is False


def test_rate_limiter_window_slides():
    from app.dedup import RateLimiter

    rl = RateLimiter(window_seconds=10, max_submits=2)
    assert rl.check_and_record("ip", now=0) is True
    assert rl.check_and_record("ip", now=1) is True
    assert rl.check_and_record("ip", now=2) is False
    # 超过窗口后旧记录过期，重新放行
    assert rl.check_and_record("ip", now=12) is True


def test_rate_limiter_distinct_ips_independent():
    from app.dedup import RateLimiter

    rl = RateLimiter(window_seconds=10, max_submits=1)
    assert rl.check_and_record("a", now=0) is True
    # 不同 IP（同 NAT 场景）互不影响
    assert rl.check_and_record("b", now=0) is True
    assert rl.check_and_record("a", now=0) is False


def test_rate_limiter_none_key_passes():
    from app.dedup import RateLimiter

    rl = RateLimiter(window_seconds=10, max_submits=1)
    assert rl.check_and_record(None, now=0) is True
    assert rl.check_and_record(None, now=0) is True


def test_rate_limiter_evicts_idle_keys():
    from app.dedup import RateLimiter

    rl = RateLimiter(window_seconds=10, max_submits=5, max_keys=2)
    rl.check_and_record("a", now=0)
    rl.check_and_record("b", now=0)
    # 第三个键触发驱逐：a、b 的最后命中已过窗口 → 被回收
    rl.check_and_record("c", now=100)
    assert "a" not in rl._hits
    assert "b" not in rl._hits
    assert "c" in rl._hits


def test_client_ip_ignores_xff_by_default():
    """默认不信任代理：忽略可伪造的 X-Forwarded-For，用直连地址（防限速绕过）。"""
    from starlette.requests import Request

    from app.dedup import client_ip

    scope = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"6.6.6.6")],
        "client": ("10.0.0.5", 12345),
    }
    assert client_ip(Request(scope)) == "10.0.0.5"


def test_client_ip_trusts_xff_when_proxy_configured(monkeypatch):
    from types import SimpleNamespace

    from starlette.requests import Request

    import app.dedup as dedup

    monkeypatch.setattr(dedup, "settings", SimpleNamespace(trust_proxy=True, force_https=False))
    scope = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"1.2.3.4, 5.6.7.8")],
        "client": ("10.0.0.5", 12345),
    }
    assert dedup.client_ip(Request(scope)) == "1.2.3.4"


def test_voter_key_generation_and_cookie():
    from starlette.requests import Request

    from app.dedup import get_or_create_voter_key

    # 无 cookie → 新 key
    scope = {"type": "http", "headers": []}
    req = Request(scope)
    key, is_new = get_or_create_voter_key(req)
    assert is_new is True and key

    # 有 cookie → 复用
    scope2 = {"type": "http", "headers": [(b"cookie", b"voter_key=abc123")]}
    req2 = Request(scope2)
    key2, is_new2 = get_or_create_voter_key(req2)
    assert is_new2 is False and key2 == "abc123"
