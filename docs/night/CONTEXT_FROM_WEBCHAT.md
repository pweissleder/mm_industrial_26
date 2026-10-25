<!-- Imported unchanged from ARINAV_NIGHT_HANDOFF_2026-10-25.zip (02_KONSOLIDIERTER_PROJEKTKONTEXT.md). Context, not a task list. -->
# Konsolidierter Projektkontext aus dem Webchat
Stand 25.10.2026. Zweck: keine wesentlichen Entscheidungen nur im alten Chat verlieren. Dies ist Kontext, kein konkurrierender Aufgabenplan.

## Menschen und Arbeitsweise
Noah Seifert und Deyan (Maschinenbau), zwei Informatik-Masterstudenten; Pascal verantwortet wesentliche AriNav-UI/3D-Arbeit, Alex Graph-/Datenstruktur. Details menschlicher Zuständigkeiten im Team prüfen, nicht ungefragt fest zuweisen.

Noah möchte natürlich per Speech-to-Text kommunizieren. Fragen, Ideen, Kritik und Wiederholung sind nicht automatisch neue Implementierungsaufträge. Klare reversible Aufträge autonom ausführen; bestehende Entscheidungen erhalten. Technische Details müssen nicht vollständig vom Nutzer gelesen werden. Ein Hauptagent in Claude Code, gezielte disjunkte Subagenten, kurze deutsche Berichte, englisches Produkt. Codex ist optional unabhängiger QA, nicht parallele zweite Implementierungsleitung. `WORKPLAN.md` einzige aktuelle Statusquelle, historische Anhänge klar abgrenzen. Webchat ist Entscheidungs-/Reviewhilfe, kein nötiger Vermittler jeder Änderung.

## Ziel und Challenge
Industrial Agents Hackathon München, 24.–25.10.2026. Track Synera, AriNav / Talk to Your Building. FIND, ROUTE, EDIT, REACT; mehrgeschossige Navigation, Übernahme strukturierter neuer Gebäude, Gesprächsinteraktion. Gewichte aus bereitgestelltem Briefing: 55 Funktion, 25 Interaktion, 10 Kreativität, 10 Demo. Die konkrete Build-Level-4-Bewertung wurde angefragt und ist in diesem Chat nicht bestätigt. Nicht zusätzlich eine Bild-/PDF-Erkennung als sicher geforderte Aufgabe erfinden.

Datensatz: 280 Gebäude (200 Train, 40 Dev, 40 Test). Bereits strukturierte building.json mit Räumen/Polygonen/Etagenhöhen, openings, vertical_connectors, assets. equipment.csv flacht Assets ab. PNG/SVG sind überwiegend Darstellung, kein Ersatz für JSON. Fünf Testfamilien: prozedurale Fabrik, prozeduraler Campus, OSM, ResPlan, Swiss (vier Ursprungsquellen). Für diese Daten kein LLM pro Gebäudeklassifikation notwendig.

655 Train/Dev-Benchmarkfragen (517+138), neun deterministische Typen: largest_room, floor_area_total, errand, wide_delivery_count, step_free_reachable_count, room_stacked, shared_walls_count, straight_line_3d, what_if_door. 1338 Warm-up-Routen, 553 Locate, 239 Count. Antworten aus Testsplit nicht zum Tuning lesen/hartkodieren. Das Einlesen von structured inputs in Werkzeugtests ist kein Nachweis freier Sprache.

Offizielles Kit 1.1.0, zuletzt verifiziert dc03c447dbe5b2e5f82c7db0d72249931817d86d. Referenz bleibt gepinnt; Neuigkeiten gezielt prüfen, kein blindes Upstream-Update nachts.

### Zwei unterschiedliche Routingverträge
Official: Möbel/Maschinen ignoriert; Raum-/Tür-/Openinggraph mit Tür-zu-Tür-Laufweg, nominal 15 cm Wandabstand, 30-cm-Türlandung gemäß konkreter Referenz; Treppe 2×Höhendifferenz, Lift 15+Höhendifferenz einmal pro Fahrt; keine Nullkosten-Außenabkürzung. Referenzimplementierung/Scorer und Artefakte sind maßgeblich für Benchmarkkosten. Diese Zahlen sind nicht einfach reale Reisezeit oder tatsächlich gelaufene XY-Meter.

Precision: zusätzlicher Produktnutzen, echte Modellpolygone/Footprints und konfigurierbare Körperbreite/Sicherheitsabstände, Zugang vor Maschine, kontrolliertes Snapping, alternative Portalquerungen. Noch nicht jede Datenquelle unterstützt; teilweise schematische Vertikale. Ein gültiger berechneter Modellpfad ist keine reale Sicherheitszertifizierung. Raumzonen sind nicht überall vermessene Wände. Keine zusätzliche Tür nur aufgrund optischer Nähe erfinden.

