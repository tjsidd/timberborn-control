import pytest
from pydantic import ValidationError
from timberborn_control.models import Configuration, Gate, Sensor, Tank
from timberborn_control.storage import ConfigStore


def test_gate_type_is_validated() -> None:
    with pytest.raises(ValidationError, match="type"):
        Gate(id="g", name="Spillway", type="4m")


def test_legacy_gate_heights_preserve_lever_polarity() -> None:
    gate = Gate.model_validate({"id": "g", "name": "Spillway", "type": "1m",
                                "off_height_m": 0, "on_height_m": 1})
    assert gate.open_when_on is False
    assert "off_height_m" not in gate.model_dump()
    assert "on_height_m" not in gate.model_dump()


def test_configuration_round_trip(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    configuration = Configuration(
        tanks=[Tank(id="t", name="North reservoir", direction="N")],
        graph_positions={"t": {"x": 125.5, "y": -80}},
        gates=[Gate(id="g", name="Outlet", type="2m", open_when_on=False,
                    tank_id="t", lever_name="Outlet lever")],
        sensors=[Sensor(adapter_name="Depth high", type="Depth", tank_id="t")],
    )

    store.save(configuration)

    assert store.load() == configuration
    assert store.load().tanks[0].priority is None
    assert store.load().tanks[0].direction == "N"
    assert store.load().graph_positions["t"].x == 125.5
    assert not (tmp_path / "config.json.tmp").exists()


def test_configuration_rejects_missing_tank() -> None:
    with pytest.raises(ValidationError, match="unknown tank"):
        Configuration(sensors=[Sensor(adapter_name="Depth", type="Depth", tank_id="missing")])


def test_tank_rejects_unknown_direction() -> None:
    with pytest.raises(ValidationError, match="direction"):
        Tank(id="t", name="Metalia", direction="upstream")


def test_tank_can_be_placed_relative_to_another_tank(tmp_path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    configuration = Configuration(
        tanks=[Tank(id="main", name="Main"),
               Tank(id="metalia", name="Metalia", direction="E"),
               Tank(id="south", name="Metalia outlet", direction="S", parent_tank_id="metalia")],
        gates=[Gate(id="in", name="S Metalia Output", type="2m", tank_id="metalia",
                    destination_tank_id="south"),
               Gate(id="out", name="S Metalia Output 2", type="2m", tank_id="south")],
    )
    store.save(configuration)
    assert store.load() == configuration


def test_tank_placement_rejects_unknown_parent_and_cycles() -> None:
    with pytest.raises(ValidationError, match="unknown parent tank"):
        Configuration(tanks=[Tank(id="a", name="A", direction="S", parent_tank_id="missing")])
    with pytest.raises(ValidationError, match="cyclic tank placement"):
        Configuration(tanks=[Tank(id="a", name="A", direction="S", parent_tank_id="b"),
                             Tank(id="b", name="B", direction="N", parent_tank_id="a")])
    with pytest.raises(ValidationError, match="direction required"):
        Configuration(tanks=[Tank(id="a", name="A"),
                             Tank(id="b", name="B", parent_tank_id="a")])


def test_graph_position_rejects_non_finite_coordinates() -> None:
    with pytest.raises(ValidationError, match="graph_positions"):
        Configuration(graph_positions={"tank": {"x": float("nan"), "y": 0}})
