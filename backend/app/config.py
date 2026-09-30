"""Central settings, read from the repo-root .env regardless of the working directory."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    # --- broker / database
    mqtt_broker_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_user: str = ""
    mqtt_password: str = ""
    db_url: str = "postgresql+psycopg://solar:solar@localhost:5432/solar"

    # --- decision engine
    baseline_ratio: float = 1.0
    eval_interval_s: int = 10
    settle_s: int = 20
    rain_threshold_pct: int = 60
    clean_duration_s: int = 20
    stale_cycle_s: int = 300  # a cycle stuck this long is closed as "insufficient"
    device_timeout_s: int = 30  # no sensor message for this long -> device unhealthy

    # --- cost / benefit (informational: shown on the dashboard, does not change thresholds)
    farm_rated_kw: float = 1.0
    peak_sun_hours: float = 5.0
    tariff_inr_per_kwh: float = 8.0
    cleaning_cost_inr: float = 10.0  # water + pump energy per cycle
    benefit_horizon_days: float = 1.0

    # --- weather
    weather_lat: float = 0.0
    weather_lon: float = 0.0
    weather_cache_s: int = 1800

    # --- vision
    min_conf: float = 0.6
    max_upload_mb: int = 5
    allow_stub_decisions: bool = False  # stub-model images never drive decisions unless set
    model_path: Path = ROOT_DIR / "ml" / "models" / "soiling_mnv3.pt"
    severity_map_path: Path = ROOT_DIR / "ml" / "models" / "severity_map.json"
    images_dir: Path = BACKEND_DIR / "storage" / "images"

    # --- security / alerts
    api_key: str = ""  # X-API-Key for /trigger-clean; empty = endpoint refuses (fails closed)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # --- runtime
    enable_background: bool = True  # MQTT + scheduler; tests switch this off
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:8080"]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


settings = Settings()
