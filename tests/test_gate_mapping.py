import asyncio

from timberborn_control.config import Settings
from timberborn_control.models import Configuration, Gate, Lever
from timberborn_control.service import TimberbornController
from timberborn_control.storage import ConfigStore


class NamedLeverGame:
    async def list_levers(self) -> list[Lever]:
        return [Lever(name="HTTP North Intake", state=False),
                Lever(name="HTTP South Output", state=False),
                Lever(name="http Unmatched", state=False)]

    async def list_adapters(self) -> list:
        return []

    async def close(self) -> None:
        pass


def test_refresh_and_configuration_save_map_exact_gate_names(tmp_path) -> None:
    path = tmp_path / "config.json"
    ConfigStore(path).save(Configuration(gates=[
        Gate(id="north", name="North Intake", type="1m", lever_name="Old Lever"),
        Gate(id="unmatched", name="Unmatched", type="1m"),
    ]))
    settings = Settings(config_path=path, event_log_path=tmp_path / "events.jsonl")
    controller = TimberbornController(settings)
    controller.client = NamedLeverGame()

    async def run() -> None:
        first = await controller.refresh_once()
        assert first.gates[0].lever_name == "HTTP North Intake"
        assert first.gates[1].lever_name is None
        assert controller.store.load().gates[0].lever_name == "HTTP North Intake"
        assert any(event.kind == "gate.lever_mapped" for event in first.events)

        await controller.replace_configuration(Configuration(gates=[
            *first.gates,
            Gate(id="south", name="South Output", type="2m"),
        ]))
        assert controller.snapshot().gates[2].lever_name == "HTTP South Output"
        assert controller.store.load().gates[2].lever_name == "HTTP South Output"
        assert len([event for event in controller.events if event.kind == "gate.lever_mapped"]) == 2
        await controller.stop()

    asyncio.run(run())

    restarted = TimberbornController(settings)
    assert [gate.lever_name for gate in restarted.configuration.gates] == [
        "HTTP North Intake", None, "HTTP South Output",
    ]
    asyncio.run(restarted.stop())
