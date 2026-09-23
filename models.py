from pydantic import BaseModel, Field
from typing import List, Literal


class EnergyState(BaseModel):
    timestamp: str
    renewable_kw: float = Field(ge=0)
    grid_kw: float = Field(ge=0)
    load_kw: float = Field(ge=0)
    grid_price_per_kwh: float = Field(ge=0)


class BatteryState(BaseModel):
    timestamp: str
    soc_percent: float = Field(ge=0, le=100)
    soh_percent: float = Field(ge=0, le=100)
    power_kw: float
    status: str
    temperature_c: float


class ForecastPoint(BaseModel):
    timestamp: str
    renewable_kw: float = Field(ge=0)


class Forecast(BaseModel):
    generated_at: str
    horizon_hours: int
    points: List[ForecastPoint]


class Workload(BaseModel):
    id: str
    name: str
    power_kw: float = Field(gt=0)
    duration_minutes: int = Field(gt=0)
    deadline: str | None = None
    priority: str
    preemptible: bool = True
    status: str
    scheduled_start: str | None = None
    scheduled_finish: str | None = None
    last_reason: str | None = None


class WorkloadCreate(BaseModel):
    id: str
    name: str
    power_kw: float = Field(gt=0)
    duration_minutes: int = Field(gt=0)
    deadline: str | None = None
    priority: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"] = "MEDIUM"
    preemptible: bool = True


class DecisionApplication(BaseModel):
    job_id: str
    action: str
    scheduled_start: str | None = None
    scheduled_finish: str | None = None
    reason: str | None = None


class SystemState(BaseModel):
    energy: EnergyState
    battery: BatteryState
    forecast: Forecast
    workloads: List[Workload]
