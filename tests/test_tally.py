"""U5：计票函数验证。"""
from __future__ import annotations


def _poll(Session, mechanism, labels, ballots, multi_max_n=None):
    """建一个 poll + options + 一组 votes，返回 (poll, options) 重新加载的对象。

    ballots: 已是各机制 payload 形状的列表（用 option 序号 0..k-1 表达，内部转 id）。
    """
    from app.models import Option, Poll, Vote
    from app.security import generate_admin_token, generate_poll_id, hash_token

    s = Session()
    try:
        poll = Poll(id=generate_poll_id(), title="t", mechanism=mechanism,
                    multi_max_n=multi_max_n,
                    admin_token_hash=hash_token(generate_admin_token()))
        poll.options = [Option(label=l, position=i) for i, l in enumerate(labels)]
        s.add(poll)
        s.commit()
        oids = [o.id for o in poll.options]

        def to_payload(b):
            if mechanism == "single":
                return oids[b]
            if mechanism == "multiple":
                return sorted(oids[i] for i in b)
            if mechanism == "ranking":
                return [oids[i] for i in b]
            if mechanism == "scoring":
                return {str(oids[i]): score for i, score in b.items()}

        for i, b in enumerate(ballots):
            s.add(Vote(poll_id=poll.id, voter_key=f"vk{i}", payload=to_payload(b)))
        s.commit()
        return poll.id, oids
    finally:
        s.close()


def _compute(Session, poll_id):
    from app.models import Poll, Vote
    from app.tally import compute_tally
    s = Session()
    try:
        poll = s.get(Poll, poll_id)
        votes = s.query(Vote).filter_by(poll_id=poll_id).all()
        return compute_tally(poll, votes)
    finally:
        s.close()


def test_single_counts_and_percent(Session):
    pid, oids = _poll(Session, "single", ["A", "B", "C"], [0, 0, 1])
    res = _compute(Session, pid)
    assert res.total_voters == 3
    rows = {r.label: r for r in res.rows}
    assert rows["A"].count == 2 and rows["A"].percent == 66.7
    assert rows["B"].count == 1
    assert rows["C"].count == 0 and rows["C"].percent == 0.0
    # 排序按票数降序
    assert res.rows[0].label == "A"


def test_single_zero_votes(Session):
    pid, oids = _poll(Session, "single", ["A", "B"], [])
    res = _compute(Session, pid)
    assert res.total_voters == 0
    assert all(r.count == 0 and r.percent == 0.0 for r in res.rows)


def test_multiple_counts(Session):
    pid, oids = _poll(Session, "multiple", ["A", "B", "C"],
                      [[0, 1], [0, 2], [0]])
    res = _compute(Session, pid)
    rows = {r.label: r for r in res.rows}
    assert rows["A"].count == 3
    assert rows["B"].count == 1
    assert rows["C"].count == 1
    # 占比基于投票人数（3）
    assert rows["A"].percent == 100.0


def test_ranking_borda_scores(Session):
    # 3 选项：第1名得2分、第2名1分、第3名0分
    # 投票1: A>B>C  投票2: B>A>C
    pid, oids = _poll(Session, "ranking", ["A", "B", "C"],
                      [[0, 1, 2], [1, 0, 2]])
    res = _compute(Session, pid)
    rows = {r.label: r for r in res.rows}
    # A: 2+1=3, B: 1+2=3, C: 0+0=0
    assert rows["A"].points == 3
    assert rows["B"].points == 3
    assert rows["C"].points == 0


def test_ranking_clear_winner(Session):
    pid, oids = _poll(Session, "ranking", ["A", "B", "C"],
                      [[0, 1, 2], [0, 2, 1], [0, 1, 2]])
    res = _compute(Session, pid)
    assert res.rows[0].label == "A"
    assert res.rows[0].points == 6  # 3 票各 2 分


def test_scoring_average(Session):
    pid, oids = _poll(Session, "scoring", ["A", "B"],
                      [{0: 5, 1: 3}, {0: 4, 1: 3}])
    res = _compute(Session, pid)
    rows = {r.label: r for r in res.rows}
    assert rows["A"].average == 4.5
    assert rows["B"].average == 3.0
    assert res.rows[0].label == "A"


def test_scoring_single_vote_decimal(Session):
    pid, oids = _poll(Session, "scoring", ["A", "B"], [{0: 5, 1: 2}])
    res = _compute(Session, pid)
    rows = {r.label: r for r in res.rows}
    assert rows["A"].average == 5.0
    assert rows["B"].average == 2.0


def test_scoring_percent_baseline_at_min(Session):
    """进度条以量程 1 为 0% 基线、5 为 100%（最低分不再显示 20% 填充）。"""
    pid, oids = _poll(Session, "scoring", ["A", "B", "C"],
                      [{0: 5, 1: 3, 2: 1}])
    res = _compute(Session, pid)
    rows = {r.label: r for r in res.rows}
    assert rows["A"].percent == 100.0   # avg 5 → 满
    assert rows["B"].percent == 50.0    # avg 3 → 半
    assert rows["C"].percent == 0.0     # avg 1 → 空
