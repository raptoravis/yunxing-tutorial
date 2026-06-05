"""投票创建与参与路由（U3 创建 / U4 参与 / U8 生命周期）。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.datastructures import FormData
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..dedup import client_ip, get_or_create_voter_key, rate_limiter, set_voter_cookie
from ..models import BALLOT_PARTIAL, SCORE_MAX, SCORE_MIN, Mechanism, Option, Poll, Vote
from ..security import generate_admin_token, generate_poll_id, hash_token
from ..templating import templates

router = APIRouter()

VALID_MECHANISMS = {m.value for m in Mechanism}


async def parse_form(request: Request) -> FormData:
    """异步依赖：读取表单。让路由保持 def（threadpool 跑同步 DB，KTD2）。"""
    return await request.form()


def poll_has_votes(db: Session, poll_id: str) -> bool:
    return db.query(Vote).filter_by(poll_id=poll_id).first() is not None


def _option_ids(poll: Poll) -> set[int]:
    return {o.id for o in poll.options}


def parse_ballot(poll: Poll, form: FormData) -> tuple[Any, str | None]:
    """按机制解析并强校验 ballot。返回 (payload, error)。

    服务端是唯一权威——不信任前端校验（R7）。payload 形状见 models.Vote。
    """
    valid = _option_ids(poll)

    def _as_ids(values: list[str]) -> tuple[list[int], str | None]:
        try:
            ids = [int(v) for v in values]
        except (ValueError, TypeError):
            return [], "选项格式无效。"
        if any(i not in valid for i in ids):
            return [], "包含无效选项。"
        return ids, None

    mech = poll.mechanism
    if mech == Mechanism.single.value:
        raw = form.get("choice")
        if raw is None or raw == "":
            return None, "请选择一项。"
        ids, err = _as_ids([raw])
        if err:
            return None, err
        return ids[0], None

    if mech == Mechanism.multiple.value:
        ids, err = _as_ids(form.getlist("choice"))
        if err:
            return None, err
        ids = sorted(set(ids))
        if len(ids) < 1:
            return None, "至少选择一项。"
        if poll.multi_max_n is not None and len(ids) > poll.multi_max_n:
            return None, f"最多可选 {poll.multi_max_n} 项。"
        return ids, None

    if mech == Mechanism.ranking.value:
        raw = form.get("ranking") or ""
        parts = [p for p in raw.split(",") if p != ""]
        ids, err = _as_ids(parts)
        if err:
            return None, err
        if sorted(ids) != sorted(valid):
            return None, "排序必须包含且仅包含全部选项各一次。"
        return ids, None

    if mech == Mechanism.scoring.value:
        scores: dict[str, int] = {}
        for oid in valid:
            raw = form.get(f"score_{oid}")
            if raw is None or raw == "":
                return None, "请为每个选项打分。"
            try:
                val = int(raw)
            except ValueError:
                return None, "打分必须为整数。"
            if not (SCORE_MIN <= val <= SCORE_MAX):
                return None, f"打分须在 {SCORE_MIN}–{SCORE_MAX} 之间。"
            scores[str(oid)] = val
        return scores, None

    return None, "未知投票机制。"


def _parse_deadline(raw: str | None) -> tuple[datetime | None, str | None]:
    """解析 datetime-local 输入。把 naive 值按 UTC 处理（v1 简化）。

    返回 (deadline, error)。空值合法（截止可选，R2）。
    """
    if not raw or not raw.strip():
        return None, None
    try:
        # datetime-local 形如 2026-06-10T15:30 或 ...:30:00
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None, "截止时间格式无效。"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if dt <= datetime.now(timezone.utc):
        return None, "截止时间必须晚于当前时间。"
    return dt, None


@router.get("/", response_class=HTMLResponse)
def create_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "create.html", {"mechanisms": list(Mechanism)}
    )


@router.post("/polls", response_class=HTMLResponse)
def create_poll(
    request: Request,
    form: FormData = Depends(parse_form),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    title = (form.get("title") or "").strip()
    mechanism = (form.get("mechanism") or "").strip()
    options = [o.strip() for o in form.getlist("options") if o and o.strip()]
    hide_results = form.get("hide_results") in ("on", "true", "1", "yes")
    multi_max_raw = (form.get("multi_max_n") or "").strip()
    deadline_raw = form.get("deadline")

    errors: list[str] = []
    if not title:
        errors.append("标题不能为空。")
    if mechanism not in VALID_MECHANISMS:
        errors.append("请选择有效的投票机制。")
    if len(options) < 2:
        errors.append("至少需要 2 个候选选项。")

    multi_max_n: int | None = None
    if mechanism == Mechanism.multiple.value and multi_max_raw:
        try:
            multi_max_n = int(multi_max_raw)
            if multi_max_n < 1:
                errors.append("最多可选项数须为正整数。")
                multi_max_n = None
        except ValueError:
            errors.append("最多可选项数须为整数。")

    deadline, deadline_err = _parse_deadline(deadline_raw)
    if deadline_err:
        errors.append(deadline_err)

    if errors:
        return templates.TemplateResponse(
            request,
            "create.html",
            {
                "mechanisms": list(Mechanism),
                "errors": errors,
                "form": {
                    "title": title,
                    "mechanism": mechanism,
                    "options": options or ["", ""],
                    "hide_results": hide_results,
                    "multi_max_n": multi_max_raw,
                    "deadline": deadline_raw or "",
                },
            },
            status_code=400,
        )

    admin_token = generate_admin_token()
    poll = Poll(
        id=generate_poll_id(),
        title=title,
        mechanism=mechanism,
        multi_max_n=multi_max_n,
        hide_results=hide_results,
        deadline=deadline,
        admin_token_hash=hash_token(admin_token),
    )
    poll.options = [Option(label=label, position=i) for i, label in enumerate(options)]
    db.add(poll)
    db.commit()

    base = str(request.base_url).rstrip("/")
    public_url = f"{base}/p/{poll.id}"
    admin_url = f"{base}/admin/{poll.id}#{admin_token}"
    return templates.TemplateResponse(
        request,
        "created.html",
        {"poll": poll, "public_url": public_url, "admin_url": admin_url},
    )


def _get_poll_or_404(db: Session, poll_id: str) -> Poll:
    poll = db.get(Poll, poll_id)
    if poll is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="投票不存在")
    return poll


def _existing_vote(db: Session, poll_id: str, voter_key: str) -> Vote | None:
    return (
        db.query(Vote)
        .filter_by(poll_id=poll_id, voter_key=voter_key)
        .one_or_none()
    )


@router.get("/p/{poll_id}", response_class=HTMLResponse)
def poll_page(poll_id: str, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    from ..routes.results import state_context

    poll = _get_poll_or_404(db, poll_id)
    voter_key, is_new = get_or_create_voter_key(request)
    ctx = state_context(db, poll, None if is_new else voter_key)
    response = templates.TemplateResponse(request, "poll.html", ctx)
    if is_new:
        set_voter_cookie(response, voter_key)
    return response


@router.post("/p/{poll_id}/vote", response_class=HTMLResponse)
def submit_vote(
    poll_id: str,
    request: Request,
    form: FormData = Depends(parse_form),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    poll = _get_poll_or_404(db, poll_id)

    # 关闭判定以服务端时间为准（R8 / U8）：在途提交被拒、不静默丢弃。
    if poll.is_closed():
        return templates.TemplateResponse(
            request,
            "_vote_error.html",
            {"message": "投票已结束，无法再提交。"},
            status_code=409,
        )

    # 软性 per-IP 限速（KTD7 / U8）。
    if not rate_limiter.check_and_record(client_ip(request)):
        return templates.TemplateResponse(
            request,
            "_vote_error.html",
            {"message": "提交过于频繁，请稍后再试。"},
            status_code=429,
        )

    voter_key, is_new = get_or_create_voter_key(request)
    payload, error = parse_ballot(poll, form)
    if error:
        from ..routes.results import render_poll_state

        response = render_poll_state(request, db, poll, voter_key=voter_key, error=error)
        if is_new:
            set_voter_cookie(response, voter_key)
        return response

    existing = _existing_vote(db, poll_id, voter_key)
    if existing is not None:
        # 关闭前可改票（R6）：覆盖旧票，不新增。
        existing.payload = payload
        existing.ip = client_ip(request)
    else:
        db.add(Vote(poll_id=poll_id, voter_key=voter_key, ip=client_ip(request), payload=payload))
    db.commit()

    # 投后实时广播（U6 接入 broker；U5 期间 no-op）。
    from ..routes.results import publish_results

    publish_results(db, poll)

    response = render_post_vote(request, db, poll, voter_key=voter_key)
    if is_new:
        set_voter_cookie(response, voter_key)
    return response


def render_post_vote(
    request: Request, db: Session, poll: Poll, *, voter_key: str
) -> HTMLResponse:
    """投票后返回片段。U5 覆写为结果面板；U4 期间为投票确认。

    在 results 模块就绪后转交其渲染（投后实时结果 R9）。
    """
    try:
        from ..routes.results import render_results_panel

        return render_results_panel(request, db, poll, voter_key=voter_key, voted=True)
    except ImportError:
        return templates.TemplateResponse(
            request, "_voted.html", {"poll": poll}
        )
