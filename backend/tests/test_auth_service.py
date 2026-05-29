from __future__ import annotations

from types import SimpleNamespace


def test_device_registration_and_authentication(isolated_runtime, monkeypatch):
    import app.services.auth_service as auth_module

    payload = auth_module.auth_service.register_device(
        display_name="Test Companion",
        platform="android",
        client_version="0.1.0",
    )
    devices = auth_module.auth_service.list_devices()
    assert devices
    assert devices[0]["id"] == payload["device_id"]

    monkeypatch.setattr(auth_module, "AUTH_MODE", "device_token", raising=False)
    request = SimpleNamespace(
        url=SimpleNamespace(path="/api/chat"),
        headers={
            "x-scenecopilot-device": payload["device_id"],
            "x-scenecopilot-token": payload["device_token"],
        },
    )
    context = auth_module.auth_service.authenticate_request(request)
    assert context.authenticated is True
    assert context.device_id == payload["device_id"]
