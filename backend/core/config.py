from functools import lru_cache
from typing import Any

from pydantic import computed_field
from pydantic.dataclasses import dataclass
from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class PhaseDurations:
    lobby: int = 0
    day: int = 120
    voting: int = 60
    night: int = 45
    night_wolf: int = 25
    night_seer: int = 20


class Settings(BaseSettings):
    # ── App ──────────────────────────────────────
    app_env: str = "development"
    log_level: str = "info"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    debug: bool = False
    secret_key: str = "changeme-in-production"

    # ── Redis ────────────────────────────────────
    redis_url: str = "redis://localhost:6379"
    redis_password: str = ""

    # Prefisso canali Pub/Sub — da allineare con Membro 1
    # Formato atteso: {channel_prefix}:{room_id}
    # Esempio: "game:room_42"
    redis_channel_prefix: str = "game"

    # Canale broadcast globale (eventi di sistema a tutti i client)
    redis_global_channel: str = "game:global"

    # ── WebSocket ────────────────────────────────
    ws_heartbeat_interval: int = 30  # secondi tra ping al client
    ws_max_size: int = 1_048_576  # 1MB max dimensione messaggio
    socketio_path: str = "/socket.io"

    # ── CORS ─────────────────────────────────────
    cors_origins: list[str] = ["http://localhost:5173"]

    # ── Durate fasi ──────────────────────────────
    phase_lobby: int = 0
    phase_day: int = 120
    phase_voting: int = 60
    phase_night: int = 45
    phase_night_wolf: int = 25
    phase_night_seer: int = 20

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @computed_field
    @property
    def phase_durations(self) -> PhaseDurations:
        return PhaseDurations(
            lobby=self.phase_lobby,
            day=self.phase_day,
            voting=self.phase_voting,
            night=self.phase_night,
            night_wolf=self.phase_night_wolf,
            night_seer=self.phase_night_seer,
        )

    def redis_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"url": self.redis_url}
        if self.redis_password:
            kwargs["password"] = self.redis_password
        return kwargs


@lru_cache
def get_settings() -> Settings:
    return Settings()
