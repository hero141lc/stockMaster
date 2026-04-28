from __future__ import annotations

import asyncio
import logging
import multiprocessing
from typing import TYPE_CHECKING, Any

from app.config import get_settings
from app.feishu_message_pipeline import extract_message_event, handle_incoming_message_event

if TYPE_CHECKING:
    from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

logger = logging.getLogger(__name__)

_ws_process: multiprocessing.Process | None = None


def _schedule_handle(msg_ev: dict[str, Any] | None) -> None:
    if not msg_ev:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.get_event_loop()
    loop.create_task(handle_incoming_message_event(msg_ev))


def _on_p2_im_message_receive_v1(data: "P2ImMessageReceiveV1") -> None:
    try:
        ev = data.event
        if not ev:
            return
        sender = ev.sender
        if sender and getattr(sender, "sender_type", None) == "app":
            return
        msg = ev.message
        if not msg:
            return
        body = {
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "message": {
                    "chat_id": msg.chat_id,
                    "message_type": msg.message_type,
                    "content": msg.content,
                    "message_id": msg.message_id,
                    "root_id": msg.root_id or "",
                }
            },
        }
        msg_ev = extract_message_event(body)
        _schedule_handle(msg_ev)
    except Exception:
        logger.exception("feishu ws: handle im.message.receive_v1 failed")


def run_feishu_ws_worker() -> None:
    """
    在独立进程中运行飞书长连接客户端（lark-oapi WebSocket）。
    须在子进程内先设置 asyncio 事件循环，再 import lark_oapi.ws.client，避免与 uvicorn 抢全局 loop。
    """
    import asyncio

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    from lark_oapi.api.im.v1 import P2ImMessageReceiveV1  # noqa: F401
    from lark_oapi.core.enum import LogLevel
    from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
    from lark_oapi.ws.client import Client

    s = get_settings()
    if not s.feishu_app_id or not s.feishu_app_secret:
        logger.error("FEISHU_APP_ID / FEISHU_APP_SECRET 未配置，长连接未启动")
        return

    encrypt_key = s.feishu_encrypt_key or ""
    verify_token = s.feishu_verification_token or ""

    handler = (
        EventDispatcherHandler.builder(encrypt_key, verify_token)
        .register_p2_im_message_receive_v1(_on_p2_im_message_receive_v1)
        .build()
    )

    cli = Client(
        s.feishu_app_id,
        s.feishu_app_secret,
        event_handler=handler,
        log_level=LogLevel.INFO,
        auto_reconnect=True,
    )
    logger.info("飞书长连接客户端启动中…")
    cli.start()


def start_ws_background_process() -> multiprocessing.Process:
    global _ws_process
    proc = multiprocessing.Process(target=run_feishu_ws_worker, name="feishu-ws", daemon=True)
    proc.start()
    _ws_process = proc
    return proc


def get_ws_process() -> multiprocessing.Process | None:
    return _ws_process
