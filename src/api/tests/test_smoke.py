from fastapi.testclient import TestClient

from src.api.main import create_app


def test_app_starts_and_healthz_is_available() -> None:
    response = TestClient(create_app()).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
