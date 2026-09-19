from contextlib import asynccontextmanager
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from timberborn_control.config import Settings
from timberborn_control.models import Configuration, Rule, Snapshot
from timberborn_control.service import TimberbornController


class LeverCommand(BaseModel):
    state: bool


class ColorCommand(BaseModel):
    color: str


class RulesPayload(BaseModel):
    rules: list[Rule]


class SensorSettingsPayload(BaseModel):
    auto_delete_missing_sensors: bool


class SessionCreatePayload(BaseModel):
    name: str


settings = Settings()
controller = TimberbornController(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await controller.start()
    yield
    await controller.stop()


app = FastAPI(title="Timberborn Control", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.cors_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_controller() -> TimberbornController:
    return controller


ControllerDep = Annotated[TimberbornController, Depends(get_controller)]


@app.get("/api/status", response_model=Snapshot)
async def status(service: ControllerDep) -> Snapshot:
    return service.snapshot()


@app.post("/api/refresh", response_model=Snapshot)
async def refresh(service: ControllerDep) -> Snapshot:
    return await service.refresh_once()


@app.post("/api/sessions", response_model=Snapshot)
async def create_session(payload: SessionCreatePayload, service: ControllerDep) -> Snapshot:
    try:
        return await service.create_session(payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not create session: {exc}") from exc


@app.post("/api/sessions/{session_id}/load", response_model=Snapshot)
async def load_session(session_id: str, service: ControllerDep) -> Snapshot:
    try:
        return await service.load_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' was not found.") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"Could not load session: {exc}") from exc


@app.put("/api/rules", response_model=Snapshot)
async def replace_rules(payload: RulesPayload, service: ControllerDep) -> Snapshot:
    try:
        await service.replace_rules(payload.rules)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not save configuration: {exc}") from exc
    return service.snapshot()


@app.put("/api/config", response_model=Snapshot)
async def replace_configuration(payload: Configuration, service: ControllerDep) -> Snapshot:
    try:
        await service.replace_configuration(payload)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not save configuration: {exc}") from exc
    return service.snapshot()


@app.delete("/api/sensors/{name}", response_model=Snapshot)
async def delete_sensor(name: str, service: ControllerDep) -> Snapshot:
    try:
        return await service.delete_sensor(name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Sensor '{name}' was not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not save configuration: {exc}") from exc


@app.put("/api/settings/sensors", response_model=Snapshot)
async def update_sensor_settings(payload: SensorSettingsPayload, service: ControllerDep) -> Snapshot:
    try:
        return await service.set_auto_delete_missing_sensors(payload.auto_delete_missing_sensors)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not save sensor settings: {exc}") from exc


@app.post("/api/levers/{name}")
async def switch_lever(name: str, payload: LeverCommand, service: ControllerDep) -> dict[str, str]:
    try:
        await service.switch_lever(name, payload.state)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"status": "ok"}


@app.post("/api/levers/{name}/color")
async def set_lever_color(
    name: str,
    payload: ColorCommand,
    service: ControllerDep,
) -> dict[str, str]:
    try:
        await service.set_lever_color(name, payload.color)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"status": "ok"}


@app.api_route("/api/timberborn/webhook/{name}/{state}", methods=["GET", "POST"], response_model=Snapshot)
async def adapter_webhook(
    name: str,
    state: str,
    service: ControllerDep,
) -> Snapshot:
    if state not in {"on", "off"}:
        raise HTTPException(status_code=422, detail="Webhook state must be 'on' or 'off'.")
    return await service.adapter_webhook(name, state == "on")


def run() -> None:
    uvicorn.run(
        "timberborn_control.api:app",
        host="127.0.0.1",
        port=8090,
        reload=True,
    )
