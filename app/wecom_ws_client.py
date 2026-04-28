from __future__ import annotations

import asyncio
import logging
import multiprocessing

from app.config import get_settings
from app.feishu_message_pipeline import build_interactive_reply
from app.metaso_client import MetasoClient

logger = logging.getLogger(__name__)

_wecom_ws_process: multiprocessing.Process | None = None


def run_wecom_ws_worker() -> None:
    """
    企业微信智能机器人长连接 worker（Bot ID + Secret）。
    在子进程启动独立 asyncio loop，避免与 uvicorn 主进程循环互相影响。
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_run_wecom_ws_async())


async def _run_wecom_ws_async() -> None:
    from wecom_aibot_sdk import WSClient
    from wecom_aibot_sdk.types.config import WSClientOptions
    from wecom_aibot_sdk.utils import generate_req_id

    s = get_settings()
    if not s.wecom_bot_id or not s.wecom_bot_secret:
        logger.error("WECOM_BOT_ID / WECOM_BOT_SECRET 未配置，企微长连接未启动")
        return

    client = WSClient(
        WSClientOptions(
            bot_id=s.wecom_bot_id,
            secret=s.wecom_bot_secret,
        )
    )

    async def on_text(frame) -> None:
        body = frame.body if hasattr(frame, "body") else {}
        text_obj = body.get("text", {}) if isinstance(body, dict) else {}
        q = str(text_obj.get("content", "")).strip()
        stream_id = generate_req_id("stream")

        if not q:
            await client.reply_stream(
                frame,
                stream_id,
                "请发送要搜索的关键词（密塔）。",
                finish=True,
            )
            return

        try:
            data = await MetasoClient().search(q)
            hits = MetasoClient.iter_hits(data)
            if not hits:
                out = f"未找到与「{q}」相关的结果。"
            else:
                out = await build_interactive_reply(q, hits)
        except Exception as e:
            logger.exception("wecom ws metaso search failed")
            out = f"搜索失败：{e}"

        if len(out) > 3500:
            out = out[:3400] + "\n…（已截断）"

        await client.reply_stream(frame, stream_id, out, finish=True)

    client.on("message.text", on_text)
    logger.info("企业微信智能机器人长连接启动中…")
    await client.connect_async()
    await asyncio.Event().wait()


def start_wecom_ws_background_process() -> multiprocessing.Process:
    global _wecom_ws_process
    proc = multiprocessing.Process(target=run_wecom_ws_worker, name="wecom-ws", daemon=True)
    proc.start()
    _wecom_ws_process = proc
    return proc


def get_wecom_ws_process() -> multiprocessing.Process | None:
    return _wecom_ws_process
