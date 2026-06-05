"""U4：投票参与与四机制交互验证。"""
from __future__ import annotations

import pytest


def make_poll(Session, mechanism, labels, multi_max_n=None):
    from app.models import Option, Poll
    from app.security import generate_admin_token, generate_poll_id, hash_token

    s = Session()
    try:
        poll = Poll(
            id=generate_poll_id(), title="t", mechanism=mechanism,
            multi_max_n=multi_max_n, admin_token_hash=hash_token(generate_admin_token()),
        )
        poll.options = [Option(label=l, position=i) for i, l in enumerate(labels)]
        s.add(poll)
        s.commit()
        return poll.id, [o.id for o in poll.options]
    finally:
        s.close()


def _vote_payload(Session, poll_id, voter_key):
    from app.models import Vote
    s = Session()
    try:
        v = s.query(Vote).filter_by(poll_id=poll_id, voter_key=voter_key).one_or_none()
        return v.payload if v else None
    finally:
        s.close()


def test_single_vote_stores_int(client, Session):
    pid, oids = make_poll(Session, "single", ["A", "B", "C"])
    resp = client.post(f"/p/{pid}/vote", data={"choice": str(oids[1])})
    assert resp.status_code == 200
    from app.models import Vote
    s = Session()
    try:
        v = s.query(Vote).filter_by(poll_id=pid).one()
        assert v.payload == oids[1]
    finally:
        s.close()


def test_multiple_vote_stores_sorted_list(client, Session):
    pid, oids = make_poll(Session, "multiple", ["A", "B", "C"], multi_max_n=3)
    resp = client.post(f"/p/{pid}/vote", data={"choice": [str(oids[2]), str(oids[0])]})
    assert resp.status_code == 200
    from app.models import Vote
    s = Session()
    try:
        v = s.query(Vote).filter_by(poll_id=pid).one()
        assert v.payload == sorted([oids[0], oids[2]])
    finally:
        s.close()


def test_multiple_over_n_rejected(client, Session):
    pid, oids = make_poll(Session, "multiple", ["A", "B", "C"], multi_max_n=2)
    resp = client.post(f"/p/{pid}/vote", data={"choice": [str(o) for o in oids]})
    assert resp.status_code == 400
    assert "最多可选" in resp.text


def test_ranking_full_order_stored(client, Session):
    pid, oids = make_poll(Session, "ranking", ["A", "B", "C"])
    order = [oids[2], oids[0], oids[1]]
    resp = client.post(f"/p/{pid}/vote", data={"ranking": ",".join(str(o) for o in order)})
    assert resp.status_code == 200
    from app.models import Vote
    s = Session()
    try:
        assert s.query(Vote).filter_by(poll_id=pid).one().payload == order
    finally:
        s.close()


def test_ranking_partial_rejected(client, Session):
    pid, oids = make_poll(Session, "ranking", ["A", "B", "C"])
    resp = client.post(f"/p/{pid}/vote", data={"ranking": f"{oids[0]},{oids[1]}"})
    assert resp.status_code == 400
    assert "全部选项" in resp.text


def test_scoring_valid_stores_map(client, Session):
    pid, oids = make_poll(Session, "scoring", ["A", "B"])
    resp = client.post(f"/p/{pid}/vote",
                       data={f"score_{oids[0]}": "5", f"score_{oids[1]}": "3"})
    assert resp.status_code == 200
    from app.models import Vote
    s = Session()
    try:
        assert s.query(Vote).filter_by(poll_id=pid).one().payload == {
            str(oids[0]): 5, str(oids[1]): 3}
    finally:
        s.close()


@pytest.mark.parametrize("bad", ["0", "6", "abc"])
def test_scoring_out_of_range_rejected(client, Session, bad):
    pid, oids = make_poll(Session, "scoring", ["A", "B"])
    resp = client.post(f"/p/{pid}/vote",
                       data={f"score_{oids[0]}": bad, f"score_{oids[1]}": "3"})
    assert resp.status_code == 400


def test_invalid_option_id_rejected(client, Session):
    pid, oids = make_poll(Session, "single", ["A", "B"])
    resp = client.post(f"/p/{pid}/vote", data={"choice": "999999"})
    assert resp.status_code == 400
    assert "无效选项" in resp.text


def test_resubmit_overwrites_not_appends(client, Session):
    pid, oids = make_poll(Session, "single", ["A", "B"])
    client.post(f"/p/{pid}/vote", data={"choice": str(oids[0])})
    client.post(f"/p/{pid}/vote", data={"choice": str(oids[1])})
    from app.models import Vote
    s = Session()
    try:
        votes = s.query(Vote).filter_by(poll_id=pid).all()
        assert len(votes) == 1
        assert votes[0].payload == oids[1]
    finally:
        s.close()


def test_voted_visitor_sees_prior_selection(client, Session):
    pid, oids = make_poll(Session, "single", ["A", "B"])
    client.post(f"/p/{pid}/vote", data={"choice": str(oids[0])})
    resp = client.get(f"/p/{pid}")
    assert resp.status_code == 200
    # 已投者再访问，单选 radio 对应项被 checked
    assert f'value="{oids[0]}"' in resp.text and "checked" in resp.text
    assert "修改你的投票" in resp.text


def test_new_visitor_gets_voter_cookie(client, Session):
    pid, oids = make_poll(Session, "single", ["A", "B"])
    resp = client.get(f"/p/{pid}")
    assert resp.status_code == 200
    # GET 为新访客分配 voter_key cookie
    assert "voter_key" in resp.headers.get("set-cookie", "")


def test_poll_has_votes_freeze_helper(client, Session):
    """R13：首票后配置应冻结（结构层面无 mechanism/选项集变更路径）。"""
    from app.db import SessionLocal
    from app.routes.polls import poll_has_votes

    pid, oids = make_poll(Session, "single", ["A", "B"])
    s = SessionLocal()
    try:
        assert poll_has_votes(s, pid) is False
    finally:
        s.close()
    client.post(f"/p/{pid}/vote", data={"choice": str(oids[0])})
    s = SessionLocal()
    try:
        assert poll_has_votes(s, pid) is True
    finally:
        s.close()


def test_vote_on_missing_poll_404(client):
    resp = client.post("/p/nonexistent/vote", data={"choice": "1"})
    assert resp.status_code == 404
