"""Pretends to be the ESP32: two panels, dust builds up on the test panel, cleaning restores it.

Usage: python tools/simulator.py [--host localhost] [--port 1883] [--dust-rate 0.002]
"""

import argparse
import json
import math
import os
import random
import time

import paho.mqtt.client as mqtt

ap = argparse.ArgumentParser()
ap.add_argument("--user", default=os.getenv("MQTT_USER", ""))
ap.add_argument("--password", default=os.getenv("MQTT_PASSWORD", ""))
ap.add_argument("--host", default="localhost")
ap.add_argument("--port", type=int, default=1883)
ap.add_argument(
    "--dust-rate", type=float, default=0.002, help="fraction of power lost per 2 s tick"
)
args = ap.parse_args()

dust_loss = 0.0  # fraction of power lost on the test panel
cleaning_until = 0.0
cycle_id = None


def on_message(c, u, msg):
    global cleaning_until, cycle_id
    p = json.loads(msg.payload)
    cycle_id, cleaning_until = p["cycle_id"], time.time() + p["duration_s"]
    c.publish("cleaning/status", json.dumps({"cycle_id": cycle_id, "state": "running"}), qos=1)
    print(f"[sim] cleaning cycle {cycle_id} started")


c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="sim")
c.on_message = on_message
if args.user:
    c.username_pw_set(args.user, args.password)
c.connect(args.host, args.port)
c.subscribe("cleaning/trigger", qos=1)
c.loop_start()

while True:
    dust_loss = min(0.35, dust_loss + args.dust_rate)
    if cycle_id is not None and time.time() >= cleaning_until:
        dust_loss = 0.02  # cleaning is nearly perfect
        c.publish("cleaning/status", json.dumps({"cycle_id": cycle_id, "state": "done"}), qos=1)
        print(f"[sim] cleaning cycle {cycle_id} done")
        cycle_id = None
    sun = 0.8 + 0.05 * math.sin(time.time() / 30)  # slowly varying irradiance
    v_ref = 18.0
    i_ref = 400 * sun + random.gauss(0, 2)  # mA
    env = {"temp": round(30 + random.gauss(0, 0.3), 1), "hum": round(45 + random.gauss(0, 1), 1)}
    c.publish("sensors/reference", json.dumps({"v": v_ref, "i_ma": round(i_ref, 1), **env}))
    c.publish(
        "sensors/test",
        json.dumps({"v": v_ref, "i_ma": round(i_ref * (1 - dust_loss), 1), **env}),
    )
    time.sleep(2)
