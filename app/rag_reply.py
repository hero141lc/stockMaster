from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.config import get_settings
from app.metaso_client import MetasoClient

logger = logging.getLogger(__name__)


SYSTEM_PROMPTS: dict[str, str] = {
    # A 股 / 港股 / 美股 卖方研究员风格
    "cn_sellside": (
        "你是一位资深卖方金融分析师，覆盖 A 股、港股、美股，熟悉宏观经济、行业景气、"
        "公司基本面、资金面与情绪面分析，习惯用研究报告的框架组织观点。\n"
        "\n"
        "【工作方式】\n"
        "1. 收到用户问题后，先评估自身已掌握的信息是否足以回答。如果涉及行情/政策/公告/"
        "数据/事件等时效性内容，或对结论的关键依据存疑，必须调用 metaso_search 工具补充资料，"
        "再作答。\n"
        "2. 复杂问题应主动拆解为多个维度（宏观流动性 / 政策 / 行业景气 / 公司基本面 / "
        "资金 / 海外联动），分多次调用 metaso_search，每次用一个聚焦的子查询。\n"
        "3. 简单的概念解释或常识性问题可不调用工具直接作答。\n"
        "4. 严禁编造检索资料中不存在的事实、数字或公司名称。如关键数据缺失，明确写出"
        "「暂未获取到 XX 数据」。\n"
        "\n"
        "【输出结构】用简体中文，按以下骨架组织最终答复（章节标题用 Markdown 加粗）：\n"
        "**核心观点**：1–2 句给出整体判断与方向。\n"
        "**驱动因子**：分点说明，按 宏观/政策/资金/行业/公司/海外 等维度展开（按需选取）。\n"
        "**关键数据与估值**：列出从资料中读到的可量化指标（PE/PB/股息率/增速/对标/成交量/"
        "北向南向资金等）；无数据时说明。\n"
        "**风险与不确定性**：列出 2–4 条主要风险点。\n"
        "**操作建议**：仅给出定性方向（关注 / 谨慎乐观 / 观望 / 规避 等），不给出具体目标价、"
        "买卖点、仓位、止损位。\n"
        "**依据来源**：在前文事实陈述后用 [n] 标注引用；本节列出关键 [n] 与对应链接。\n"
        "\n"
        "【免责】结尾固定附一行：「以上内容基于公开资料整理，仅供研究参考，不构成投资建议；"
        "市场有风险，决策需谨慎。」\n"
        "\n"
        "【风格】克制、客观、避免空话与煽动性表述；多用条件句（若…则…）；不要使用「绝对会」"
        "「必涨」「稳赚」等表达。"
    ),
}


METASO_TOOL_SCHEMA: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "metaso_search",
            "description": (
                "调用密塔（Metaso）中文搜索引擎获取最新的财经/资讯/政策/公告类网页结果。"
                "当问题涉及实时行情、近期政策、公司动态、宏观数据、行业事件、海外联动等"
                "需要外部信息时必须调用。可多次调用以拆分子问题（宏观/行业/个股/海外），"
                "但请避免使用几乎相同的关键词重复搜索。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "q": {
                        "type": "string",
                        "description": (
                            "搜索查询词，建议中文关键词组合，并尽量带上时间或限定词。"
                            "例：'港股科技股 回调原因 2026 年 5 月'、'A 股 北向资金 流入 本周'。"
                        ),
                    },
                    "size": {
                        "type": "integer",
                        "description": "返回条数，3~15 之间，默认 8。",
                        "minimum": 3,
                        "maximum": 15,
                    },
                },
                "required": ["q"],
            },
        },
    }
]


