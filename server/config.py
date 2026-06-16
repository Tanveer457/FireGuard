"""
config.py — Central settings loaded from .env
All modules import `settings` from here. Never read os.environ directly.
"""
from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://fire:fire_secret_db@localhost:5432/fire_detection"

    # Redis
    redis_url: str = "redis://:fire_secret_redis@localhost:6379/0"

    # Auth
    edge_token: str = "fire-secret-token"
    dashboard_token: str = "dashboard-secret-token"

    # Storage
    storage_root: str = "./storage"
    snapshots_dir: str = "./storage/snapshots"
    clips_dir: str = "./storage/clips"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"

    # Alert settings
    alert_retention_days: int = 30
    max_snapshot_size_mb: int = 5

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

    def create_storage_dirs(self):
        """Create all storage directories on startup."""
        for path in [self.storage_root, self.snapshots_dir, self.clips_dir]:
            Path(path).mkdir(parents=True, exist_ok=True)


settings = Settings()