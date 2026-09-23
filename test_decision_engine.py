from datetime import datetime

from decision_engine import (
    decide_battery_action,
    decide_workloads,
    find_and_reserve_slot,
    parse_deadline,
)


def test_battery_charges_from_remaining_surplus():
    battery = {"soc_percent": 72}

    result = decide_battery_action(
        battery,
        remaining_surplus_kw=2.5,
    )

    assert result["action"] == "CHARGE"
    assert result["power_kw"] == 2.5


def test_low_battery_uses_grid_during_deficit():
    battery = {"soc_percent": 20}

    result = decide_battery_action(
        battery,
        remaining_surplus_kw=-3.0,
    )

    assert result["action"] == "USE_GRID"
    assert result["power_kw"] == 3.0


def test_deadline_rolls_to_next_day_when_passed():
    current_time = datetime.fromisoformat(
        "2026-09-21T20:00:00"
    )

    deadline = parse_deadline("18:00", current_time)

    assert deadline.isoformat() == "2026-09-22T18:00:00"


def test_forecast_capacity_cannot_be_double_booked():
    capacity = [
        {
            "timestamp": "2026-09-21T10:30:00",
            "available_kw": 4.0,
        },
        {
            "timestamp": "2026-09-21T11:00:00",
            "available_kw": 4.0,
        },
        {
            "timestamp": "2026-09-21T11:30:00",
            "available_kw": 4.0,
        },
    ]

    job = {
        "power_kw": 3.0,
        "duration_minutes": 60,
    }

    first_reservation = find_and_reserve_slot(
        job,
        capacity,
        slot_minutes=30,
        deadline=None,
    )

    second_reservation = find_and_reserve_slot(
        job,
        capacity,
        slot_minutes=30,
        deadline=None,
    )

    assert first_reservation is not None
    assert second_reservation is None


def test_started_workload_is_removed_from_battery_surplus():
    state = {
        "energy": {
            "timestamp": "2026-09-21T10:00:00",
            "renewable_kw": 4.5,
            "load_kw": 1.0,
        },
        "battery": {
            "soc_percent": 72,
        },
        "forecast": {
            "points": [
                {
                    "timestamp": "2026-09-21T10:30:00",
                    "renewable_kw": 5.0,
                },
                {
                    "timestamp": "2026-09-21T11:00:00",
                    "renewable_kw": 5.0,
                },
            ],
        },
        "workloads": [
            {
                "id": "JOB001",
                "name": "Analytics",
                "power_kw": 2.0,
                "duration_minutes": 60,
                "deadline": "18:00",
                "priority": "MEDIUM",
                "preemptible": True,
                "status": "WAITING",
            }
        ],
    }

    decisions, remaining_surplus = decide_workloads(state)

    assert decisions[0]["decision"] == "START_NOW"
    assert decisions[0]["energy_source"] == "SURPLUS_RENEWABLE"
    assert remaining_surplus == 1.5