async def _exec_metaso_search(
    q: str,
    size: int,
    accumulated: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """执行一次密塔搜索，按 hit id 去重并累积到 accumulated 中（带 __idx 全局编号）。"""
    client = MetasoClient()
    data = await client.search(q, size=size)
    raw_hits = MetasoClient.iter_hits(data)
    seen_ids = {h["id"] for h in accumulated}
    new_hits: list[dict[str, Any]] = []
    for h in raw_hits:
        hid = h.get("id")
        if not hid or hid in seen_ids:
            continue
        seen_ids.add(hid)
        item = dict(h)
        item["__idx"] = len(accumulated) + 1
        accumulated.append(item)
        new_hits.append(item)
    return new_hits


def _format_tool_result(
    q: str,
    new_hits: list[dict[str, Any]],
    accumulated: list[dict[str, Any]],
) -> str:
    if not new_hits:
        return (
            f"对「{q}」的搜索未返回新资料（可能与已有结果重复，或密塔暂无相关网页）。"
            f"当前累计资料 {len(accumulated)} 条。"
        )
    parts: list[str] = [
        f"对「{q}」获取到 {len(new_hits)} 条新资料（累计 {len(accumulated)} 条）。"
        "下文 [n] 为全局引用编号，可在最终答复中引用。",
    ]
    total = 0
    for h in new_hits:
        idx = h.get("__idx", 0)
        title = str(h.get("title") or "(无标题)").strip()
        url = str(h.get("url") or "").strip()
        summ = str(h.get("summary") or "").replace("\n", " ").strip()
        raw = str(h.get("raw") or "").replace("\n", " ").strip()
        if len(summ) > 400:
            summ = summ[:400] + "…"
        if len(raw) > 700:
            raw = raw[:700] + "…"
        block = f"[{idx}] {title}\n摘要：{summ}"
        if raw:
            block += f"\n摘录：{raw}"
        block += f"\n链接：{url}"
        if total + len(block) > 8000:
            parts.append("（超出长度，后续条目已省略）")
            break
        parts.append(block)
        total += len(block) + 2
    return "\n\n".join(parts)


def _select_system_prompt() -> str:
    s = get_settings()
    persona = (s.analyst_persona or "cn_sellside").strip().lower()
    return SYSTEM_PROMPTS.get(persona) or SYSTEM_PROMPTS["cn_sellside"]


async def generate_analyst_reply(
    query: str,
    *,
    max_tool_calls: int | None = None,
    timeout: float | None = None,
) -> str:
    """金融分析师 Agent：让 LLM 自主决定是否多次调用 metaso_search 工具，再综合输出。

    - max_tool_calls：允许的工具调用轮数上限（每轮 LLM 推理可发起一次或多次工具调用）。
      None 时取 settings.llm_max_tool_calls。fast 调用方可传 1 用于企微 5 秒被动回复场景。
    - timeout：单次 HTTP 请求的超时（秒），None 时取 settings.llm_timeout。
    """
    s = get_settings()
    key = (s.llm_api_key or "").strip()
    if not key:
        raise RuntimeError("LLM_API_KEY 未配置")

    max_rounds = (
        max_tool_calls if max_tool_calls is not None else s.llm_max_tool_calls
    )
    if max_rounds < 0:
        max_rounds = 0

    base = s.llm_api_base.rstrip("/")
    url = f"{base}/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _select_system_prompt()},
        {"role": "user", "content": query.strip()},
    ]
    accumulated_hits: list[dict[str, Any]] = []
    request_timeout = timeout if timeout is not None else s.llm_timeout

    async def _call_llm(client: httpx.AsyncClient, with_tools: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": s.llm_model,
            "temperature": s.llm_temperature,
            "messages": messages,
        }
        if with_tools:
            payload["tools"] = METASO_TOOL_SCHEMA
            payload["tool_choice"] = "auto"
        r = await client.post(url, headers=headers, json=payload)
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            detail = r.text[:500] if r.text else str(e)
            logger.error("LLM HTTP %s: %s", r.status_code, detail)
            raise RuntimeError(f"LLM 请求失败 HTTP {r.status_code}") from e
        return r.json()

    def _extract_final(msg: dict[str, Any]) -> str:
        content = msg.get("content")
        if not content or not str(content).strip():
            raise RuntimeError("LLM 返回空内容")
        out = str(content).strip()
        if len(out) > 12000:
            out = out[:11800] + "\n…（已截断）"
        return out

    async with httpx.AsyncClient(timeout=request_timeout) as client:
        for round_idx in range(max_rounds):
            data = await _call_llm(client, with_tools=True)
            choices = data.get("choices") or []
            if not choices:
                raise RuntimeError("LLM 响应无 choices")
            msg = choices[0].get("message") or {}
            tool_calls = msg.get("tool_calls") or []

            if not tool_calls:
                return _extract_final(msg)

            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": msg.get("content") or "",
                "tool_calls": tool_calls,
            }
            messages.append(assistant_msg)

            for tc in tool_calls:
                fn = tc.get("function") or {}
                tc_id = tc.get("id") or ""
                fname = fn.get("name") or ""
                fargs_raw = fn.get("arguments") or "{}"
                try:
                    fargs = (
                        json.loads(fargs_raw)
                        if isinstance(fargs_raw, str)
                        else dict(fargs_raw)
                    )
                except json.JSONDecodeError:
                    fargs = {}

                if fname != "metaso_search":
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "content": (
                                f"工具 {fname!r} 不存在；当前仅支持 metaso_search。"
                            ),
                        }
                    )
                    continue

                q = str(fargs.get("q") or "").strip()
                try:
                    size = int(fargs.get("size") or 8)
                except (TypeError, ValueError):
                    size = 8
                size = max(3, min(15, size))

                if not q:
                    tool_text = "搜索失败：参数 q 为空。"
                else:
                    try:
                        new_hits = await _exec_metaso_search(
                            q, size, accumulated_hits
                        )
                        tool_text = _format_tool_result(q, new_hits, accumulated_hits)
                    except Exception as e:
                        logger.exception("metaso tool failed for q=%r", q)
                        tool_text = f"密塔搜索失败：{e}"

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": tool_text,
                    }
                )
            logger.info(
                "analyst round=%s tool_calls=%s accumulated=%s",
                round_idx + 1,
                len(tool_calls),
                len(accumulated_hits),
            )

        # 工具轮次已用尽，强制收尾：禁用工具、追加系统提示要求基于现有资料作答
        messages.append(
            {
                "role": "system",
                "content": (
                    "已达到最大检索轮数。请仅基于上述已检索到的资料给出最终回答；"
                    "对资料不足以支撑的部分，请明确写出「信息不充分」并说明。"
                ),
            }
        )
        async with httpx.AsyncClient(timeout=request_timeout) as final_client:
            data = await _call_llm(final_client, with_tools=False)
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("LLM 响应无 choices（兜底轮）")
        return _extract_final(choices[0].get("message") or {})


# ---- 兼容保留：旧版「单轮 RAG」接口（外部已检索好 hits 后直接综合）----
async def generate_rag_reply(query: str, hits: list[dict[str, Any]]) -> str:
    """旧版单轮 RAG：直接基于传入的 hits 综合作答，不再触发工具调用。

    保留以便 search 模式或外部已有检索结果时复用；新代码请优先使用
    generate_analyst_reply()。
    """
    s = get_settings()
    key = (s.llm_api_key or "").strip()
    if not key:
        raise RuntimeError("LLM_API_KEY 未配置")

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
        if total + len(block) > 16000:
            break
        parts.append(block)
        total += len(block) + 2
    context = "\n\n".join(parts)
    if not context.strip():
        raise RuntimeError("上下文为空")

    user_content = f"参考资料：\n{context}\n\n用户问题：{query.strip()}"
    payload: dict[str, Any] = {
        "model": s.llm_model,
        "temperature": s.llm_temperature,
        "messages": [
            {"role": "system", "content": _select_system_prompt()},
            {"role": "user", "content": user_content},
        ],
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    base = s.llm_api_base.rstrip("/")
    url = f"{base}/chat/completions"
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
