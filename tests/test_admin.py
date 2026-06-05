"""U7：创建者管理与 capability-URL 安全验证（R11/R12/R15）。"""
from __future__ import annotations


def make_poll(Session, mechanism="single", labels=("A", "B")):
    """返回 (poll_id, option_ids, admin_token 明文)。"""
    from app.models import Option, Poll
    from app.security import generate_admin_token, generate_poll_id, hash_token

    token = generate_admin_token()
    s = Session()
    try:
        poll = Poll(id=generate_poll_id(), title="t", mechanism=mechanism,
                    admin_token_hash=hash_token(token))
        poll.options = [Option(label=l, position=i) for i, l in enumerate(labels)]
        s.add(poll)
        s.commit()
        return poll.id, [o.id for o in poll.options], token
    finally:
        s.close()


def test_correct_token_grants_view(client, Session):
    pid, oids, token = make_poll(Session)
    resp = client.get(f"/admin/{pid}/view", headers={"X-Admin-Token": token})
    assert resp.status_code == 200
    assert "当前结果" in resp.text


def test_missing_token_forbidden(client, Session):
    pid, oids, token = make_poll(Session)
    resp = client.get(f"/admin/{pid}/view")
    assert resp.status_code == 403


def test_wrong_token_forbidden(client, Session):
    pid, oids, token = make_poll(Session)
    resp = client.get(f"/admin/{pid}/view", headers={"X-Admin-Token": "wrong"})
    assert resp.status_code == 403


def test_equal_length_wrong_token_forbidden(client, Session):
    """constant-time 比较：等长但错误的 token 同样 403（不泄漏时序）。"""
    pid, oids, token = make_poll(Session)
    fake = "x" * len(token)
    resp = client.get(f"/admin/{pid}/view", headers={"X-Admin-Token": fake})
    assert resp.status_code == 403


def test_token_in_query_does_not_authenticate(client, Session):
    """密钥仅认请求头——放进 query 不应通过（R15：不走 path/query）。"""
    pid, oids, token = make_poll(Session)
    resp = client.get(f"/admin/{pid}/view?token={token}")
    assert resp.status_code == 403


def test_close_sets_closed_and_blocks_new_votes(client, Session):
    pid, oids, token = make_poll(Session)
    resp = client.post(f"/admin/{pid}/close", headers={"X-Admin-Token": token})
    assert resp.status_code == 200
    assert "已结束" in resp.text
    # 关闭后公开页拒收新票（R8）
    vote = client.post(f"/p/{pid}/vote", data={"choice": str(oids[0])})
    assert vote.status_code == 409


def test_close_requires_token(client, Session):
    pid, oids, token = make_poll(Session)
    resp = client.post(f"/admin/{pid}/close")
    assert resp.status_code == 403
    # 未授权不应关闭
    from app.models import Poll
    s = Session()
    try:
        assert s.get(Poll, pid).closed_at is None
    finally:
        s.close()


def test_admin_view_stats_match_tally(client, Session):
    pid, oids, token = make_poll(Session)
    # 两票投 A、一票投 B
    from app.models import Vote
    s = Session()
    try:
        s.add(Vote(poll_id=pid, voter_key="a", payload=oids[0]))
        s.add(Vote(poll_id=pid, voter_key="b", payload=oids[0]))
        s.add(Vote(poll_id=pid, voter_key="c", payload=oids[1]))
        s.commit()
    finally:
        s.close()
    resp = client.get(f"/admin/{pid}/view", headers={"X-Admin-Token": token})
    assert "总票数：<strong>3</strong>" in resp.text


def test_admin_responses_have_security_headers(client, Session):
    pid, oids, token = make_poll(Session)
    shell = client.get(f"/admin/{pid}")
    assert shell.headers.get("cache-control") == "no-store"
    assert shell.headers.get("x-robots-tag") == "noindex"
    view = client.get(f"/admin/{pid}/view", headers={"X-Admin-Token": token})
    assert view.headers.get("cache-control") == "no-store"
    assert view.headers.get("x-robots-tag") == "noindex"


def test_admin_shell_does_not_embed_token(client, Session):
    """壳页不含明文 token——服务端从未收到它（仅在 fragment）。"""
    pid, oids, token = make_poll(Session)
    shell = client.get(f"/admin/{pid}")
    assert token not in shell.text


def test_admin_view_missing_poll_404(client):
    resp = client.get("/admin/nope/view", headers={"X-Admin-Token": "x"})
    assert resp.status_code == 404
