"""计票函数（KTD5）。

单/多选：各项票数 + 占比（占比 = 该项票数 / 投票人数）。
排序：Borda 累加——N 选项中第 1 名得 N-1 分、第 2 名 N-2 分…末名 0 分。
打分：各项平均分（sum / 投票人数）。

返回归一化结构供 _results.html 直接渲染。row 至少含 id/label/value/percent。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import SCORE_MAX, SCORE_MIN, Mechanism, Poll, Vote


@dataclass
class TallyRow:
    id: int
    label: str
    count: int = 0           # 单/多选票数；其它机制为 0
    points: int = 0          # 排序 Borda 总分
    average: float = 0.0     # 打分平均
    percent: float = 0.0     # 进度条用（0–100）


@dataclass
class TallyResult:
    mechanism: str
    total_voters: int
    rows: list[TallyRow] = field(default_factory=list)


def _percent(part: float, whole: float) -> float:
    if whole <= 0:
        return 0.0
    return round(part / whole * 100, 1)


def compute_tally(poll: Poll, votes: list[Vote]) -> TallyResult:
    options = list(poll.options)
    n = len(votes)
    by_id = {o.id: TallyRow(id=o.id, label=o.label) for o in options}
    mech = poll.mechanism

    if mech == Mechanism.single.value:
        for v in votes:
            if v.payload in by_id:
                by_id[v.payload].count += 1
        for row in by_id.values():
            row.percent = _percent(row.count, n)
        rows = sorted(by_id.values(), key=lambda r: r.count, reverse=True)

    elif mech == Mechanism.multiple.value:
        for v in votes:
            for oid in (v.payload or []):
                if oid in by_id:
                    by_id[oid].count += 1
        for row in by_id.values():
            row.percent = _percent(row.count, n)
        rows = sorted(by_id.values(), key=lambda r: r.count, reverse=True)

    elif mech == Mechanism.ranking.value:
        size = len(options)
        for v in votes:
            for idx, oid in enumerate(v.payload or []):
                if oid in by_id:
                    by_id[oid].points += (size - 1 - idx)
        max_points = max((r.points for r in by_id.values()), default=0)
        for row in by_id.values():
            row.percent = _percent(row.points, max_points)
        rows = sorted(by_id.values(), key=lambda r: r.points, reverse=True)

    elif mech == Mechanism.scoring.value:
        sums: dict[int, int] = {oid: 0 for oid in by_id}
        for v in votes:
            for oid_str, score in (v.payload or {}).items():
                oid = int(oid_str)
                if oid in sums:
                    sums[oid] += int(score)
        span = SCORE_MAX - SCORE_MIN  # 量程 1–5：进度条以 1 为 0% 基线、5 为 100%
        for oid, row in by_id.items():
            row.average = round(sums[oid] / n, 2) if n else 0.0
            row.percent = _percent(row.average - SCORE_MIN, span) if n else 0.0
        rows = sorted(by_id.values(), key=lambda r: r.average, reverse=True)

    else:
        rows = list(by_id.values())

    return TallyResult(mechanism=mech, total_voters=n, rows=rows)


def tally_to_dict(result: TallyResult) -> dict[str, Any]:
    return {
        "mechanism": result.mechanism,
        "total_voters": result.total_voters,
        "rows": [r.__dict__ for r in result.rows],
    }
