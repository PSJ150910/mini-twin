# mini-twin
A pretend smart office: live sensors, a building control system, repair records and an AI chat, all connected in Node-RED

## Floor-plan screen (Task 5)
1. Start Node-RED, then `python run_all.py` (add `--fault room3` to see an alarm).
2. In Node-RED, import `node-red/floorplan-flow.json` and press Deploy (once only).
3. Open http://localhost:1880/floorplan

Rooms are blue below 20 °C, green from 20 to 25 °C and red above 25 °C.
Click a room to see its live readings, its air machine (BMS) and its repair jobs (CAFM) together.
The page reads the combined room data from http://localhost:1880/twin every 3 seconds.
