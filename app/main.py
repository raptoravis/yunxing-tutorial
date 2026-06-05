"""FastAPI 应用入口：挂载 static、模板、路由，启动建表，安全头中间件。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from .config import settings
from .db import init_db

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """全站安全头：HTTPS 重定向 + HSTS（R15）。管理页另加 no-store/noindex。"""

    async def dispatch(self, request: Request, call_next):  # noqa: ANN001
        if settings.force_https and request.url.scheme == "http":
            https_url = request.url.replace(scheme="https")
            return RedirectResponse(str(https_url), status_code=307)
        response: Response = await call_next(request)
        if settings.force_https:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
            )
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN001
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="投票系统", lifespan=lifespan)
    app.add_middleware(SecurityHeadersMiddleware)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # 路由在各实现单元中挂载（U3 polls、U5 results、U7 admin）。
    from .routes import admin, polls, results

    app.include_router(polls.router)
    app.include_router(results.router)
    app.include_router(admin.router)

    return app


app = create_app()
