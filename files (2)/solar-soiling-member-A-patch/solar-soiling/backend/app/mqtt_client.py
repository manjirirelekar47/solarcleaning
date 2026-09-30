"""MQTT ingestion: sensors/* -> readings + device_state, cleaning/status -> cleaning_cycles."""

import json

import paho.mqtt.client as mqtt

from . import health
from .config import settings
from .db import SessionLocal
from .models import CleaningCycle, PanelReading, now

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="backend")


def on_connect(c, userdata, flags, reason_code, properties):
    c.subscribe([("sensors/#", 0), ("cleaning/status", 1)])


def on_message(c, userdata, msg):
    try:
        p = json.loads(msg.payload)
        with SessionLocal() as db:
            if msg.topic in ("sensors/test", "sensors/reference"):
                source = msg.topic.split("/")[1]
                amps = p["i_ma"] / 1000
                fault = health.check_reading(p["v"], amps)
                health.touch(db, source, fault)
                if fault is None:  # implausible readings never reach the loss calculation
                    db.add(
                        PanelReading(
                            panel_type=source,
                            voltage=p["v"],
                            current=amps,
                            power=p["v"] * amps,
                            temp=p.get("temp"),
                            humidity=p.get("hum"),
                        )
                    )
            elif msg.topic == "sensors/fault":  # firmware: INA219 missing etc.
                if p["source"] in health.SOURCES:
                    health.touch(db, p["source"], str(p.get("fault", "sensor fault")))
            elif msg.topic == "cleaning/status":
                cyc = db.get(CleaningCycle, p["cycle_id"])
                if cyc and cyc.status != "complete":  # a late message must not revive a cycle
                    if p["state"] == "running":
                        cyc.status = "running"
                    elif p["state"] == "done":
                        cyc.status, cyc.completed_at = "verifying", now()
                    elif p["state"] == "rejected":  # device refused the trigger (busy/bad payload)
                        cyc.status, cyc.result, cyc.completed_at = "complete", "failed", now()
            db.commit()
    except Exception as e:  # never let a bad payload kill the network loop
        print("MQTT handler error:", e)


client.on_connect, client.on_message = on_connect, on_message


def start() -> None:
    client.connect_async(settings.mqtt_broker_host, settings.mqtt_port)
    client.loop_start()


def stop() -> None:
    client.loop_stop()
    client.disconnect()
