# AriNav — user guide (night build)

All screenshots are from the real app on this branch (headless Chrome, software rendering).

## Start

```powershell
# offline (no keys needed: routing, map, first person, walking, HQ models)
powershell -File scripts/windows/Start-AriNav.ps1 -DatasetsDir <SyneraKit-data\dev>
# with Claude + ElevenLabs (keys registered once with Register-ApiKey.ps1, DPAPI-protected)
powershell -File scripts/windows/Start-AriNav.ps1 -Live -DatasetsDir <SyneraKit-data\dev> [-Profile immersive]
```

Open http://localhost:4321 (`?profile=core|immersive|showcase`). Pick a building in the panel
(Location tab). Buildings with a saved high-quality model show **HQ ready**.

## 1. Ask and get a route

Say where you are and what you need ("I'm in Office 0.38. Where can I get a coffee?"), then
"How do I get there?". The official route and step-by-step directions appear; the navigation card
on the right offers **Start navigation** and **Preview route** (the preview moves only the camera).

![Route with directions](scenes/S1-2-route.png)

## 2. Walk it in first person

**Start navigation** walks a simulated person along the route (speeds 0.5×–8×, short spoken
hints when voice is available). **First person** shows the eye-height view with the next step
and a direction arrow; the minimap stays in the corner. The badge says *Simulated walkthrough*:
nothing is measured.

![First person in the corridor](scenes/S1-4-walking-first-person.png)

![Arrived](scenes/S1-5-arrived.png)

## 3. Accessibility is binding

Say "I use a wheelchair": every route stays step-free. If the only lift is closed, AriNav says
there is no step-free way upstairs (never a stairs fallback) and offers alternatives. Closing or
reopening anything needs your confirmation.

![No step-free route while the lift is closed](scenes/S2-2-no-step-free-route.png)

## 4. Walk yourself and ask what is around you

**Walk myself** (or **First person** after telling AriNav where you are): WASD / arrow keys,
drag to look, Shift to walk faster; on touch screens a joystick appears. Walls stop you; doors let
you through. Ask "What's on my right?" — the answer uses your position and view direction and
never claims to see through walls.

![What's on my right](scenes/S3-3-whats-on-my-right.png)

## 5. On a phone

The layout keeps the view centre free in portrait and landscape; the navigation card folds to
status and controls in first person. **Phone rotation** asks for permission, then turns the view
with the phone (rotation only, never position); **Calibrate** re-aligns it. Not yet tested on a
physical phone.

![Phone portrait, first person](e2e/M01-portrait-390x844-pov.png)

## 6. High-quality models and honesty about them

Saved HQ models load by building id and are never regenerated on open; **Rebuild** in the
processing dialog always runs. If a thing was moved after the build, the chip says
**HQ outdated · showing LQ** and the live view (which matches routing) is shown.

![HQ outdated](e2e/HQ-Q05-outdated-1440.png)

## 7. Session recap (showcase profile)

**Remember session** (opt-in) records only real navigation (previews do not count). **Session
recap** shows places reached, distance walked and floors; export and delete are one click.
Demo sessions are labelled synthetic.

![Session recap](e2e/H01-recap-1280.png)
