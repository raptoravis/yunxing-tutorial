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
