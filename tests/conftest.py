"""共享测试夹具。

单个会话级临时 SQLite 文件，避免 Windows 上 WAL/-shm 文件占用导致的删除失败。
每个测试前 drop_all + create_all 保证隔离。环境变量在导入任何 app 模块前设置
（config 在 import 时读取）。
"""
from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

import pytest

_TMP_DIR = Path(tempfile.gettempdir()) / "voting_tests"
_TMP_DIR.mkdir(exist_ok=True)
_DB_PATH = _TMP_DIR / f"session_{uuid.uuid4().hex}.db"
os.environ["VOTING_DATABASE_URL"] = f"sqlite:///{_DB_PATH.as_posix()}"

# 在环境变量就位后导入 app（绑定到临时库）。
from app import db as db_mod  # noqa: E402
from app import models  # noqa: E402,F401  确保映射注册
from app.main import app as fastapi_app  # noqa: E402


def pytest_sessionfinish(session, exitstatus):  # noqa: ANN001
    db_mod.engine.dispose()
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(_DB_PATH) + suffix)
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass


@pytest.fixture(autouse=True)
def _fresh_schema():
    """每个测试前重建表并重置进程内限速状态，保证隔离。"""
    db_mod.Base.metadata.drop_all(bind=db_mod.engine)
    db_mod.Base.metadata.create_all(bind=db_mod.engine)
    from app.dedup import rate_limiter

    rate_limiter.reset()
    yield


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    with TestClient(fastapi_app) as c:
        yield c


@pytest.fixture()
def Session():
    return db_mod.SessionLocal


@pytest.fixture()
def engine():
    return db_mod.engine
