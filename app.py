import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from auth import auth_service, router as auth_router
from decision_engine import generate_decision
from models import (
    BatteryState,
    DecisionApplication,
    Forecast,
    SystemState,
    Workload,
    WorkloadCreate,
)
from simulator import Simulator
from workload_manager import WorkloadManager


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

load_dotenv()

simulator = Simulator()
workload_manager = WorkloadManager()
sim_minutes = float(os.getenv("SIM_MINUTES_PER_REAL_SECOND", "1"))


def current_state() -> dict:
    return {
        "energy": simulator.energy_state(),
        "battery": simulator.battery_state(),
        "forecast": {
            "generated_at": simulator.sim_time.isoformat(),
            "horizon_hours": 8,
            "points": simulator.forecast(8),
        },
        "workloads": simulator.workloads,
    }


async def simulation_loop():
    while True:
        await asyncio.sleep(1)
        simulator.advance(sim_minutes)


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(simulation_loop())
    yield
    task.cancel()


app = FastAPI(
    title="Renewable AI Coordinator",
    description=(
        "Integrated renewable simulator, explainable decision engine, "
        "workload manager and dashboard."
    ),
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)


@app.middleware("http")
async def require_api_authentication(request: Request, call_next):
    path = request.url.path
    public_api_paths = {"/api/health"}
    is_auth_api = path.startswith("/api/auth/")
    if path.startswith("/api/") and path not in public_api_paths and not is_auth_api:
        email = auth_service.session_email(
            request.cookies.get(auth_service.cookie_name)
        )
        if not email:
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required."},
            )
        request.state.user_email = email
    return await call_next(request)


@app.get("/", include_in_schema=False)
def home(request: Request):
    email = auth_service.session_email(request.cookies.get(auth_service.cookie_name))
    return RedirectResponse("/dashboard" if email else "/login", status_code=303)


@app.get("/login", include_in_schema=False)
def login_page(request: Request):
    email = auth_service.session_email(request.cookies.get(auth_service.cookie_name))
    if email:
        return RedirectResponse("/dashboard", status_code=303)
    return FileResponse(STATIC_DIR / "auth.html")


@app.get("/dashboard", include_in_schema=False)
def dashboard(request: Request):
    email = auth_service.session_email(request.cookies.get(auth_service.cookie_name))
    if not email:
        return RedirectResponse("/login", status_code=303)
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    return {"status": "healthy", "service": "renewable-ai-coordinator"}


@app.get("/api/energy")
def get_energy():
    return simulator.energy_state()


@app.get("/api/battery", response_model=BatteryState)
def get_battery():
    return simulator.battery_state()


@app.get("/api/forecast", response_model=Forecast)
def get_forecast():
    return current_state()["forecast"]


@app.get("/api/workloads", response_model=list[Workload])
def get_workloads():
    return simulator.workloads


@app.post("/api/workloads", response_model=Workload, status_code=201)
def add_workload(workload: WorkloadCreate):
    try:
        return workload_manager.add_workload(simulator.workloads, workload)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/api/state", response_model=SystemState)
def get_state():
    return current_state()


@app.get("/api/decisions")
def get_decisions():
    return generate_decision(current_state())


@app.post("/api/decisions")
def apply_decision(decision: DecisionApplication):
    try:
        workload = workload_manager.apply(simulator.workloads, decision)
        return {"message": "Decision applied successfully", "workload": workload}
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/coordinate")
def coordinate():
    """Generate recommendations and apply them through Person 3's manager."""
    decisions = generate_decision(current_state())
    applied = workload_manager.apply_engine_result(simulator.workloads, decisions)
    return {"decisions": decisions, "applied_workloads": applied}


@app.get("/api/schedule")
def get_schedule():
    return workload_manager.schedule(simulator.workloads)


@app.get("/api/status")
def get_status():
    return workload_manager.status(simulator.workloads)


@app.get("/api/history")
def get_history():
    return workload_manager.history


@app.post("/api/demo/solar-drop")
def solar_drop():
    simulator.set_demo_mode("solar_drop")
    return {"message": "Solar drop scenario enabled"}


@app.post("/api/demo/solar-peak")
def solar_peak():
    simulator.set_demo_mode("solar_peak")
    return {"message": "Solar peak scenario enabled"}


@app.post("/api/demo/battery-low")
def battery_low():
    simulator.set_demo_mode("battery_low")
    return {"message": "Battery-low scenario enabled", "soc_percent": simulator.battery_soc}


@app.post("/api/demo/normal")
def normal():
    simulator.set_demo_mode("normal")
    return {"message": "Normal simulation enabled"}


@app.post("/api/reset")
def reset():
    simulator.reset()
    workload_manager.history.clear()
    return {"message": "Simulation and workload history reset"}
