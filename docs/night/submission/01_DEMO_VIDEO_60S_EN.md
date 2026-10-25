# AriNav — Demo video draft (60 seconds)
Working title: **Ask. See. Navigate.**

Real application capture; use one supported building and a valid route. On-screen badge during movement: **Simulated indoor walkthrough**. Demonstrate only completed features.

| Time | Actual image to capture | Spoken/message content |
|---|---|---|
|0–6 s|Building overview, calm camera, microphone/text prompt|“Finding your way inside an unfamiliar building should be as simple as asking a question.”|
|6–15 s|Ask “Where can I get coffee?”; highlight a real matching destination|“AriNav turns a building's structured plans into a place you can explore by conversation.”|
|15–24 s|Show the verified route and optional short preview|“It finds the destination, checks your route and shows where to go—across rooms and floors.”|
|24–40 s|Press Start; actual POV/manual movement; compact guidance|“Preview the journey, then follow it in a simulated first-person view. Look around and keep the next direction in sight.”|
|40–51 s|One real confirmed closure and reroute, only if stable; otherwise show accessible route choice|“When conditions change, the application rechecks the route instead of quietly ignoring your constraints.”|
|51–60 s|True arrival, optional verified recap card, title|“Your position, the map and the assistant stay connected. AriNav: ask, see, navigate.”|

If POV or reroute is not ready, substitute a real working route overview/manual interaction. Do not make a pre-rendered imaginary path appear to be app execution. Speech may be edited to fit actual runtime; export and verify duration, audio, readability and labels.

Optional recap caption, only from actual demo events: “Your session, at a glance.” No invented visited-room or distance counts.

## Final cut (night build) — `arinav_demo_60s.mp4`

Built by `scripts/media/build_videos.py` from real app recordings/screenshots; file outside Git in `HackathonTransfer/Night_2026-10-25/media/`. Narration as subtitled:

- AriNav turns a building file into an indoor navigator you can talk to.
- I say where I am and ask for a coffee. It finds the nearest machine, about twenty metres away.
- One more question, and the official route appears, with step by step directions.
- I start the walkthrough: a guidance card, short spoken hints, and a first person view at eye height. It is a labelled simulation, not live positioning.
- And I have arrived.
- Now the only lift is closed. For a wheelchair user, AriNav says honestly that there is no step free way upstairs. It never falls back to the stairs.
- Instead it offers a step free destination on the same floor.
- I can also walk myself, with the keyboard or a touch joystick. Walls stop me.
- Asked 'What's on my right?', AriNav answers from the geometry around the person's position and view direction, and never claims to see through walls.
- Grounded in the data. Accessible by default. Honest about what it knows.
