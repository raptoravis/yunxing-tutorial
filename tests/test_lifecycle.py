"""U8：截止/生命周期与防滥用验证（R2/R6/R8）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def make_poll(Session, deadline=None, closed_at=None):
    from app.models import Option, Poll
    from app.security import generate_admin_token, generate_poll_id, hash_token

    s = Session()
    try:
        poll = Poll(id=generate_poll_id(), title="t", mechanism="single",
                    deadline=deadline, closed_at=closed_at,
                    admin_token_hash=hash_token(generate_admin_token()))
        poll.options = [Option(label="A", position=0), Option(label="B", position=1)]
        s.add(poll)
        s.commit()
        return poll.id, [o.id for o in poll.options]
    finally:
        s.close()


def test_deadline_passed_rejects_vote(client, Session):
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    pid, oids = make_poll(Session, deadline=past)
    resp = client.post(f"/p/{pid}/vote", data={"choice": str(oids[0])})
    assert resp.status_code == 409
    assert "已结束" in resp.text


def test_closed_page_shows_results_no_ballot(client, Session):
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    pid, oids = make_poll(Session, deadline=past)
    resp = client.get(f"/p/{pid}")
    assert resp.status_code == 200
    assert "投票已结束" in resp.text
    assert "提交投票" not in resp.text  # ballot 不再渲染


def test_inflight_submit_at_deadline_rejected_with_feedback(client, Session):
    # 截止瞬间（已过）在途提交：以服务端时间判定拒收、明确反馈，非静默丢弃
    just_past = datetime.now(timezone.utc) - timedelta(seconds=1)
    pid, oids = make_poll(Session, deadline=just_past)
    resp = client.post(f"/p/{pid}/vote", data={"choice": str(oids[0])})
    assert resp.status_code == 409
    assert "无法再提交" in resp.text
    # 确认未落库（非静默接受）
    from app.models import Vote
    s = Session()
    try:
        assert s.query(Vote).filter_by(poll_id=pid).count() == 0
    finally:
        s.close()


def test_manually_closed_blocks_votes(client, Session):
    now = datetime.now(timezone.utc)
    pid, oids = make_poll(Session, closed_at=now)
    resp = client.post(f"/p/{pid}/vote", data={"choice": str(oids[0])})
    assert resp.status_code == 409


def test_closed_results_visible_to_unvoted(client, Session):
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    pid, oids = make_poll(Session, deadline=past)
    resp = client.get(f"/p/{pid}/results")
    assert "人投票" in resp.text  # 关闭后未投票者也能看结果


def test_rate_limit_throttles_high_frequency(client, Session, monkeypatch):
    from app.dedup import rate_limiter

    # 收紧阈值便于触发：窗口内最多 3 次
    monkeypatch.setattr(rate_limiter, "max_submits", 3)
    rate_limiter.reset()
    pid, oids = make_poll(Session)
    codes = [client.post(f"/p/{pid}/vote", data={"choice": str(oids[0])}).status_code
             for _ in range(4)]
    assert codes[:3] == [200, 200, 200]
    assert codes[3] == 429


def test_rate_limit_does_not_block_normal_volume(client, Session):
    """同 IP 在阈值内多次提交（NAT 多人场景）不被误伤。"""
    from app.dedup import rate_limiter

    rate_limiter.reset()
    pid, oids = make_poll(Session)
    # 默认阈值（5）内连续提交均放行
    for _ in range(5):
        resp = client.post(f"/p/{pid}/vote", data={"choice": str(oids[0])})
        assert resp.status_code == 200


def test_is_closed_server_time_authoritative(Session):
    """关闭判定以服务端时间为准（naive deadline 视为 UTC）。"""
    from app.models import Poll

    s = Session()
    try:
        future = Poll(id="f", title="t", mechanism="single", admin_token_hash="hf",
                      deadline=datetime.now(timezone.utc) + timedelta(hours=1))
        past = Poll(id="p", title="t", mechanism="single", admin_token_hash="hp",
                    deadline=datetime.now(timezone.utc) - timedelta(hours=1))
        assert future.is_closed() is False
        assert past.is_closed() is True
    finally:
        s.close()
