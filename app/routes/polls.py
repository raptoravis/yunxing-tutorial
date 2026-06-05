"""投票创建与参与路由（U3 创建 / U4 参与 / U8 生命周期）。"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Mechanism, Option, Poll
from ..security import generate_admin_token, generate_poll_id, hash_token
from ..templating import templates

router = APIRouter()

VALID_MECHANISMS = {m.value for m in Mechanism}


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
async def create_poll(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    form = await request.form()
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
