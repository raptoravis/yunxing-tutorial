"""结果展示与实时 SSE 路由（U5 结果 / U6 SSE）。

可见性门控（R9/R14）：
  关闭后        → 所有人可见最终结果
  隐藏结果开启  → 投票中对所有人隐藏，仅关闭后公布
  否则          → 已投票者可见、未投票者不可见
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..dedup import get_or_create_voter_key
from ..models import BALLOT_PARTIAL, Poll, Vote
from ..tally import compute_tally
from ..templating import templates

router = APIRouter()


def _votes(db: Session, poll_id: str) -> list[Vote]:
    return db.query(Vote).filter_by(poll_id=poll_id).all()


def visibility(poll: Poll, has_voted: bool, closed: bool) -> tuple[bool, str]:
    if closed:
        return True, ""
    if poll.hide_results:
        return False, "本投票设为「截止前隐藏结果」，将在投票结束后公布。"
    if has_voted:
        return True, ""
    return False, "投票后即可查看实时结果。"


def state_context(
    db: Session, poll: Poll, voter_key: str | None, *, voted: bool = False, error: str | None = None
) -> dict:
    closed = poll.is_closed()
    existing = None
    if voter_key:
        existing = db.query(Vote).filter_by(poll_id=poll.id, voter_key=voter_key).one_or_none()
    has_voted = voted or existing is not None
    visible, reason = visibility(poll, has_voted, closed)
    tally = compute_tally(poll, _votes(db, poll.id)) if visible else None
    return {
        "poll": poll,
        "closed": closed,
        "has_voted": has_voted,
        "visible": visible,
        "reason": reason,
        "tally": tally,
        "ballot_partial": BALLOT_PARTIAL[poll.mechanism],
        "existing_payload": existing.payload if existing else None,
        "error": error,
    }


def render_poll_state(
    request: Request, db: Session, poll: Poll, *, voter_key: str | None,
    voted: bool = False, error: str | None = None,
) -> HTMLResponse:
    ctx = state_context(db, poll, voter_key, voted=voted, error=error)
    status = 400 if error else 200
    return templates.TemplateResponse(request, "_poll_state.html", ctx, status_code=status)


def render_results_panel(
    request: Request, db: Session, poll: Poll, *, voter_key: str | None, voted: bool = False
) -> HTMLResponse:
    """polls.render_post_vote 的回调：投票后返回完整面板（结果 + 可改票表单）。"""
    return render_poll_state(request, db, poll, voter_key=voter_key, voted=voted)


def results_html(db: Session, poll: Poll, voter_key: str | None, *, voted: bool = False) -> str:
    """供 SSE 广播使用（U6）：仅结果片段的 HTML 字符串。"""
    ctx = state_context(db, poll, voter_key, voted=voted)
    return templates.get_template("_results.html").render(**ctx)


def publish_results(db: Session, poll: Poll) -> None:
    """投后广播钩子。U6 接入 broker 扇出；U5 期间为 no-op。"""
    return None


@router.get("/p/{poll_id}/results", response_class=HTMLResponse)
def results_fragment(
    poll_id: str, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    poll = db.get(Poll, poll_id)
    if poll is None:
        raise HTTPException(status_code=404, detail="投票不存在")
    voter_key, _ = get_or_create_voter_key(request)
    ctx = state_context(db, poll, voter_key)
    return templates.TemplateResponse(request, "_results.html", ctx)
