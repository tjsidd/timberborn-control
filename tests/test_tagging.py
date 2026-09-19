import asyncio

from timberborn_control.config import Settings
from timberborn_control.models import Adapter, Configuration, Gate, Sensor, SensorType, Tank
from timberborn_control.service import TimberbornController
from timberborn_control.storage import ConfigStore
from timberborn_control.tagging import auto_tag, match_tank


def test_match_tank_uses_exact_name_or_cardinal_alias() -> None:
    tanks = [Tank(id="m", name="Metalia"), Tank(id="nm", name="Metalia N")]
    assert match_tank("N Metalia", tanks).id == "nm"
    assert match_tank("Metalia Tank", tanks).id == "m"
    assert match_tank("NW Metalia", tanks) is None


def test_auto_tag_sensors_and_gate_endpoints_without_guessing() -> None:
    configuration = Configuration(
        tanks=[
            Tank(id="main", name="Main"), Tank(id="north", name="Main N"),
            Tank(id="metalia", name="Metalia"), Tank(id="north-metalia", name="Metalia N"),
            Tank(id="west", name="W"),
        ],
        sensors=[
            Sensor(adapter_name="HTTP W D85"),
            Sensor(adapter_name="HTTP N Metalia D75"),
            Sensor(adapter_name="HTTP N Main C"),
            Sensor(adapter_name="HTTP NW Main C"),
            Sensor(adapter_name="HTTP Adapter 2"),
        ],
        gates=[
            Gate(id="west-in", name="W In", type="3m", tank_id="north"),
            Gate(id="south-main", name="S Main Output", type="2m"),
            Gate(id="north-main", name="N Main Diversion", type="2m"),
            Gate(id="northwest-main", name="NW Main Diversion", type="2m"),
            Gate(id="north-metalia", name="N Metalia Diversion", type="2m"),
            Gate(id="west-diversion", name="W Diversion", type="3m", tank_id="north"),
        ],
    )

    tagged, tags = auto_tag(configuration)

    sensors = {sensor.adapter_name: sensor for sensor in tagged.sensors}
    gates = {gate.id: gate for gate in tagged.gates}
    assert sensors["HTTP W D85"].tank_id == "west"
    assert sensors["HTTP W D85"].type == SensorType.depth
    assert sensors["HTTP N Metalia D75"].tank_id == "north-metalia"
    assert sensors["HTTP N Main C"].tank_id == "north"
    assert sensors["HTTP N Main C"].type == SensorType.badwater
    assert sensors["HTTP NW Main C"].tank_id is None
    assert sensors["HTTP Adapter 2"].tank_id is None
    assert gates["west-in"].destination_tank_id == "west"
    assert gates["south-main"].tank_id == "main"
    assert gates["north-main"].tank_id == "north"
    assert gates["northwest-main"].tank_id is None
    assert gates["north-metalia"].tank_id == "north-metalia"
    assert gates["west-diversion"].tank_id == "north"
    assert tags
    assert auto_tag(tagged)[1] == []


def test_auto_tag_preserves_manual_assignments() -> None:
    configuration = Configuration(
        tanks=[Tank(id="main", name="Main"), Tank(id="west", name="W")],
        sensors=[Sensor(adapter_name="HTTP W D65", tank_id="main", type="Resource")],
        gates=[Gate(id="gate", name="W In", type="1m", destination_tank_id="main")],
    )

    tagged, tags = auto_tag(configuration)

    assert tagged == configuration
    assert tags == []


def test_refresh_persists_discovered_sensor_and_gate_tags(tmp_path) -> None:
    path = tmp_path / "config.json"
    ConfigStore(path).save(Configuration(
        tanks=[Tank(id="main", name="Main"), Tank(id="north", name="Main N")],
        gates=[Gate(id="diversion", name="N Main Diversion", type="2m")],
    ))
    controller = TimberbornController(Settings(
        config_path=path, event_log_path=tmp_path / "events.jsonl",
    ))

    class Game:
        async def list_levers(self) -> list:
            return []

        async def list_adapters(self) -> list[Adapter]:
            return [Adapter(name="HTTP N Main D85", state=True)]

        async def close(self) -> None:
            pass

    controller.client = Game()

    async def run() -> None:
        snapshot = await controller.refresh_once()
        assert snapshot.sensors[0].tank_id == "north"
        assert snapshot.sensors[0].type == SensorType.depth
        assert snapshot.gates[0].tank_id == "north"
        assert controller.store.load().sensors[0].tank_id == "north"
        assert controller.store.load().gates[0].tank_id == "north"
        count = len([event for event in snapshot.events if event.kind == "config.auto_tagged"])
        assert count == 2
        await controller.refresh_once()
        assert len([event for event in controller.events if event.kind == "config.auto_tagged"]) == count
        await controller.stop()

    asyncio.run(run())
