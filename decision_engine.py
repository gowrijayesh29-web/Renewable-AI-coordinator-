import json
import math
import os
from datetime import datetime, timedelta
from urllib.request import urlopen

API_URL = os.getenv("SIMULATOR_API_URL", "http://127.0.0.1:8000/api/state")

MIN_BATTERY_SOC = 30
TARGET_BATTERY_SOC = 90

PRIORITY_ORDER = {
    "CRITICAL": 0,
    "HIGH": 1,
    "MEDIUM": 2,
    "LOW": 3,
}


def fetch_state():
    """Fetch live data from Person 1's simulator."""
    with urlopen(API_URL, timeout=5) as response:
        return json.load(response)


def parse_deadline(deadline_text, current_time):
    """
    Convert a deadline such as '18:00' into a datetime.

    If that time has already passed today, treat it as tomorrow's
    deadline because the simulator represents repeating daily jobs.
    """
    if not deadline_text:
        return None

    hour, minute = map(int, deadline_text.split(":"))

    deadline = current_time.replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )

    if deadline <= current_time:
        deadline += timedelta(days=1)

    return deadline


def calculate_slot_minutes(forecast_points):
    """Determine the interval between forecast points."""
    if len(forecast_points) < 2:
        return 30

    first = datetime.fromisoformat(forecast_points[0]["timestamp"])
    second = datetime.fromisoformat(forecast_points[1]["timestamp"])

    minutes = int((second - first).total_seconds() / 60)
    return max(minutes, 1)


def prepare_forecast_capacity(forecast_points, base_load_kw):
    """
    Calculate available renewable power for every forecast slot.
    The dictionary is later reduced when a workload reserves power.
    """
    capacity = []

    for point in forecast_points:
        capacity.append({
            "timestamp": point["timestamp"],
            "available_kw": max(
                0.0,
                point["renewable_kw"] - base_load_kw,
            ),
        })

    return capacity


def find_and_reserve_slot(
    job,
    forecast_capacity,
    slot_minutes,
    deadline,
):
    """
    Find consecutive forecast slots that can power the complete job.

    When a slot is selected, reserve its power so another workload
    cannot claim the same renewable capacity.
    """
    slots_required = max(
        1,
        math.ceil(job["duration_minutes"] / slot_minutes),
    )

    for start_index in range(
        0,
        len(forecast_capacity) - slots_required + 1,
    ):
        selected_slots = forecast_capacity[
            start_index:start_index + slots_required
        ]

        start_time = datetime.fromisoformat(
            selected_slots[0]["timestamp"]
        )

        finish_time = (
            start_time
            + timedelta(minutes=job["duration_minutes"])
        )

        if deadline and finish_time > deadline:
            continue

        enough_power = all(
            slot["available_kw"] >= job["power_kw"]
            for slot in selected_slots
        )

        if not enough_power:
            continue

        # Reserve renewable capacity for this workload.
        for slot in selected_slots:
            slot["available_kw"] -= job["power_kw"]

        return {
            "start_time": start_time,
            "finish_time": finish_time,
        }

    return None


