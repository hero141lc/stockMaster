from __future__ import annotations

import hashlib
from typing import Any

import httpx

from app.config import get_settings


def _stable_id(url: str, title: str) -> str:
    return hashlib.sha256(f"{url}|{title}".encode("utf-8")).hexdigest()


class MetasoClient:
    def __init__(self) -> None:
        s = get_settings()
        self._base = s.metaso_base_url.rstrip("/")
        self._token = s.metaso_api_key
        self._default_size = s.metaso_search_size

    async def search(
        self,
        q: str,
        *,
        size: int | None = None,
        scope: str = "webpage",
    ) -> dict[str, Any]:
        if not self._token:
            raise RuntimeError("METASO_API_KEY 未配置")
        payload = {
            "q": q,
            "scope": scope,
            "includeSummary": True,
            "size": str(size or self._default_size),
            "includeRawContent": True,
            "conciseSnippet": True,
        }
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(self._base, headers=headers, json=payload)
            r.raise_for_status()
            return r.json()

    @staticmethod
    def iter_hits(data: dict[str, Any]) -> list[dict[str, Any]]:
        """Normalize Metaso response into list of {title, url, summary}."""
        hits: list[dict[str, Any]] = []
        # Common shapes: { "results": [...] } or { "data": [...] } or top-level list
        candidates: Any = None
        for key in ("results", "data", "items", "documents"):
            if key in data and isinstance(data[key], list):
                candidates = data[key]
                break
        if candidates is None and isinstance(data.get("result"), list):
            candidates = data["result"]
        if candidates is None:
            # Fallback: scan for first list of dicts
            for v in data.values():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    candidates = v
                    break
        if not candidates:
            return hits

        for item in candidates:
            if not isinstance(item, dict):
                continue
            title = (
                item.get("title")
                or item.get("name")
                or item.get("snippet")
                or ""
            )
            url = item.get("url") or item.get("link") or item.get("href") or ""
            summary = (
                item.get("summary")
                or item.get("snippet")
                or item.get("abstract")
                or ""
            )
            raw = (
                item.get("rawContent")
                or item.get("raw_content")
                or item.get("content")
                or item.get("text")
                or ""
            )
            if not title and not url:
                continue
            hid = _stable_id(str(url), str(title))
            hits.append(
                {
                    "id": hid,
                    "title": str(title).strip()[:500],
                    "url": str(url).strip(),
                    "summary": str(summary).strip()[:2000],
                    "raw": str(raw).strip()[:8000],
                }
            )
        return hits
