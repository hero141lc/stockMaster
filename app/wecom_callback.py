from __future__ import annotations

import logging
import random
import time
import xml.etree.ElementTree as ET
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from app.config import get_settings
from app.feishu_message_pipeline import build_interactive_reply
from app.metaso_client import MetasoClient

logger = logging.getLogger(__name__)

router = APIRouter()

try:
    from wechatpy.enterprise.crypto import WeChatCrypto
    from wechatpy.exceptions import InvalidSignatureException

    _HAS_WECOM_CRYPTO = True
except ImportError:
    _HAS_WECOM_CRYPTO = False
    WeChatCrypto = None  # type: ignore[misc, assignment]

    class InvalidSignatureException(Exception):
        pass


def _crypto() -> Any:
    s = get_settings()
    if not _HAS_WECOM_CRYPTO:
        raise HTTPException(
            status_code=503,
            detail="未安装 wechatpy，无法处理企业微信回调。请 pip install wechatpy",
        )
    if not (
        s.wecom_corp_id
        and s.wecom_callback_token
        and s.wecom_encoding_aes_key
    ):
        raise HTTPException(status_code=404, detail="企业微信回调未配置")
    return WeChatCrypto(
        s.wecom_callback_token,
        s.wecom_encoding_aes_key,
        s.wecom_corp_id,
    )


def _xml_text(root: ET.Element, tag: str) -> str:
    el = root.find(tag)
    if el is None or el.text is None:
        return ""
    return str(el.text)


@router.get("/wecom/callback")
async def wecom_verify(
    msg_signature: str,
    timestamp: str,
    nonce: str,
    echostr: str,
) -> Response:
    """企业微信 URL 验证（GET）。"""
    get_settings()  # 确保配置可读
    crypto = _crypto()
    try:
        plain = crypto.check_signature(msg_signature, timestamp, nonce, echostr)
    except InvalidSignatureException:
        raise HTTPException(status_code=403, detail="signature")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("wecom verify")
        raise HTTPException(status_code=400, detail=str(e)) from e

    if isinstance(plain, bytes):
        plain = plain.decode("utf-8", errors="replace")
    return Response(content=plain, media_type="text/plain")


@router.post("/wecom/callback")
async def wecom_receive(
    msg_signature: str,
    timestamp: str,
    nonce: str,
    request: Request,
) -> Response:
    """接收应用消息，文本则走密塔搜索并被动回复。"""
    crypto = _crypto()
    body = await request.body()
    try:
        xml_str = crypto.decrypt_message(body, msg_signature, timestamp, nonce)
    except InvalidSignatureException:
        raise HTTPException(status_code=403, detail="signature")
    except Exception as e:
        logger.exception("wecom decrypt")
        raise HTTPException(status_code=400, detail=str(e)) from e

    if isinstance(xml_str, bytes):
        xml_str = xml_str.decode("utf-8", errors="replace")

    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        return Response(content="success", media_type="text/plain")

    msg_type = _xml_text(root, "MsgType")
    if msg_type != "text":
        return Response(content="success", media_type="text/plain")

    content = _xml_text(root, "Content").strip()
    from_user = _xml_text(root, "FromUserName")
    to_user = _xml_text(root, "ToUserName")

    if not content:
        reply = "请发送要搜索的关键词（密塔）。"
    else:
        try:
            client = MetasoClient()
            data = await client.search(content)
            hits = MetasoClient.iter_hits(data)
            if not hits:
                reply = f"未找到与「{content}」相关的结果。"
            else:
                reply = await build_interactive_reply(content, hits)
        except Exception as e:
            logger.exception("wecom metaso")
            reply = f"搜索失败：{e}"

    if len(reply) > 2000:
        reply = reply[:1990] + "…"

    reply_xml = f"""<xml>
<ToUserName><![CDATA[{from_user}]]></ToUserName>
<FromUserName><![CDATA[{to_user}]]></FromUserName>
<CreateTime>{int(time.time())}</CreateTime>
<MsgType><![CDATA[text]]></MsgType>
<Content><![CDATA[{reply}]]></Content>
</xml>"""
    out_nonce = str(random.randint(100000000, 999999999999))
    enc = crypto.encrypt_message(reply_xml, out_nonce)
    return Response(content=enc, media_type="text/xml; charset=utf-8")
