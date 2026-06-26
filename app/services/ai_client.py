import json
import re
from typing import Any

import httpx

from app.config import settings


def _extract_text(data: dict[str, Any]) -> str:
    candidates = data.get("candidates") or []
    if not candidates:
        return ""

    content = candidates[0].get("content") or {}
    parts = content.get("parts") or []
    texts = [part.get("text", "") for part in parts if isinstance(part, dict)]
    return "\n".join(text for text in texts if text).strip()


def _extract_error(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text

    error = data.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        code = error.get("code")
        if message and code:
            return f"{message} ({code})"
        if message:
            return message
    return str(data)


async def ask_ai(prompt: str) -> str:
    if not settings.ai_api_key:
        return "AI API Key 还没有配置，请先在 .env 中填写 AI_API_KEY。"

    body = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            }
        ]
    }

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            settings.ai_api_url,
            headers={
                "x-goog-api-key": settings.ai_api_key,
                "Content-Type": "application/json",
            },
            json=body,
        )

    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = _extract_error(response)
        raise RuntimeError(f"AI HTTP error {response.status_code}: {detail}") from exc

    data = response.json()
    text = _extract_text(data)
    if text:
        return text
    return f"AI 已返回，但没有解析到文本内容：{data}"


def parse_json_from_text(text: str) -> Any:
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.S | re.I)
    if fence:
        cleaned = fence.group(1).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start_candidates = [idx for idx in (cleaned.find("{"), cleaned.find("[")) if idx >= 0]
        if not start_candidates:
            raise
        start = min(start_candidates)
        end = max(cleaned.rfind("}"), cleaned.rfind("]"))
        if end <= start:
            raise
        return json.loads(cleaned[start : end + 1])
