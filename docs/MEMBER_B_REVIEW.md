# Member B review and merge notes

Member B wrote against a plan-level contract without seeing the repo (`app.database`, `Reading`,
`started_at`, naive timestamps, a separate `b_config`). This merge adapts B's work to what Member A
actually pushed, keeps B's design where it was better, and fixes what did not survive contact with
the real code.

## What was taken from B as-is (or nearly)
Session-aware `ml/splits.py` and `ml/train.py`; `vision.py` quality gates (dark/blank/sky/EXIF/
confidence, `used` flag, isotonic severity map); `tools/capture.py`; `tools/calibrate_severity.py`;
the whole dashboard; `X-API-Key` guard; bucketed `/loss-trend` and `/readings`; Mosquitto auth files.

## Problems found and fixed during the merge

| # | Problem | Fix |
|---|---|---|
| 1 | B's code imported modules/columns A never had (`app.database`, `Reading`, `Alert`, `DeviceState`, `started_at`, `power_w`, `image_captures.filename/used/...`) | Added the missing tables/columns to `models.py`; `api.py` queries A's names. `b_adapter.py` removed |
| 2 | Two config systems. B read `os.environ` only, so values in `.env` were invisible when running uvicorn locally (API key -> always 503) | One pydantic `Settings` in `config.py` (reads root `.env`); `b_config.py` removed |
| 3 | Dashboard says "Automatic cleaning is blocked until devices report again" but nothing implemented it | `DeviceState` heartbeat in `mqtt_client`, `refresh_device_health` in the scheduler, engine action `blocked` |
| 4 | B's contract says images with `used=false` must be ignored; A's engine used every image | `latest_image_severity` filters `used`, prefers calibrated `severity_pct` |
| 5 | Dashboard reads `reason`, `net_benefit_inr`, alerts table; engine wrote none | Engine now stores `reason` + `net_benefit_inr` (config-driven formula) and writes `alerts` rows |
| 6 | `weather.py` used the DAILY max, i.e. "today", not the next 24 h; and raised instead of returning "unknown" | Hourly `forecast_hours=24`; keeps B's cache, stale fallback and 0,0 startup check; returns `None` when unknown |
| 7 | `make_password.sh` did `chmod 600`; the broker drops privileges and cannot read it, so Mosquitto refuses to start (reproduced) | `chmod 644` (hashes only, git-ignored) |
| 8 | Backend MQTT client, simulator and firmware could not authenticate to B's locked-down broker | `MQTT_USER/PASSWORD` support in all three |
| 9 | Naive UTC timestamps vs A's timezone-aware ones | API uses aware UTC and emits `...Z` |
| 10 | B's classes had no `mixed` (blueprint has 4) and `bird_drop` vs guide's `bird_dropping` | 4 classes: clean, dusty, bird_drop, mixed |
| 11 | `/trigger-clean` had no "already running" check although B's dashboard handles 409 | 409 while a cycle is active |
| 12 | Model/upload paths were relative to the working directory (`data/images`, `ml/models`) | Absolute, derived from the repo root |

## Known limits (not fixed, worth knowing)
* `POST /images` is unauthenticated. On an open network anyone could upload a dirty-looking photo and
  influence cleaning once a real CNN is enabled. Put it behind the same key or a firewall before deployment.
* `ml/train.py` fine-tunes all layers at `lr=1e-3` from epoch 1. That is aggressive for a pretrained
  MobileNet on a small dataset; if validation F1 is unstable try `--lr 3e-4` or freezing the backbone first.
  It was not run (needs your data and the PyTorch weight download).
* Combined loss now mixes CNN severity as "expected loss %" when a severity map exists, raw 0-100 score otherwise.
  Recalibrate (`tools/calibrate_severity.py`) whenever you change the camera or angle.
* The cost/benefit formula is informational and its defaults are placeholders; edit `FARM_RATED_KW`,
  `TARIFF_INR_PER_KWH`, `CLEANING_COST_INR` for your site.
* Schema is still `create_all`; adopt Alembic before anyone keeps real data.

## Verified vs not
Verified: 102 pytest tests (backend, ML splits, tools, API<->TypeScript contract); ruff and black clean;
dashboard `npm ci && npm run build` (includes `tsc`); live run on SQLite with an authenticated
Mosquitto broker + simulator + `capture.py` + API: alerts, device health, trend/readings endpoints, 401/200/409
on `/trigger-clean`, full clean-and-verify cycle.

Not verified: PostgreSQL, any Docker build or the Docker Mosquitto auth path (only the native broker),
`ml/train.py`, CNN inference with a real checkpoint, the dashboard in a browser, ESP32 firmware, Telegram
and Open-Meteo network calls.
