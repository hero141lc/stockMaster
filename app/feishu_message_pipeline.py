from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.config import get_settings
from app.feishu_notify import reply_to_message
from app.metaso_client import MetasoClient
from app.rag_reply import generate_analyst_reply

logger = logging.getLogger(__name__)

_AT_PATTERN = re.compile(r"<at[^>]*>.*?</at>", re.I | re.S)
_USER_MENTION = re.compile(r"@[^\s]+\s*")


def strip_mentions(text: str) -> str:
    t = _AT_PATTERN.sub("", text)
    t = _USER_MENTION.sub("", t)
    return t.strip()


def extract_message_event(body: dict[str, Any]) -> dict[str, Any] | None:
    header = body.get("header") or {}
    et = header.get("event_type")
    event = body.get("event")
    if isinstance(event, dict) and "message" in event:
        msg = event["message"]
    elif isinstance(body.get("message"), dict):
        msg = body["message"]
    else:
        return None

    if et and et != "im.message.receive_v1":
        return None

    if not isinstance(msg, dict):
        return None

    chat_id = msg.get("chat_id")
    if not chat_id:
        return None

    msg_type = msg.get("message_type")
    if msg_type != "text":
        return {
            "chat_id": chat_id,
            "text": "",
            "unsupported": True,
            "message_id": msg.get("message_id"),
            "root_id": msg.get("root_id"),
        }

    raw_content = msg.get("content") or "{}"
    try:
        if isinstance(raw_content, str):
            cj = json.loads(raw_content)
        else:
            cj = raw_content
        text = str(cj.get("text", ""))
    except json.JSONDecodeError:
        text = str(raw_content)

    text = strip_mentions(text)
    return {
        "chat_id": chat_id,
        "text": text,
        "unsupported": False,
        "message_id": msg.get("message_id"),
        "root_id": msg.get("root_id"),
    }


def format_metaso_reply(q: str, hits: list[dict[str, Any]]) -> str:
    if not hits:
        return f"未找到与「{q}」相关的结果。"
    lines: list[str] = [f"密塔 · 「{q}」", ""]
    for i, h in enumerate(hits[:10], 1):
        title = h.get("title") or "(无标题)"
        url = h.get("url") or ""
        summ = (h.get("summary") or "").replace("\n", " ")
        if len(summ) > 220:
            summ = summ[:220] + "…"
        line = f"{i}. {title}\n{summ}\n{url}".strip()
        lines.append(line)
        lines.append("")
    out = "\n".join(lines).strip()
    if len(out) > 9000:
        out = out[:8900] + "\n…（已截断）"
    return out


async def _metaso_fallback(q: str) -> str:
    """走老的「单次搜索 + 列表展示」路径，作为 RAG 失败时的兜底。"""
    try:
        client = MetasoClient()
        data = await client.search(q)
        hits = MetasoClient.iter_hits(data)
    except Exception as e:
        logger.exception("metaso fallback search failed")
        return f"搜索失败：{e}"
    return format_metaso_reply(q, hits)


async def build_interactive_reply(
    q: str,
    hits: list[dict[str, Any]] | None = None,
    *,
    fast: bool = False,
) -> str:
    """生成对用户问题的回复。

    - REPLY_MODE=rag（默认）：调用金融分析师 Agent（自主多轮密塔检索 + 综合）。
      `hits` 参数被忽略。Agent 失败时回退为「先搜一次 + 列表展示」。
    - REPLY_MODE=search：旧版行为，仅展示密塔搜索结果列表。
      若未传入 `hits`，会现场搜一次。

    fast=True：用于响应时间敏感的渠道（如企微被动回复 5 秒上限），
    将 max_tool_calls 限制为 1，并使用较短超时；失败仍走 metaso 兜底。
    """
    s = get_settings()
    mode = (s.reply_mode or "rag").strip().lower()

    if mode == "search":
        if not hits:
            try:
                client = MetasoClient()
                data = await client.search(q)
                hits = MetasoClient.iter_hits(data)
            except Exception as e:
                logger.exception("metaso search failed for q=%r", q)
                return f"搜索失败：{e}"
        return format_metaso_reply(q, hits or [])

    # rag mode
    if not (s.llm_api_key or "").strip():
        logger.warning("REPLY_MODE=rag 但未配置 LLM_API_KEY，回退为列表模式")
        if hits:
            return format_metaso_reply(q, hits)
        return await _metaso_fallback(q)

    try:
        if fast:
            return await generate_analyst_reply(q, max_tool_calls=1, timeout=4.0)
        return await generate_analyst_reply(q)
    except Exception:
        logger.exception("analyst agent failed; fallback to list mode")
        if hits:
            return format_metaso_reply(q, hits)
        return await _metaso_fallback(q)


async def handle_incoming_message_event(msg_ev: dict[str, Any]) -> None:
    if msg_ev.get("unsupported"):
        await reply_to_message(
            msg_ev["chat_id"],
            "当前仅支持文本消息搜索，请发送文字关键词。",
            root_id=msg_ev.get("root_id") or msg_ev.get("message_id"),
        )
        return

    q = (msg_ev.get("text") or "").strip()
    if not q:
        await reply_to_message(
            msg_ev["chat_id"],
            "请直接输入要搜索的内容，或 @机器人 后输入关键词。",
            root_id=msg_ev.get("root_id") or msg_ev.get("message_id"),
        )
        return

    s = get_settings()
    mode = (s.reply_mode or "rag").strip().lower()

    if mode == "rag":
        # rag 模式：由 analyst agent 内部自主搜索，省掉外部预搜索
        out = await build_interactive_reply(q)
    else:
        # search 模式：保留原行为（外部先搜一次 → 列表展示）
        try:
            client = MetasoClient()
            data = await client.search(q)
            hits = MetasoClient.iter_hits(data)
        except Exception as e:
            logger.exception("Metaso search failed")
            await reply_to_message(
                msg_ev["chat_id"],
                f"密塔搜索失败：{e}",
                root_id=msg_ev.get("root_id") or msg_ev.get("message_id"),
            )
            return
        out = await build_interactive_reply(q, hits)

    await reply_to_message(
        msg_ev["chat_id"],
        out,
        root_id=msg_ev.get("root_id") or msg_ev.get("message_id"),
    )
