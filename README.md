# Smart Solar Soiling Detection: MVP

Two panels (test + clean reference) report V/I over MQTT; a camera image is classified by a CNN;
the backend combines both into one loss %, decides whether to clean (rain-aware), triggers a
cleaning cycle, verifies the result, and the dashboard shows everything.

## Quick start (laptop + simulator, ~10 min)

```bash
cp .env.example .env        # set WEATHER_LAT/LON and API_KEY (python -c "import secrets;print(secrets.token_urlsafe(32))")
python -m venv venv && source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r backend/requirements.txt -r backend/requirements-dev.txt

docker compose up -d                                     # Mosquitto + Postgres
pytest                                                   # from the repo root: ~100 tests, all green

cd backend && uvicorn app.main:app --reload              # terminal 1 -> http://localhost:8000/docs
python tools/simulator.py                                # terminal 2 (repo root)
cd dashboard && cp .env.example .env.local && npm install && npm run dev    # terminal 3 -> :5173
```

The dashboard's "Clean now" button asks for the same `API_KEY` you put in `.env`.

> **Upgrading from the Phase-0/1 database?** New columns were added and `create_all` does not alter
> existing tables. On a dev machine run `docker compose down -v && docker compose up -d` once
> (the data is only simulator output).

## Optional pieces

| Piece | How |
|---|---|
| Real CNN (else: stub, never used for decisions) | `pip install -r ml/requirements.txt`; photos in `ml/data/<session>/<class>/` ([layout](ml/data/README.md)); `python ml/train.py --data ml/data --test-sessions own_panel_01`; restart the API |
| Feed camera frames | `python tools/capture.py --folder tools/test_frames --interval 60` (or `--source-url` phone / `--camera 0`) |
| Baseline (both panels clean) | `python tools/calibrate.py` -> `BASELINE_RATIO` in `.env` |
| Severity calibration (CNN vs measured loss) | `python tools/calibrate_severity.py --baseline-ratio 0.98` -> `ml/models/severity_map.json` |
| Locked-down MQTT | `./infra/mosquitto/make_password.sh backend 'pw'`, `... esp32 'pw2' --append`, then `docker compose -f docker-compose.yml -f infra/docker-compose.secure.yml up -d`; set `MQTT_USER/MQTT_PASSWORD` in `.env` and `MQTT_USER/MQTT_PASS` in `firmware/include/secrets.h` |
| Telegram alerts | `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` in `.env` |
| ESP32 firmware | copy `firmware/include/secrets.h.example` -> `secrets.h`; `pio run -t upload` |
| Whole stack in Docker | `docker compose --profile full up -d --build` (API :8000, dashboard :8080) |

## Layout

```
solar-soiling/
├── backend/app/   config db models mqtt_client loss vision weather notify security decision_engine api main
├── backend/tests/ decision table, pipeline, ingestion, API, vision, weather, API<->TypeScript contract
├── dashboard/     Vite + React + TS + Recharts (status, gauge, image, trend, cleaning, power, cost, alerts)
├── ml/            train.py splits.py (session-aware) data/ models/ (weights git-ignored)
├── tools/         simulator calibrate calibrate_severity capture
├── firmware/      PlatformIO ESP32 project
├── infra/         mosquitto.conf (dev)  mosquitto/ + docker-compose.secure.yml (authenticated)
└── .github/workflows/ci.yml   ruff + black + pytest + dashboard build
```

Review notes and known limits: [docs/MEMBER_B_REVIEW.md](docs/MEMBER_B_REVIEW.md).
