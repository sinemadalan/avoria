from fastapi.testclient import TestClient

from backend.app.main import app


def test_health_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "avoria-api"}


def test_websocket_connection_contract() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/ws/jobs/job-123") as websocket:
            assert websocket.receive_json() == {"type": "connected", "job_id": "job-123"}

