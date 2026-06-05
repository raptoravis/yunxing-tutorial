"""capability-URL 安全模型（KTD4 / R15）。

管理密钥用 CSPRNG 生成（256 bit），服务端只存 SHA-256 哈希，校验用
constant-time 比较。公开投票 id 同样用 CSPRNG 生成，不可枚举。
"""
from __future__ import annotations

import hashlib
import secrets


def generate_poll_id() -> str:
    """公开投票 id：128 bit，URL 安全、不可枚举。"""
    return secrets.token_urlsafe(16)


def generate_admin_token() -> str:
    """管理密钥：256 bit（KTD4）。明文只在创建响应里出现一次。"""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """存储用哈希：SHA-256 hex。token 本身从不落库。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_token(token: str, stored_hash: str) -> bool:
    """constant-time 校验，避免时序侧信道（U7 测试覆盖）。"""
    if not token or not stored_hash:
        return False
    candidate = hash_token(token)
    return secrets.compare_digest(candidate, stored_hash)
