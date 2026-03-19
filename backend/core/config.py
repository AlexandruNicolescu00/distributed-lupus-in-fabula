from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # ── App ──────────────────────────────────────
    app_env: str = "development"
    log_level: str = "info"

    # ── Redis ────────────────────────────────────
    redis_url: str = "redis://localhost:6379"

    # Prefisso canali Pub/Sub — da allineare con Membro 1
    # Formato atteso: {channel_prefix}:{room_id}
    # Esempio: "game:room_42"
    redis_channel_prefix: str = "game"

    # Canale broadcast globale (eventi di sistema a tutti i client)
    redis_global_channel: str = "game:global"

    # ── WebSocket ────────────────────────────────
    ws_heartbeat_interval: int = 30  # secondi tra ping al client
    ws_max_size: int = 1_048_576  # 1MB max dimensione messaggio

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
