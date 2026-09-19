import asyncio

import pytest
from timberborn_control.config import Settings
from timberborn_control.models import Adapter, Configuration, Gate, Lever, Rule
from timberborn_control.service import TimberbornController
from timberborn_control.storage import ConfigStore


class FakeGame:
    def __init__(self, states: dict[str, bool], levers: dict[str, bool]) -> None:
        self.states = states
        self.levers = levers
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


def controller_for(tmp_path, rules: list[Rule], states: dict[str, bool],
                   levers: dict[str, bool]) -> tuple[TimberbornController, FakeGame]:
    path = tmp_path / "config.json"
    ConfigStore(path).save(Configuration(
        gates=[Gate(id="gate", name="Gate", type="2m", lever_name="HTTP Gate")],
        rules=rules,
    ))
    controller = TimberbornController(Settings(
        config_path=path, event_log_path=tmp_path / "events.jsonl",
    ))
    game = FakeGame(states, levers)
    controller.client = game
    return controller, game


def rule(id: str, conditions: dict[str, bool], action: str, lever: str,
         operator: str = "all") -> Rule:
    return Rule(id=id, when_adapters=conditions, action=action,
                lever=lever, operator=operator)


def test_ordered_rules_are_only_actuator_and_trace_inputs(tmp_path) -> None:
    controller, game = controller_for(tmp_path, [
        rule("first", {"High": True}, "switch_on", "HTTP Gate"),
        rule("second", {"High": True}, "switch_off", "HTTP Gate"),
    ], {"High": True}, {"HTTP Gate": False})

    async def run() -> None:
        snapshot = await controller.refresh_once()
        assert game.commands == [("HTTP Gate", True)]
        assert [decision.rule for decision in snapshot.decisions] == ["first"]
        assert snapshot.decisions[0].inputs == {"High": True}
        assert snapshot.last_rule_evaluations[1].reason == "shadowed by rule first"
        assert snapshot.events[-1].decision.rule == "first"
        with pytest.raises(ValueError, match="controlled by an enabled rule"):
            await controller.switch_lever("HTTP Gate", False)
        await controller.stop()

    asyncio.run(run())


def test_missing_sensor_holds_lever(tmp_path) -> None:
    controller, game = controller_for(tmp_path, [
        rule("requires-signal", {"Missing": True}, "switch_on", "HTTP Gate"),
    ], {}, {"HTTP Gate": False})

    async def run() -> None:
        snapshot = await controller.refresh_once()
        assert game.commands == []
        assert snapshot.decisions == []
        assert snapshot.last_rule_evaluations[0].skipped
        await controller.stop()

    asyncio.run(run())


@pytest.mark.parametrize("contamination", ["N C", "NW C"])
def test_w_second_intake_is_forced_off_by_either_contamination(tmp_path,
                                                               contamination) -> None:
    controller, game = controller_for(tmp_path, [
        rule("w-in-2-off", {"W D65": True, "N C": True, "NW C": True},
             "switch_off", "W In 2", "any"),
        rule("w-in-2-on", {"W D65": False, "N C": False, "NW C": False},
             "switch_on", "W In 2"),
    ], {"W D65": False, "N C": contamination == "N C",
        "NW C": contamination == "NW C"}, {"W In 2": True})

    async def run() -> None:
        snapshot = await controller.refresh_once()
        assert game.commands == [("W In 2", False)]
        assert snapshot.decisions[0].rule == "w-in-2-off"
        await controller.stop()

    asyncio.run(run())


def test_n_main_high_opens_nw_diversion(tmp_path) -> None:
    controller, game = controller_for(tmp_path, [
        rule("nw-main-diversion-on", {"N D75": True, "N C": True, "NW C": True},
             "switch_on", "NW Diversion", "any"),
    ], {"N D75": True, "N C": False, "NW C": False}, {"NW Diversion": False})

    async def run() -> None:
        await controller.refresh_once()
        assert game.commands == [("NW Diversion", True)]
        await controller.stop()

    asyncio.run(run())
