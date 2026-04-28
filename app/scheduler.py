from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import get_settings
from app.db import get_state, init_db, set_state, try_insert_dedup
from app.im_broadcast import push_notification
from app.metaso_client import MetasoClient

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def _has_broadcast_channel() -> bool:
    s = get_settings()
    for p in s.parsed_im_providers():
        if p == "feishu" and (s.feishu_chat_id or s.feishu_webhook_url):
            return True
        if p == "wecom" and (s.wecom_webhook_key or s.wecom_webhook_url):
            return True
    return False


def _format_hits_block(title: str, hits: list[dict]) -> str:
    lines = [title, ""]
    for i, h in enumerate(hits[:15], 1):
        t = h.get("title") or "(无标题)"
        u = h.get("url") or ""
        s = (h.get("summary") or "").replace("\n", " ")
        if len(s) > 180:
            s = s[:180] + "…"
        lines.append(f"{i}. {t}\n{s}\n{u}")
        lines.append("")
    return "\n".join(lines).strip()


async def run_digest() -> None:
    s = get_settings()
    queries = s.parsed_digest_queries()
    if not queries:
        logger.warning("DIGEST_QUERIES 为空，跳过简报")
        return
    if not _has_broadcast_channel():
        logger.warning("未配置任何推送渠道（飞书群/Webhook 或企业微信群机器人），跳过简报")
        return

    client = MetasoClient()
    merged: list[dict] = []
    seen: set[str] = set()
    for q in queries:
        try:
            data = await client.search(q)
            for h in MetasoClient.iter_hits(data):
                hid = h.get("id")
                if not hid or hid in seen:
                    continue
                if not try_insert_dedup("digest", hid):
                    continue
                seen.add(hid)
                merged.append(h)
        except Exception:
            logger.exception("digest query failed: %s", q)

    if not merged:
        return

    tz = ZoneInfo(s.tz)
    now = datetime.now(tz).strftime("%Y-%m-%d %H:%M")
    text = _format_hits_block(f"【资讯简报】{now}（密塔）", merged)
    if len(text) > 9500:
        text = text[:9300] + "\n…（已截断）"
    await push_notification(text)


async def run_alerts() -> None:
    s = get_settings()
    kws = s.parsed_alert_keywords()
    if not kws:
        return
    if not _has_broadcast_channel():
        return

    try:
        idx = int(get_state("alert_kw_idx", "0"))
    except ValueError:
        idx = 0
    batch_size = 4
    batch = []
    for i in range(batch_size):
        if not kws:
            break
        batch.append(kws[(idx + i) % len(kws)])
    set_state("alert_kw_idx", str((idx + batch_size) % max(len(kws), 1)))

    client = MetasoClient()
    for kw in batch:
        q = f"财经 突发 快讯 {kw}"
        try:
            data = await client.search(q, size=6)
            hits = MetasoClient.iter_hits(data)
        except Exception:
            logger.exception("alert search failed: %s", kw)
            continue
        for h in hits:
            hid = h.get("id")
            if not hid:
                continue
            if not try_insert_dedup("alert", hid):
                continue
            title = h.get("title") or ""
            url = h.get("url") or ""
            summ = (h.get("summary") or "").replace("\n", " ")[:400]
            msg = (
                f"【关键词预警】匹配「{kw}」\n{title}\n{summ}\n{url}".strip()
            )
            if len(msg) > 9000:
                msg = msg[:8900] + "…"
            try:
                await push_notification(msg)
            except Exception:
                logger.exception("alert broadcast failed")


def setup_scheduler() -> AsyncIOScheduler:
    global _scheduler
    init_db()
    s = get_settings()
    tz = ZoneInfo(s.tz)
    sched = AsyncIOScheduler(timezone=tz)

    times = s.parsed_digest_times()
    for h, m in times:
        sched.add_job(
            run_digest,
            "cron",
            hour=h,
            minute=m,
            id=f"digest_{h}_{m}",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

    sched.add_job(
        run_alerts,
        "interval",
        minutes=max(1, s.alert_interval_minutes),
        id="alerts",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    _scheduler = sched
    return sched


def get_scheduler() -> AsyncIOScheduler | None:
    return _scheduler
