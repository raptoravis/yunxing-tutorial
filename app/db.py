"""数据库引擎与会话依赖。

KTD2/KTD3：同步 SQLAlchemy Session（SQLite 驱动本身同步），FastAPI 在
threadpool 跑 def 路由不阻塞事件循环。SQLite 必开 WAL，否则并发投票
会 "database is locked"。Postgres 切换仅改 engine URL + 跳过 PRAGMA 监听。
"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


def _make_engine(url: str):
    connect_args = {}
    if url.startswith("sqlite"):
        # check_same_thread=False：允许 threadpool 中跨线程使用连接（KTD3）。
        connect_args["check_same_thread"] = False
    return create_engine(url, connect_args=connect_args, future=True)


engine = _make_engine(settings.database_url)


if settings.is_sqlite:

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


# expire_on_commit=False：commit 后对象属性仍可读，避免渲染时重新查库（KTD3）。
SessionLocal = sessionmaker(
    bind=engine, autoflush=False, expire_on_commit=False, class_=Session
)


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：产出短生命周期 session，请求结束后关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """建表。导入 models 以注册映射，再 create_all。"""
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
