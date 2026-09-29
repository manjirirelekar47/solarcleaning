"""Application settings, loaded from the repo-root .env (works from any working directory)."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    mqtt_broker_host: str = "localhost"
    mqtt_port: int = 1883
    db_url: str = "postgresql+psycopg://solar:solar@localhost:5432/solar"
    weather_lat: float = 0.0
    weather_lon: float = 0.0
    baseline_ratio: float = 1.0
    eval_interval_s: int = 10
    settle_s: int = 20
    rain_threshold_pct: int = 60
    clean_duration_s: int = 20
    cycle_timeout_s: int = 300  # stale-cycle guard (backend restarts, dead edge device)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    cors_origins: str = "http://localhost:5173"
    image_dir: Path = BACKEND_DIR / "storage" / "images"
    model_path: Path = ROOT_DIR / "ml" / "artifacts" / "soiling_mnv3.pt"
    classes_path: Path = ROOT_DIR / "ml" / "artifacts" / "classes.json"


settings = Settings()
