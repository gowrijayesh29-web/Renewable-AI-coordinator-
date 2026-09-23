from fastapi.testclient import TestClient

from app import app
from auth import auth_service


client = TestClient(app)
client.cookies.set(
    auth_service.cookie_name,
    auth_service.create_session_token("integration-tests@example.com"),
)


def setup_function():
    client.post("/api/reset")


def test_dashboard_and_health_are_available():
    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "Renewable AI Coordinator" in dashboard.text
    assert client.get("/api/health").json()["status"] == "healthy"


def test_workload_manager_can_add_and_apply_a_decision():
    created = client.post(
        "/api/workloads",
        json={
            "id": "JOB005",
            "name": "Report_Generation",
            "power_kw": 1.5,
            "duration_minutes": 45,
            "deadline": "20:00",
            "priority": "LOW",
            "preemptible": True,
        },
    )
    assert created.status_code == 201

    applied = client.post(
        "/api/decisions",
        json={
            "job_id": "JOB005",
            "action": "RUN",
            "reason": "Integration test",
        },
    )
    assert applied.status_code == 200
    assert applied.json()["workload"]["status"] == "RUNNING"


def test_coordinator_applies_decision_engine_output():
    response = client.post("/api/coordinate")
    assert response.status_code == 200
    data = response.json()
    assert data["decisions"]["workload_decisions"]
    assert len(data["applied_workloads"]) == 4

    statuses = client.get("/api/status").json()
    assert statuses["total"] == 4


def test_schedule_and_history_endpoints():
    response = client.post(
        "/api/decisions",
        json={
            "job_id": "JOB003",
            "action": "SCHEDULE",
            "scheduled_start": "2026-09-21T14:00:00",
            "scheduled_finish": "2026-09-21T15:00:00",
            "reason": "Use forecast renewable power",
        },
    )
    assert response.status_code == 200
    assert len(client.get("/api/schedule").json()) == 1
    assert len(client.get("/api/history").json()) == 1
