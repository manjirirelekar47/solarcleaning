"""FastAPI entrypoint: tables, MQTT client, evaluation scheduler, REST API, image hosting."""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import models, mqtt_client  # noqa: F401  (models registers the tables)
from .api import router
from .config import settings
from .db import Base, SessionLocal, engine
from .decision_engine import (
    evaluate_once,
    expire_stale_cycles,
    refresh_device_health,
    verify_cycles,
)
from .weather import assert_weather_configured


def run_cycle():
    with SessionLocal() as db:
        refresh_device_health(db)
        expire_stale_cycles(db)
        verify_cycles(db)
        evaluate_once(db, mqtt_client.client)


async def scheduler():
    while True:
        try:
            await asyncio.to_thread(run_cycle)
        except Exception as e:  # keep the loop alive whatever happens
            print("scheduler error:", e)
        await asyncio.sleep(settings.eval_interval_s)


@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)  # swap for Alembic once the schema settles
    task = None
    if settings.enable_background:
        assert_weather_configured()  # refuse to run with the 0,0 default location
        mqtt_client.start()
        task = asyncio.create_task(scheduler())
    yield
    if task:
        task.cancel()
        mqtt_client.stop()


app = FastAPI(title="Solar Soiling MVP", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.get("/health")
def health():
    return {"ok": True}
