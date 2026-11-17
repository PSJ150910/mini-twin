"""
Mini Twin - pretend building sensors (Task 2)

Think of this file as six fake sensor boxes, one in each room of a small office.
Every few seconds each box "reads" the room's temperature, CO2 (how stale the
air is) and whether people are in, then shouts the reading out over MQTT,
which is like a building-wide radio channel. Node-RED listens to that channel.

The clock runs fast: 1 real second = 1 pretend minute, so a whole office day
passes in about 24 real minutes. That way the demo shows mornings, lunch
and evenings without waiting a whole day.

Every so often one room "breaks" and starts overheating, so later we have
something interesting for alerts and the AI chat to explain.

Task 3 addition: whenever a room breaks or gets fixed, this program also
announces it on a behind-the-scenes MQTT channel, "simulation/fault", so the
pretend BMS (bms_sim.py) and repair logbook (cafm_api.py) can react.

Run it with:   python sensors.py
Break a room straight away (handy for demos):   python sensors.py --fault room3
Stop it with:  Ctrl + C
"""

import json
import random
import sys
import time
from datetime import datetime, timedelta

import paho.mqtt.client as mqtt

# ---------------- Settings you can change ----------------
BROKER_HOST = "localhost"   # the MQTT "radio station" inside Node-RED
BROKER_PORT = 1883
SEND_EVERY_SECONDS = 5      # how often each room sends its readings
SPEED = 60                  # 60 = one real second is one pretend minute
FAULT_CHANCE = 0.03         # chance each round that a new fault starts (only one at a time)
# ----------------------------------------------------------

# The six rooms. "people_max" is how many people fit, "busy" says when it fills up.
ROOMS = {
    "room1": {"name": "Reception",      "people_max": 3,  "busy": "all_day"},
    "room2": {"name": "Open Office",    "people_max": 12, "busy": "all_day"},
    "room3": {"name": "Meeting Room",   "people_max": 8,  "busy": "meetings"},
    "room4": {"name": "Kitchen",        "people_max": 6,  "busy": "lunch"},
    "room5": {"name": "Manager Office", "people_max": 2,  "busy": "all_day"},
    "room6": {"name": "Quiet Room",     "people_max": 2,  "busy": "meetings"},
}

OUTSIDE_CO2 = 420  # fresh outdoor air, in ppm (parts per million)


def chance_someone_is_in(busy, hour):
    """How likely the room is in use at this pretend hour (0 = never, 1 = always)."""
    if hour < 8 or hour >= 18:
        return 0.02                       # night: nearly empty
    if busy == "lunch":
        return 0.9 if 12 <= hour < 14 else 0.15
    if busy == "meetings":
        return 0.7 if hour in (10, 11, 14, 15) else 0.2
    return 0.85                           # normal working rooms


class Room:
    def __init__(self, room_id, info):
        self.id = room_id
        self.name = info["name"]
        self.people_max = info["people_max"]
        self.busy = info["busy"]
        self.temperature = 19.0 + random.uniform(-0.5, 0.5)
        self.co2 = OUTSIDE_CO2 + random.uniform(0, 50)
        self.people = 0
        self.fault_minutes_left = 0       # >0 means the room is overheating

    def update(self, hour, minutes_passed):
        # 1. People come and go (not every round, so it doesn't flicker)
        if random.random() < 0.3:
            if random.random() < chance_someone_is_in(self.busy, hour):
                self.people = random.randint(1, self.people_max)
            else:
                self.people = 0

        # 2. Temperature: heating aims for 21.5 C in the day, 18 C at night,
        #    and every person adds a little warmth.
        target = 21.5 if 7 <= hour < 18 else 18.0
        target += self.people * 0.25

        # 3. If this room's air machine is "broken", it overheats
        if self.fault_minutes_left > 0:
            target = 31.0
            self.fault_minutes_left -= minutes_passed
            if self.fault_minutes_left <= 0:
                print(f"  ** {self.name} is fixed and cooling down **")

        # Move part of the way towards the target, plus a tiny wobble like a real sensor
        self.temperature += (target - self.temperature) * 0.08 + random.uniform(-0.1, 0.1)

        # 4. CO2: people breathe it out, fresh air brings it back down
        self.co2 += self.people * 12 - (self.co2 - OUTSIDE_CO2) * 0.1 + random.uniform(-5, 5)
        self.co2 = max(OUTSIDE_CO2 - 20, self.co2)

    def readings(self):
        return [
            ("temperature", round(self.temperature, 1), "°C"),
            ("co2",         round(self.co2),            "ppm"),
            ("occupancy",   self.people,                "people"),
        ]


