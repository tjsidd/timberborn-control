import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from timberborn_control.adaptive import AdaptiveFlowController, eligible_inputs
from timberborn_control.config import Settings
from timberborn_control.models import (
    Adapter,
    AdaptiveFlowEstimate,
    Configuration,
    Gate,
    Lever,
    Rule,
    Sensor,
    Tank,
)
from timberborn_control.service import TimberbornController
from timberborn_control.storage import ConfigStore

LOW = "HTTP Main D65"
HIGH = "HTTP Main D85"
LEVER = "HTTP Main Input"


def configuration(enabled: bool = False, rate: float | None = None) -> Configuration:
    estimate = ({"input": AdaptiveFlowEstimate(fill_percent_per_second=rate,
                                                  updated_at=datetime.now(UTC))}
                if rate is not None else {})
    return Configuration(
        adaptive_flow_enabled=enabled,
        adaptive_flow_estimates=estimate,
        tanks=[Tank(id="main", name="Main")],
        gates=[Gate(id="input", name="Main Input", type="2m",
                    destination_tank_id="main", lever_name=LEVER)],
        sensors=[Sensor(adapter_name=LOW, type="Depth", tank_id="main"),
                 Sensor(adapter_name=HIGH, type="Depth", tank_id="main")],
        rules=[Rule(id="open", when_adapters={HIGH: False}, action="switch_on", lever=LEVER),
               Rule(id="close", when_adapters={HIGH: True}, action="switch_off", lever=LEVER)],
    )


def test_flow_rate_uses_d65_to_d85_not_gate_open_time() -> None:
    config = configuration()
    adaptive = AdaptiveFlowController()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    states = {LOW: False, HIGH: False}
    adaptive.observe(config, states, {LEVER: False}, start)
    adaptive.command(config.gates[0], True, start)
    assert adaptive.observe(config, states, {LEVER: True}, start + timedelta(seconds=5)).samples == []
    states[LOW] = True
    adaptive.observe(config, states, {LEVER: True}, start + timedelta(seconds=10))
    states[HIGH] = True
    observation = adaptive.observe(config, states, {LEVER: True}, start + timedelta(seconds=12))
    assert len(observation.samples) == 1
    assert observation.samples[0].elapsed_seconds == 2
    assert observation.samples[0].estimate.fill_percent_per_second == 10


def test_predicted_cutoff_warns_and_holds_until_d65_clears() -> None:
    config = configuration(enabled=True, rate=10)
    adaptive = AdaptiveFlowController()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    states = {LOW: False, HIGH: False}
    adaptive.observe(config, states, {LEVER: False}, start)
    adaptive.command(config.gates[0], True, start)
    states[LOW] = True
    observation = adaptive.observe(config, states, {LEVER: True}, start + timedelta(seconds=1))
    assert observation.next_deadline == start + timedelta(seconds=4)
    observation = adaptive.observe(config, states, {LEVER: True}, start + timedelta(seconds=4))
    assert observation.cutoffs[0].predicted
    assert any(log.kind == "adaptive.warning" for log in observation.logs)
    adaptive.command(config.gates[0], False, start + timedelta(seconds=4))
    observation = adaptive.observe(config, states, {LEVER: False}, start + timedelta(seconds=5))
    assert len(observation.cutoffs) == 1
    assert not any(log.kind == "adaptive.warning" for log in observation.logs)
    states[LOW] = False
    observation = adaptive.observe(config, states, {LEVER: False}, start + timedelta(seconds=6))
    assert observation.cutoffs == []
    assert adaptive.statuses(config)[0].held_off is False


