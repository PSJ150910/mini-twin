"""
Mini Twin - start the whole pretend building in one window

Instead of opening four PowerShell windows, this starts:
    bms_sim.py      the pretend air machines (BACnet)
    cafm_api.py     the pretend repair logbook (web API)
    bms_gateway.py  the BACnet -> MQTT translator
    sensors.py      the pretend room sensors
and shows all their messages here, each line labelled with who said it.

Node-RED must already be running (it holds the MQTT broker).

Run it with:   python run_all.py
Demo a broken room straight away:   python run_all.py --fault room3
Stop everything with:  Ctrl + C
"""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).parent
PROGRAMS = [
    ("BMS    ", ["bms_sim.py"]),
    ("CAFM   ", ["cafm_api.py"]),
    ("GATEWAY", ["bms_gateway.py"]),
    ("SENSORS", ["sensors.py"] + sys.argv[1:]),   # passes on --fault roomN
]


def relay(label, proc):
    for line in proc.stdout:
        print(f"[{label}] {line.rstrip()}", flush=True)


def main():
    running = []
    for label, args in PROGRAMS:
        proc = subprocess.Popen(
            [sys.executable, "-u", *args], cwd=HERE, text=True, encoding="utf-8",
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        threading.Thread(target=relay, args=(label, proc), daemon=True).start()
        running.append(proc)
        time.sleep(2)          # give each one a moment before starting the next
    print("\nEverything is running. CAFM test page: http://localhost:8000/docs")
    print("Ctrl + C stops it all.\n")
    try:
        while all(p.poll() is None for p in running):
            time.sleep(1)
        print("One of the programs stopped, so stopping the rest.")
    except KeyboardInterrupt:
        time.sleep(3)          # Ctrl + C reaches every program; let them tidy up first
    for p in running:
        if p.poll() is None:
            p.terminate()
    for p in running:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
    print("All stopped. Bye!")


if __name__ == "__main__":
    main()
