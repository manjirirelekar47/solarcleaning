"""Pretends to be the ESP32: publishes both panels and obeys cleaning/trigger.
Dust builds up over time until a cleaning cycle resets it.

Usage: python tools/simulator.py [--host localhost] [--port 1883] [--dust-rate 0.002] [--period 2]
                                 [--fault MODE] [--fault-after 30]

Fault modes (start --fault-after seconds after launch, so normal data comes first):
  shadow       test panel loses 30% instantly (cloud edge / shadow). Upload a CLEAN panel photo
               (POST /images) so the CNN disagrees: expected result is notify only, no cleaning.
  offline      the ESP32 stops publishing. Expected: one "device offline" alert, no cleaning.
  sensor_zero  the test panel's INA219 reads 0 mA. Expected: sensor-fault alert, no cleaning.
  stuck_relay  the pump never sprays: cleaning "completes" but the panel stays dirty.
               Expected: cycle 1 ends insufficient, ONE retry (attempt 2), then a single
               "manual inspection needed" alert and no more auto-cleans. The retry waits for
               MIN_CLEAN_INTERVAL_S: set it to 60 in .env for a short demo.
  night        no light at all. Expected: "insufficient light", no loss figure, no cleaning.
"""

import argparse
import json
import math
import os
import random
import time

import paho.mqtt.client as mqtt

ap = argparse.ArgumentParser()
ap.add_argument("--host", default="localhost")
ap.add_argument("--port", type=int, default=1883)
ap.add_argument("--dust-rate", type=float, default=0.002, help="fraction of power lost per tick")
ap.add_argument("--period", type=float, default=2.0, help="seconds between publishes")
ap.add_argument(
    "--fault",
    choices=["shadow", "offline", "sensor_zero", "stuck_relay", "night"],
    help="inject a fault (see module docstring)",
)
ap.add_argument("--fault-after", type=float, default=30.0, help="seconds before the fault starts")
args = ap.parse_args()

started = time.time()
dust_loss = 0.0  # fraction of power lost on the test panel
cleaning_until = 0.0
cycle_id = None


def fault_on(mode: str) -> bool:
    return args.fault == mode and time.time() - started >= args.fault_after


def on_message(c, u, msg):
    global cleaning_until, cycle_id
    p = json.loads(msg.payload)
    cycle_id, cleaning_until = p["cycle_id"], time.time() + p["duration_s"]
    c.publish("cleaning/status", json.dumps({"cycle_id": cycle_id, "state": "running"}), qos=1)
    print(f"cleaning cycle {cycle_id} started")


c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="sim")
if os.getenv("MQTT_USER"):
    c.username_pw_set(os.environ["MQTT_USER"], os.getenv("MQTT_PASSWORD", ""))
c.on_message = on_message
c.connect(args.host, args.port)
c.subscribe("cleaning/trigger", qos=1)
c.loop_start()

announced = False
while True:
    if args.fault and not announced and fault_on(args.fault):
        print(f"*** fault injected: {args.fault}")
        announced = True
    dust_loss = min(0.35, dust_loss + args.dust_rate)
    if cycle_id is not None and time.time() >= cleaning_until:
        if not fault_on("stuck_relay"):  # a stuck relay sprays nothing, so nothing gets cleaner
            dust_loss = 0.02  # cleaning is nearly perfect
        c.publish("cleaning/status", json.dumps({"cycle_id": cycle_id, "state": "done"}), qos=1)
        print(f"cleaning cycle {cycle_id} done")
        cycle_id = None
    if fault_on("offline"):  # the device is gone: no sensor messages at all
        time.sleep(args.period)
        continue
    sun = 0.8 + 0.05 * math.sin(time.time() / 30)  # slowly varying irradiance
    if fault_on("night"):
        sun = 0.0
    v_ref = 18.0
    i_ref = max(0.0, 400 * sun + random.gauss(0, 2 if sun else 0))  # mA
    env = {"temp": round(30 + random.gauss(0, 0.3), 1), "hum": round(45 + random.gauss(0, 1), 1)}
    c.publish("sensors/reference", json.dumps({"v": v_ref, "i_ma": round(i_ref, 1), **env}))
    test_i = round(i_ref * (1 - dust_loss), 1)
    if fault_on("shadow"):
        test_i = round(test_i * 0.7, 1)
    if fault_on("sensor_zero"):
        test_i = 0.0
    c.publish("sensors/test", json.dumps({"v": v_ref, "i_ma": test_i, **env}))
    time.sleep(args.period)
