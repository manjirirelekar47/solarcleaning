# Smart Solar Soiling Detection: MVP

Two panels (a *test* panel that gets dirty, a *reference* panel kept clean) report V/I over MQTT.
A camera image of the test panel is classified by a CNN. The backend fuses both signals into one
loss %, checks the rain forecast, triggers a cleaning cycle, verifies the result and serves a
React dashboard. Everything runs on a laptop against `tools/simulator.py` first; real ESP32
hardware speaks the same MQTT contract.

## Quick start (about 10 minutes, no hardware)

```bash
cp .env.example .env                      # set WEATHER_LAT / WEATHER_LON to your site
python -m venv venv && source venv/bin/activate    # Windows: venv\Scripts\activate
pip install -r backend/requirements.txt   # add -r backend/requirements-ml.txt for the CNN

docker compose up -d                      # Mosquitto :1883 + Postgres :5432

cd backend && uvicorn app.main:app --reload          # terminal 1  -> http://localhost:8000/docs
python tools/simulator.py                            # terminal 2 (from repo root)
cd dashboard && npm install && npm run dev           # terminal 3  -> http://localhost:5173
```

Optional: `curl -F file=@panel.jpg localhost:8000/images` to feed the image classifier.

## Tests and code style

```bash
pytest            # decision engine, debounce, lifecycle + alerts, loss maths, API, MQTT, vision
ruff check . && black --check .
```
CI (`.github/workflows/ci.yml`) runs the same checks plus a dashboard build on every PR.

## MQTT contract

| Topic | Direction | Payload |
| --- | --- | --- |
| `sensors/test`, `sensors/reference` | edge -> backend | `{"v":18.2,"i_ma":310.5,"temp":31.4,"hum":42}` |
| `cleaning/trigger` | backend -> edge | `{"cycle_id":7,"duration_s":20}` |
| `cleaning/status` | edge -> backend | `{"cycle_id":7,"state":"running"}` then `"done"` |

## Calibration and training

- **Baseline:** with both panels clean and publishing, `python tools/calibrate.py`, then paste the
  printed `BASELINE_RATIO` into `.env`.
- **CNN:** put images in `ml/data/{train,val,test}/{clean,dusty,bird_dropping,mixed}/`
  (split by photo session), then `cd ml && python train.py`. The API switches from the stub to the
  CNN automatically once `ml/artifacts/soiling_mnv3.pt` exists.
- **Firmware:** copy `firmware/include/secrets.h.example` to `secrets.h`, fill it in, open
  `firmware/` in PlatformIO and upload.

## Layout

```
solar-soiling/
├── docker-compose.yml  .env.example  pyproject.toml
├── infra/mosquitto.conf
├── backend/app/        config db models mqtt_client loss vision weather notify
│                       decision_engine api main
├── backend/tests/
├── ml/                 train.py, data/, artifacts/
├── tools/              simulator.py, calibrate.py
├── firmware/           PlatformIO ESP32 project
├── dashboard/          Vite + React + TypeScript
└── .github/workflows/ci.yml
```
