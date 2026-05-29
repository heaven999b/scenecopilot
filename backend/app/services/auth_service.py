from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import Request

from ..config import (
    ALLOW_OPEN_DEVICE_REGISTRATION,
    AUTH_MODE,
    DATA_RETENTION_DAYS,
    DEMO_USER_ID,
    DEVICE_TOKEN_TTL_DAYS,
    ENABLE_CLOUD_MODE,
    SERVER_API_KEY,
)
from ..db import conn_ctx, get_conn, row_to_dict


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _parse_bearer(value: str | None) -> str | None:
    if not value:
        return None
    lower = value.lower()
    prefix = "bearer "
    if lower.startswith(prefix):
        token = value[len(prefix):].strip()
        return token or None
    return None


@dataclass(slots=True)
class AuthContext:
    authenticated: bool
    required: bool
    user_id: int = DEMO_USER_ID
    device_id: str | None = None
    auth_mode: str = AUTH_MODE
    principal: str = "anonymous"
    failure_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "authenticated": self.authenticated,
            "required": self.required,
            "user_id": self.user_id,
            "device_id": self.device_id,
            "auth_mode": self.auth_mode,
            "principal": self.principal,
            "failure_reason": self.failure_reason,
        }


class AuthService:
    public_api_paths = frozenset({
        "/api/health",
        "/api/devices/register",
        "/api/security/profile",
    })

    def security_profile(self) -> dict[str, Any]:
        return {
            "auth_mode": AUTH_MODE,
            "auth_required": AUTH_MODE != "disabled",
            "open_device_registration": ALLOW_OPEN_DEVICE_REGISTRATION,
            "cloud_mode_enabled": ENABLE_CLOUD_MODE,
            "data_retention_days": DATA_RETENTION_DAYS,
            "device_token_ttl_days": DEVICE_TOKEN_TTL_DAYS,
        }

    def can_register_device(self, *, request: Request | None = None) -> bool:
        if AUTH_MODE == "disabled" or ALLOW_OPEN_DEVICE_REGISTRATION:
            return True
        if request is None or not SERVER_API_KEY:
            return False
        token = request.headers.get("x-scenecopilot-key") or _parse_bearer(request.headers.get("authorization"))
        return bool(token and hmac.compare_digest(token, SERVER_API_KEY))

    def register_device(
        self,
        *,
        display_name: str,
        platform: str | None,
        client_version: str | None,
        user_id: int = DEMO_USER_ID,
    ) -> dict[str, Any]:
        device_id = uuid.uuid4().hex[:16]
        raw_token = secrets.token_urlsafe(24)
        now = int(time.time())
        metadata = {
            "registered_at_epoch": now,
            "platform": (platform or "").strip() or None,
            "client_version": (client_version or "").strip() or None,
            "cloud_mode_enabled": ENABLE_CLOUD_MODE,
            "data_retention_days": DATA_RETENTION_DAYS,
        }
        with conn_ctx() as conn:
            conn.execute(
                """
                INSERT INTO devices
                  (id, user_id, display_name, platform, client_version, api_token_hash, status, metadata_json, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, 'active', ?, datetime('now'))
                """,
                (
                    device_id,
                    user_id,
                    display_name.strip() or "SceneCopilot device",
                    metadata["platform"],
                    metadata["client_version"],
                    _hash_token(raw_token),
                    json.dumps(metadata, default=str),
                ),
            )
        return {
            "device_id": device_id,
            "device_token": raw_token,
            "auth_mode": AUTH_MODE,
            "cloud_mode_enabled": ENABLE_CLOUD_MODE,
            "data_retention_days": DATA_RETENTION_DAYS,
        }

    def get_device(self, device_id: str) -> dict[str, Any] | None:
        conn = get_conn()
        try:
            row = conn.execute(
                """
                SELECT id, user_id, display_name, platform, client_version, api_token_hash, status,
                       metadata_json, created_at, updated_at, last_seen_at
                FROM devices
                WHERE id = ?
                """,
                (device_id,),
            ).fetchone()
        finally:
            conn.close()
        return row_to_dict(row) if row is not None else None

    def list_devices(self, *, user_id: int = DEMO_USER_ID, limit: int = 20) -> list[dict[str, Any]]:
        conn = get_conn()
        try:
            rows = conn.execute(
                """
                SELECT id, user_id, display_name, platform, client_version, status, metadata_json,
                       created_at, updated_at, last_seen_at
                FROM devices
                WHERE user_id = ?
                ORDER BY COALESCE(last_seen_at, created_at) DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        finally:
            conn.close()
        return [row_to_dict(row) for row in rows]

    def authenticate_request(self, request: Request) -> AuthContext:
        path = request.url.path
        if not path.startswith("/api"):
            return AuthContext(authenticated=True, required=False, principal="browser")
        if path in self.public_api_paths or AUTH_MODE == "disabled":
            return AuthContext(
                authenticated=True,
                required=AUTH_MODE != "disabled" and path not in self.public_api_paths,
                principal="local-demo",
            )

        server_key = request.headers.get("x-scenecopilot-key") or _parse_bearer(request.headers.get("authorization"))
        if SERVER_API_KEY and server_key and hmac.compare_digest(server_key, SERVER_API_KEY):
            return AuthContext(authenticated=True, required=True, principal="server-key")

        if AUTH_MODE not in {"device", "device_token"}:
            return AuthContext(
                authenticated=False,
                required=True,
                failure_reason="A valid SceneCopilot server key is required.",
            )

        device_id = request.headers.get("x-scenecopilot-device", "").strip()
        device_token = request.headers.get("x-scenecopilot-token", "").strip()
        if not device_id or not device_token:
            return AuthContext(
                authenticated=False,
                required=True,
                failure_reason="Device credentials are required.",
            )
        device = self.get_device(device_id)
        if device is None or device.get("status") != "active":
            return AuthContext(
                authenticated=False,
                required=True,
                failure_reason="The device is not registered or is inactive.",
            )
        actual_hash = _hash_token(device_token)
        expected_hash = str(device.get("api_token_hash") or "")
        if not expected_hash or not hmac.compare_digest(expected_hash, actual_hash):
            return AuthContext(
                authenticated=False,
                required=True,
                failure_reason="The device token is invalid.",
            )
        with conn_ctx() as conn:
            conn.execute(
                """
                UPDATE devices
                SET last_seen_at = datetime('now'),
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (device_id,),
            )
        return AuthContext(
            authenticated=True,
            required=True,
            device_id=device_id,
            principal=f"device:{device_id}",
        )


auth_service = AuthService()
