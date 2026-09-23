from copy import deepcopy
from datetime import datetime, timezone

from models import DecisionApplication, WorkloadCreate


RUN_ACTIONS = {"RUN", "START_NOW", "KEEP_RUNNING"}
SCHEDULE_ACTIONS = {"DELAY", "SCHEDULE", "SCHEDULE_WITH_BACKUP"}


class WorkloadManager:
    """Applies the decision engine's output to the simulator's workloads."""

    def __init__(self):
        self.history: list[dict] = []

    @staticmethod
    def _find(workloads: list[dict], job_id: str) -> dict | None:
        return next((job for job in workloads if job["id"] == job_id), None)

    def add_workload(self, workloads: list[dict], request: WorkloadCreate) -> dict:
        if self._find(workloads, request.id):
            raise ValueError("Workload already exists")

        workload = {
            **request.model_dump(),
            "status": "WAITING",
            "scheduled_start": None,
            "scheduled_finish": None,
            "last_reason": None,
        }
        workloads.append(workload)
        return deepcopy(workload)

    def apply(self, workloads: list[dict], request: DecisionApplication) -> dict:
        workload = self._find(workloads, request.job_id)
        if not workload:
            raise LookupError("Workload not found")

        action = request.action.upper()
        previous = {
            "status": workload.get("status"),
            "scheduled_start": workload.get("scheduled_start"),
            "scheduled_finish": workload.get("scheduled_finish"),
        }

        if action in RUN_ACTIONS:
            workload["status"] = "RUNNING"
            workload["scheduled_start"] = None
            workload["scheduled_finish"] = None
        elif action in SCHEDULE_ACTIONS:
            if workload["priority"] == "CRITICAL" and action == "DELAY":
                raise ValueError("Critical workloads cannot be delayed")
            workload["status"] = "SCHEDULED" if request.scheduled_start else "DEFERRED"
            workload["scheduled_start"] = request.scheduled_start
            workload["scheduled_finish"] = request.scheduled_finish
        elif action == "DEFER":
            if workload["priority"] == "CRITICAL":
                raise ValueError("Critical workloads cannot be deferred")
            workload["status"] = "DEFERRED"
            workload["scheduled_start"] = None
            workload["scheduled_finish"] = None
        else:
            raise ValueError(f"Unsupported action: {request.action}")

        workload["last_reason"] = request.reason

        current = {
            "status": workload.get("status"),
            "scheduled_start": workload.get("scheduled_start"),
            "scheduled_finish": workload.get("scheduled_finish"),
        }
        if previous != current:
            self.history.append({
                "applied_at": datetime.now(timezone.utc).isoformat(),
                "job_id": request.job_id,
                "action": action,
                "scheduled_start": request.scheduled_start,
                "scheduled_finish": request.scheduled_finish,
                "reason": request.reason,
            })

        return deepcopy(workload)

    def apply_engine_result(self, workloads: list[dict], result: dict) -> list[dict]:
        applied = []
        for decision in result["workload_decisions"]:
            request = DecisionApplication(
                job_id=decision["job_id"],
                action=decision["decision"],
                scheduled_start=decision.get("scheduled_start"),
                scheduled_finish=decision.get("scheduled_finish"),
                reason=decision.get("reason"),
            )
            applied.append(self.apply(workloads, request))
        return applied

    @staticmethod
    def schedule(workloads: list[dict]) -> list[dict]:
        return [
            deepcopy(job)
            for job in workloads
            if job.get("scheduled_start") is not None
        ]

    @staticmethod
    def status(workloads: list[dict]) -> dict:
        statuses = [str(job.get("status", "")).upper() for job in workloads]
        priorities = [str(job.get("priority", "")).upper() for job in workloads]
        return {
            "total": len(workloads),
            "running": statuses.count("RUNNING"),
            "waiting": statuses.count("WAITING"),
            "scheduled": statuses.count("SCHEDULED"),
            "deferred": statuses.count("DEFERRED"),
            "critical": priorities.count("CRITICAL"),
        }