Discord nannte zusätzliche Produktionszonen-/Forkliftregeln, die nicht vollständig im Kit abgebildet waren; zuletzt opt-in/als nicht im Kit markiert. Keine eigenmächtige neue autoritative Klassifikation. Rollstuhl-/step-free-Wünsche sind im Produkt harte bestätigte Nutzerconstraints.

### Referenzergebnisse
Laut aktueller Phase-B-Guide: 349 Backendtests, 4 übersprungen; Frontend72; guidance_browser14/14; Phase-A-browser10/10; QA dialog20/20; N01–N03 korrigiert; Referenz655/655,1338/1338,553/553,239/239. Das sind gemeldete beziehungsweise in früherer unabhängiger QA reproduzierte Werte; im Nachtstand erneut korrekt binden. Nicht „100% alle Sprache/Bilder/Gebäude“ sagen.

Die unabhängige Phase-A-QA hatte 8 Ursachen: gelöschter Standort im Assistant (D1), drei serialisierte Vertikal-/Waypointfehler (N01–03), fehlender Inspect-Kontext (D2), verworfener exakter Punktstart (D3), veraltete Route (U1), veraltete Preview (U2). Phase B meldet Fixcommit a5de921. Original-QA ist als Regression nützlich, kein Beweis weiterhin offener Fehler.

## Repositories und Architektur
- Teamrepo pweissleder/mm_industrial_26, privat. Webchat-GitHub-Verbindung hatte wiederholt 404; lokale gh-Anmeldung war zeitweise verfügbar. Zugriff beim Arbeiten tatsächlich prüfen, nicht Erfolg erfinden.
- Früherer Funktionsbranch noah/unified-nav-voice-poc, u.a. dbf8d63.
- Phase A noah/phase-a-integration; ee2c219, später mit Team-main auf6884927 vereinigt.
- Phase B noah/phase-b-navigation vom Stand6884927; neueren HEAD lokal feststellen. Kein unaufgeforderter main-Merge.
- Arbeitsverzeichnisse unter C:\Users\noah3\Documents\Repositories\; Phase-A-Worktree mm_industrial_26-phase-a; Phase-B-Worktree unbekannt, git worktree list.
- Historische IndustrialAgentsHackathon/.private-Prototypen und SyneraVoicePOC erhalten, nicht neue konkurrierende Produkte bauen.
- Team-Frontend Astro/AriNav mit bestehendem 3D-Renderer; Backend FastAPI/Python. Zwei Graph-/Routerimplementierungen existierten. NavigationService/BuildingRepository und gemeinsames Szenario sind maßgeblich. Chat-/Overlay-Wege nicht widersprüchlich machen; keine große Konsolidierung aus Vollständigkeitsdrang.

Phase B nennt als vorhandene Module GuidanceSim, useGuidance, GuidancePanel, scene/Viewer3D, guidanceVoice, assistant/speak, exact_start im NavigationService und usePipeline. Bestehende Preview, Autowalk, Pause/Resume, räumliche Übersicht, Szenario-Polling alle2s und Audioqueue verwenden.

## Voice und API
Claude Messages API/Tool Use mit strikten Schemas, begrenzter Schleife, gemeinsamen Funktionen, Rückfragen, Zustand und bestätigten Änderungen. ElfLabs STT/TTS funktionsfähig; Produktmodell lokal Sonnet5.5 Medium im letzten Vergleich am zuverlässigsten. Keine neuen Modellbezeichnungen aus diesem Kontext als aktuelle Preis-/Verfügbarkeitsgarantie ableiten.

ElevenLabs Creator-Coupon, Auto Top Up aus; Nutzer berichtet ausreichend Credits. Vorhandene lokale DPAPI-Key-Skripte in SyneraVoicePOC; keine Keys im Paket. Claude Development-Workspace früher50USD, Speech5USD. Jetzt **60USD maximal zusätzlich insgesamt** für diesen Auftrag freigegeben; verfügbare tatsächliche Limits beachten. API arbeitet in CLI; Desktop verweigerte konkret das Überschreiben von ANTHROPIC_API_KEY. Nicht erneut diesen UI-Weg behaupten. Zugänge bleiben sicher lokal, kein neuer Service.

