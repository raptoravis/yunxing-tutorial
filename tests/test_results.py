"""U5：结果可见性门控验证（R9/R14）。U6 追加 SSE 测试。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def make_poll(Session, mechanism="single", labels=("A", "B"), hide_results=False, deadline=None):
    from app.models import Option, Poll
    from app.security import generate_admin_token, generate_poll_id, hash_token

    s = Session()
    try:
        poll = Poll(id=generate_poll_id(), title="t", mechanism=mechanism,
                    hide_results=hide_results, deadline=deadline,
                    admin_token_hash=hash_token(generate_admin_token()))
        poll.options = [Option(label=l, position=i) for i, l in enumerate(labels)]
        s.add(poll)
        s.commit()
        return poll.id, [o.id for o in poll.options]
    finally:
        s.close()


def test_unvoted_cannot_see_results(client, Session):
    pid, oids = make_poll(Session)
    resp = client.get(f"/p/{pid}/results")
    assert resp.status_code == 200
    assert "投票后即可查看" in resp.text
    assert "结果" not in resp.text or "查看实时结果" in resp.text


def test_voter_sees_results_after_voting(client, Session):
    pid, oids = make_poll(Session)
    client.post(f"/p/{pid}/vote", data={"choice": str(oids[0])})
    resp = client.get(f"/p/{pid}/results")
    assert resp.status_code == 200
    assert "共 1 人投票" in resp.text


def test_hide_results_hidden_during_voting(client, Session):
    pid, oids = make_poll(Session, hide_results=True)
    client.post(f"/p/{pid}/vote", data={"choice": str(oids[0])})
    resp = client.get(f"/p/{pid}/results")
    # 即使已投票，hide_results 下投票中也不可见
    assert "截止前隐藏结果" in resp.text or "投票结束后公布" in resp.text
    assert "共 1 人投票" not in resp.text


def test_hide_results_visible_after_close(client, Session):
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    pid, oids = make_poll(Session, hide_results=True, deadline=past)
    # 已过截止 → 关闭 → 结果对所有人可见
    resp = client.get(f"/p/{pid}/results")
    assert resp.status_code == 200
    assert "共 " in resp.text and "人投票" in resp.text


def test_closed_results_visible_to_unvoted(client, Session):
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    pid, oids = make_poll(Session, deadline=past)
    resp = client.get(f"/p/{pid}/results")
    assert resp.status_code == 200
    assert "人投票" in resp.text


def test_poll_page_shows_results_after_vote(client, Session):
    pid, oids = make_poll(Session)
    client.post(f"/p/{pid}/vote", data={"choice": str(oids[0])})
    resp = client.get(f"/p/{pid}")
    assert "共 1 人投票" in resp.text
    # 仍可改票
    assert "修改你的投票" in resp.text
