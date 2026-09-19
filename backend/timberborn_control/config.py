from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TIMBERBORN_", env_file=".env", extra="ignore")

    game_base_url: str = Field(
        default="http://localhost:8080",
        description="Base URL for Timberborn's local automation API.",
    )
    poll_interval_seconds: float = Field(default=2.0, ge=0.25)
    request_timeout_seconds: float = Field(default=2.0, ge=0.25)
    cors_origin: str = Field(default="http://localhost:5173")
    config_path: Path = Field(default=Path("data/config.json"))
    event_log_path: Path = Field(default=Path("data/events.jsonl"))
