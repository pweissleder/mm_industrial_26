# Indoor positioning on smartphones — recommendation (concept only)

Nothing here is implemented beyond what the app already does (map click / room selection).
No BLE, UWB, Wi-Fi fingerprinting or mobile tracking is built.

## Recommended order

1. **Manual choice on the map (now).** The user taps a room or a point; the app sets the start.
   Already works: map click → *Set as start*, room dropdowns, or telling the assistant
   ("I'm in the quality lab"). Precision routing may move a blocked point ≤ 1 m inside the
   same room and says so. Cheap, transparent, works everywhere.
2. **Known room / door ID or QR code (next, low effort).** A QR sticker at each door or room
   sign encodes a stable ID, e.g. `synera://locate?building=proc-factory-dev-003&opening=F0-O0026&side=F0-S021`.
   Scanning sets the start to the door's landing on that side (official 30 cm landing or the
   precision door approach). Accuracy = the door itself; no infrastructure beyond printed codes;
   the ID must survive building edits (use dataset IDs, never coordinates).
3. **Optional automatic positioning (later).** Any provider (BLE beacons, UWB, Wi-Fi RTT,
   visual markers, phone AR anchors) only delivers a *position estimate with uncertainty*;
   the backend snaps it to a room conservatively and asks the user when ambiguous.
   Never route from an estimate whose uncertainty circle spans several rooms without asking.

## Lean backend interface (future)

```http
POST /api/location/resolve
{ "building_id": "...",
  "source": "map_click" | "room_id" | "door_qr" | "estimate",
  "space_id": "...",                       // map_click / room_id
  "opening_id": "...", "side_space_id": "...",   // door_qr
  "position": [x, y], "floor_id": "...",   // map_click / estimate, building XY frame in metres
  "accuracy_m": 3.0,                       // estimate only
  "observed_at": "2026-10-24T18:00:00Z" }
→ 200 { "status": "resolved" | "ambiguous" | "outside_building",
        "endpoint": {"kind": "space"|"door"|"point", ...},   // directly usable as route start
        "candidates": [{"space_id": "...", "name": "...", "probability": 0.7}],
        "explanation": "Inside Quality lab 0.21 (door QR)." }
```

Rules: the result is an ordinary route endpoint, so both routers stay unchanged; `ambiguous`
returns candidates for a one-tap/voice confirmation; estimates older than ~30 s or with
`accuracy_m` above the room size are rejected; positions are not stored (no tracking history)
unless a later privacy decision allows it.
