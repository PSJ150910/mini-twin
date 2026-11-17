"""
Mini Twin - pretend air machines / BMS (Task 3, part A)

A real office has a "Building Management System" (BMS): the computer that runs
the heating, cooling and fresh-air machines. Those machines talk a special
language called BACnet. Think of BACnet as the official language that building
equipment uses, the way phones all use the same standard so any phone can
call any other phone.

This program pretends to be one BACnet controller looking after six air
machines (AHUs, "Air Handling Units"), one per room. Each machine has five
"points" (a point is just one value you can read, like one dial on a dashboard):

    Status       Off / Running / Fault
    SupplyTemp   temperature of the air it blows into the room (C)
    Setpoint     the temperature it is aiming for (C)
    FanSpeed     how hard the fan is spinning (%)
    Alarm        a short message saying what is wrong, if anything

IMPORTANT: the BMS uses its OWN names for things ("AHU-L1-MTG"), not the
sensor names ("room3"). That is on purpose, because real buildings are like
this, and Task 4 is all about matching the names up.

How it knows when to break: the sensor program (sensors.py) announces on a
behind-the-scenes MQTT channel, "simulation/fault", when it makes a room
overheat. This program listens and puts the matching machine into Fault.
That channel is only stage machinery for our pretend building; a real BMS
would simply notice its own fan had stopped.

Run it with:   python bms_sim.py
Stop it with:  Ctrl + C
"""

import argparse
import asyncio
import json
import random

import paho.mqtt.client as mqtt
from bacpypes3.app import Application
from bacpypes3.local.analog import AnalogValueObject
from bacpypes3.local.multistate import MultiStateValueObject
from bacpypes3.local.object import Object as LocalObject
from bacpypes3.object import CharacterStringValueObject

# ---------------- Settings you can change ----------------
BACNET_ADDRESS = "127.0.0.1/8:47808"   # this laptop only, standard BACnet port
DEVICE_ID = 1001                       # the controller's BACnet "house number"
DEVICE_NAME = "MiniTwin-BMS"
BROKER_HOST = "localhost"
BROKER_PORT = 1883
UPDATE_EVERY_SECONDS = 5
# ----------------------------------------------------------

# The six air machines, with BMS-style names. "serves" is ONLY used for the
# behind-the-scenes link to the pretend sensors; nothing in BACnet shows it.
AHUS = [
    {"name": "AHU-L1-RCP", "serves": "room1"},   # Reception
    {"name": "AHU-L1-OPN", "serves": "room2"},   # Open Office
    {"name": "AHU-L1-MTG", "serves": "room3"},   # Meeting Room
    {"name": "AHU-L1-KIT", "serves": "room4"},   # Kitchen
    {"name": "AHU-L1-MGR", "serves": "room5"},   # Manager Office
    {"name": "AHU-L1-QUI", "serves": "room6"},   # Quiet Room
]

FAULT_REASONS = [
    "Supply fan motor tripped",
    "Chilled water valve stuck closed",
    "Filter blocked - high pressure drop",
    "Compressor failed to start",
]

STATUS_TEXT = ["Off", "Running", "Fault"]   # BACnet multi-state values count from 1


class StringValueObject(LocalObject, CharacterStringValueObject):
    """A BACnet point that holds a piece of text (used for the Alarm message)."""


