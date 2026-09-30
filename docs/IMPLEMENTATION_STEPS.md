# Implementation steps (Phases 1-9 on top of your Phase 0 skeleton)

Each step lists what to create and how to confirm it works.

| Step | Create / change | Confirm |
|---|---|---|
| 1. Data model | `backend/app/config.py`, `db.py`, `models.py`; new `main.py` with lifespan + `create_all` | `uvicorn app.main:app --reload`; `/docs` loads; 4 tables exist in Postgres |
| 2. MQTT + simulator | `mqtt_client.py`, `tools/simulator.py` | `SELECT count(*) FROM panel_readings` keeps growing while the simulator runs |
| 3. Loss maths | `loss.py`, `tools/calibrate.py` | `pytest tests/test_decision_engine.py` |
| 4. Vision | `vision.py` (stub first), `ml/train.py`, `POST /images` | `curl -F file=@x.jpg localhost:8000/images` returns class + severity |
| 5. Decision engine | `weather.py`, `notify.py`, `decision_engine.py`, scheduler in `main.py` | `soiling_events` rows every 10 s; level ok -> watch -> clean_recommended; cycle ends `success` |
| 6. API + dashboard | `api.py`, CORS in `main.py`, `dashboard/` (Member B's version) | 7 widgets show live data; "Clean now" creates a cycle |
| 7. Tests + CI | `backend/tests/*`, `.github/workflows/ci.yml` | `pytest` green locally and on GitHub |
| 8. Hardware | `firmware/` (PlatformIO), `secrets.h` | cover half the test panel -> loss rises; relay clicks on trigger |
| 9. Packaging | `backend/Dockerfile`, `dashboard/Dockerfile`, compose `full` profile | `docker compose --profile full up -d --build` |

## Deviations from the build guide (bugs found while implementing it)

1. **`.env` was never found.** The guide reads `.env` relative to the working directory, but you run
   uvicorn from `backend/` while `.env` lives at the repo root. `config.py` now resolves the root path.
2. **Cleaning loop after a successful clean.** Verification and evaluation used a 5-minute median that
   still contained pre-clean readings, and the old photo (up to 24 h old) kept severity high.
   Both now only use data received after the last completed cleaning.
3. **SQLite drops timezones**, which breaks tz-aware comparisons in tests. `models.UTCDateTime` fixes it.
4. **Stale cycles** (the guide's "Common pitfalls") are handled by `expire_stale_cycles`.
5. Weather forecast is cached 15 min instead of an HTTP call every 10 s.
6. Image filenames are UUIDs (second-resolution names can collide); uploads are validated as images.
7. torch/scikit-learn moved to `ml/requirements.txt` so CI and the default Docker image stay small.
8. WiFi credentials live in a git-ignored `firmware/include/secrets.h`, not in `main.cpp`.
9. Compose backend/dashboard are behind a `full` profile so your existing `docker compose up -d`
   (broker + DB only, API run locally) keeps working.

## What was and was not verified

Verified here: 28 pytest tests pass; ruff + black clean; dashboard `npm run build` succeeds;
live run with a real Mosquitto broker + simulator + API (on SQLite) went
watch -> clean_recommended -> trigger -> cleaning -> verification `success` (10.5% -> 2.6%).

Not verified here: PostgreSQL itself, Docker builds (no Docker in my sandbox), CNN training
(needs your dataset + PyTorch weight download), the Telegram and Open-Meteo network calls,
and the ESP32 firmware (needs hardware / a PlatformIO build).

## Member B merge
See [MEMBER_B_REVIEW.md](MEMBER_B_REVIEW.md): what was taken, 12 integration fixes, known limits.
