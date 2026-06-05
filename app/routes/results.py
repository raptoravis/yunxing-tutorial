"""结果展示与实时 SSE 路由（U5 结果 / U6 SSE）。

可见性门控（R9/R14）：
  关闭后        → 所有人可见最终结果
  隐藏结果开启  → 投票中对所有人隐藏，仅关闭后公布
  否则          → 已投票者可见、未投票者不可见
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from sqlalchemy.orm import Session

from ..broker import broker
from ..db import SessionLocal, get_db
from ..dedup import get_or_create_voter_key
from ..models import BALLOT_PARTIAL, Poll, Vote
from ..tally import compute_tally
from ..templating import templates

router = APIRouter()

# SSE 心跳间隔：无更新时定期发注释行保活并探测断连。
SSE_KEEPALIVE_SECONDS = 15


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


def render_ballot_region(
    request: Request, db: Session, poll: Poll, *, voter_key: str | None,
    voted: bool = False, error: str | None = None,
) -> HTMLResponse:
    """#vote-panel 的投票/改票表单片段。结果区在外层 #results、由 SSE 更新。"""
    ctx = state_context(db, poll, voter_key, voted=voted, error=error)
    status = 400 if error else 200
    return templates.TemplateResponse(request, "_ballot_region.html", ctx, status_code=status)


def results_html(db: Session, poll: Poll, voter_key: str | None, *, voted: bool = False) -> str:
    """供 SSE 广播使用（U6）：仅结果片段的 HTML 字符串。"""
    ctx = state_context(db, poll, voter_key, voted=voted)
    return templates.get_template("_results.html").render(**ctx)


def publish_results(db: Session, poll: Poll) -> None:
    """投后广播钩子：发信号，各 SSE 连接按自己的 voter_key 重渲染（KTD6）。"""
    broker.publish(poll.id)


def _render_stream_html(poll_id: str, voter_key: str | None) -> str | None:
    """SSE 内重渲染：短生命周期同步 session（KTD2，跑在 threadpool）。

    渲染异常时返回 None（跳过本次推送），不让瞬时 DB 错误中断整条 SSE 流。
    """
    db = SessionLocal()
    try:
        poll = db.get(Poll, poll_id)
        if poll is None:
            return None
        return results_html(db, poll, voter_key)
    except Exception:
        return None
    finally:
        db.close()


@router.get("/p/{poll_id}/stream", response_class=EventSourceResponse)
async def results_stream(poll_id: str, request: Request):
    """原生 SSE：投后实时结果（R9 / KTD1）。每连接按自身可见性重渲染。"""
    voter_key, _ = get_or_create_voter_key(request)
    queue = broker.subscribe(poll_id)
    try:
        # 连接即推一次当前状态
        html = await run_in_threadpool(_render_stream_html, poll_id, voter_key)
        if html is not None:
            yield ServerSentEvent(raw_data=html, event="update")
        while True:
            if await request.is_disconnected():
                break
            try:
                await asyncio.wait_for(queue.get(), timeout=SSE_KEEPALIVE_SECONDS)
            except asyncio.TimeoutError:
                yield ServerSentEvent(comment="keepalive")
                continue
            html = await run_in_threadpool(_render_stream_html, poll_id, voter_key)
            if html is not None:
                yield ServerSentEvent(raw_data=html, event="update")
    finally:
        broker.unsubscribe(poll_id, queue)  # 断连清理，防注册表泄漏


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
