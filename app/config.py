"""应用设置。环境变量可覆盖默认值（DB URL、HTTPS 强制开关等）。"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.environ.get("VOTING_DATABASE_URL", "sqlite:///./voting.db")
    # 生产置真：强制 HTTPS 重定向 + HSTS（R15）。测试/本地默认关闭。
    force_https: bool = os.environ.get("VOTING_FORCE_HTTPS", "0") == "1"
    # 仅当部署在可信反向代理之后才置真：信任 X-Forwarded-For 取真实客户端 IP。
    # 默认关闭——直连暴露时 XFF 由客户端控制，信任它会让 per-IP 限速被伪造头绕过。
    trust_proxy: bool = os.environ.get("VOTING_TRUST_PROXY", "0") == "1"
    # 软性 per-IP 限速：窗口秒数与窗口内最大提交次数（KTD7 / R-2）。
    rate_limit_window_seconds: int = int(os.environ.get("VOTING_RATE_WINDOW", "10"))
    rate_limit_max_submits: int = int(os.environ.get("VOTING_RATE_MAX", "5"))

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


def get_settings() -> Settings:
    return Settings()


settings = get_settings()
