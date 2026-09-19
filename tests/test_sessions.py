import asyncio

import pytest
from timberborn_control.config import Settings
from timberborn_control.models import Configuration, ServiceEvent, Tank
from timberborn_control.service import TimberbornController
from timberborn_control.storage import ConfigStore, EventStore, SessionStore


def test_legacy_data_moves_to_beaverton(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    event_path = tmp_path / "events.jsonl"
    configuration = Configuration(tanks=[Tank(id="main", name="Main Tank")])
    ConfigStore(config_path).save(configuration)
    EventStore(event_path).append(ServiceEvent(kind="old.event", message="Before sessions"))

    sessions = SessionStore(config_path, event_path)
    migrated_config, migrated_events = sessions.paths("beaverton")

    assert sessions.current().name == "Beaverton"
    assert ConfigStore(migrated_config).load() == configuration
    assert EventStore(migrated_events).recent()[0].kind == "old.event"
    assert not config_path.exists()
    assert not event_path.exists()
    assert SessionStore(config_path, event_path).index == sessions.index


def test_create_and_load_isolate_configuration_and_events(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    ConfigStore(config_path).save(Configuration(tanks=[Tank(id="main", name="Main Tank")]))
    settings = Settings(config_path=config_path, event_log_path=tmp_path / "events.jsonl")
    controller = TimberbornController(settings)

    class EmptyGame:
        async def list_levers(self):
            return []

        async def list_adapters(self):
            return []

        async def close(self):
            pass

    controller.client = EmptyGame()

    async def run() -> None:
        created = await controller.create_session("Second Town")
        second_id = created.active_session_id
        assert created.session_name == "Second Town"
        assert created.tanks == []
        assert [session.name for session in created.sessions] == ["Beaverton", "Second Town"]
        assert any(event.kind == "session.created" for event in created.events)

        await controller.replace_configuration(Configuration(
            tanks=[Tank(id="second", name="Second Tank")],
        ))
        beaverton = await controller.load_session("beaverton")
        assert [tank.id for tank in beaverton.tanks] == ["main"]
        assert not any(event.kind == "session.created" for event in beaverton.events)

        with pytest.raises(ValueError, match="already exists"):
            await controller.create_session("second town")
        with pytest.raises(KeyError):
            await controller.load_session("unknown")
        assert controller.snapshot().session_name == "Beaverton"

        second = await controller.load_session(second_id)
        assert [tank.id for tank in second.tanks] == ["second"]
        assert controller.store.path == controller.session_store.paths(second_id)[0]
        await controller.stop()

    asyncio.run(run())

    restarted = TimberbornController(settings)
    assert restarted.snapshot().session_name == "Second Town"
    assert [tank.id for tank in restarted.configuration.tanks] == ["second"]
    asyncio.run(restarted.stop())


def test_missing_session_configuration_is_not_treated_as_empty(tmp_path) -> None:
    sessions = SessionStore(tmp_path / "config.json", tmp_path / "events.jsonl")
    config_path, _ = sessions.paths("beaverton")
    config_path.unlink()

    with pytest.raises(FileNotFoundError, match="no configuration file"):
        sessions.paths("beaverton")
