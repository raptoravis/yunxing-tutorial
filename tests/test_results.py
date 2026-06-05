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


def test_results_sse_node_outside_vote_panel(client, Session):
    """#results(SSE) 必须在 #vote-panel 之外，避免投票 swap 销毁重建 EventSource。"""
    pid, oids = make_poll(Session)
    html = client.get(f"/p/{pid}").text
    results_pos = html.find('id="results"')
    panel_pos = html.find('id="vote-panel"')
    assert results_pos != -1 and panel_pos != -1
    assert results_pos < panel_pos  # 结果区是 vote-panel 的前置兄弟
    assert "sse-connect" in html[:panel_pos]  # SSE 连接挂在 results 上


def test_vote_response_has_no_duplicate_sse_node(client, Session):
    """投票后 swap 进 #vote-panel 的片段不得再含 sse-connect（否则重复连接）。"""
    pid, oids = make_poll(Session)
    resp = client.post(f"/p/{pid}/vote", data={"choice": str(oids[0])})
    assert resp.status_code == 200
    assert "sse-connect" not in resp.text


# ---------- U6：SSE + pub/sub ----------


def test_broker_subscribe_publish_unsubscribe():
    import asyncio

    from app.broker import Broker

    async def run():
        b = Broker()
        q = b.subscribe("p1")
        assert b.subscriber_count("p1") == 1
        b.publish("p1")
        await asyncio.sleep(0)  # 让 call_soon_threadsafe 投递落地
        assert not q.empty()
        assert await q.get() is None
        b.unsubscribe("p1", q)
        assert b.subscriber_count("p1") == 0  # 注册表清理，不泄漏

    asyncio.run(run())


def test_broker_fans_out_to_all_subscribers():
    import asyncio

    from app.broker import Broker

    async def run():
        b = Broker()
        q1, q2 = b.subscribe("p"), b.subscribe("p")
        b.publish("p")
        await asyncio.sleep(0)
        assert not q1.empty() and not q2.empty()

    asyncio.run(run())


def test_broker_publish_no_subscribers_noop():
    from app.broker import Broker

    b = Broker()
    b.publish("nobody")  # 不应抛错
    assert b.subscriber_count("nobody") == 0


def test_broker_drops_signal_when_queue_full():
    import asyncio

    from app.broker import Broker

    async def run():
        b = Broker()
        q = b.subscribe("p")
        for _ in range(64):  # 填满 maxsize=64
            q.put_nowait(None)
        b.publish("p")  # 队列满，应丢弃信号而非抛 QueueFull
        await asyncio.sleep(0)
        assert q.qsize() == 64

    asyncio.run(run())


def test_vote_triggers_broker_publish(client, Session, monkeypatch):
    import app.routes.results as results

    calls = []
    monkeypatch.setattr(results.broker, "publish", lambda pid: calls.append(pid))
    pid, oids = make_poll(Session)
    client.post(f"/p/{pid}/vote", data={"choice": str(oids[0])})
    assert calls == [pid]


# SSE 广播载荷（每连接重渲染）直接测 _render_stream_html / results_html，
# 不经 HTTP 流式传输——TestClient 对无限 SSE 生成器会在断连检测处挂起，
# 实时传输本身在浏览器级（U6 Verification）验证。


def _vote_directly(Session, poll_id, voter_key, payload):
    from app.models import Vote
    s = Session()
    try:
        s.add(Vote(poll_id=poll_id, voter_key=voter_key, payload=payload))
        s.commit()
    finally:
        s.close()


def test_stream_payload_voted_shows_results(Session):
    from app.routes.results import _render_stream_html

    pid, oids = make_poll(Session)
    _vote_directly(Session, pid, "vk1", oids[0])
    html = _render_stream_html(pid, "vk1")
    assert html is not None
    assert "共 1 人投票" in html


def test_stream_payload_hidden_during_voting(Session):
    from app.routes.results import _render_stream_html

    pid, oids = make_poll(Session, hide_results=True)
    _vote_directly(Session, pid, "vk1", oids[0])
    html = _render_stream_html(pid, "vk1")
    # 隐藏结果时广播的是隐藏提示，不含计票
    assert "共 1 人投票" not in html
    assert "隐藏结果" in html or "投票结束后公布" in html


def test_stream_payload_unvoted_hidden(Session):
    from app.routes.results import _render_stream_html

    pid, oids = make_poll(Session)
    html = _render_stream_html(pid, "never-voted")
    assert "投票后即可查看" in html


def test_stream_payload_missing_poll_returns_none(Session):
    from app.routes.results import _render_stream_html

    assert _render_stream_html("nonexistent", "vk") is None


def test_stream_endpoint_registered():
    """路由已挂载且声明为 SSE（content-type 在传输层验证，此处只查注册）。"""
    from app.main import app

    paths = {r.path for r in app.routes}
    assert "/p/{poll_id}/stream" in paths
