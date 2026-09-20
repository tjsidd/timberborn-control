import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from timberborn_control.models import (
    AdaptiveFlowEstimate,
    AdaptiveFlowStatus,
    Configuration,
    Gate,
    SensorType,
    Tank,
)


@dataclass(frozen=True)
class AdaptiveInput:
    gate: Gate
    tank: Tank
    low_sensor: str
    high_sensor: str


@dataclass
class FillCycle:
    opened_at: datetime
    band_started_at: datetime | None = None
    sampled: bool = False


@dataclass(frozen=True)
class FlowSample:
    gate: Gate
    elapsed_seconds: float
    observed_rate: float
    estimate: AdaptiveFlowEstimate


@dataclass(frozen=True)
class AdaptiveCutoff:
    gate: Gate
    low_sensor: str
    high_sensor: str
    reason: str
    predicted: bool = False


@dataclass(frozen=True)
class AdaptiveLog:
    kind: str
    message: str
    detail: dict[str, str | bool | int | float | None]


@dataclass
class AdaptiveObservation:
    samples: list[FlowSample]
    cutoffs: list[AdaptiveCutoff]
    logs: list[AdaptiveLog]
    next_deadline: datetime | None


def eligible_inputs(config: Configuration) -> list[AdaptiveInput]:
    inputs_by_tank: dict[str, list[Gate]] = {}
    for gate in input_gates(config):
        if gate.destination_tank_id:
            inputs_by_tank.setdefault(gate.destination_tank_id, []).append(gate)

    result = []
    for tank in config.tanks:
        gates = inputs_by_tank.get(tank.id, [])
        if (len(gates) != 1 or not gates[0].lever_name
                or not any(rule.enabled and rule.lever == gates[0].lever_name for rule in config.rules)):
            continue
        sensors = [sensor for sensor in config.sensors
                   if sensor.tank_id == tank.id and sensor.type in (None, SensorType.depth)]
        low = [sensor.adapter_name for sensor in sensors
               if re.search(r"\bD65$", sensor.adapter_name, re.IGNORECASE)]
        high = [sensor.adapter_name for sensor in sensors
                if re.search(r"\bD85$", sensor.adapter_name, re.IGNORECASE)]
        if len(low) == len(high) == 1:
            result.append(AdaptiveInput(gates[0], tank, low[0], high[0]))
    return result


def input_gates(config: Configuration) -> list[Gate]:
    return [gate for gate in config.gates
            if re.search(r"\b(?:Input|In)(?: \d+)?$", gate.name, re.IGNORECASE)]


