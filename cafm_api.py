"""
Mini Twin - pretend repair logbook / CAFM (Task 3, part B)

Facilities teams keep a "CAFM" system (Computer-Aided Facilities Management):
a digital logbook of every piece of equipment (the "assets") and every repair
job (the "tickets"). Think of it as the building's medical records.

This program is a tiny pretend CAFM. Other programs ask it questions over the
web, using a "REST API": you visit an address like
    http://localhost:8000/api/tickets?status=Open
and it answers with a list in JSON (structured text that computers read
easily). It's like a library counter: you ask for something by name, the
librarian hands it over.

The CAFM uses ITS OWN names: rooms are "Meeting Rm 01" and machines are
"AST-1003". Different from the sensors and the BMS, on purpose (see Task 4).

Pretend helpdesk: when sensors.py makes a room overheat, a pretend person
"phones the helpdesk" about 15 seconds later (15 pretend minutes) and a new
ticket appears. When the fault clears, the engineer closes the ticket.

Run it with:   python cafm_api.py
Then open:     http://localhost:8000/docs   (a free, clickable test page)
Stop it with:  Ctrl + C
"""

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

import paho.mqtt.client as mqtt
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# ---------------- Settings you can change ----------------
PORT = 8000
BROKER_HOST = "localhost"
BROKER_PORT = 1883
HELPDESK_DELAY_SECONDS = 15
# ----------------------------------------------------------

DATA = json.loads((Path(__file__).parent / "cafm_data.json").read_text(encoding="utf-8"))
LOCATIONS = {loc["location_code"]: loc for loc in DATA["locations"]}
ASSETS = {a["asset_id"]: a for a in DATA["assets"]}
TICKETS = DATA["tickets"]           # kept in memory: restarting resets the logbook
lock = threading.Lock()             # stops two updates clashing at the same moment

# Behind-the-scenes only: which sensor room each air machine sits in.
# The real API never shows this; working it out properly is Task 4's job.
SIM_ROOM_TO_ASSET = {
    "room1": "AST-1001", "room2": "AST-1002", "room3": "AST-1003",
    "room4": "AST-1004", "room5": "AST-1005", "room6": "AST-1006",
}

app = FastAPI(
    title="Mini Twin CAFM (pretend repair logbook)",
    description="Assets, locations and maintenance tickets for the Mini Twin office.",
    version="1.0",
)


def now():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def with_location(asset):
    """Add the friendly location name to an asset, like a real CAFM screen would."""
    loc = LOCATIONS.get(asset["location_code"], {})
    return {**asset, "location_name": loc.get("name"), "floor": loc.get("floor")}


# ------------------------------ the API ------------------------------

@app.get("/")
def welcome():
    return {
        "system": "Mini Twin CAFM",
        "try": ["/docs", "/api/locations", "/api/assets", "/api/tickets?status=Open"],
    }


@app.get("/api/locations")
def list_locations():
    return list(LOCATIONS.values())


@app.get("/api/assets")
def list_assets(category: Optional[str] = None, location_code: Optional[str] = None):
    items = [with_location(a) for a in ASSETS.values()]
    if category:
        items = [a for a in items if a["category"].lower() == category.lower()]
    if location_code:
        items = [a for a in items if a["location_code"] == location_code]
    return items


@app.get("/api/assets/{asset_id}")
def get_asset(asset_id: str):
    if asset_id not in ASSETS:
        raise HTTPException(404, f"No asset called {asset_id}")
    with lock:
        history = [t for t in TICKETS if t["asset_id"] == asset_id]
    return {**with_location(ASSETS[asset_id]), "tickets": history}


@app.get("/api/tickets")
def list_tickets(status: Optional[str] = None, asset_id: Optional[str] = None):
    with lock:
        items = list(TICKETS)
    if status:
        items = [t for t in items if t["status"].lower() == status.lower()]
    if asset_id:
        items = [t for t in items if t["asset_id"] == asset_id]
    return items


@app.get("/api/tickets/{ticket_id}")
def get_ticket(ticket_id: int):
    with lock:
        for t in TICKETS:
            if t["ticket_id"] == ticket_id:
                return t
    raise HTTPException(404, f"No ticket number {ticket_id}")


class NewTicket(BaseModel):
    asset_id: str
    title: str
    priority: str = "P3"
    type: str = "Reactive"
    reported_by: str = "API"


@app.post("/api/tickets", status_code=201)
def create_ticket(new: NewTicket):
    if new.asset_id not in ASSETS:
        raise HTTPException(404, f"No asset called {new.asset_id}")
    return add_ticket(new.asset_id, new.title, new.priority, new.type, new.reported_by)


class CloseTicket(BaseModel):
    resolution: str


@app.post("/api/tickets/{ticket_id}/close")
def close_ticket(ticket_id: int, body: CloseTicket):
    with lock:
        for t in TICKETS:
            if t["ticket_id"] == ticket_id:
                t.update(status="Closed", closed=now(), resolution=body.resolution)
                return t
    raise HTTPException(404, f"No ticket number {ticket_id}")


def add_ticket(asset_id, title, priority, kind, reported_by):
    with lock:
        ticket = {
            "ticket_id": max(t["ticket_id"] for t in TICKETS) + 1,
            "asset_id": asset_id,
            "type": kind,
            "priority": priority,
            "title": title,
            "status": "Open",
            "reported_by": reported_by,
            "assigned_to": "CoolTech Engineering",
            "opened": now(),
            "closed": None,
            "resolution": None,
        }
        TICKETS.append(ticket)
    print(f"  ** New ticket #{ticket['ticket_id']} for {asset_id}: {title} **")
    return ticket


# ------------------- the pretend helpdesk (behind the scenes) -------------------

current_fault = {"room": None}


def phone_helpdesk(room):
    if current_fault["room"] != room:
        return                      # fixed before anyone called
    asset_id = SIM_ROOM_TO_ASSET[room]
    location = LOCATIONS[ASSETS[asset_id]["location_code"]]["name"]
    add_ticket(asset_id, f"{location} too hot - air con not working", "P2",
               "Reactive", "Helpdesk call")


def engineer_fixes(room):
    asset_id = SIM_ROOM_TO_ASSET[room]
    with lock:
        for t in TICKETS:
            if (t["asset_id"] == asset_id and t["status"] == "Open"
                    and t["reported_by"] == "Helpdesk call" and t["priority"] == "P2"):
                t.update(status="Closed", closed=now(),
                         resolution="Engineer attended. Unit reset and tested OK.")
                print(f"  ** Ticket #{t['ticket_id']} closed: {asset_id} fixed **")


def on_connect(client, userdata, flags, reason_code, properties):
    client.subscribe("simulation/fault")


def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload)
    except ValueError:
        return
    new_room = data.get("room") if data.get("active") else None
    old_room = current_fault["room"]
    if new_room == old_room:
        return
    current_fault["room"] = new_room
    if old_room:
        engineer_fixes(old_room)
    if new_room in SIM_ROOM_TO_ASSET:
        threading.Timer(HELPDESK_DELAY_SECONDS, phone_helpdesk, args=[new_room]).start()


def start_helpdesk():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="mini-twin-cafm")
    client.on_connect = on_connect
    client.on_message = on_message
    try:
        client.connect(BROKER_HOST, BROKER_PORT)
    except OSError:
        print("Note: no MQTT broker, so the pretend helpdesk is off. The API still works.")
        return
    client.loop_start()


if __name__ == "__main__":
    start_helpdesk()
    print(f"Pretend CAFM running. Open http://localhost:{PORT}/docs in your browser.")
    print("Ctrl + C to stop.\n")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
