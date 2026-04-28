from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（含 .env）；避免在 app/ 下启动时只从当前工作目录找 .env 导致读不到配置
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(
            _PROJECT_ROOT / ".env",
            Path(".env"),
        ),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    feishu_app_id: str = Field(default="", alias="FEISHU_APP_ID")
    feishu_app_secret: str = Field(default="", alias="FEISHU_APP_SECRET")
    feishu_verification_token: str = Field(default="", alias="FEISHU_VERIFICATION_TOKEN")
    feishu_encrypt_key: str = Field(default="", alias="FEISHU_ENCRYPT_KEY")
    feishu_chat_id: str = Field(default="", alias="FEISHU_CHAT_ID")
    feishu_webhook_url: str = Field(default="", alias="FEISHU_WEBHOOK_URL")

    # 推送/交互渠道：feishu | wecom | both（逗号也可 feishu,wecom）
    im_provider: str = Field(default="feishu", alias="IM_PROVIDER")

    # 事件订阅：长连接（推荐，免公网 HTTPS）；与 HTTP 回调二选一即可
    feishu_ws_enabled: bool = Field(default=True, alias="FEISHU_WS_ENABLED")
    # 是否保留 POST /feishu/events（仅在使用「请求地址」HTTP 回调时需要）
    feishu_http_events_enabled: bool = Field(default=False, alias="FEISHU_HTTP_EVENTS_ENABLED")

    # 企业微信群机器人（仅推送：简报/预警）；二选一填 key 或完整 URL
    wecom_webhook_key: str = Field(default="", alias="WECOM_WEBHOOK_KEY")
    wecom_webhook_url: str = Field(default="", alias="WECOM_WEBHOOK_URL")
    # 企业微信智能机器人长连接（Bot ID + Secret）
    wecom_ws_enabled: bool = Field(default=True, alias="WECOM_WS_ENABLED")
    wecom_bot_id: str = Field(default="", alias="WECOM_BOT_ID")
    wecom_bot_secret: str = Field(default="", alias="WECOM_BOT_SECRET")
    # 企业微信「接收消息」回调（自建应用）：用于在企微内发文字触发密塔搜索
    wecom_corp_id: str = Field(default="", alias="WECOM_CORP_ID")
    wecom_callback_token: str = Field(default="", alias="WECOM_CALLBACK_TOKEN")
    wecom_encoding_aes_key: str = Field(default="", alias="WECOM_ENCODING_AES_KEY")

    metaso_api_key: str = Field(default="", alias="METASO_API_KEY")
    metaso_base_url: str = Field(
        default="https://metaso.cn/api/v1/search", alias="METASO_BASE_URL"
    )
    metaso_search_size: int = Field(default=10, alias="METASO_SEARCH_SIZE")

    # 交互回复：rag=密塔检索 + LLM 综合；search=仅列出密塔结果（旧版）
    reply_mode: str = Field(default="rag", alias="REPLY_MODE")
    llm_api_base: str = Field(
        default="https://api.openai.com/v1", alias="LLM_API_BASE"
    )
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_model: str = Field(default="gpt-4o-mini", alias="LLM_MODEL")
    llm_timeout: float = Field(default=90.0, alias="LLM_TIMEOUT")

    tz: str = Field(default="Asia/Shanghai", alias="TZ")
    digest_times: str = Field(
        default="09:30,12:30,15:30,21:00",
        alias="DIGEST_TIMES",
        description="Comma-separated HH:MM",
    )
    digest_queries: str = Field(
        default='["A股 宏观 今日","港股 美股 期货 隔夜"]',
        alias="DIGEST_QUERIES",
    )
    alert_keywords: str = Field(
        default="熔断,停牌,重大资产重组,立案调查",
        alias="ALERT_KEYWORDS",
    )
    alert_interval_minutes: int = Field(default=15, alias="ALERT_INTERVAL_MINUTES")

    database_path: str = Field(default="./data/app.db", alias="DATABASE_PATH")
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")

    @field_validator("digest_queries", mode="before")
    @classmethod
    def _strip_digest_queries(cls, v: Any) -> Any:
        if v is None:
            return '["A股 宏观 今日"]'
        return v

    def parsed_digest_queries(self) -> list[str]:
        raw = self.digest_queries.strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                data = json.loads(raw)
                return [str(x).strip() for x in data if str(x).strip()]
            except json.JSONDecodeError:
                pass
        return [s.strip() for s in raw.split(",") if s.strip()]

    def parsed_digest_times(self) -> list[tuple[int, int]]:
        out: list[tuple[int, int]] = []
        for part in self.digest_times.split(","):
            part = part.strip()
            if not part:
                continue
            h, m = part.split(":", 1)
            out.append((int(h), int(m)))
        return out

    def parsed_alert_keywords(self) -> list[str]:
        return [s.strip() for s in self.alert_keywords.split(",") if s.strip()]

    def parsed_im_providers(self) -> list[str]:
        raw = (self.im_provider or "feishu").strip().lower()
        if raw in ("both", "all", "feishu,wecom", "wecom,feishu", "feishu|wecom"):
            return ["feishu", "wecom"]
        if raw == "wecom":
            return ["wecom"]
        return ["feishu"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
