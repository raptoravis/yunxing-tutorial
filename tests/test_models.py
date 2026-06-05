"""U2：数据模型与约束验证。"""
from __future__ import annotations

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError


def test_tables_created(engine):
    names = set(inspect(engine).get_table_names())
    assert {"polls", "options", "votes"} <= names


def test_unique_poll_voter_key(Session):
    from app.models import Mechanism, Poll, Vote

    s = Session()
    try:
        poll = Poll(
            id="p1", title="午餐", mechanism=Mechanism.single.value,
            admin_token_hash="h1",
        )
        s.add(poll)
        s.commit()
        s.add(Vote(poll_id="p1", voter_key="vk", payload=1))
        s.commit()
        s.add(Vote(poll_id="p1", voter_key="vk", payload=2))
        with pytest.raises(IntegrityError):
            s.commit()
    finally:
        s.rollback()
        s.close()


@pytest.mark.parametrize(
    "payload",
    [
        1,                       # 单选
        [1, 2, 3],               # 多选
        [3, 1, 2],               # 排序
        {"1": 5, "2": 3},        # 打分
    ],
)
def test_json_payload_roundtrip(Session, payload):
    from app.models import Mechanism, Poll, Vote

    s = Session()
    try:
        poll = Poll(id=f"p_{id(payload)}", title="t", mechanism=Mechanism.single.value,
                    admin_token_hash=f"h_{id(payload)}")
        s.add(poll)
        s.commit()
        v = Vote(poll_id=poll.id, voter_key="vk", payload=payload)
        s.add(v)
        s.commit()
        vid = v.id
        s.expunge_all()
        got = s.get(Vote, vid)
        assert got.payload == payload
    finally:
        s.close()


def test_cascade_delete_options(Session):
    from app.models import Mechanism, Option, Poll

    s = Session()
    try:
        poll = Poll(id="pc", title="t", mechanism=Mechanism.single.value, admin_token_hash="hc")
        poll.options = [Option(label="A", position=0), Option(label="B", position=1)]
        s.add(poll)
        s.commit()
        s.delete(poll)
        s.commit()
        from app.models import Option as O
        assert s.query(O).filter_by(poll_id="pc").count() == 0
    finally:
        s.close()