def test_missing_or_ambiguous_signals_are_not_controlled() -> None:
    config = configuration(enabled=True, rate=10)
    config.gates.append(Gate(id="second", name="Main In 2", type="1m",
                             destination_tank_id="main", lever_name="HTTP Main In 2"))
    config.rules.append(Rule(id="second-open", when_adapters={HIGH: False},
                             action="switch_on", lever="HTTP Main In 2"))
    assert eligible_inputs(config) == []
    assert all(status.eligibility_error == "Multiple input gates share this tank."
               for status in AdaptiveFlowController().statuses(config))
    config.gates.pop()
    config.rules.pop()
    adaptive = AdaptiveFlowController()
    observation = adaptive.observe(config, {LOW: True}, {LEVER: True}, datetime.now(UTC))
    assert observation.cutoffs == []


def test_beta_off_keeps_learning_but_does_not_close_at_prediction() -> None:
    config = configuration(rate=10)
    adaptive = AdaptiveFlowController()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    states = {LOW: False, HIGH: False}
    adaptive.observe(config, states, {LEVER: False}, start)
    adaptive.command(config.gates[0], True, start)
    states[LOW] = True
    adaptive.observe(config, states, {LEVER: True}, start + timedelta(seconds=1))
    observation = adaptive.observe(config, states, {LEVER: True}, start + timedelta(seconds=10))
    assert observation.cutoffs == []
    assert observation.next_deadline is None


def test_missing_d65_does_not_release_an_existing_hold() -> None:
    config = configuration(enabled=True, rate=10)
    adaptive = AdaptiveFlowController()
    adaptive.reset({"input"})
    observation = adaptive.observe(config, {HIGH: False}, {LEVER: False}, datetime.now(UTC))
    assert len(observation.cutoffs) == 1
    assert adaptive.statuses(config)[0].held_off
    observation = adaptive.observe(config, {LOW: False}, {LEVER: False}, datetime.now(UTC))
    assert observation.cutoffs == []
    assert not adaptive.statuses(config)[0].held_off


def test_auto_deleted_d85_does_not_release_existing_hold() -> None:
    config = configuration(enabled=True, rate=10)
    config.sensors = [sensor for sensor in config.sensors if sensor.adapter_name != HIGH]
    adaptive = AdaptiveFlowController()
    adaptive.reset({"input"})
    observation = adaptive.observe(config, {LOW: True}, {LEVER: False}, datetime.now(UTC))
    assert len(observation.cutoffs) == 1
    assert adaptive.statuses(config)[0].held_off
    adaptive.observe(config, {LOW: False}, {LEVER: False}, datetime.now(UTC))
    assert not adaptive.statuses(config)[0].held_off


class FakeGame:
    def __init__(self) -> None:
        self.states = {LOW: False, HIGH: False}
        self.levers = {LEVER: False}
        self.commands: list[tuple[str, bool]] = []

    async def list_levers(self) -> list[Lever]:
        return [Lever(name=name, state=state) for name, state in self.levers.items()]

    async def list_adapters(self) -> list[Adapter]:
        return [Adapter(name=name, state=state) for name, state in self.states.items()]

    async def switch_on(self, name: str) -> None:
        self.commands.append((name, True))
        self.levers[name] = True

    async def switch_off(self, name: str) -> None:
        self.commands.append((name, False))
        self.levers[name] = False

    async def close(self) -> None:
        pass


def test_service_closes_at_deadline_and_does_not_reopen_until_d65_clears(tmp_path) -> None:
    path = tmp_path / "config.json"
    ConfigStore(path).save(configuration(enabled=True, rate=100))
    controller = TimberbornController(Settings(
        config_path=path, event_log_path=tmp_path / "events.jsonl"))
    game = FakeGame()
    controller.client = game

    async def run() -> None:
        try:
            await controller.refresh_once()
            game.states[LOW] = True
            await controller.refresh_once()
            await asyncio.sleep(0.45)
            assert game.commands == [(LEVER, True), (LEVER, False)]
            assert any(event.kind == "adaptive.warning" for event in controller.events)
            assert any(event.kind == "adaptive.closed" for event in controller.events)
            assert ConfigStore(controller.store.path).load().adaptive_flow_held_gate_ids == {"input"}
            await controller.refresh_once()
            assert game.commands == [(LEVER, True), (LEVER, False)]
            game.states[LOW] = False
            await controller.refresh_once()
            assert game.commands[-1] == (LEVER, True)
        finally:
            await controller.stop()

    asyncio.run(run())


