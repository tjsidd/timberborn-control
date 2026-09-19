import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from timberborn_control.config import Settings
from timberborn_control.models import Adapter, Configuration, Lever, Rule, Sensor
from timberborn_control.service import TimberbornController
from timberborn_control.storage import ConfigStore


class FakeGameClient:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.adapters = [Adapter(name="High water", state=True)]
        self.fail = False

    async def list_levers(self) -> list[Lever]:
        return [Lever(name="Outlet", state=False)]

    async def list_adapters(self) -> list[Adapter]:
        if self.fail:
            raise httpx.ConnectError("game unavailable")
        return self.adapters

    async def switch_on(self, name: str) -> None:
        self.commands.append(name)

    async def switch_off(self, name: str) -> None:
        self.commands.append(name)

    async def close(self) -> None:
        pass


def test_webhook_refreshes_and_applies_rule(tmp_path) -> None:
    path = tmp_path / "config.json"
    ConfigStore(path).save(Configuration(rules=[Rule(
        id="release", when_adapters={"High water": True}, action="switch_on", lever="Outlet",
    )]))
    controller = TimberbornController(Settings(config_path=path, event_log_path=tmp_path / "events.jsonl"))
    fake = FakeGameClient()
    controller.client = fake

    async def run() -> None:
        snapshot = await controller.adapter_webhook("High water", True)
        assert snapshot.connection.ok
        assert snapshot.last_rule_evaluations[0].matched
        assert fake.commands == ["Outlet"]
        await controller.stop()

    asyncio.run(run())
    restarted = TimberbornController(Settings(config_path=path, event_log_path=tmp_path / "events.jsonl"))
    command = next(event for event in restarted.events if event.kind == "automation.commanded")
    assert "High water=true" in command.message
    assert "set Outlet to on" in command.message
    assert command.decision is not None
    assert command.decision.inputs == {"High water": True}
    asyncio.run(restarted.stop())


def test_unconfirmed_lever_command_is_throttled_and_reported(tmp_path) -> None:
    path = tmp_path / "config.json"
    ConfigStore(path).save(Configuration(rules=[Rule(
        id="release", when_adapters={"High water": True}, action="switch_on", lever="Outlet",
    )]))
    controller = TimberbornController(Settings(config_path=path, event_log_path=tmp_path / "events.jsonl"))
    fake = FakeGameClient()
    controller.client = fake

    async def run() -> None:
        first = await controller.refresh_once()
        assert fake.commands == ["Outlet"]
        assert first.levers[0].state is False
        await controller.refresh_once()
        assert fake.commands == ["Outlet"]

        controller._pending_commands["Outlet"] = (True, datetime.now(UTC) - timedelta(seconds=10))
        waiting = await controller.refresh_once()
        assert "game still reports 'Outlet' off" in waiting.automation_error
        assert fake.commands == ["Outlet"]

        controller._pending_commands["Outlet"] = (True, datetime.now(UTC) - timedelta(seconds=31))
        await controller.refresh_once()
        assert fake.commands == ["Outlet", "Outlet"]
        await controller.stop()

    asyncio.run(run())


def test_discovered_sensor_persists_and_missing_state_is_reported(tmp_path) -> None:
    path = tmp_path / "config.json"
    controller = TimberbornController(Settings(config_path=path, event_log_path=tmp_path / "events.jsonl"))
    fake = FakeGameClient()
    controller.client = fake

    async def run() -> None:
        first = await controller.refresh_once()
        assert first.sensors[0].adapter_name == "High water"
        assert first.sensors[0].type is None
        assert first.sensors[0].state is True
        assert first.sensors[0].last_state is True
        assert first.sensors[0].last_seen_at is not None
        assert controller.store.load().sensors[0].adapter_name == "High water"
        stale_configuration = controller.configuration.model_copy(deep=True)

        with pytest.raises(ValueError, match="still reported"):
            await controller.delete_sensor("High water")

        fake.adapters = []
        missing = await controller.refresh_once()
        assert missing.connection.ok
        assert missing.sensors[0].state is None
        assert "No state received" in missing.sensors[0].error
        assert missing.sensors[0].last_seen_at == first.sensors[0].last_seen_at
        controller.connection.ok = False
        with pytest.raises(ValueError, match="unavailable"):
            await controller.delete_sensor("High water")
        await controller.refresh_once()
        deleted = await controller.delete_sensor("High water")
        assert deleted.sensors == []
        assert controller.store.load().sensors == []
        assert any(event.kind == "sensor.deleted" for event in deleted.events)

        await controller.replace_configuration(stale_configuration)
        assert controller.snapshot().sensors == []
        assert controller.store.load().sensors == []
        await controller.stop()

    asyncio.run(run())
    restarted = TimberbornController(Settings(config_path=path, event_log_path=tmp_path / "events.jsonl"))
    assert restarted.configuration.sensors == []
    asyncio.run(restarted.stop())