Softwaretests mit synthetischer Stimme und Browserbreite sind kein realer Mikrofon-/Handy-Sensornachweis. Latenzen lagen früher bei mehreren Sekunden Claude, Tools häufig Millisekunden; kein pauschales Performanceversprechen.

## Reale Nutzerbefunde und gewünschte Verbesserung
Screenshot reference/UI_OCCLUSION.png zeigt großen Chat über Karte plus Navigationsoverlay/mehrere Regler. Root soll echte Renderbilder bewerten, nicht DOM grün=UX gut.

Noah verlangt keine allgemeine UI-Neuentwicklung (Kollegen tun das), sondern funktionierende POV-/Navigationsansicht, kompakte Spracheingabe, Gesamtweg sichtbar und mobile Bedienbarkeit. Find→Raumhighlight→Routenvorschau→Start→POV/Übersicht→Ankunft. Game-Controls, frei umschauen, mobile Drehung mit sich änderndem Pfeil. Perspektive und Standort nicht verwechseln. Simulierter Kunde und Developer-Gesamtansicht teilen Pose.

Handtrockner-Ortsvermutung darf Orientierung helfen; keine gemessene Position behaupten. Ausgang muss an Tür enden. „The biggest one“ benötigt Kontext oder Rückfrage. Keine rohen step-budget-errors/Typausprobieren opening als asset dann space. Kaffee-/Toilettensuche funktioniert grundsätzlich, Antworten natürlich statt technische IDs. Scherzfragen dürfen ohne Tool beantwortet werden. Zwei Sätze ist Normalfall, kein hartes Verbot sinnvoller Erklärung.

Wichtigster zuletzt gemeldeter negativer Fall: Rollstuhlnutzer bekommt bei kaputtem Lift Treppenempfehlung. Als durchgehenden Regressionstest prüfen – Backend, Gespräch, Anzeige, Reroute und Audio.

## Aktuelle Freigaben und Grenzen
Nutzer will einen umfassenden autonomen Nachtlauf innerhalb Budget, keine starre90min-Grenze. Module einzeln nutzbar und abschaltbar; keine aufgeblähte Plattform. Bestehenden Core bewahren. Kein OpenStreetMap/Outdoor-Neubau (vorhandene OSM-Indoor-Daten weiter unterstützen), keine generative HQ-Massenproduktion, keine behauptete reale AR-/Sicherheitsnavigation.

HQ: zwei bis drei repräsentative Gebäude, vorhandene Assetbibliothek, deterministischer Aufbau, dauerhaft unter Gebäude-ID speichern, bewusster Rebuild. **Keine Hashlogik fürs Caching.** Guide meldet noch Alt-SHA-Guard bei explizitem Rebuild und gebackene veraltete Möbel; gezielt auflösen, nicht gesamten Integritätsmechanismus anderswo entfernen.

Memory/Recap neu: lokale freiwillige Nutzungs-/Zielhistorie, nachvollziehbare Fakten, kleine Rückblickskarte, optional Clip. Tatsächliche Bewegung statt geplante Routen zählen; Demo/Tests markieren, private Rohdaten nicht verteilen.

## Abgabegrundlage
Bekannter Participant Guide: Sonntag25.10.2026,13:00 Europe/Berlin; DST-Rückstellung in dieser Nacht. 1-min Demo,1-min Technikvideo, Architektur-/Integrationsnotizen, Pitchfolien; kein autonomes Absenden. Startvorgaben/Portal durch Team prüfen. 145 veröffentlichte Testfragen: UTF-8-CSV question_id,answer mit letzter Answer:-Zeile. Zusätzlicher Sonntagsfall und Level4-Details nur nach tatsächlicher Veranstalterantwort als bekannt behandeln; kein JSON-/CSV-Vorrang für unbekannten Sonderfall erfinden.

Abgabeentwürfe aus diesem Paket sind Arbeitsmaterial, keine fertigen Behauptungen. Videos müssen reales Programm zeigen; Simulation als Simulation kennzeichnen. Echte Raum-/Routefunktion nicht durch generativen Film faken. Architekturtext muss tatsächliche Integration, Kosten, Tests und Grenzen zeigen.

## Finale Übergabe
Am Ende Ergebnis, gepflegter Workplan, Bedienung, API-/Feature-Schnittstellen, Testmatrix, bekannte Fehler, geeignete Modulprofile und Media-Dateien. Neues Webchat-ZIP + eigenständiger Startprompt erstellen; fehlende Inputs/Device-Tests ehrlich nennen. Dieses Dokument enthält relevante Entscheidungen, ersetzt nicht Quellcode oder aktuelle Tests. Keine persönlichen BA-Dokumente oder vollständige alte Chats übertragen.
