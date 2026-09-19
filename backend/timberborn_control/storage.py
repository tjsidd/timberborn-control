import os
from collections import deque
from pathlib import Path
from uuid import uuid4

from timberborn_control.models import Configuration, ServiceEvent, SessionIndex, SessionInfo


class ConfigStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> Configuration:
        if not self.path.exists():
            return Configuration()
        return Configuration.model_validate_json(self.path.read_text(encoding="utf-8"))

    def save(self, configuration: Configuration) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            temporary.write_text(configuration.model_dump_json(indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


class EventStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def recent(self, limit: int = 100) -> list[ServiceEvent]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as handle:
            return [ServiceEvent.model_validate_json(line) for line in deque(handle, maxlen=limit)]

    def append(self, event: ServiceEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json() + "\n")


class SessionStore:
    def __init__(self, legacy_config_path: Path, legacy_event_path: Path) -> None:
        self.root = legacy_config_path.parent / "sessions"
        self.index_path = legacy_config_path.parent / "sessions.json"
        if not self.index_path.exists():
            self._migrate_legacy(legacy_config_path, legacy_event_path)
        self.index = SessionIndex.model_validate_json(self.index_path.read_text(encoding="utf-8"))

    def _migrate_legacy(self, config_path: Path, event_path: Path) -> None:
        directory = self.root / "beaverton"
        directory.mkdir(parents=True, exist_ok=True)
        target_config = directory / "config.json"
        target_events = directory / "events.jsonl"
        if config_path.exists() and not target_config.exists():
            os.replace(config_path, target_config)
        if event_path.exists() and not target_events.exists():
            os.replace(event_path, target_events)
        if not target_config.exists():
            ConfigStore(target_config).save(Configuration())
        self._save_index(SessionIndex(
            active_id="beaverton", sessions=[SessionInfo(id="beaverton", name="Beaverton")],
        ))

    def _save_index(self, index: SessionIndex) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.index_path.with_name(self.index_path.name + ".tmp")
        try:
            temporary.write_text(index.model_dump_json(indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, self.index_path)
        finally:
            temporary.unlink(missing_ok=True)

    def paths(self, session_id: str) -> tuple[Path, Path]:
        if not any(session.id == session_id for session in self.index.sessions):
            raise KeyError(session_id)
        directory = self.root / session_id
        config_path = directory / "config.json"
        if not config_path.exists():
            raise FileNotFoundError(f"Session '{session_id}' has no configuration file: {config_path}")
        return config_path, directory / "events.jsonl"

    def current(self) -> SessionInfo:
        return next(session for session in self.index.sessions
                    if session.id == self.index.active_id)

    def create(self, name: str) -> SessionInfo:
        name = name.strip()
        if not name or len(name) > 64:
            raise ValueError("Session name must be between 1 and 64 characters.")
        if any(session.name.casefold() == name.casefold() for session in self.index.sessions):
            raise ValueError(f"Session '{name}' already exists.")
        session = SessionInfo(id=uuid4().hex, name=name)
        ConfigStore(self.root / session.id / "config.json").save(Configuration())
        self._save_index(self.index.model_copy(update={"sessions": [*self.index.sessions, session]}))
        self.index = SessionIndex.model_validate_json(self.index_path.read_text(encoding="utf-8"))
        return session

    def activate(self, session_id: str) -> None:
        self.paths(session_id)
        updated = self.index.model_copy(update={"active_id": session_id})
        self._save_index(updated)
        self.index = updated