def test_last_known_sensor_state_survives_missing_data_and_restart(tmp_path) -> None:
    path = tmp_path / "config.json"
    settings = Settings(config_path=path, event_log_path=tmp_path / "events.jsonl")
    controller = TimberbornController(settings)
    fake = FakeGameClient()
    controller.client = fake

    async def run() -> None:
        await controller.refresh_once()
        fake.adapters = [Adapter(name="High water", state=False)]
        latest = await controller.refresh_once()
        assert latest.sensors[0].state is False
        assert latest.sensors[0].last_state is False
        assert controller.store.load().sensors[0].last_state is False

        fake.adapters = []
        missing = await controller.refresh_once()
        assert missing.sensors[0].state is None
        assert missing.sensors[0].last_state is False
        assert missing.sensors[0].last_seen_at == latest.sensors[0].last_seen_at
        await controller.stop()

    asyncio.run(run())
    restarted = TimberbornController(settings)
    assert restarted.configuration.sensors[0].last_state is False
    asyncio.run(restarted.stop())


def test_auto_delete_missing_sensors_is_persisted_and_logs_removals(tmp_path) -> None:
    path = tmp_path / "config.json"
    ConfigStore(path).save(Configuration(sensors=[
        Sensor(adapter_name="High water"), Sensor(adapter_name="Removed adapter"),
    ]))
    controller = TimberbornController(Settings(config_path=path, event_log_path=tmp_path / "events.jsonl"))
    fake = FakeGameClient()
    controller.client = fake

    async def run() -> None:
        initial = await controller.refresh_once()
        assert len(initial.sensors) == 2

        enabled = await controller.set_auto_delete_missing_sensors(True)
        assert enabled.auto_delete_missing_sensors is True
        assert [sensor.adapter_name for sensor in enabled.sensors] == ["High water"]
        assert controller.store.load().auto_delete_missing_sensors is True
        assert any(event.kind == "sensor.auto_deleted" and
                   event.detail["adapter"] == "Removed adapter" for event in enabled.events)

        fake.adapters = []
        empty = await controller.refresh_once()
        assert empty.sensors == []
        assert controller.store.load().sensors == []
        await controller.stop()

    asyncio.run(run())
    restarted = TimberbornController(Settings(config_path=path, event_log_path=tmp_path / "events.jsonl"))
    assert restarted.configuration.auto_delete_missing_sensors is True
    assert restarted.configuration.sensors == []
    asyncio.run(restarted.stop())


def test_auto_delete_does_not_run_when_game_is_unavailable(tmp_path) -> None:
    path = tmp_path / "config.json"
    ConfigStore(path).save(Configuration(
        auto_delete_missing_sensors=True,
        sensors=[Sensor(adapter_name="Previously seen")],
    ))
    controller = TimberbornController(Settings(config_path=path, event_log_path=tmp_path / "events.jsonl"))
    fake = FakeGameClient()
    fake.fail = True
    controller.client = fake

    async def run() -> None:
        snapshot = await controller.refresh_once()
        assert snapshot.connection.ok is False
        assert [sensor.adapter_name for sensor in snapshot.sensors] == ["Previously seen"]
        assert [sensor.adapter_name for sensor in controller.store.load().sensors] == ["Previously seen"]
        assert not any(event.kind == "sensor.auto_deleted" for event in snapshot.events)
        await controller.stop()

    asyncio.run(run())
