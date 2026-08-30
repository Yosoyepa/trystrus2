"""Config Rappi login flow: capture, custody, masked labels, endpoints."""

import json
import pathlib

import pytest
from fastapi.testclient import TestClient
from src.rappi_bridge.app import create_app
from src.rappi_bridge.config import BridgeConfig
from src.rappi_bridge.login import LoginFlow, mask_email, mask_name


def make_config(tmp_path, **overrides) -> BridgeConfig:
    return BridgeConfig(
        session_file=tmp_path / "secrets" / "rappi-config.json",
        state_db_path=tmp_path / "state.sqlite3",
        **overrides,
    )


def fake_launcher() -> dict:
    return {"token": "ft.gAAAA-captured", "device_id": "dev-1"}


def fake_prober(token: str) -> dict:
    assert token.startswith("ft.")
    return {
        "account_label": "Fabian F. •••••@gmail.com",
        "address_label": "Casa (Cl. 40B)",
        "lat": "4.6751868",
        "lng": "-74.1340906",
    }


def test_masks_never_leak_full_identity() -> None:
    assert mask_name("fabian  Espitia ").startswith("fabian E.")
    assert mask_email("fabianespitia@gmail.com") == "f" + "•" * 12 + "@gmail.com"


def test_login_captures_token_with_custody(tmp_path) -> None:
    config = make_config(tmp_path)
    flow = LoginFlow(config, launcher=fake_launcher, prober=fake_prober)
    status = flow.start()
    assert status["state"] == "waiting_login"
    flow._thread.join(timeout=5)  # noqa: SLF001 — test synchronization
    status = flow.status()
    assert status["state"] == "captured"
    assert status["has_token"] is True
    assert status["address_label"] == "Casa (Cl. 40B)"
    raw = config.session_file.read_text()
    assert "ft.gAAAA-captured" in raw
    data = json.loads(raw)
    assert data["lat"] == "4.6751868"  # coords from the active address
    mode = config.session_file.stat().st_mode & 0o777
    assert mode == 0o600  # custody: owner read/write only


def test_login_error_is_reported_not_raised(tmp_path) -> None:
    def bad_launcher() -> dict:
        raise TimeoutError("login OTP window timed out (5 min)")

    config = make_config(tmp_path)
    flow = LoginFlow(config, launcher=bad_launcher, prober=fake_prober)
    flow.start()
    flow._thread.join(timeout=5)  # noqa: SLF001
    status = flow.status()
    assert status["state"] == "error"
    assert "timed out" in status["error"]


def test_double_start_opens_one_window(tmp_path) -> None:
    import threading

    config = make_config(tmp_path)
    gate = threading.Event()

    def blocked_launcher() -> dict:
        gate.wait(timeout=5)
        return {"token": "ft.x"}

    flow = LoginFlow(config, launcher=blocked_launcher, prober=fake_prober)
    flow.start()
    flow.start()  # while waiting: must NOT spawn a second browser
    assert flow.launches == 1
    gate.set()
    flow._thread.join(timeout=5)  # noqa: SLF001


def test_disconnect_removes_token(tmp_path) -> None:
    config = make_config(tmp_path)
    flow = LoginFlow(config, launcher=fake_launcher, prober=fake_prober)
    flow.start()
    flow._thread.join(timeout=5)  # noqa: SLF001
    assert flow.status()["has_token"] is True
    flow.disconnect()
    assert flow.status()["has_token"] is False
    assert not config.session_file.exists()


def test_session_endpoints_over_http(tmp_path) -> None:
    config = make_config(tmp_path)
    app = create_app(
        config,
        service=object(),  # commerce endpoints unused here
        login_flow=LoginFlow(config, launcher=fake_launcher, prober=fake_prober),
    )
    client = TestClient(app)
    assert client.get("/healthz").json()["dry_run"] is True
    assert client.get("/v1/rappi/session/status").json()["state"] == "idle"
    assert client.post("/v1/rappi/session/login").json()["state"] == "waiting_login"
    app.state.login_flow._thread.join(timeout=5)  # noqa: SLF001
    status = client.get("/v1/rappi/session/status").json()
    assert status["state"] == "captured"
    assert client.request("DELETE", "/v1/rappi/session").json()["has_token"] is False


def test_cors_allows_platform_front(tmp_path) -> None:
    config = make_config(tmp_path)
    app = create_app(config, service=object(), login_flow=LoginFlow(config))
    client = TestClient(app)
    response = client.get("/healthz", headers={"Origin": "http://localhost:3000"})
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_manual_connect_with_pasted_token(tmp_path) -> None:
    config = make_config(tmp_path)
    flow = LoginFlow(config, prober=fake_prober)
    status = flow.connect_with_token(" ft.gAAAA-manual ", device_id="dev-9")
    assert status["state"] == "captured"
    assert status["account_label"] == "Fabian F. •••••@gmail.com"
    data = json.loads(config.session_file.read_text())
    assert data["token"] == "ft.gAAAA-manual"
    assert data["deviceId"] == "dev-9"


def test_manual_connect_rejects_non_ft_token(tmp_path) -> None:
    from src.rappi_bridge.errors import BridgeError

    config = make_config(tmp_path)
    flow = LoginFlow(config, prober=fake_prober)
    with pytest.raises(BridgeError):
        flow.connect_with_token("Bearer eyJhbGciOi")
    assert flow.status()["state"] == "error"
    assert not config.session_file.exists()


def test_manual_endpoint_over_http(tmp_path) -> None:
    config = make_config(tmp_path)
    app = create_app(
        config,
        service=object(),
        login_flow=LoginFlow(config, prober=fake_prober),
    )
    client = TestClient(app)
    response = client.post("/v1/rappi/session/manual", json={"token": "ft.gAAAA-manual"})
    assert response.status_code == 200
    assert response.json()["state"] == "captured"
    bad = client.post("/v1/rappi/session/manual", json={"token": "nope"})
    assert bad.status_code == 409
    assert bad.json()["reason"] == "BRIDGE_ERROR"


def test_login_profile_dir_is_created(tmp_path) -> None:
    config = make_config(tmp_path)
    assert config.login_profile_dir == pathlib.Path("var/rappi-bridge/login-profile")
