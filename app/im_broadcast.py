from __future__ import annotations

import logging

from app.config import get_settings
from app.feishu_notify import send_text_to_chat, send_webhook_text
from app.wecom_notify import send_wecom_group_robot_text

logger = logging.getLogger(__name__)


async def push_notification(text: str) -> None:
    """按 IM_PROVIDER 将简报/预警推到已配置的飞书 / 企业微信渠道。"""
    s = get_settings()
    for p in s.parsed_im_providers():
        if p == "feishu":
            await _push_feishu(text)
        elif p == "wecom":
            try:
                await send_wecom_group_robot_text(text)
            except Exception:
                logger.exception("企业微信群机器人推送失败")


async def _push_feishu(text: str) -> None:
    s = get_settings()
    if s.feishu_chat_id:
        try:
            await send_text_to_chat(s.feishu_chat_id, text)
        except Exception:
            logger.exception("飞书 API 发群失败")
    if s.feishu_webhook_url:
        try:
            await send_webhook_text(s.feishu_webhook_url, text)
        except Exception:
            logger.exception("飞书 Webhook 推送失败")
