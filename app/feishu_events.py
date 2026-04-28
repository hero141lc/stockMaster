from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.feishu_crypto import decrypt_feishu_event
from app.feishu_message_pipeline import extract_message_event, handle_incoming_message_event

router = APIRouter()


def _parse_event_body(raw: dict[str, Any]) -> dict[str, Any]:
    s = get_settings()
    if "encrypt" in raw and s.feishu_encrypt_key:
        inner = decrypt_feishu_event(s.feishu_encrypt_key, str(raw["encrypt"]))
        return inner if isinstance(inner, dict) else {}
    return raw


def _find_url_verification(body: dict[str, Any]) -> dict[str, Any] | None:
    if body.get("type") == "url_verification" and "challenge" in body:
        return {"challenge": body["challenge"], "token": body.get("token")}
    header = body.get("header") or {}
    et = header.get("event_type")
    if et in ("url_verification", "http_callback_verification"):
        ev = body.get("event") or {}
        ch = ev.get("challenge")
        if ch:
            return {"challenge": ch, "token": header.get("token") or body.get("token")}
    if body.get("schema") == "2.0" and isinstance(body.get("event"), dict):
        ev = body["event"]
        if "challenge" in ev:
            return {
                "challenge": ev["challenge"],
                "token": header.get("token"),
            }
    return None


@router.post("/feishu/events")
async def feishu_events(request: Request) -> JSONResponse:
    s = get_settings()
    if not s.feishu_http_events_enabled:
        raise HTTPException(status_code=404, detail="HTTP 事件回调未启用（当前使用长连接时可在 .env 关闭）")

    try:
        raw = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json")

    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="invalid body")

    body = _parse_event_body(raw)

    uv = _find_url_verification(body)
    if uv:
        if s.feishu_verification_token and uv.get("token") != s.feishu_verification_token:
            raise HTTPException(status_code=403, detail="verification token mismatch")
        return JSONResponse(content={"challenge": uv["challenge"]})

    msg_ev = extract_message_event(body)
    if not msg_ev:
        return JSONResponse(content={})

    await handle_incoming_message_event(msg_ev)
    return JSONResponse(content={})
