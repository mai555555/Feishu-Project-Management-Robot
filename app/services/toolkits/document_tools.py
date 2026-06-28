import re
from dataclasses import dataclass
from typing import Any

from app.feishu_client import feishu_client
from app.services.docx_service import extract_docx_text
from app.services.pdf_service import extract_pdf_text
from app.services.recent_file_store import get_recent_file


DOCX_RE = re.compile(r"/docx/([A-Za-z0-9_-]{27,})")
BASE_RE = re.compile(r"https://[^\s]+/base/([A-Za-z0-9_-]+)(?:\?[^\s]*table=([A-Za-z0-9_-]+))?", re.I)
FEISHU_FILE_RE = re.compile(r"/(?:file|drive)/(?:[^/\s]+/)?([A-Za-z0-9_-]{20,})")
FEISHU_HOST_RE = re.compile(r"https://([^/\s]+)/(?:docx|docs|base|wiki|file|drive)/", re.I)


@dataclass(frozen=True)
class BaseLinkInfo:
    app_token: str
    table_id: str | None
    link: str


def find_first_key(value: object, keys: set[str]) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys and item:
                return str(item)
        for item in value.values():
            found = find_first_key(item, keys)
            if found:
                return found

    if isinstance(value, list):
        for item in value:
            found = find_first_key(item, keys)
            if found:
                return found

    return None


def extract_docx_id(text: str) -> str | None:
    match = DOCX_RE.search(text)
    return match.group(1) if match else None


def extract_feishu_origin(text: str) -> str | None:
    match = FEISHU_HOST_RE.search(text)
    if not match:
        return None
    return f"https://{match.group(1)}"


def extract_base_link(text: str) -> BaseLinkInfo | None:
    match = BASE_RE.search(text)
    if not match:
        return None
    return BaseLinkInfo(app_token=match.group(1), table_id=match.group(2), link=match.group(0))


def is_remember_task_table_intent(text: str) -> bool:
    if not extract_base_link(text):
        return False
    words = ("当前任务表", "最近任务表", "这个任务表", "这张任务表", "记住", "保存", "以后更新", "同步这个表")
    return any(word in text for word in words)


def file_info_from_payload(payload: object) -> dict[str, str] | None:
    file_key = find_first_key(payload, {"file_key", "fileKey"})
    file_name = (
        find_first_key(payload, {"file_name", "fileName", "name", "title"})
        or "attachment.pdf"
    )
    if file_key:
        return {"file_key": file_key, "file_name": file_name}

    file_token = find_first_key(
        payload,
        {"token", "file_token", "fileToken", "obj_token", "objToken", "doc_token", "docToken"},
    )
    if file_token:
        return {"file_token": file_token, "file_name": file_name}

    url = find_first_key(payload, {"url", "href", "link"})
    if url:
        return {"url": url, "file_name": file_name}

    return None


def doc_link_help(command: str) -> str:
    return (
        f"请发送真实的飞书新版文档链接，例如：\n"
        f"{command} https://xxx.feishu.cn/docx/真实文档Token\n\n"
        "注意：示例链接里的 xxxxx 不能直接使用。"
    )


async def download_attached_file(message_id: str | None, file_info: dict[str, str]) -> bytes | None:
    file_key = file_info.get("file_key")
    if file_key and message_id:
        return await feishu_client.download_message_file(message_id, file_key)

    file_token = file_info.get("file_token")
    if not file_token and file_info.get("url"):
        match = FEISHU_FILE_RE.search(file_info["url"])
        if match:
            file_token = match.group(1)
    if not file_token:
        return None

    return await feishu_client.download_drive_file(file_token)


async def read_attached_document(message_id: str | None, file_info: dict[str, str] | None) -> str | None:
    if not file_info and message_id:
        message_data = await feishu_client.get_message(message_id)
        file_info = file_info_from_payload(message_data)

    if not file_info:
        return None

    file_name = file_info.get("file_name", "")
    lower_name = file_name.lower()
    if lower_name and "." in lower_name and not lower_name.endswith((".pdf", ".docx")):
        return None

    file_bytes = await download_attached_file(message_id, file_info)
    if not file_bytes:
        return None

    if lower_name.endswith(".docx"):
        return extract_docx_text(file_bytes)
    return extract_pdf_text(file_bytes)


async def resolve_document_content(
    command_text: str,
    *,
    chat_id: str | None,
    sender_open_id: str | None,
    message_id: str | None,
    file_info: dict[str, str] | None,
) -> str | None:
    document_id = extract_docx_id(command_text)
    if document_id:
        return await feishu_client.get_docx_raw_content(document_id)

    document_file_info = file_info
    document_message_id = message_id
    if document_file_info is None and FEISHU_FILE_RE.search(command_text):
        document_file_info = {"url": command_text, "file_name": "attachment.pdf"}
    if document_file_info is None:
        document_message_id, document_file_info = get_recent_file(chat_id, sender_open_id)
    return await read_attached_document(document_message_id, document_file_info)
