from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import get_settings
from app.feishu_auth import get_tenant_access_token

_MSG_URL = "https://open.feishu.cn/open-apis/im/v1/messages"


async def send_text_to_chat(chat_id: str, text: str) -> dict[str, Any]:
    if not chat_id:
        raise ValueError("chat_id 为空")
    token = await get_tenant_access_token()
    body = {
        "receive_id": chat_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    headers = {"Authorization": f"Bearer {token}"}
    params = {"receive_id_type": "chat_id"}
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(_MSG_URL, params=params, headers=headers, json=body)
        data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"飞书发消息失败: {data}")
    return data


async def reply_to_message(
    chat_id: str, text: str, *, root_id: str | None = None
) -> dict[str, Any]:
    """Reply in thread when root_id is provided (message_id of root)."""
    token = await get_tenant_access_token()
    body: dict[str, Any] = {
        "receive_id": chat_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    if root_id:
        body["root_id"] = root_id
    headers = {"Authorization": f"Bearer {token}"}
    params = {"receive_id_type": "chat_id"}
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(_MSG_URL, params=params, headers=headers, json=body)
        data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"飞书回复消息失败: {data}")
    return data


async def send_webhook_text(url: str, text: str) -> None:
    if not url:
        return
    payload = {"msg_type": "text", "content": {"text": text}}
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        try:
            data = r.json()
        except Exception:
            return
    if not isinstance(data, dict):
        return
    if data.get("StatusCode") == 0:
        return
    if data.get("code") in (0, None) and data.get("msg") in ("success", None):
        return
    if "StatusCode" not in data and "code" not in data:
        return
    if data.get("code") not in (0, None) and data.get("StatusCode") not in (0, None):
        raise RuntimeError(f"Webhook 推送失败: {data}")
