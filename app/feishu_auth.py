from __future__ import annotations

import time
from typing import Any

import httpx

from app.config import get_settings

_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"

_cached: dict[str, Any] = {"token": "", "expire": 0.0}


async def get_tenant_access_token() -> str:
    s = get_settings()
    if not s.feishu_app_id or not s.feishu_app_secret:
        raise RuntimeError("FEISHU_APP_ID / FEISHU_APP_SECRET 未配置")
    now = time.time()
    if _cached["token"] and now < _cached["expire"] - 60:
        return str(_cached["token"])

    payload = {"app_id": s.feishu_app_id, "app_secret": s.feishu_app_secret}
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(_TOKEN_URL, json=payload)
        r.raise_for_status()
        data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取 tenant_access_token 失败: {data}")
    token = str(data["tenant_access_token"])
    expire = int(data.get("expire", 7200))
    _cached["token"] = token
    _cached["expire"] = now + expire
    return token
