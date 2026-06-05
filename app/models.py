"""SQLAlchemy 2.x 数据模型：Poll / Option / Vote。

VOTE.payload 用 JSON 列承载四机制不同 ballot 形状：
  单选 = 选项 id（int）
  多选 = id 数组（list[int]）
  排序 = 有序 id 全列表（list[int]）
  打分 = {id: 1-5}（dict[str,int]）
(poll_id, voter_key) 唯一 —— 改票即 upsert（R6/R13）。
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Mechanism(str, enum.Enum):
    single = "single"
    multiple = "multiple"
    ranking = "ranking"
    scoring = "scoring"


SCORE_MIN = 1
SCORE_MAX = 5


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Poll(Base):
    __tablename__ = "polls"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    mechanism: Mapped[str] = mapped_column(String(16), nullable=False)
    multi_max_n: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hide_results: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    admin_token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )

    options: Mapped[list["Option"]] = relationship(
        back_populates="poll",
        cascade="all, delete-orphan",
        order_by="Option.position",
    )
    votes: Mapped[list["Vote"]] = relationship(
        back_populates="poll", cascade="all, delete-orphan"
    )

    def is_closed(self, *, now: datetime | None = None) -> bool:
        """关闭判定以服务端时间为准（R2/R8）：手动结束或已过截止。"""
        if self.closed_at is not None:
            return True
        if self.deadline is not None:
            ref = now or _utcnow()
            deadline = self.deadline
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            return ref >= deadline
        return False


class Option(Base):
    __tablename__ = "options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poll_id: Mapped[str] = mapped_column(
        ForeignKey("polls.id", ondelete="CASCADE"), index=True, nullable=False
    )
    label: Mapped[str] = mapped_column(String(300), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    poll: Mapped["Poll"] = relationship(back_populates="options")


class Vote(Base):
    __tablename__ = "votes"
    __table_args__ = (UniqueConstraint("poll_id", "voter_key", name="uq_vote_poll_voter"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poll_id: Mapped[str] = mapped_column(
        ForeignKey("polls.id", ondelete="CASCADE"), index=True, nullable=False
    )
    voter_key: Mapped[str] = mapped_column(String(64), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[Any] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    poll: Mapped["Poll"] = relationship(back_populates="votes")
