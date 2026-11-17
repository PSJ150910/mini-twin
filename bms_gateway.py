"""
Mini Twin - BACnet to MQTT gateway (Task 3, part A2)

The BMS (bms_sim.py) speaks BACnet. Node-RED and the rest of our twin listen
on MQTT. This little program is the translator in the middle, a bit like an
interpreter at a meeting: it asks the BMS questions in BACnet and repeats the
answers on MQTT.

Real smart-building projects do exactly this with a "BACnet gateway" box.

What it does:
  1. Asks the BMS "what points do you have?" (BACnet object-list)
  2. Asks the name and unit of each point
  3. Every few seconds, reads every value and publishes it to
     bms/<machine>/<point>, for example  bms/AHU-L1-MTG/Status

Run it with:   python bms_gateway.py     (start bms_sim.py first)
Stop it with:  Ctrl + C
"""

import argparse
import asyncio
import json
from datetime import datetime

import paho.mqtt.client as mqtt
from bacpypes3.app import Application

# ---------------- Settings you can change ----------------
MY_ADDRESS = "127.0.0.1/8:47809"     # the gateway's own spot (different port to the BMS)
BMS_ADDRESS = "127.0.0.1:47808"      # where the BMS lives
BMS_DEVICE_ID = 1001
BROKER_HOST = "localhost"
BROKER_PORT = 1883
POLL_EVERY_SECONDS = 5
# ----------------------------------------------------------

UNIT_TEXT = {"degrees-celsius": "°C", "percent": "%"}


async def discover_points(app):
    """Ask the BMS for its list of points, then the name/unit/states of each."""
    object_list = await app.read_property(
        BMS_ADDRESS, f"device,{BMS_DEVICE_ID}", "object-list"
    )
    points = []
    for objid in object_list:
        kind = str(objid[0])
        if kind in ("device", "network-port"):
            continue
        name = str(await app.read_property(BMS_ADDRESS, objid, "object-name"))
        point = {"objid": objid, "kind": kind, "name": name, "unit": None, "states": None}
        if kind == "analog-value":
            unit = str(await app.read_property(BMS_ADDRESS, objid, "units"))
            point["unit"] = UNIT_TEXT.get(unit, unit)
        if kind == "multi-state-value":
            states = await app.read_property(BMS_ADDRESS, objid, "state-text")
            point["states"] = [str(s) for s in states]
        points.append(point)
    return points


def to_plain(point, raw):
    """Turn a raw BACnet value into something friendly for JSON."""
    if point["states"]:
        return point["states"][int(raw) - 1]     # 3 -> "Fault"
    if point["kind"] == "analog-value":
        return round(float(raw), 1)
    return str(raw)


async def main():
    mq = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="mini-twin-bms-gateway")
    try:
        mq.connect(BROKER_HOST, BROKER_PORT)
    except OSError:
        print("Could not reach the MQTT broker on port 1883. Is Node-RED running?")
        return
    mq.loop_start()

    settings = argparse.Namespace(
        vendoridentifier=999, instance=1002, name="MiniTwin-Gateway",
        address=MY_ADDRESS, network=0, foreign=None, bbmd=None, ttl=30,
    )
    app = Application.from_args(settings)

    try:
        print(f"Asking the BMS at {BMS_ADDRESS} what points it has...")
        try:
            points = await asyncio.wait_for(discover_points(app), timeout=10)
        except (asyncio.TimeoutError, Exception) as err:
            print(f"No answer from the BMS ({err!r}). Is bms_sim.py running?")
            return
        print(f"Found {len(points)} points. Publishing to MQTT every "
              f"{POLL_EVERY_SECONDS} s. Ctrl + C to stop.\n")

        while True:
            stamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            summary = {}
            for point in points:
                try:
                    raw = await app.read_property(BMS_ADDRESS, point["objid"], "present-value")
                except Exception as err:
                    print(f"  could not read {point['name']}: {err!r}")
                    continue
                machine, what = point["name"].split(".", 1)
                value = to_plain(point, raw)
                message = {
                    "device": machine,
                    "point": what,
                    "value": value,
                    "unit": point["unit"],
                    "bacnet_object": f"{point['objid'][0]},{point['objid'][1]}",
                    "time": stamp,
                }
                mq.publish(f"bms/{machine}/{what}", json.dumps(message))
                if what == "Status":
                    summary[machine] = value
            print(stamp[11:], "  ".join(f"{m}:{s}" for m, s in summary.items()))
            await asyncio.sleep(POLL_EVERY_SECONDS)
    finally:
        mq.loop_stop()
        mq.disconnect()
        app.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopping the gateway. Bye!")
