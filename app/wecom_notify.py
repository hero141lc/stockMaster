from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

_WECOM_API = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send"


def _normalize_wecom_webhook_url(u: str) -> str:
    """
    支持三种写法：
    - 完整 https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...
    - 仅 key（常见误把 key 填进 WECOM_WEBHOOK_URL）
    - 缺协议的企微域名路径，自动补 https://
    """
    u = u.strip()
    if not u:
        return u
    low = u.lower()
    if low.startswith("http://") or low.startswith("https://"):
        return u
    if "qyapi.weixin.qq.com" in low:
        return f"https://{u}" if not u.lower().startswith("//") else f"https:{u}"
    return f"{_WECOM_API}?key={u}"


def _webhook_url() -> str | None:
    s = get_settings()
    u = (s.wecom_webhook_url or "").strip()
    if u:
        return _normalize_wecom_webhook_url(u)
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
