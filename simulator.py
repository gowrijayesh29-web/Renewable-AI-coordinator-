import math
import random
from datetime import datetime, timedelta


class Simulator:
    def __init__(self):
        self.reset()

    def reset(self):
        self.sim_time = datetime(2026, 9, 21, 10, 0, 0)
        self.battery_soc = 72.0
        self.battery_soh = 92.0
        self.battery_power_kw = 0.0
        self.battery_status = "IDLE"
        self.temperature_c = 29.0
        self.demo_mode = "normal"
        self.last_renewable_kw = 0.0

        self.workloads = [
            {
                "id": "JOB001",
                "name": "Security_Monitoring",
                "power_kw": 1.0,
                "duration_minutes": 1440,
                "deadline": None,
                "priority": "CRITICAL",
                "preemptible": False,
                "status": "RUNNING",
                "scheduled_start": None,
                "scheduled_finish": None,
                "last_reason": None,
            },
            {
                "id": "JOB002",
                "name": "AI_Training",
                "power_kw": 4.0,
                "duration_minutes": 120,
                "deadline": "18:00",
                "priority": "MEDIUM",
                "preemptible": True,
                "status": "WAITING",
                "scheduled_start": None,
                "scheduled_finish": None,
                "last_reason": None,
            },
            {
                "id": "JOB003",
                "name": "Database_Backup",
                "power_kw": 2.0,
                "duration_minutes": 60,
                "deadline": "23:00",
                "priority": "LOW",
                "preemptible": True,
                "status": "WAITING",
                "scheduled_start": None,
                "scheduled_finish": None,
                "last_reason": None,
            },
            {
                "id": "JOB004",
                "name": "Analytics",
                "power_kw": 2.0,
                "duration_minutes": 120,
                "deadline": "22:00",
                "priority": "MEDIUM",
                "preemptible": True,
                "status": "WAITING",
                "scheduled_start": None,
                "scheduled_finish": None,
                "last_reason": None,
            },
        ]

    def renewable_kw(self, dt=None):
        dt = dt or self.sim_time
        hour = dt.hour + dt.minute / 60

        if hour < 6 or hour >= 18:
            base = 0.0
        else:
            base = 9.5 * math.exp(-((hour - 13.0) / 3.2) ** 2)

        if self.demo_mode == "solar_drop":
            base *= 0.20
        elif self.demo_mode == "solar_peak":
            base = min(10.0, base * 1.35 + 1.0)

        return round(max(0.0, base + random.uniform(-0.15, 0.15)), 2)

    def current_load_kw(self):
        return round(sum(
            w["power_kw"] for w in self.workloads if w["status"] == "RUNNING"
        ), 2)

    def grid_price(self):
        hour = self.sim_time.hour + self.sim_time.minute / 60
        if 17 <= hour < 21:
            return 12.0
        if 10 <= hour < 16:
            return 7.0
        return 8.0

    def advance(self, minutes=1):
        renewable = self.renewable_kw()
        self.last_renewable_kw = renewable
        load = self.current_load_kw()
        surplus = renewable - load

        if self.demo_mode == "battery_low":
            self.battery_soc = max(18.0, self.battery_soc)

        if surplus > 0 and self.battery_soc < 95:
            charge_kw = min(surplus * 0.8, 3.0)
            self.battery_power_kw = charge_kw
            self.battery_status = "CHARGING"
            self.battery_soc += charge_kw * minutes / 60 / 10
        elif surplus < 0 and self.battery_soc > 15:
            discharge_kw = min(abs(surplus), 3.0)
            self.battery_power_kw = -discharge_kw
            self.battery_status = "DISCHARGING"
            self.battery_soc -= discharge_kw * minutes / 60 / 10
        else:
            self.battery_power_kw = 0.0
            self.battery_status = "IDLE"

        self.battery_soc = round(max(0, min(100, self.battery_soc)), 2)

        if abs(self.battery_power_kw) > 0:
            self.battery_soh = round(
                max(70.0, self.battery_soh - 0.00002 * abs(self.battery_power_kw) * minutes),
                4,
            )

        self.temperature_c = round(
            29.0 + abs(self.battery_power_kw) * 0.7 + random.uniform(-0.2, 0.2),
            2,
        )
        self.sim_time += timedelta(minutes=minutes)
        self._start_due_workloads()

    def _start_due_workloads(self):
        """Start workloads once their scheduled simulation time is reached."""
        for workload in self.workloads:
            scheduled_start = workload.get("scheduled_start")
            if workload.get("status") != "SCHEDULED" or not scheduled_start:
                continue

            if datetime.fromisoformat(scheduled_start) <= self.sim_time:
                workload["status"] = "RUNNING"
                workload["scheduled_start"] = None
                workload["scheduled_finish"] = None

    def forecast(self, hours=8):
        points = []
        for i in range(hours * 2 + 1):
            dt = self.sim_time + timedelta(minutes=i * 30)
            points.append({
                "timestamp": dt.isoformat(),
                "renewable_kw": self.renewable_kw(dt),
            })
        return points

    def energy_state(self):
        renewable = self.last_renewable_kw if self.last_renewable_kw >= 0 else self.renewable_kw()
        load = self.current_load_kw()
        # Battery power is positive while charging and negative while discharging.
        # Grid power is the remaining demand after renewable generation and battery action.
        grid = max(0.0, load + self.battery_power_kw - renewable)
        return {
            "timestamp": self.sim_time.isoformat(),
            "renewable_kw": renewable,
            "grid_kw": round(grid, 2),
            "load_kw": load,
            "grid_price_per_kwh": self.grid_price(),
        }

    def battery_state(self):
        return {
            "timestamp": self.sim_time.isoformat(),
            "soc_percent": self.battery_soc,
            "soh_percent": self.battery_soh,
            "power_kw": round(self.battery_power_kw, 2),
            "status": self.battery_status,
            "temperature_c": self.temperature_c,
        }

    def set_demo_mode(self, mode):
        self.demo_mode = mode
        if mode == "battery_low":
            self.battery_soc = 22.0
