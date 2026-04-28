from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# 从 app/ 目录执行 python main.py 时，需把项目根目录加入 path，才能 import app.*
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from fastapi import FastAPI

from app.config import get_settings
from app.db import init_db
from app.feishu_events import router as feishu_router
from app.feishu_ws_client import get_ws_process, start_ws_background_process
from app.scheduler import get_scheduler, setup_scheduler
from app.wecom_callback import router as wecom_router
from app.wecom_ws_client import (
    get_wecom_ws_process,
    start_wecom_ws_background_process,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    sched = setup_scheduler()
    sched.start()
    ws_proc = None
    wecom_ws_proc = None
    s = get_settings()
    use_feishu = "feishu" in s.parsed_im_providers()
    use_wecom = "wecom" in s.parsed_im_providers()
    if s.feishu_ws_enabled and use_feishu:
        try:
            ws_proc = start_ws_background_process()
            logging.getLogger(__name__).info(
                "飞书事件：长连接子进程已启动 pid=%s", ws_proc.pid
            )
        except Exception:
            logging.getLogger(__name__).exception("飞书长连接启动失败")
    if s.wecom_ws_enabled and use_wecom:
        try:
            wecom_ws_proc = start_wecom_ws_background_process()
            logging.getLogger(__name__).info(
                "企微机器人：长连接子进程已启动 pid=%s", wecom_ws_proc.pid
            )
        except Exception:
            logging.getLogger(__name__).exception("企微长连接启动失败")
    yield
    if ws_proc is not None and ws_proc.is_alive():
        ws_proc.terminate()
        ws_proc.join(timeout=8)
    if wecom_ws_proc is not None and wecom_ws_proc.is_alive():
        wecom_ws_proc.terminate()
        wecom_ws_proc.join(timeout=8)
    sched.shutdown(wait=False)


app = FastAPI(title="Feishu Metaso News Bot", lifespan=lifespan)
app.include_router(feishu_router)
app.include_router(wecom_router)


@app.get("/health")
async def health() -> dict:
    s = get_settings()
    sched = get_scheduler()
    p = get_ws_process()
    p_wecom = get_wecom_ws_process()
    ws_alive = bool(p and p.is_alive())
    wecom_ws_alive = bool(p_wecom and p_wecom.is_alive())
    return {
        "ok": True,
        "tz": s.tz,
        "im_provider": s.im_provider,
        "im_providers": s.parsed_im_providers(),
        "scheduler_running": bool(sched and sched.running),
        "feishu_ws_enabled": s.feishu_ws_enabled,
        "feishu_ws_process_alive": ws_alive,
        "feishu_http_events_enabled": s.feishu_http_events_enabled,
        "wecom_ws_enabled": s.wecom_ws_enabled,
        "wecom_ws_process_alive": wecom_ws_alive,
        "wecom_bot_configured": bool(
            (s.wecom_bot_id or "").strip() and (s.wecom_bot_secret or "").strip()
        ),
        "wecom_webhook_configured": bool(
            (s.wecom_webhook_key or "").strip() or (s.wecom_webhook_url or "").strip()
        ),
        "wecom_callback_configured": bool(
            (s.wecom_corp_id or "").strip()
            and (s.wecom_callback_token or "").strip()
            and (s.wecom_encoding_aes_key or "").strip()
        ),
    }


def main() -> None:
    import uvicorn

    s = get_settings()
    uvicorn.run(
        "app.main:app",
        host=s.host,
        port=s.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
