"""创建者管理路由（U7 capability-URL 管理端，R11/R12/R15）。

密钥经 URL fragment 交付、由前端 JS 读出后置于 X-Admin-Token 请求头——
绝不进入 path/query，因而不入服务端访问日志与 Referer（KTD4/R15）。
服务端逐次以 constant-time 校验；管理响应加 no-store + noindex。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from ..broker import broker
from ..db import get_db
from ..models import Poll, Vote
from ..security import verify_token
from ..tally import compute_tally
from ..templating import templates

router = APIRouter()


def _harden(response: Response) -> Response:
    """管理响应安全头（R15）：不缓存、不被搜索引擎索引。"""
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex"
    return response


def require_admin(
    poll_id: str,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
) -> Poll:
    """逐次校验管理密钥（仅认请求头，不认 query/path）。失败 403。"""
    poll = db.get(Poll, poll_id)
    if poll is None:
        raise HTTPException(status_code=404, detail="投票不存在")
    if not verify_token(x_admin_token or "", poll.admin_token_hash):
        raise HTTPException(status_code=403, detail="管理密钥无效或缺失")
    return poll


@router.get("/admin/{poll_id}", response_class=HTMLResponse)
def admin_shell(poll_id: str, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """壳页：仅校验投票存在；密钥留在 fragment，由前端 JS 读出走请求头。"""
    poll = db.get(Poll, poll_id)
    if poll is None:
        raise HTTPException(status_code=404, detail="投票不存在")
    response = templates.TemplateResponse(request, "admin.html", {"poll": poll})
    return _harden(response)


def _admin_view_response(request: Request, db: Session, poll: Poll) -> HTMLResponse:
    votes = db.query(Vote).filter_by(poll_id=poll.id).all()
    tally = compute_tally(poll, votes)
    stats = {
        "total_votes": len(votes),
        "closed": poll.is_closed(),
        "closed_at": poll.closed_at,
        "deadline": poll.deadline,
    }
    response = templates.TemplateResponse(
        request, "_admin_view.html", {"poll": poll, "tally": tally, "stats": stats}
    )
    return _harden(response)


@router.get("/admin/{poll_id}/view", response_class=HTMLResponse)
def admin_view(
    request: Request, poll: Poll = Depends(require_admin), db: Session = Depends(get_db)
) -> HTMLResponse:
    """管理视图（R12）：当前结果 + 基础统计。管理者始终可见（不受可见性门控）。"""
    return _admin_view_response(request, db, poll)


@router.post("/admin/{poll_id}/close", response_class=HTMLResponse)
def admin_close(
    request: Request, poll: Poll = Depends(require_admin), db: Session = Depends(get_db)
) -> HTMLResponse:
    """手动结束投票（R11）。设 closed_at 后广播，让投票者实时看到最终结果。"""
    if poll.closed_at is None:
        poll.closed_at = datetime.now(timezone.utc)
        db.commit()
        broker.publish(poll.id)
    return _admin_view_response(request, db, poll)
