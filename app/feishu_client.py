import json as jsonlib
import time
import uuid
from typing import Any

import httpx

from app.config import settings


class FeishuClient:
    def __init__(self) -> None:
        self._tenant_token = ""
        self._tenant_token_expires_at = 0.0

    async def tenant_access_token(self) -> str:
        now = time.time()
        if self._tenant_token and now < self._tenant_token_expires_at - 60:
            return self._tenant_token

        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{settings.api_base_url}/auth/v3/tenant_access_token/internal",
                json={
                    "app_id": settings.app_id,
                    "app_secret": settings.app_secret,
                },
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise RuntimeError(
                    f"Feishu HTTP error {response.status_code}: {response.text}"
                ) from exc
            data = response.json()

        if data.get("code") != 0:
            raise RuntimeError(f"Failed to get tenant token: {data}")

        self._tenant_token = data["tenant_access_token"]
        self._tenant_token_expires_at = now + int(data.get("expire", 7200))
        return self._tenant_token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        token = await self.tenant_access_token()
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(
                method,
                f"{settings.api_base_url}{path}",
                params=params,
                json=json,
                headers={"Authorization": f"Bearer {token}"},
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise RuntimeError(
                    f"Feishu HTTP error {response.status_code}: {response.text}"
                ) from exc
            data = response.json()

        if data.get("code") != 0:
            raise RuntimeError(f"Feishu API error: {data}")
        return data

    async def _request_bytes(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> bytes:
        token = await self.tenant_access_token()
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.request(
                method,
                f"{settings.api_base_url}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise RuntimeError(
                    f"Feishu HTTP error {response.status_code}: {response.text}"
                ) from exc

        return response.content

    async def send_text(self, chat_id: str, text: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            json={
                "receive_id": chat_id,
                "msg_type": "text",
                "content": jsonlib.dumps({"text": text}, ensure_ascii=False),
            },
        )

    async def get_docx_raw_content(self, document_id: str) -> str:
        data = await self._request(
            "GET",
            f"/docx/v1/documents/{document_id}/raw_content",
        )
        return data.get("data", {}).get("content", "")

    async def get_message(self, message_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/im/v1/messages/{message_id}")

    async def download_message_file(self, message_id: str, file_key: str) -> bytes:
        return await self._request_bytes(
            "GET",
            f"/im/v1/messages/{message_id}/resources/{file_key}",
            params={"type": "file"},
        )

    async def download_drive_file(self, file_token: str) -> bytes:
        last_error: Exception | None = None
        for path in (
            f"/drive/v1/files/{file_token}/download",
            f"/drive/v1/medias/{file_token}/download",
        ):
            try:
                return await self._request_bytes("GET", path)
            except Exception as exc:
                last_error = exc

        raise RuntimeError(f"Failed to download drive file: {last_error}")

    async def create_bitable_app(self, name: str, folder_token: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": name}
        if folder_token:
            payload["folder_token"] = folder_token

        return await self._request("POST", "/bitable/v1/apps", json=payload)

    async def create_project_table(self, app_token: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables",
            json={
                "table": {
                    "name": "项目任务",
                    "default_view_name": "全部任务",
                    "fields": [
                        {"field_name": "任务名称", "type": 1},
                        {"field_name": "任务说明", "type": 1},
                        {"field_name": "模块", "type": 1},
                        {"field_name": "负责人", "type": 1},
                        {"field_name": "优先级", "type": 1},
                        {"field_name": "状态", "type": 1},
                        {"field_name": "开始时间", "type": 1},
                        {"field_name": "截止时间", "type": 1},
                        {"field_name": "风险", "type": 1},
                        {"field_name": "依赖项", "type": 1},
                        {"field_name": "备注", "type": 1},
                    ],
                }
            },
        )

    async def batch_create_records(
        self,
        app_token: str,
        table_id: str,
        records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
            json={"records": records},
        )

    async def create_task(
        self,
        summary: str,
        *,
        description: str = "",
        assignee_open_id: str | None = None,
        client_token: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"summary": summary}
        if description:
            payload["description"] = description
        if assignee_open_id:
            payload["members"] = [
                {
                    "id": assignee_open_id,
                    "type": "user",
                    "role": "assignee",
                }
            ]
        if client_token:
            payload["client_token"] = client_token
        else:
            payload["client_token"] = str(uuid.uuid4())

        return await self._request(
            "POST",
            "/task/v2/tasks",
            params={"user_id_type": "open_id"},
            json=payload,
        )


feishu_client = FeishuClient()
