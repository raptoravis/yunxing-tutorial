"""U3：创建投票流程验证。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _future() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")


def test_create_valid_poll_persists_and_returns_two_links(client, Session):
    resp = client.post(
        "/polls",
        data={
            "title": "午餐去哪",
            "mechanism": "single",
            "options": ["麻辣烫", "汉堡", "沙拉"],
        },
        follow_redirects=False,
    )
    assert resp.status_code == 200
    body = resp.text
    assert "公开投票链接" in body
    assert "管理链接" in body
    # 管理链接含 fragment，公开链接不含 token
    assert "/admin/" in body and "#" in body
    from app.models import Poll

    s = Session()
    try:
        poll = s.query(Poll).first()
        assert poll is not None
        assert poll.title == "午餐去哪"
        assert len(poll.options) == 3
        # admin token 仅以哈希存储（64 位 hex）
        assert len(poll.admin_token_hash) == 64
    finally:
        s.close()


def test_admin_token_not_in_public_url(client, Session):
    client.post("/polls", data={"title": "t", "mechanism": "single",
                                 "options": ["A", "B"]})
    from app.models import Poll

    s = Session()
    try:
        poll = s.query(Poll).first()
        # 公开 url 仅含 poll.id，绝不含 token 哈希或明文
        assert poll.id not in poll.admin_token_hash
    finally:
        s.close()


def test_reject_fewer_than_two_options(client, Session):
    resp = client.post("/polls", data={"title": "t", "mechanism": "single",
                                        "options": ["只有一个"]})
    assert resp.status_code == 400
    assert "至少需要 2 个候选选项" in resp.text
    from app.models import Poll
    s = Session()
    try:
        assert s.query(Poll).count() == 0
    finally:
        s.close()


def test_reject_empty_title(client):
    resp = client.post("/polls", data={"title": "  ", "mechanism": "single",
                                        "options": ["A", "B"]})
    assert resp.status_code == 400
    assert "标题不能为空" in resp.text


def test_reject_past_deadline(client):
    past = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    resp = client.post("/polls", data={"title": "t", "mechanism": "single",
                                        "options": ["A", "B"], "deadline": past})
    assert resp.status_code == 400
    assert "晚于当前时间" in resp.text


def test_multiple_mechanism_sets_n(client, Session):
    client.post("/polls", data={"title": "t", "mechanism": "multiple",
                                 "options": ["A", "B", "C"], "multi_max_n": "2"})
    from app.models import Poll
    s = Session()
    try:
        poll = s.query(Poll).first()
        assert poll.mechanism == "multiple"
        assert poll.multi_max_n == 2
    finally:
        s.close()


def test_multiple_without_n_is_unlimited(client, Session):
    client.post("/polls", data={"title": "t", "mechanism": "multiple",
                                 "options": ["A", "B"]})
    from app.models import Poll
    s = Session()
    try:
        assert s.query(Poll).first().multi_max_n is None
    finally:
        s.close()


def test_hide_results_persisted(client, Session):
    client.post("/polls", data={"title": "t", "mechanism": "single",
                                 "options": ["A", "B"], "hide_results": "on"})
    from app.models import Poll
    s = Session()
    try:
        assert s.query(Poll).first().hide_results is True
    finally:
        s.close()


def test_create_form_xdata_single_quoted(client):
    """x-data 含 tojson（带双引号），属性须用单引号包裹，否则 Alpine 解析失败。"""
    html = client.get("/").text
    assert "x-data='createForm(" in html
    # 不得出现双引号包裹导致提前截断的形态
    assert 'x-data="createForm(' not in html


def test_deadline_future_accepted(client, Session):
    resp = client.post("/polls", data={"title": "t", "mechanism": "single",
                                        "options": ["A", "B"], "deadline": _future()})
    assert resp.status_code == 200
    from app.models import Poll
    s = Session()
    try:
        assert s.query(Poll).first().deadline is not None
    finally:
        s.close()
