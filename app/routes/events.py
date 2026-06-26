import json
import logging
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.config import settings
from app.feishu_client import feishu_client
from app.services.commands import handle_command
from app.services.dedupe import mark_processed


router = APIRouter()
logger = logging.getLogger(__name__)


def _message_content(event: dict[str, Any]) -> dict[str, Any]:
    content = event.get("message", {}).get("content", "{}")
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return {"text": content}
    else:
        parsed = content

    return parsed if isinstance(parsed, dict) else {}


def _collect_text(value: Any) -> str:
    chunks: list[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            tag = item.get("tag")
            if tag in {"text", "a", "at"}:
                text = item.get("text") or item.get("name") or item.get("href")
                if text:
                    chunks.append(str(text))
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return " ".join(chunk.strip() for chunk in chunks if chunk.strip()).strip()


def _verify_token(payload: dict[str, Any]) -> None:
    expected = settings.verification_token
    if not expected:
        return

    token = payload.get("token") or payload.get("header", {}).get("token")
    if token and token != expected:
        raise HTTPException(status_code=403, detail="Invalid Feishu verification token")


def _message_text(event: dict[str, Any]) -> str:
    parsed = _message_content(event)
    direct = parsed.get("text") or parsed.get("title") or parsed.get("file_name") or ""
    return str(direct or _collect_text(parsed))


def _find_first_key(value: Any, keys: set[str]) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys and item:
                return str(item)
        for item in value.values():
            found = _find_first_key(item, keys)
            if found:
                return found

    if isinstance(value, list):
        for item in value:
            found = _find_first_key(item, keys)
            if found:
                return found

    return None


def _message_file(event: dict[str, Any]) -> dict[str, str] | None:
    parsed = _message_content(event)
    file_key = _find_first_key(parsed, {"file_key", "fileKey"})
    file_name = (
        _find_first_key(parsed, {"file_name", "fileName", "name"})
        or parsed.get("title")
        or "attachment.pdf"
    )
    if file_key:
        return {"file_key": file_key, "file_name": str(file_name)}

    file_token = _find_first_key(
        parsed,
        {"token", "file_token", "fileToken", "obj_token", "objToken", "doc_token", "docToken"},
    )
    if file_token:
        return {"file_token": file_token, "file_name": str(file_name)}

    url = _find_first_key(parsed, {"url", "href", "link"})
    if url:
        return {"url": url, "file_name": str(file_name)}

    return None


def _has_mention(event: dict[str, Any]) -> bool:
    mentions = event.get("message", {}).get("mentions") or []
    return bool(mentions)


def _strip_leading_mentions(text: str, event: dict[str, Any]) -> str:
    cleaned = text.strip()
    for mention in event.get("message", {}).get("mentions") or []:
        for key in ("name", "key", "id"):
            value = str(mention.get(key) or "").strip()
            if value and cleaned.startswith(f"@{value}"):
                cleaned = cleaned[len(value) + 1 :].strip()

    return re.sub(r"^@\S+\s*", "", cleaned).strip()


def _should_ignore_message(event: dict[str, Any], text: str, file_info: dict[str, str] | None) -> bool:
    message = event.get("message", {})
    chat_type = message.get("chat_type")
    stripped = text.strip()

    if chat_type == "p2p":
        return False

    if file_info:
        return False

    if stripped.startswith("/"):
        return False

    return not _has_mention(event)


def _event_key(payload: dict[str, Any], event: dict[str, Any]) -> str:
    header = payload.get("header", {})
    message = event.get("message", {})
    return (
        message.get("message_id")
        or header.get("event_id")
        or header.get("event_id_v2")
        or ""
    )


@router.post("/feishu/events")
async def feishu_events(request: Request) -> dict[str, Any]:
    payload = await request.json()

    if payload.get("type") == "url_verification":
        _verify_token(payload)
        return {"challenge": payload.get("challenge")}

    _verify_token(payload)

    event_type = payload.get("header", {}).get("event_type")
    if event_type != "im.message.receive_v1":
        return {"ok": True, "ignored": event_type}

    event = payload.get("event", {})
    message = event.get("message", {})
    chat_id = message.get("chat_id")
    sender_open_id = event.get("sender", {}).get("sender_id", {}).get("open_id")
    raw_text = _message_text(event)
    file_info = _message_file(event)
    event_key = _event_key(payload, event)

    logger.warning(
        "received feishu message event_key=%s message_type=%s chat_type=%s text_prefix=%r file_info=%r content_prefix=%r",
        event_key,
        message.get("message_type"),
        message.get("chat_type"),
        raw_text[:40],
        file_info,
        str(_message_content(event))[:500],
    )

    if not chat_id:
        return {"ok": True, "ignored": "missing chat_id"}

    if not mark_processed(event_key):
        logger.info("ignored duplicate feishu event event_key=%s", event_key)
        return {"ok": True, "ignored": "duplicate"}

    if _should_ignore_message(event, raw_text, file_info):
        return {"ok": True, "ignored": "group_message_without_mention_or_command"}

    text = _strip_leading_mentions(raw_text, event)
    if not text:
        text = "/帮助"

    try:
        result = await handle_command(
            text,
            sender_open_id=sender_open_id,
            chat_id=chat_id,
            mentions=message.get("mentions") or [],
            message_id=message.get("message_id"),
            file_info=file_info,
        )
        await feishu_client.send_text(chat_id, result.text)
    except Exception as exc:
        logger.exception("Failed to handle Feishu event")
        try:
            await feishu_client.send_text(chat_id, f"处理失败：{exc}")
        except Exception:
            logger.exception("Failed to send Feishu error message")

    return {"ok": True}
