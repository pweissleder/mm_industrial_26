# Nachtauftrag 25.10.2026 – Ergebnis (Claude Code Root)

Branch `noah/night-immersive` (Worktree `Repositories/mm_industrial_26-night`, ab `86d0ca3` = main =
Phase B). Phase-B-Worktree unverändert. **Kein Merge nach main, keine Veranstalterabgabe.**
Belege: `docs/night/TEST_SUMMARY.md`, Fähigkeiten/Module: `docs/night/CAPABILITIES.md`,
Guide (EN): `docs/night/GUIDE_EN.md`.

## Empfohlenes Profil

**Immersive** (Standard): Core + Navigationskarte + Ich-Perspektive + selbst gehen + Handy-Drehung.
Core bleibt allein lauffähig (A02 geprüft), Showcase = Immersive + Sitzungs-Recap für Aufnahmen.

## ERLEDIGT

- **P0 Rollstuhl/Lift:** step-free wird nur verschärft, nie gelockert; kein Treppen-Fallback;
  Constraints in jeder Route zurückgemeldet; Reroute behält sie. Offline, live und im Browser-Szenario.
- **Immersive Navigation:** POV auf 1,65 m, Minikarte, Developer-Split neben dem Panel, WASD/Pfeile/
  Ziehen, Pointer-Lock nur per Doppelklick, Touch-Joystick nur bei Touch, ein Fahrer (Auto/Manuell),
  Kollision mit 0,05-m-Teilschritten, Off-Route-Replan ohne Teleport, Handy-Drehung mit
  Erlaubnis-Button/Kalibrierung/ehrlichem Status.
- **Räumliche Assistenz:** „Was ist rechts von mir?“ aus Pose + Blickrichtung (Sektoren + Wandseite
  für Türen weiter vorn), Verdeckung, Rückfrage ohne Blickrichtung; Leck → Raum direkt darunter.
- **HQ-Showcase:** 3 Gebäude offline/deterministisch (je ~6 s), gespeichert unter Gebäude-ID,
  Öffnen ohne Neugenerierung, Rebuild läuft immer, verschobenes Asset → „HQ outdated · showing LQ“.
- **Memory/Recap** (opt-in, Showcase): nur echte Bewegung zählt, Demo als synthetisch markiert,
  Export/Löschen/Neue Sitzung.
- **Tests:** Harness Basis+Ende 655/1338/553/239; 140 Seed-Fälle + 600 weitere Seeds; 54
  Bewegungsfälle + 990 Stress; 60 Offline-Dialogszenarien; 20 Live-Dialoge (19/20, 7/7 Held-out);
  Backend 444, Frontend 173; Browser 14/14 + 14/14 + 10/10 + 8/8; 3 Live-Szenen mit Video.
- **Gefundene und behobene Defekte (18):** Orientation-Listener verlor Events; POV auf Handy/1000 px
  verdeckt; Split-Hälfte hinter Panel; HQ-Wände in POV ~1 m; Raum-Highlight färbt POV; rohe ID in
  Routenhinweis; HUD hinter Chatkarte; Selbst-Gehen ignorierte genannten Standort; Routenpfad bei
  Etagen-Umweg (60 m statt 31,85 m, durch Wände); breite Türen ließen durch Wände; Fahrstuhl-Landung
  außerhalb konkaver Räume; Assistent: Punkt außerhalb Raum, Breite bei „nächster“ ignoriert,
  unbekannte Quelle als „manual“, veraltete Quellen-Labels, Pose außerhalb Raum, Route von anderem Ort
  verschob Nutzerposition; Recap-Label „Simulated time“ war Uhrzeit.