class AdaptiveFlowController:
    def __init__(self) -> None:
        self.cycles: dict[str, FillCycle] = {}
        self.last_open: dict[str, bool] = {}
        self.last_low: dict[str, bool] = {}
        self.held_off: set[str] = set()

    def reset(self, held_gate_ids: set[str] | None = None) -> None:
        self.cycles.clear()
        self.last_open.clear()
        self.last_low.clear()
        self.held_off = set(held_gate_ids or ())

    def observe(self, config: Configuration, adapters: dict[str, bool],
                levers: dict[str, bool], now: datetime) -> AdaptiveObservation:
        samples: list[FlowSample] = []
        cutoffs: list[AdaptiveCutoff] = []
        logs: list[AdaptiveLog] = []
        deadlines = []
        eligible = eligible_inputs(config)
        eligible_ids = {item.gate.id for item in eligible}
        self.held_off.intersection_update(gate.id for gate in config.gates)
        for item in eligible:
            gate = item.gate
            lever_state = levers.get(gate.lever_name or "")
            low = adapters.get(item.low_sensor)
            high = adapters.get(item.high_sensor)
            if low is False:
                self.held_off.discard(gate.id)
            if lever_state is None or low is None or high is None:
                self.cycles.pop(gate.id, None)
                self.last_open.pop(gate.id, None)
                self.last_low.pop(gate.id, None)
                if config.adaptive_flow_enabled and gate.id in self.held_off:
                    cutoffs.append(AdaptiveCutoff(gate, item.low_sensor, item.high_sensor,
                                                  "Adaptive input held closed until D65 is confirmed inactive."))
                continue

            is_open = lever_state is gate.open_when_on
            previous_open = self.last_open.get(gate.id)
            cycle = self.cycles.get(gate.id)
            if previous_open is False and is_open:
                cycle = FillCycle(opened_at=now)
                self.cycles[gate.id] = cycle
                logs.append(AdaptiveLog("adaptive.timer_started", f"Started fill timer for {gate.name}.",
                                        {"gate": gate.name}))
            elif previous_open is True and not is_open:
                if cycle:
                    logs.append(self._stopped_log(gate, cycle, now))
                self.cycles.pop(gate.id, None)
                cycle = None
            self.last_open[gate.id] = is_open

            if not low:
                if cycle:
                    cycle.band_started_at = None
                    cycle.sampled = False
            elif cycle and is_open and self.last_low.get(gate.id) is False and not high:
                cycle.band_started_at = now
                cycle.sampled = False
                logs.append(AdaptiveLog("adaptive.band_started", f"{gate.name} reached D65; measuring fill to D85.",
                                        {"gate": gate.name, "sensor": item.low_sensor}))
            self.last_low[gate.id] = low

            if cycle and is_open and low and high and cycle.band_started_at and not cycle.sampled:
                elapsed = (now - cycle.band_started_at).total_seconds()
                if elapsed > 0:
                    rate = 20 / elapsed
                    previous = config.adaptive_flow_estimates.get(gate.id)
                    smoothed = rate if previous is None else previous.fill_percent_per_second * 0.6 + rate * 0.4
                    estimate = AdaptiveFlowEstimate(fill_percent_per_second=smoothed,
                                                    updated_at=now,
                                                    samples=1 if previous is None else previous.samples + 1)
                    samples.append(FlowSample(gate, elapsed, rate, estimate))
                    cycle.sampled = True

            if not config.adaptive_flow_enabled or not low:
                continue
            if high:
                self.held_off.add(gate.id)
                cutoffs.append(AdaptiveCutoff(gate, item.low_sensor, item.high_sensor,
                                              "D85 is active; adaptive input held closed."))
                continue
            if gate.id in self.held_off:
                cutoffs.append(AdaptiveCutoff(gate, item.low_sensor, item.high_sensor,
                                              "Adaptive input held closed until D65 is inactive."))
                continue
            estimate = config.adaptive_flow_estimates.get(gate.id)
            if not cycle or not cycle.band_started_at or estimate is None:
                continue
            deadline = cycle.band_started_at + timedelta(
                seconds=(config.adaptive_target_percent - 65) / estimate.fill_percent_per_second)
            if now >= deadline:
                self.held_off.add(gate.id)
                cutoffs.append(AdaptiveCutoff(gate, item.low_sensor, item.high_sensor,
                                              f"Predicted {config.adaptive_target_percent:g}% fill reached before D85.",
                                              predicted=True))
                logs.append(AdaptiveLog("adaptive.warning",
                                        f"{gate.name}: predicted fill target reached, but D85 is still inactive.",
                                        {"gate": gate.name, "target_percent": config.adaptive_target_percent,
                                         "rate_percent_per_second": estimate.fill_percent_per_second}))
            else:
                deadlines.append(deadline)

        for gate in config.gates:
            if gate.id not in self.held_off or gate.id in eligible_ids:
                continue
            low_names = [sensor.adapter_name for sensor in config.sensors
                         if sensor.tank_id == gate.destination_tank_id
                         and re.search(r"\bD65$", sensor.adapter_name, re.IGNORECASE)]
            low_name = low_names[0] if len(low_names) == 1 else "D65"
            if adapters.get(low_name) is False:
                self.held_off.discard(gate.id)
            elif config.adaptive_flow_enabled and gate.lever_name:
                cutoffs.append(AdaptiveCutoff(gate, low_name, "D85",
                                              "Adaptive input held closed until D65 is confirmed inactive."))

        return AdaptiveObservation(samples, cutoffs, logs,
                                   min(deadlines) if deadlines else None)

    def command(self, gate: Gate, open_gate: bool, now: datetime) -> AdaptiveLog | None:
        previous = self.last_open.get(gate.id)
        self.last_open[gate.id] = open_gate
        if open_gate and previous is not True:
            self.cycles[gate.id] = FillCycle(opened_at=now)
            return AdaptiveLog("adaptive.timer_started", f"Started fill timer for {gate.name}.",
                               {"gate": gate.name})
        if not open_gate:
            cycle = self.cycles.pop(gate.id, None)
            if cycle:
                return self._stopped_log(gate, cycle, now)
        return None

    def statuses(self, config: Configuration) -> list[AdaptiveFlowStatus]:
        result = []
        eligible = {item.gate.id: item for item in eligible_inputs(config)}
        for gate in input_gates(config):
            item = eligible.get(gate.id)
            tank = next((tank for tank in config.tanks if tank.id == gate.destination_tank_id), None)
            if item:
                error = None
            elif not tank:
                error = "Destination tank is not assigned."
            elif not gate.lever_name:
                error = "HTTP Lever is not assigned."
            elif not any(rule.enabled and rule.lever == gate.lever_name for rule in config.rules):
                error = "No enabled rule controls this lever."
            elif sum(other.destination_tank_id == tank.id for other in input_gates(config)) > 1:
                error = "Multiple input gates share this tank."
            else:
                error = "A unique D65 and D85 sensor pair is required."
            cycle = self.cycles.get(gate.id)
            estimate = config.adaptive_flow_estimates.get(gate.id)
            deadline = (cycle.band_started_at + timedelta(
                seconds=(config.adaptive_target_percent - 65) / estimate.fill_percent_per_second)
                if cycle and cycle.band_started_at and estimate and config.adaptive_flow_enabled else None)
            result.append(AdaptiveFlowStatus(
                gate_id=gate.id, gate_name=gate.name, tank_name=tank.name if tank else "Unassigned",
                fill_percent_per_second=estimate.fill_percent_per_second if estimate else None,
                opened_at=cycle.opened_at if cycle else None,
                band_started_at=cycle.band_started_at if cycle else None,
                predicted_close_at=deadline, held_off=gate.id in self.held_off,
                eligibility_error=error,
            ))
        return result

    @staticmethod
    def _stopped_log(gate: Gate, cycle: FillCycle, now: datetime) -> AdaptiveLog:
        return AdaptiveLog("adaptive.timer_stopped", f"Stopped fill timer for {gate.name}.",
                           {"gate": gate.name, "open_seconds": max(0, (now - cycle.opened_at).total_seconds()),
                            "sampled": cycle.sampled})