def decide_workloads(state):
    energy = state["energy"]
    workloads = state["workloads"]
    forecast_points = state["forecast"]["points"]

    current_time = datetime.fromisoformat(energy["timestamp"])
    base_load_kw = energy["load_kw"]

    available_now_kw = max(
        0.0,
        energy["renewable_kw"] - base_load_kw,
    )

    slot_minutes = calculate_slot_minutes(forecast_points)

    forecast_capacity = prepare_forecast_capacity(
        forecast_points,
        base_load_kw,
    )

    def sorting_key(job):
        deadline = parse_deadline(
            job.get("deadline"),
            current_time,
        )

        deadline_value = (
            deadline
            if deadline
            else datetime.max
        )

        return (
            PRIORITY_ORDER.get(job["priority"], 99),
            deadline_value,
        )

    sorted_workloads = sorted(
        workloads,
        key=sorting_key,
    )

    decisions = []
    newly_started_power_kw = 0.0

    for job in sorted_workloads:
        deadline = parse_deadline(
            job.get("deadline"),
            current_time,
        )

        # Running workloads are already included in the current load.
        if job["status"] == "RUNNING":
            decisions.append({
                "job_id": job["id"],
                "name": job["name"],
                "priority": job["priority"],
                "decision": "KEEP_RUNNING",
                "energy_source": "CURRENT_SUPPLY",
                "reason": "The workload is already running.",
            })
            continue

        # Critical jobs must not be postponed.
        if job["priority"] == "CRITICAL":
            decisions.append({
                "job_id": job["id"],
                "name": job["name"],
                "priority": job["priority"],
                "decision": "START_NOW",
                "energy_source": "RENEWABLE_BATTERY_OR_GRID",
                "reason": "Critical workloads cannot be postponed.",
            })

            newly_started_power_kw += job["power_kw"]
            available_now_kw = max(
                0.0,
                available_now_kw - job["power_kw"],
            )
            continue

        # Start immediately only when unreserved renewable power exists.
        if job["power_kw"] <= available_now_kw:
            decisions.append({
                "job_id": job["id"],
                "name": job["name"],
                "priority": job["priority"],
                "decision": "START_NOW",
                "energy_source": "SURPLUS_RENEWABLE",
                "allocated_power_kw": job["power_kw"],
                "reason": (
                    "Unreserved renewable power is available now."
                ),
            })

            available_now_kw -= job["power_kw"]
            newly_started_power_kw += job["power_kw"]
            continue

        reservation = find_and_reserve_slot(
            job,
            forecast_capacity,
            slot_minutes,
            deadline,
        )

        if reservation:
            decisions.append({
                "job_id": job["id"],
                "name": job["name"],
                "priority": job["priority"],
                "decision": "SCHEDULE",
                "energy_source": "FORECAST_RENEWABLE",
                "scheduled_start": (
                    reservation["start_time"].isoformat()
                ),
                "scheduled_finish": (
                    reservation["finish_time"].isoformat()
                ),
                "allocated_power_kw": job["power_kw"],
                "reason": (
                    "Consecutive renewable capacity was reserved "
                    "for the complete workload duration."
                ),
            })
            continue

        if deadline:
            latest_start = deadline - timedelta(
                minutes=job["duration_minutes"]
            )

            if latest_start <= current_time:
                decision = "START_NOW"
                scheduled_start = current_time
                reason = (
                    "The workload must start now to avoid missing "
                    "its deadline."
                )
                newly_started_power_kw += job["power_kw"]
            else:
                decision = "SCHEDULE_WITH_BACKUP"
                scheduled_start = latest_start
                reason = (
                    "Renewable capacity is insufficient; battery or "
                    "grid backup may be required to meet the deadline."
                )

            decisions.append({
                "job_id": job["id"],
                "name": job["name"],
                "priority": job["priority"],
                "decision": decision,
                "energy_source": "BATTERY_OR_GRID_BACKUP",
                "scheduled_start": scheduled_start.isoformat(),
                "deadline": deadline.isoformat(),
                "reason": reason,
            })
        else:
            decisions.append({
                "job_id": job["id"],
                "name": job["name"],
                "priority": job["priority"],
                "decision": "DEFER",
                "energy_source": None,
                "reason": (
                    "No renewable slot is available and the job "
                    "has no immediate deadline."
                ),
            })

    remaining_surplus_kw = (
        energy["renewable_kw"]
        - base_load_kw
        - newly_started_power_kw
    )

    return decisions, remaining_surplus_kw


def decide_battery_action(battery, remaining_surplus_kw):
    """
    Decide battery action only after current workload power
    has been allocated.
    """
    soc = battery["soc_percent"]

    if remaining_surplus_kw > 0 and soc < TARGET_BATTERY_SOC:
        return {
            "action": "CHARGE",
            "power_kw": round(remaining_surplus_kw, 2),
            "reason": (
                "Renewable power remains after supplying all "
                "current workloads."
            ),
        }

    if remaining_surplus_kw < 0 and soc > MIN_BATTERY_SOC:
        return {
            "action": "DISCHARGE",
            "power_kw": round(abs(remaining_surplus_kw), 2),
            "reason": (
                "Current demand exceeds renewable generation and "
                "the battery reserve is sufficient."
            ),
        }

    if remaining_surplus_kw < 0:
        return {
            "action": "USE_GRID",
            "power_kw": round(abs(remaining_surplus_kw), 2),
            "reason": (
                "The battery reserve is protected because its "
                "state of charge is too low."
            ),
        }

    return {
        "action": "IDLE",
        "power_kw": 0.0,
        "reason": (
            "No renewable surplus or power deficit remains."
        ),
    }


def generate_decision(state=None):
    """Generate decisions from supplied state, or fetch state for CLI use."""
    state = state or fetch_state()

    workload_decisions, remaining_surplus_kw = (
        decide_workloads(state)
    )

    battery_decision = decide_battery_action(
        state["battery"],
        remaining_surplus_kw,
    )

    return {
        "generated_at": state["energy"]["timestamp"],
        "input_summary": {
            "renewable_kw": state["energy"]["renewable_kw"],
            "base_load_kw": state["energy"]["load_kw"],
            "battery_soc_percent": (
                state["battery"]["soc_percent"]
            ),
        },
        "battery_decision": battery_decision,
        "workload_decisions": workload_decisions,
    }


if __name__ == "__main__":
    try:
        result = generate_decision()
        print(json.dumps(result, indent=2))
    except Exception as error:
        print(f"Decision engine failed: {error}")