class AirMachine:
    """One pretend AHU and its five BACnet points."""

    def __init__(self, number, info):
        self.name = info["name"]
        self.serves = info["serves"]
        self.broken = False
        base = number * 10   # AHU 1 uses objects 11-15, AHU 2 uses 21-25, ...

        self.status = MultiStateValueObject(
            objectIdentifier=("multi-state-value", base + 1),
            objectName=f"{self.name}.Status",
            presentValue=2,
            numberOfStates=3,
            stateText=STATUS_TEXT,
            statusFlags=[0, 0, 0, 0],
            description=f"{self.name} run status",
        )
        self.supply_temp = AnalogValueObject(
            objectIdentifier=("analog-value", base + 2),
            objectName=f"{self.name}.SupplyTemp",
            presentValue=18.0,
            units="degreesCelsius",
            statusFlags=[0, 0, 0, 0],
            description=f"{self.name} supply air temperature",
        )
        self.setpoint = AnalogValueObject(
            objectIdentifier=("analog-value", base + 3),
            objectName=f"{self.name}.Setpoint",
            presentValue=18.0,
            units="degreesCelsius",
            statusFlags=[0, 0, 0, 0],
            description=f"{self.name} supply air setpoint",
        )
        self.fan_speed = AnalogValueObject(
            objectIdentifier=("analog-value", base + 4),
            objectName=f"{self.name}.FanSpeed",
            presentValue=65.0,
            units="percent",
            statusFlags=[0, 0, 0, 0],
            description=f"{self.name} supply fan speed",
        )
        self.alarm = StringValueObject(
            objectIdentifier=("characterstring-value", base + 5),
            objectName=f"{self.name}.Alarm",
            presentValue="",
            statusFlags=[0, 0, 0, 0],
            description=f"{self.name} active alarm text",
        )

    def points(self):
        return [self.status, self.supply_temp, self.setpoint, self.fan_speed, self.alarm]

    def set_broken(self, broken):
        if broken == self.broken:
            return
        self.broken = broken
        if broken:
            reason = random.choice(FAULT_REASONS)
            self.status.presentValue = 3
            self.status.statusFlags = [1, 1, 0, 0]   # "in alarm" + "fault" flags
            self.alarm.presentValue = reason
            print(f"  ** {self.name} -> FAULT: {reason} **")
        else:
            self.status.presentValue = 2
            self.status.statusFlags = [0, 0, 0, 0]
            self.alarm.presentValue = ""
            print(f"  ** {self.name} -> back to Running **")

    def update(self):
        temp = float(self.supply_temp.presentValue)
        aim = float(self.setpoint.presentValue)
        if self.broken:
            fan = 0.0
            aim = 28.0          # no cooling: the air drifts up towards room heat
        else:
            fan = 65.0 + random.uniform(-5, 5)
        temp += (aim - temp) * 0.15 + random.uniform(-0.1, 0.1)
        self.supply_temp.presentValue = round(temp, 1)
        self.fan_speed.presentValue = round(fan, 1)


def start_fault_listener(machines):
    """Listen on the behind-the-scenes MQTT channel for which room is broken."""
    by_room = {m.serves: m for m in machines}

    def on_connect(client, userdata, flags, reason_code, properties):
        client.subscribe("simulation/fault")

    def on_message(client, userdata, msg):
        try:
            data = json.loads(msg.payload)
        except ValueError:
            return
        broken_room = data.get("room") if data.get("active") else None
        for room, machine in by_room.items():
            machine.set_broken(room == broken_room)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="mini-twin-bms")
    client.on_connect = on_connect
    client.on_message = on_message
    try:
        client.connect(BROKER_HOST, BROKER_PORT)
    except OSError:
        print("Note: could not reach the MQTT broker, so faults from sensors.py")
        print("won't show up. The BACnet side still works.\n")
        return None
    client.loop_start()
    return client


async def main():
    settings = argparse.Namespace(
        vendoridentifier=999,          # 999 = "test / not a real manufacturer"
        instance=DEVICE_ID,
        name=DEVICE_NAME,
        address=BACNET_ADDRESS,
        network=0,
        foreign=None,
        bbmd=None,
        ttl=30,
    )
    app = Application.from_args(settings)

    machines = [AirMachine(i, info) for i, info in enumerate(AHUS, start=1)]
    for m in machines:
        for point in m.points():
            app.add_object(point)

    mqtt_client = start_fault_listener(machines)

    print(f"BMS controller '{DEVICE_NAME}' (BACnet device {DEVICE_ID}) is live")
    print(f"on {BACNET_ADDRESS.split('/')[0]} port 47808 with "
          f"{len(machines)} air machines, {len(machines) * 5} points.")
    print("Ctrl + C to stop.\n")

    try:
        while True:
            for m in machines:
                m.update()
            line = "  ".join(
                f"{m.name[-3:]}:{'FAULT' if m.broken else 'ok'}" for m in machines
            )
            print(line)
            await asyncio.sleep(UPDATE_EVERY_SECONDS)
    finally:
        if mqtt_client:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
        app.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopping the pretend BMS. Bye!")
