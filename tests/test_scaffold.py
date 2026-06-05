"""U1：脚手架与持久层基座验证。"""
from __future__ import annotations

from sqlalchemy import text


def test_healthz_ok(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_sqlite_wal_enabled(engine):
    with engine.connect() as conn:
        mode = conn.execute(text("PRAGMA journal_mode")).scalar()
    assert str(mode).lower() == "wal"


def test_get_db_dependency_yields_usable_session(Session):
    from app.db import get_db

    gen = get_db()
    session = next(gen)
    try:
        assert session.execute(text("SELECT 1")).scalar() == 1
    finally:
        # 触发 finally 关闭
        try:
            next(gen)
        except StopIteration:
            pass
