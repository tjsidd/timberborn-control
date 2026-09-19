import re

from timberborn_control.models import Configuration, SensorType, Tank

SENSOR_NAME = re.compile(r"^HTTP (?P<location>.+?) (?P<signal>D\d+|C)$", re.IGNORECASE)
GATE_NAME = re.compile(
    r"^(?P<location>.+?) (?P<kind>Input|In|Output|Out|Diversion)(?: \d+)?$",
    re.IGNORECASE,
)


def match_tank(location: str, tanks: list[Tank]) -> Tank | None:
    name = re.sub(r"\s+Tank$", "", location.strip(), flags=re.IGNORECASE)
    candidates = [name]
    directional = re.fullmatch(r"([NESW]) (.+)", name, flags=re.IGNORECASE)
    if directional:
        candidates.append(f"{directional.group(2)} {directional.group(1)}")
    for candidate in candidates:
        matches = [tank for tank in tanks if tank.name.casefold() == candidate.casefold()]
        if len(matches) == 1:
            return matches[0]
    return None


def auto_tag(configuration: Configuration) -> tuple[Configuration, list[tuple[str, str, str]]]:
    tags: list[tuple[str, str, str]] = []
    sensors = []
    for sensor in configuration.sensors:
        match = SENSOR_NAME.fullmatch(sensor.adapter_name)
        updates = {}
        if match:
            tank = match_tank(match["location"], configuration.tanks)
            if tank and sensor.tank_id is None:
                updates["tank_id"] = tank.id
                tags.append(("sensor", sensor.adapter_name, tank.name))
            if sensor.type is None and sensor.role is None:
                updates["type"] = (
                    SensorType.depth if match["signal"].upper().startswith("D")
                    else SensorType.badwater
                )
        sensors.append(sensor.model_copy(update=updates) if updates else sensor)

    gates = []
    for gate in configuration.gates:
        match = GATE_NAME.fullmatch(gate.name)
        updates = {}
        if match:
            kind = match["kind"].casefold()
            tank = match_tank(match["location"], configuration.tanks)
            if kind in ("input", "in") and gate.destination_tank_id is None and tank:
                if tank.id != gate.tank_id:
                    updates["destination_tank_id"] = tank.id
                    tags.append(("gate destination", gate.name, tank.name))
            elif kind in ("output", "out", "diversion") and gate.tank_id is None:
                if tank is None and kind in ("output", "out"):
                    south = re.fullmatch(r"S (.+)", match["location"], flags=re.IGNORECASE)
                    if south:
                        tank = match_tank(south.group(1), configuration.tanks)
                if tank and tank.id != gate.destination_tank_id:
                    updates["tank_id"] = tank.id
                    tags.append(("gate source", gate.name, tank.name))
        gates.append(gate.model_copy(update=updates) if updates else gate)
    return configuration.model_copy(update={"sensors": sensors, "gates": gates}), tags