def announce_fault(client, room, stamp):
    """Tell the other pretend systems which room is broken (None = all fine)."""
    message = {"room": room.id if room else None, "active": room is not None, "time": stamp}
    # retain=True means late-joining programs still get the latest news
    client.publish("simulation/fault", json.dumps(message), qos=1, retain=True)


def main():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="mini-twin-sensors")
    try:
        client.connect(BROKER_HOST, BROKER_PORT)
    except OSError:
        print("Could not reach the MQTT broker on port 1883.")
        print("Is Node-RED running, with the aedes broker node deployed?")
        return
    client.loop_start()
    print("Connected to the MQTT broker. Sending readings... (Ctrl + C to stop)\n")

    rooms = [Room(rid, info) for rid, info in ROOMS.items()]
    pretend_time = datetime.now().replace(hour=7, minute=0, second=0, microsecond=0)
    minutes_per_round = SEND_EVERY_SECONDS * SPEED / 60
    start_stamp = pretend_time.strftime("%Y-%m-%dT%H:%M:%S")

    # Demo switch: "python sensors.py --fault room3" breaks room3 straight away
    forced = sys.argv[2] if len(sys.argv) == 3 and sys.argv[1] == "--fault" else None
    if forced and forced in ROOMS:
        unlucky = next(r for r in rooms if r.id == forced)
        unlucky.fault_minutes_left = random.randint(60, 180)
        print(f"  ** FAULT (demo): {unlucky.name} has started overheating! **")
        announce_fault(client, unlucky, start_stamp)
    else:
        if forced:
            print(f"  (ignoring --fault {forced}: rooms are room1 to room6)")
        announce_fault(client, None, start_stamp)

    try:
        while True:
            pretend_time += timedelta(minutes=minutes_per_round)
            stamp = pretend_time.strftime("%Y-%m-%dT%H:%M:%S")
            print(f"Pretend time {pretend_time:%H:%M}")

            # Now and then, break one room (only if nothing is broken already)
            if not any(r.fault_minutes_left > 0 for r in rooms) and random.random() < FAULT_CHANCE:
                unlucky = random.choice(rooms)
                unlucky.fault_minutes_left = random.randint(60, 180)   # 1 to 3 pretend hours
                print(f"  ** FAULT: {unlucky.name} has started overheating! **")
                announce_fault(client, unlucky, stamp)

            for room in rooms:
                was_broken = room.fault_minutes_left > 0
                room.update(pretend_time.hour, minutes_per_round)
                if was_broken and room.fault_minutes_left <= 0:
                    announce_fault(client, None, stamp)
                for sensor, value, unit in room.readings():
                    topic = f"building/floor1/{room.id}/{sensor}"
                    message = {
                        "room": room.id,
                        "name": room.name,
                        "sensor": sensor,
                        "value": value,
                        "unit": unit,
                        "time": stamp,
                    }
                    client.publish(topic, json.dumps(message))
                print(f"  {room.name:<15} {room.temperature:5.1f} C  "
                      f"{room.co2:5.0f} ppm  {room.people:2d} people")

            time.sleep(SEND_EVERY_SECONDS)
    except KeyboardInterrupt:
        print("\nStopping the pretend sensors. Bye!")
    finally:
        # Building switched off: clear any fault so the BMS doesn't stay "broken"
        try:
            client.publish("simulation/fault", json.dumps({"room": None, "active": False}),
                           qos=1, retain=True).wait_for_publish(timeout=2)
        except (RuntimeError, ValueError):
            pass   # broker already gone, nothing to tidy
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
