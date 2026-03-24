"""
settings.py — Centralized configuration via environment variables.

Usage:
    from settings import settings
    print(settings.redis_url)

"""

import os
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Phase durations (seconds) — overridable via env for tests
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PhaseDurations:
    lobby:       int = 0    # Lobby has no timer; waits for manual start
    day:         int = 120
    voting:      int = 60
    night:       int = 45   # total; internally divided by night_subtimer
    night_wolf:  int = 25   # wolf sub-phase
    night_seer:  int = 20   # seer sub-phase


@dataclass(frozen=True)
class Settings:
    # --- Redis ---
    redis_url:      str  = field(default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379"))
    redis_password: str  = field(default_factory=lambda: os.getenv("REDIS_PASSWORD", ""))

    # --- App ---
    app_host:    str  = field(default_factory=lambda: os.getenv("APP_HOST", "0.0.0.0"))
    app_port:    int  = field(default_factory=lambda: int(os.getenv("APP_PORT", "8000")))
    debug:       bool = field(default_factory=lambda: os.getenv("DEBUG", "false").lower() == "true")
    secret_key:  str  = field(default_factory=lambda: os.getenv("SECRET_KEY", "changeme-in-production"))

    # --- CORS ---
    cors_origins: list[str] = field(
        default_factory=lambda: os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
    )

    # --- Phase durations ---
    phase_durations: PhaseDurations = field(default_factory=PhaseDurations)

    # --- Logging ---
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))

    # --- Socket.IO ---
    socketio_path: str = field(default_factory=lambda: os.getenv("SOCKETIO_PATH", "/socket.io"))

    def redis_kwargs(self) -> dict:
        """Returns kwargs for aioredis / redis-py connection."""
        kwargs: dict = {"url": self.redis_url}
        if self.redis_password:
            kwargs["password"] = self.redis_password
        return kwargs


# Singleton instance importable by all modules
settings = Settings()