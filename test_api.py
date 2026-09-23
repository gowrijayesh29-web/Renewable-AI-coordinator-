from fastapi.testclient import TestClient

from app import app, simulator
from auth import auth_service

client = TestClient(app)
client.cookies.set(
    auth_service.cookie_name,
    auth_service.create_session_token("api-tests@example.com"),
)


def test_root():
    assert client.get("/").status_code == 200


def test_energy_schema_and_ranges():
    data = client.get("/api/energy").json()
    assert data["renewable_kw"] >= 0
    assert data["grid_kw"] >= 0
    assert data["load_kw"] >= 0
    assert data["grid_price_per_kwh"] >= 0


def test_battery_schema_and_ranges():
    data = client.get("/api/battery").json()
    assert 0 <= data["soc_percent"] <= 100
    assert 0 <= data["soh_percent"] <= 100
    assert data["status"] in {"IDLE", "CHARGING", "DISCHARGING"}


def test_forecast_shape_and_nonnegative_values():
    data = client.get("/api/forecast").json()
    assert data["horizon_hours"] == 8
    assert len(data["points"]) == 17
    assert all(point["renewable_kw"] >= 0 for point in data["points"])


def test_workloads_have_required_fields():
    data = client.get("/api/workloads").json()
    assert len(data) >= 1
    required = {"id", "name", "power_kw", "duration_minutes", "deadline", "priority", "preemptible", "status"}
    assert all(required <= set(job) for job in data)
    assert all(job["power_kw"] >= 0 and job["duration_minutes"] > 0 for job in data)


def test_full_state_is_ai_consumable():
    response = client.get("/api/state")
    assert response.status_code == 200
    data = response.json()
    assert {"energy", "battery", "forecast", "workloads"} <= set(data)
    assert "grid_price_per_kwh" in data["energy"]


def test_simulation_advances():
    client.post("/api/reset")
    before = client.get("/api/state").json()["energy"]["timestamp"]
    simulator.advance(10)
    after = client.get("/api/state").json()["energy"]["timestamp"]
    assert after != before


def test_demo_scenarios():
    assert client.post("/api/demo/solar-drop").status_code == 200
    assert simulator.demo_mode == "solar_drop"

    assert client.post("/api/demo/solar-peak").status_code == 200
    assert simulator.demo_mode == "solar_peak"

    response = client.post("/api/demo/battery-low")
    assert response.status_code == 200
    assert response.json()["soc_percent"] == 22.0
    assert simulator.demo_mode == "battery_low"

    assert client.post("/api/demo/normal").status_code == 200
    assert simulator.demo_mode == "normal"


def test_battery_bounds_over_many_steps():
    client.post("/api/reset")
    for _ in range(300):
        simulator.advance(1)
        state = simulator.battery_state()
        assert 0 <= state["soc_percent"] <= 100
        assert 70 <= state["soh_percent"] <= 100


def test_reset_restores_known_state():
    client.post("/api/demo/battery-low")
    simulator.advance(5)
    client.post("/api/reset")
    data = client.get("/api/battery").json()
    assert data["soc_percent"] == 72.0
    assert data["soh_percent"] == 92.0
    assert data["status"] == "IDLE"
