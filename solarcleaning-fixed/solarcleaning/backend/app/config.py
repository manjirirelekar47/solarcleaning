"""Application settings, loaded from the repo-root .env (works from any working directory)."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    mqtt_broker_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_user: str = (
        ""  # empty = anonymous dev broker; set both with infra/docker-compose.secure.yml
    )
    mqtt_password: str = ""
    db_url: str = "postgresql+psycopg://solar:solar@localhost:5432/solar"
    weather_lat: float = 0.0
    weather_lon: float = 0.0
    baseline_ratio: float = 1.0
    eval_interval_s: int = 10
    settle_s: int = 20
    rain_threshold_pct: int = 60
    clean_duration_s: int = 20
    cycle_timeout_s: int = 300  # stale-cycle guard (backend restarts, dead edge device)
    # --- A1: cleaning limits ---
    min_clean_interval_s: int = 600  # cooldown between auto-cleans (measured from trigger time)
    max_cleans_per_day: int = 6  # rolling 24 h cap on cleaning cycles
    # --- A3: device health and sensor sanity ---
    offline_after_s: int = 30  # no reading from a source for this long -> offline
    max_panel_v: float = 26.0  # INA219 bus-voltage ceiling; above this the reading is bogus
    min_panel_current_a: float = -0.05  # below this (negative) the reading is bogus
    dead_test_ratio: float = 0.02  # test power below this fraction of reference = dead sensor
    # --- A4/A5: debounce and loss estimate ---
    confirm_n: int = 3  # consecutive evaluations above threshold before auto-clean
    hysteresis_pct: float = 1.0  # A4: level drops only after loss falls this far below a threshold
    min_ref_power_w: float = 0.05  # below this the reference gives no usable light signal
    temp_coeff: float = 0.0  # power temperature coefficient per deg C (e.g. -0.004); 0 = off
    pair_tolerance_s: float = 3.0  # A5: max time gap when pairing a test sample to a reference one
    # --- A8: cleaning lifecycle ---
    retry_window_s: int = (
        3600  # an insufficient clean is retried once if the next clean is inside this
    )
    # --- A12: time-of-day baseline (written by tools/calibrate.py --hourly) ---
    baseline_hourly_path: Path = ROOT_DIR / "ml" / "artifacts" / "baseline_hourly.json"
    timezone: str = "Asia/Kolkata"  # hour buckets are local solar time
    # --- A6: fusion gating ---
    severity_loss_factor: float = 0.3  # raw severity index (0-100) -> expected loss %, until B4
    gate_electrical_pct: float = 10.0  # auto-clean needs measured electrical loss at least this
    # --- A7: cost-benefit (demo values; scale to the real plant for the slide) ---
    tariff_inr_per_kwh: float = 8.0
    plant_kwp: float = 1000.0
    peak_sun_hours: float = 5.0
    water_cost_per_clean: float = 50.0
    pump_w: float = 30.0
    labor_cost_per_clean: float = 0.0
    dry_days_horizon: float = 3.0  # days the benefit of a clean is counted when no rain is due
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    cors_origins: str = "http://localhost:5173"
    image_dir: Path = BACKEND_DIR / "storage" / "images"
    # --- B: vision, uploads, weather cache, API key
    model_path: Path = ROOT_DIR / "ml" / "models" / "soiling_mnv3.pt"
    severity_map_path: Path = ROOT_DIR / "ml" / "models" / "severity_map.json"
    min_conf: float = 0.6  # CNN results below this confidence are stored but not used
    max_upload_mb: int = 5
    allow_stub_decisions: bool = False  # stub-model images never drive decisions unless set
    weather_cache_s: int = 1800
    api_key: str = ""  # X-API-Key for POST /trigger-clean; empty = endpoint refuses (fails closed)

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


settings = Settings()
