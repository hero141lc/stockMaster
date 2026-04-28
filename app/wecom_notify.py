from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

_WECOM_API = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send"


def _webhook_url() -> str | None:
    s = get_settings()
    u = (s.wecom_webhook_url or "").strip()
    if u:
        return u
    k = (s.wecom_webhook_key or "").strip()
    if k:
        return f"{_WECOM_API}?key={k}"
    return None


async def send_wecom_group_robot_text(text: str) -> None:
    """企业微信群机器人 webhook（文本）。单条上限约 2048 字节，超出截断。"""
    url = _webhook_url()
    if not url:
        raise RuntimeError("未配置 WECOM_WEBHOOK_URL 或 WECOM_WEBHOOK_KEY")

    raw = text.encode("utf-8")
    if len(raw) > 2040:
        text = raw[:2030].decode("utf-8", errors="ignore") + "…"

    payload: dict[str, Any] = {
        "msgtype": "text",
        "text": {"content": text},
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
    err = data.get("errcode", 0)
    if err != 0:
        raise RuntimeError(f"企业微信 webhook 错误: {data}")
