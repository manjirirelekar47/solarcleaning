"""FastAPI entrypoint: DB tables, MQTT ingestion, evaluation scheduler, REST API, image media."""

import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import (
    models,  # noqa: F401  (registers tables)
    mqtt_client,
)
from .api import router
from .config import settings
from .db import Base, SessionLocal, engine
from .decision_engine import evaluate_once, expire_stale_cycles, verify_cycles
from .weather import assert_weather_configured


def run_cycle() -> None:
    with SessionLocal() as db:
        expire_stale_cycles(db)
        verify_cycles(db)
        evaluate_once(db, mqtt_client.client)


async def scheduler() -> None:
    while True:
        try:
            await asyncio.to_thread(run_cycle)
        except Exception as e:  # keep the loop alive through transient DB/network errors
            print("scheduler error:", e)
        await asyncio.sleep(settings.eval_interval_s)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)  # swap for Alembic once the schema settles
    assert_weather_configured()  # refuse to run with the 0,0 default location
    mqtt_client.start()
    task = asyncio.create_task(scheduler())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    mqtt_client.stop()


settings.image_dir.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="Solar Soiling MVP", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
app.mount("/media", StaticFiles(directory=settings.image_dir), name="media")


@app.get("/health")
def health():
    return {"ok": True}