def test_adaptive_hold_survives_restart(tmp_path) -> None:
    path = tmp_path / "config.json"
    config = configuration(enabled=True, rate=10)
    config.adaptive_flow_held_gate_ids = {"input"}
    ConfigStore(path).save(config)
    controller = TimberbornController(Settings(
        config_path=path, event_log_path=tmp_path / "events.jsonl"))
    game = FakeGame()
    game.states[LOW] = True
    controller.client = game

    async def run() -> None:
        try:
            await controller.refresh_once()
            assert game.commands == []
            game.states[LOW] = False
            await controller.refresh_once()
            assert game.commands == [(LEVER, True)]
            assert ConfigStore(controller.store.path).load().adaptive_flow_held_gate_ids == set()
        finally:
            await controller.stop()

    asyncio.run(run())


def test_flush_open_rule_overrides_adaptive_cutoff(tmp_path) -> None:
    path = tmp_path / "config.json"
    config = configuration(enabled=True)
    config.adaptive_flow_held_gate_ids = {"input"}
    config.rules.insert(0, Rule(id="flush-open-input", when_adapters={"HTTP Flush": True},
                                action="switch_on", lever=LEVER))
    ConfigStore(path).save(config)
    controller = TimberbornController(Settings(
        config_path=path, event_log_path=tmp_path / "events.jsonl"))
    game = FakeGame()
    game.states.update({LOW: True, HIGH: False, "HTTP Flush": True})
    controller.client = game

    async def run() -> None:
        try:
            snapshot = await controller.refresh_once()
            assert game.commands == [(LEVER, True)]
            assert snapshot.decisions[0].rule == "flush-open-input"
            game.states["HTTP Flush"] = False
            snapshot = await controller.refresh_once()
            assert game.commands[-1] == (LEVER, False)
            assert snapshot.decisions[0].rule == "adaptive-flow-cutoff"
        finally:
            await controller.stop()

    asyncio.run(run())


def test_service_persists_learned_rate_with_beta_off(tmp_path) -> None:
    path = tmp_path / "config.json"
    ConfigStore(path).save(configuration())
    controller = TimberbornController(Settings(
        config_path=path, event_log_path=tmp_path / "events.jsonl"))
    game = FakeGame()
    controller.client = game

    async def run() -> None:
        try:
            await controller.refresh_once()
            game.states[LOW] = True
            await controller.refresh_once()
            await asyncio.sleep(0.02)
            game.states[HIGH] = True
            await controller.refresh_once()
            estimate = ConfigStore(controller.store.path).load().adaptive_flow_estimates["input"]
            assert estimate.fill_percent_per_second > 0
            assert any(event.kind == "adaptive.rate_updated" for event in controller.events)
            assert game.commands == [(LEVER, True), (LEVER, False)]
        finally:
            await controller.stop()

    asyncio.run(run())


def test_target_must_be_above_d85() -> None:
    with pytest.raises(ValueError, match="adaptive_target_percent"):
        Configuration(adaptive_target_percent=85)


def test_config_save_preserves_service_owned_estimate(tmp_path) -> None:
    path = tmp_path / "config.json"
    ConfigStore(path).save(configuration(enabled=True, rate=10))
    controller = TimberbornController(Settings(
        config_path=path, event_log_path=tmp_path / "events.jsonl"))
    game = FakeGame()
    controller.client = game

    async def run() -> None:
        try:
            incoming = controller.configuration.model_copy(deep=True)
            incoming.adaptive_target_percent = 96
            incoming.adaptive_flow_estimates["input"].fill_percent_per_second = 1
            await controller.replace_configuration(incoming)
            assert controller.configuration.adaptive_flow_estimates["input"].fill_percent_per_second == 10
            assert ConfigStore(controller.store.path).load().adaptive_target_percent == 96
        finally:
            await controller.stop()

    asyncio.run(run())
