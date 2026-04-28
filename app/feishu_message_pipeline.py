from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.config import get_settings
from app.feishu_notify import reply_to_message
from app.metaso_client import MetasoClient
from app.rag_reply import generate_rag_reply

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


async def build_interactive_reply(q: str, hits: list[dict[str, Any]]) -> str:
    """根据 REPLY_MODE 生成对用户问题的回复（RAG 或原始列表）。"""
    s = get_settings()
    mode = (s.reply_mode or "rag").strip().lower()
    if mode == "search":
        return format_metaso_reply(q, hits)
    if not (s.llm_api_key or "").strip():
        logger.warning("REPLY_MODE=rag 但未配置 LLM_API_KEY，使用列表模式")
        return format_metaso_reply(q, hits)
    try:
        return await generate_rag_reply(q, hits)
    except Exception:
        logger.exception("RAG 生成失败，回退为列表模式")
        return format_metaso_reply(q, hits)


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
            "请直接输入要搜索的内容，或 @机器人 后输入关键词（密塔搜索）。",
            root_id=msg_ev.get("root_id") or msg_ev.get("message_id"),
        )
        return

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

    if not hits:
        await reply_to_message(
            msg_ev["chat_id"],
            f"未找到与「{q}」相关的结果。",
            root_id=msg_ev.get("root_id") or msg_ev.get("message_id"),
        )
        return

    out = await build_interactive_reply(q, hits)
    await reply_to_message(
        msg_ev["chat_id"],
        out,
        root_id=msg_ev.get("root_id") or msg_ev.get("message_id"),
    )
