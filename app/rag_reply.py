from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


def _build_context_block(hits: list[dict[str, Any]], *, max_total_chars: int = 16000) -> str:
    parts: list[str] = []
    total = 0
    for i, h in enumerate(hits[:12], 1):
        title = str(h.get("title") or "").strip() or "(无标题)"
        url = str(h.get("url") or "").strip()
        summ = str(h.get("summary") or "").replace("\n", " ").strip()
        raw = str(h.get("raw") or "").replace("\n", " ").strip()
        if len(summ) > 800:
            summ = summ[:800] + "…"
        if len(raw) > 1200:
            raw = raw[:1200] + "…"
        block = f"[{i}] {title}\n摘要：{summ}"
        if raw:
            block += f"\n摘录：{raw}"
        block += f"\n链接：{url}"
        if total + len(block) > max_total_chars:
            break
        parts.append(block)
        total += len(block) + 2
    return "\n\n".join(parts)


async def generate_rag_reply(query: str, hits: list[dict[str, Any]]) -> str:
    """用检索到的资料作为上下文，调用 LLM 生成回答（OpenAI 兼容 /v1/chat/completions）。"""
    s = get_settings()
    key = (s.llm_api_key or "").strip()
    if not key:
        raise RuntimeError("LLM_API_KEY 未配置")

    context = _build_context_block(hits)
    if not context.strip():
        raise RuntimeError("上下文为空")

    system = (
        "你是专业的财经与资讯助理。请严格根据用户提供的「参考资料」回答用户问题。"
        "若资料不足以回答，请明确说明，不要编造参考资料中不存在的事实。"
        "用简体中文作答，条理清晰；必要时用简短列表。可在结尾用一行标注主要依据的参考资料序号（如：依据 [1][3]）。"
    )
    user_content = f"参考资料：\n{context}\n\n用户问题：{query.strip()}"

    base = s.llm_api_base.rstrip("/")
    url = f"{base}/chat/completions"
    payload: dict[str, Any] = {
        "model": s.llm_model,
        "temperature": 0.3,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(timeout=s.llm_timeout) as client:
        r = await client.post(url, headers=headers, json=payload)
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            detail = r.text[:500] if r.text else str(e)
            logger.error("LLM HTTP %s: %s", r.status_code, detail)
            raise RuntimeError(f"LLM 请求失败 HTTP {r.status_code}") from e
        data = r.json()

    choices = data.get("choices")
    if not choices or not isinstance(choices, list):
        raise RuntimeError("LLM 响应无 choices")
    msg = choices[0].get("message") or {}
    content = msg.get("content")
    if not content or not str(content).strip():
        raise RuntimeError("LLM 返回空内容")
    out = str(content).strip()
    if len(out) > 12000:
        out = out[:11800] + "\n…（已截断）"
    return out
