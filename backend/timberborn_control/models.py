from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class GameConnection(BaseModel):
    ok: bool = False
    base_url: str
    last_error: str | None = None
    last_seen_at: datetime | None = None


class Lever(BaseModel):
    name: str
    state: bool
    springReturn: bool = False


class Adapter(BaseModel):
    name: str
    state: bool


class SensorType(StrEnum):
    depth = "Depth"
    resource = "Resource"
    flow = "Flow"
    badwater = "BadWater"


class SensorRole(StrEnum):
    needs_water = "needs_water"
    full = "full"
    reserve = "reserve"
    heavy_flow = "heavy_flow"
    badwater = "badwater"


class Sensor(BaseModel):
    adapter_name: str = Field(min_length=1)
    type: SensorType | None = None
    role: SensorRole | None = None
    tank_id: str | None = None
    last_state: bool | None = None
    last_seen_at: datetime | None = None

    @model_validator(mode="after")
    def role_matches_type(self) -> "Sensor":
        expected = {
            SensorRole.needs_water: SensorType.depth,
            SensorRole.full: SensorType.depth,
            SensorRole.reserve: SensorType.depth,
            SensorRole.heavy_flow: SensorType.flow,
            SensorRole.badwater: SensorType.badwater,
        }
        if self.role is not None and self.type != expected[self.role]:
            raise ValueError(f"{self.role} requires a {expected[self.role]} sensor")
        return self


class SensorStatus(Sensor):
    state: bool | None = None
    error: str | None = None


class TankDirection(StrEnum):
    north = "N"
    east = "E"
    south = "S"
    west = "W"


class Tank(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    priority: int | None = Field(default=None, ge=0)
    direction: TankDirection | None = None
    parent_tank_id: str | None = None


class GateType(StrEnum):
    one_meter = "1m"
    two_meter = "2m"
    three_meter = "3m"


class Gate(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    type: GateType
    open_when_on: bool = True
    tank_id: str | None = None
    destination_tank_id: str | None = None
    lever_name: str | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_heights(cls, value: object) -> object:
        if isinstance(value, dict) and "open_when_on" not in value:
            off_height = value.get("off_height_m")
            on_height = value.get("on_height_m")
            if off_height is not None and on_height is not None and off_height != on_height:
                return {**value, "open_when_on": float(on_height) < float(off_height)}
        return value


class GraphPosition(BaseModel):
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)


class Configuration(BaseModel):
    auto_delete_missing_sensors: bool = False
    graph_positions: dict[str, GraphPosition] = Field(default_factory=dict)
    tanks: list[Tank] = Field(default_factory=list)
    gates: list[Gate] = Field(default_factory=list)
    sensors: list[Sensor] = Field(default_factory=list)
    rules: list["Rule"] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_and_valid_references(self) -> "Configuration":
        for label, values in (
            ("tank", [item.id for item in self.tanks]),
            ("gate", [item.id for item in self.gates]),
            ("sensor", [item.adapter_name for item in self.sensors]),
            ("rule", [item.id for item in self.rules]),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {label} identifier")
        tank_ids = {tank.id for tank in self.tanks}
        parents = {tank.id: tank.parent_tank_id for tank in self.tanks}
        for tank in self.tanks:
            if tank.parent_tank_id is not None and tank.parent_tank_id not in tank_ids:
                raise ValueError(f"unknown parent tank: {tank.parent_tank_id}")
            if tank.parent_tank_id is not None and tank.direction is None:
                raise ValueError(f"direction required for relative tank: {tank.name}")
            seen = {tank.id}
            parent = tank.parent_tank_id
            while parent is not None:
                if parent in seen:
                    raise ValueError(f"cyclic tank placement: {tank.name}")
                seen.add(parent)
                parent = parents[parent]
        for item in [*self.gates, *self.sensors]:
            if item.tank_id is not None and item.tank_id not in tank_ids:
                raise ValueError(f"unknown tank: {item.tank_id}")
        for gate in self.gates:
            if gate.destination_tank_id is not None and gate.destination_tank_id not in tank_ids:
                raise ValueError(f"unknown destination tank: {gate.destination_tank_id}")
            if gate.tank_id is not None and gate.tank_id == gate.destination_tank_id:
                raise ValueError(f"gate {gate.name} cannot feed its source tank")
        return self


class RuleOperator(StrEnum):
    all = "all"
    any = "any"


class RuleAction(StrEnum):
    switch_on = "switch_on"
    switch_off = "switch_off"


class Rule(BaseModel):
    id: str
    enabled: bool = True
    description: str = ""
    when_adapters: dict[str, bool] = Field(default_factory=dict)
    operator: RuleOperator = RuleOperator.all
    action: RuleAction
    lever: str


class RuleEvaluation(BaseModel):
    rule_id: str
    matched: bool
    skipped: bool = False
    reason: str | None = None
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AutomationDecision(BaseModel):
    rule: str
    inputs: dict[str, bool | None] = Field(default_factory=dict)
    lever_name: str | None = None
    output_state: bool | None = None
    reason: str
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ServiceEvent(BaseModel):
    kind: str
    message: str
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    detail: dict[str, str | bool | int | float | None] = Field(default_factory=dict)
    decision: AutomationDecision | None = None


class SessionInfo(BaseModel):
    id: str
    name: str


class SessionIndex(BaseModel):
    active_id: str
    sessions: list[SessionInfo]

    @model_validator(mode="after")
    def active_session_exists(self) -> "SessionIndex":
        ids = [session.id for session in self.sessions]
        if len(ids) != len(set(ids)) or self.active_id not in ids:
            raise ValueError("session index has duplicate IDs or an unknown active session")
        return self


class Snapshot(BaseModel):
    active_session_id: str
    session_name: str
    sessions: list[SessionInfo]
    connection: GameConnection
    auto_delete_missing_sensors: bool = False
    graph_positions: dict[str, GraphPosition] = Field(default_factory=dict)
    config_error: str | None = None
    automation_error: str | None = None
    event_log_error: str | None = None
    levers: list[Lever] = Field(default_factory=list)
    adapters: list[Adapter] = Field(default_factory=list)
    tanks: list[Tank] = Field(default_factory=list)
    gates: list[Gate] = Field(default_factory=list)
    sensors: list[SensorStatus] = Field(default_factory=list)
    rules: list[Rule] = Field(default_factory=list)
    last_rule_evaluations: list[RuleEvaluation] = Field(default_factory=list)
    decisions: list[AutomationDecision] = Field(default_factory=list)
    events: list[ServiceEvent] = Field(default_factory=list)