- **Medien** (außerhalb Git, `C:\Users\noah3\Documents\HackathonTransfer\Night_2026-10-25\media\`):
  `arinav_demo_60s.mp4` (58,7 s, + `.srt`), `arinav_tech_60s.mp4` (57,9 s, + `.srt`),
  `AriNav_pitch.pptx` (5 Folien mit Notizen); Rohaufnahmen `media\raw\`. Abspielbarkeit geprüft
  (vollständig dekodiert, keine Schwarzbilder, Ton vorhanden, Beschleunigungen im Bild markiert).
- **Abgabeentwürfe:** `docs/night/submission/03_ARCHITECTURE_INTEGRATION_EN.md` mit echten Werten.

## Nachtrag 25.10. Vormittag (Fortsetzung nach API-Limit)

Fertiggestellt und geprüft (Details `TEST_SUMMARY.md`, Abschnitt *Follow-up*):
- **Selbst gehen nur im Developer-Modus**, Geschwindigkeit 1–8 m/s (Standard 3, Shift ×2,
  lokal gemerkt). User-Ansicht: Ich-Perspektive ohne Gehsteuerung, ohne WASD-Hinweis und ohne
  Treppen-Teleport (vorher noch sichtbar – behoben).
- **Handy:** `Start-AriNav.ps1 -Phone` (HTTPS mit Wegwerf-Zertifikat im LAN), Status
  „Rotation needs HTTPS“ auf http. Kein Test auf echtem Handy.
- **Set start:** ersetzt die gelaufene Position (auch derselbe Start erneut); lokale Routen,
  Sperr-Vorschau und Notausgang starten wie der Assistent an der gelaufenen Position (vorher
  konnte die lokale Route am alten Start beginnen – behoben).
- **Abbiegehinweise an Türen** (Backend `directions`, Frontend-Karte): links/rechts/umdrehen nur
  bei klarer Geometrie, sonst unveränderter Text.
- **Sprechtext:** Einheiten ausgeschrieben („16 metres“). Ein Schreibfehler des abgebrochenen
  Laufs (Steuerzeichen statt `` im Regex) hätte Satzzeichen durch ein Steuerzeichen ersetzt – behoben,
  Regressionstest ergänzt. ElevenLabs antwortet wieder mit HTTP 200.
- **Fly-through** (4 Phasen): Drohnenphase lag unter der Decke des Obergeschosses – Etagen
  oberhalb der überflogenen werden während des Flugs ausgeblendet. HQ-Rendering nicht verändert.

## BELEG

`docs/night/TEST_SUMMARY.md` (alle Zahlen + Befehle), `docs/night/e2e/*.json|png`,
`docs/night/scenes/*`, `docs/night/tests/*`, `docs/reports/dialog_sim_night_live_v1|v2.md`.

## Nur Simulation / Experiment / nicht getestet

- Positionen sind simuliert oder genannt, nie gemessen. Etagenwechsel schematisch.
- Handy-Drehung und Mikrofon: **NOT_TESTED** auf echter Hardware (synthetische Events grün).
- Bildzeiten nur mit Software-Rendering (SwiftShader) gemessen; keine Hardware-Aussage.
- Kamera-Lokalisierung: nicht gebaut (Budget/Zeit, bewusst ausgelassen, 0 USD).

## BLOCKER

- ~~ElevenLabs-Kontingent erschöpft~~ – am 25.10. vormittags wieder HTTP 200 (Limit erhöht). Die
  in der Nacht gebauten Demo-Videos haben in den letzten zwei Segmenten weiterhin keinen Sprecher.

## Budget

Produkt-Live-Kosten heute Nacht (Anthropic, aus Nutzungsangaben der Läufe): Live-Dialoge 2 × 0,30 $
= 0,61 $; Szenenaufnahmen geschätzt ≈ 0,5 $ (≈ 40 Assistant-Züge); Bild-Experiment 0 $.
Summe Produkt nachts ≈ 1,1 $ (Grenze 6 $); mit früheren Runden (≈ 2,95 $) ≈ 4,1 $.
Token-Kosten von Root und Subagenten (Claude Code selbst) sind von innen nicht messbar – bitte im
Anthropic-Dashboard prüfen; die 45-$-Schwelle für optionale Module wurde nach eigener Schätzung
nicht erreicht, ist aber unbelegt.

## NÄCHSTER SCHRITT

1. Entscheidung Noah (siehe unten). 2. Echttest Handy (Drehung/Kalibrierung) + Mikrofon.
3. ElevenLabs-Kontingent erneuern oder Stimme festlegen. 4. Draw-Calls des Campus-HQ reduzieren
(Instancing). 5. Danach Abgabe durch Noah.

**Eine Entscheidung für Noah:** Soll `noah/night-immersive` (Profil *Immersive* als Standard) nach
`main` gemergt werden?
