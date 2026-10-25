<!-- Imported unchanged from ARINAV_NIGHT_HANDOFF_2026-10-25.zip (01_NACHTAUFTRAG_CLAUDE.md). Status lives in WORKPLAN.md. -->
# AriNav – autonomer Nachtauftrag: spielbare Navigation, mobile Orientierung, robuste Assistenz und Demo

## 0. Auftrag und Erfolg
Du übernimmst beziehungsweise behältst als **einziger leitender Claude-Code-Agent** die Weiterentwicklung der bestehenden AriNav-Anwendung. Ziel ist ein sichtbarer, benutzbarer Prototyp für die Teamentscheidung am Morgen – nicht eine möglichst große Codeänderung.

Die Navigation soll wie ein kleines Computerspiel bedienbar sein: durch das echte 3D-Gebäude gehen, umsehen, Ziele finden, Route ansehen, optional eine Kameravorschau abspielen, dann selbst oder automatisch navigieren. Auf einem geeigneten Smartphone verändert Drehen die Blickrichtung und den Richtungspfeil. Dazu ein kleiner lokaler Aktivitätsverlauf/Recap, echte Demoaufnahmen und vorbereitete Abgabeunterlagen.

**Arbeite modular:** Funktionierender Kern ohne neue Extras; darüber einzeln abschaltbare Fähigkeiten. Kein kompletter UI-Neubau, keine zweite Anwendung, keine neue Agentenplattform. Die Kollegen bearbeiten die allgemeine UI; du ergänzt abgegrenzte Navigations-/Simulationskomponenten und nötige lokale Korrekturen.

Zuerst sichtbare Zuverlässigkeit, dann Inszenierung. Ein bedienbarer Zwischenstand und Abgabeentwürfe entstehen früh, nicht erst nach dem letzten Experiment. Kein starrer 90-Minuten-Stopp; Ende bei erfüllten Zielen, tatsächlicher Budgetgrenze oder nicht autonom lösbarem externem Blocker. Keine Arbeit lediglich auf einen späteren Lauf verschieben, wenn sie im vereinbarten Umfang selbstständig ausführbar ist.

## 1. Bestehenden Stand übernehmen, nicht neu anfangen
Lies diesen Auftrag und `02_KONSOLIDIERTER_PROJEKTKONTEXT.md`. Danach gezielt die tatsächlichen Projektregeln, `WORKPLAN.md`, `docs/PHASE_B_GUIDE.md` beziehungsweise die real vorhandene Guide-Datei, Phase-A/B-Verträge und aktuelle Quellmodule.

Bekannte Arbeitsorte, **lokal verifizieren**:
- Teamrepo: `pweissleder/mm_industrial_26`.
- Hauptklon: `C:\Users\noah3\Documents\Repositories\mm_industrial_26`.
- Phase-A-Worktree: `C:\Users\noah3\Documents\Repositories\mm_industrial_26-phase-a`.
- Letzter dokumentierter Phase-B-Branch: `noah/phase-b-navigation`, begonnen von Team-main `6884927`; aktueller B-HEAD ist hier unbekannt.
- Historischer Setup-Ordner: `IndustrialAgentsHackathon`; alte Voice-Komponenten: `SyneraVoicePOC`. Sie sind keine zweite neue Implementierungsbasis.

Git-Worktrees, aktuelle Branches, schmutzige Dateien, laufende Server und Teamarbeit zuerst kurz prüfen. Keine fremden Prozesse stoppen, keine uncommittete Arbeit verlieren. Den jüngsten tatsächlichen Phase-B-Stand als Ausgangspunkt sichern. Eigener Nachtbranch/Worktree; die bisher startbare Version erhalten. Nicht blind vom alten main oder alten Prototyp starten. Neue Teamänderungen nur nach gezieltem Vergleich integrieren, keine pauschale Zusammenführung während Kollegen dieselben Dateien ändern.

Bereits dokumentiert: offizielles Kit 1.1.0, zwei Routingverträge, Alex' Graphstruktur, Pascals AriNav-Renderer, Claude-Text-/Voice-Agent und ElevenLabs. Nicht noch einmal dieselben Systeme bauen. Der bekannte Guide nennt `simulation.ts`, `useGuidance.ts`, `GuidancePanel.tsx`, `scene.ts`, `Viewer3D.tsx`, `guidanceVoice.ts`, `usePipeline.ts` und NavigationService; Pfade am echten Stand bestätigen.

Übernehme die relevanten neuen Webchat-Entscheidungen in `docs/night/CONTEXT_FROM_WEBCHAT.md` und diesen Auftrag in `docs/night/NIGHT_TASK.md`, sofern es kein passend benanntes bestehendes Dokument gibt. Kurzer aktueller Status nur oben in `WORKPLAN.md`; überholte STOP-/HOLD-Vermerke eindeutig als Historie behandeln. Keine zweite Aufgabenverwaltung.

## 2. Budget, Autonomie und Zugänge
Freigegeben sind **maximal 60 USD zusätzliche Anthropic-API-Nutzung insgesamt für diese Runde**, nicht 60 USD pro Worker. Das umfasst Claude-Code-Root, Subagenten/Reviewer und Produkt-Livetests, soweit sie aus den verfügbaren Console-Credits stammen. Tatsächlich verfügbare Restguthaben und Workspace-/Organisationslimits können niedriger sein; diese gelten zusätzlich. Der Development-Workspace hatte früher 50 USD, Speech 5 USD – keine automatische Erhöhung unterstellen.

- Vor Beginn vorhandene sichere Authentifizierung und zugängliche Verbrauchsanzeigen prüfen. Keine Schlüssel im Chat, Git, Browser, Logs oder Screenshots.
- Bestehende Console-/CLI-Abrechnung verwenden. Kein erneuter Versuch, den vom Desktop verwalteten `ANTHROPIC_API_KEY` zu überschreiben.
- Bestehende Usage-Erfassung weiterverwenden. Root und Worker müssen ihren Verbrauch in dieselbe Budgetübersicht liefern; keine neuen Kostenplattformen. Wenn Console-Werte nicht zugänglich sind: konservative Tokenkostenschätzung, Quelle/Lücken nennen und Reserve behalten. Nicht fälschlich einen Prompt als technischen Hard-Cap ausgeben.
- Bei ungefähr **45 USD** keine neuen optionalen Module mehr anfangen; verbleibenden Rahmen für Stabilisierung, Medien und Übergabe reservieren. Nicht zwingend das gesamte Budget ausschöpfen.
- Produkt-Livetests als Teilbudget zunächst höchstens 6 USD; optionales bildbasiertes Lokalisierungsexperiment höchstens 3 USD. Beides zählt in die 60 USD. Entwicklung nicht durch Live-Dauerschleifen verteuern.
- Vorhandene ElevenLabs-Credits dürfen sinnvoll für Tests und Demo genutzt werden; kein neues Abo/Auto-Top-up. Vorhandene Antworten/Audio bei identischem Inhalt wiederverwenden. „Reichlich Guthaben“ ist kein Grund für endlose Synthese.
- Bei Auth-/Quota-/Netzwerkfehlern Ursache prüfen, keine blinden Wiederholungen. Ein optionales blockiertes Modul deaktivieren und mit unabhängig möglicher Arbeit fortfahren.
- Höchstens zwei klar getrennte schreibende Subagenten gleichzeitig. Root integriert, liest Befunde und nimmt ab. Kein zweiter Hauptchat, kein LLM-Polling für Renderjobs/Tests.
- Modell wählen, das im vorhandenen Setup tatsächlich funktioniert. Endnutzeragent zunächst bei der lokal erfolgreichen Sonnet-Medium-Konfiguration lassen. Kein allgemeines neues Modellbenchmarking.

Sichere lokale Implementierung, notwendige Benutzerkontext-Abhängigkeiten aus offiziellen Quellen, Tests, Commits und Push des **eigenen** Nachtbranches sind freigegeben. Kein Force-Push, `git clean`/hard reset fremder Arbeit, Merge nach Team-main, öffentliche Freigabe, neue bezahlte Dienste oder Administrator-/Firewall-/Zertifikatseingriffe ohne konkrete Zustimmung. Keine Berechtigungs-Bypasses. Behalte bestehende sichere Key-Startskripte.

## 3. Kleine Module, keine Variantenvervielfachung
Nutze die vorhandene Konfiguration/Featureflags; keine neue Pluginarchitektur. Drei dokumentierte Startprofile reichen:
- **Core:** geprüfte Suche/Routen, Text/Voice, manuelle Standortwahl; neue Extras abschaltbar.
- **Immersive:** Core + POV, Spielsteuerung, Routenvorschau, mobile Orientierung und Guidance.
- **Showcase:** Immersive + vorbereitete HQ-Gebäude + explizit aktiviertes Demo-Memory/Recap + Aufnahmeablauf.

Kameralokalisierung bleibt ein separat ausgeschaltetes Experiment. Jedes neue Modul hat eigene Tests, klare Grenzen und einen abschaltbaren Einstieg. Keine drei Kopien des Programms. Einzelne sinnvolle Commits erlauben gezielte Übernahme. Ein Ausfall von HQ, Voice, Sensoren, Memory oder Experimenten darf Core nicht blockieren.

## 4. Zuerst echte Zustands- und Sicherheitsfehler schließen
Der bereitgestellte Phase-B-Guide meldet ältere Codex-QA-Befunde bereits als behoben. **Nicht blind erneut implementieren:** Reproduktionen prüfen, nur verbleibende/regressierte Fehler bearbeiten.

Zusätzlich vom Nutzer gemeldet:
- Der Chat verdeckt große Teile der Karte; POV wurde nicht überzeugend visuell belegt.
- Ankunft/Bewegung aktualisiert den Standort für die nächste Anfrage nicht zuverlässig.
- Ein Rollstuhlnutzer bekam bei kaputtem Lift eine Treppe empfohlen.
- Fehlende Auswahl-/Blickrichtungsinformationen und lange technische Antworten erschweren die Bedienung.

Verbindliche Invarianten:
1. Ein autoritativer Gebäude-/Szenariozustand, sitzungsbezogene Position, Auswahl und Dialogkontext. `null`/Clear ist ein echtes Löschen, kein „ignoriere das Feld“.
2. Suchen, Fokussieren und Kameravorschau ändern **nicht** die Nutzerposition. Tatsächliche simulierte Bewegung und Ankunft aktualisieren sie. Inspizierter Raum ist nicht automatisch Aufenthaltsort.
3. Standortquellen kennzeichnen: manuell bestätigt, Simulation, QR-Anker, Hypothese, experimentelle Bildschätzung. Eine Landmarkenvermutung ist keine Messung.
4. Rollstuhl/step-free und gewählte erforderliche Breite bleiben über Folgefragen, Szenarioänderungen, Moduswechsel und Re-Routing bindend. Ohne passende Route: stehenbleiben und erklären, **keine Treppe als Ersatz empfehlen oder befahren**. Kein stilles Aufweichen von Sicherheitsabständen.
5. Veraltete Route, Vorschau, Audio und verspätete Antworten nach Standort-/Gebäude-/Ziel-/Revisionswechsel verwerfen.
6. Aktuelle Endpunkte erhalten: Tür/Koordinate/Assetzugang nicht heimlich auf Raummitte reduzieren. Offizielle Referenzantworten dabei nicht umdefinieren.
7. Räume ohne belegte Verbindung nicht nur wegen einer optisch offenen Stelle verbinden. Fehlende Topologie, „im gewählten Modell nicht erreichbar“ und Programmfehler unterscheiden.
8. Möbelverschiebung nur als transparente bestätigte Szenarioänderung; Maschinen nicht automatisch verschiebbar machen. Anzeige, Router und Hindernisprüfung müssen dieselbe neue Position nutzen. Keine stillen Abkürzungen durch Möbel.

## 5. Immersive Navigation mit tatsächlichem Kameraoutput
### Ablauf und Sichtbarkeit
Zielwunsch → Ziel im Gebäude hervorheben → Gesamtweg → optionale kurze Kameravorschau → `Start navigation` → POV oder Übersicht → Ankunft.

Die bestehende UI nicht grundsätzlich umgestalten. Für den Navigationsmodus genügen gezielte Verbesserungen: Chat standardmäßig kompakt/einklappbar, Mikrofon gut erreichbar, ein kleiner nächster Schritt und Mini-/Gesamtkarte. Kein automatisch voll aufgeklappter Chat nach jeder Antwort. HQ-/Debug-Regler bleiben im Developer-Bereich. Gemeinsam mit den Kollegen bearbeitete Layoutdateien nicht ohne Abgleich überschreiben; neue Kamera-/Control-Komponenten vorzugsweise isolieren.

### Drei getrennte Größen
`route geometry`, `user pose` und `camera pose` getrennt halten. Rendern/Etagenexplosion darf Weltkoordinaten und Kosten nicht verändern. Eine zentrale dokumentierte Transformation zwischen Gebäudedaten (Meter, XY, Etagenhöhe), Renderkoordinaten und Geräteausrichtung verwenden.

### Spielsteuerung
- Desktop: WASD/Pfeiltasten bewegen die Person; Maus/Drag verändert Blickrichtung. Pointer Lock nur nach aktiver Zustimmung, ESC gibt ihn frei. In Textfeldern keine Bewegungskeys abfangen.
- Mobil: kompakter Touch-Joystick oder Vorwärts-/Rückwärtssteuerung plus Drag-to-look; alltagstauglicher Fallback ohne Sensoren. Touchziele dürfen Karte/Mikrofon nicht verdecken.
- Modi `manual` und `autowalk` eindeutig wechseln; nicht gleichzeitig zwei Treiber auf dieselbe Position wirken lassen.
- Bewegung gegen die vorhandene freie Geometrie prüfen, einschließlich ganzer Bewegungsschritte: keine Wanddurchquerung bei hohem Tempo oder Bildratenabfall. Keine neue starre Physikengine nötig.
- Abseits der Route zulässige Bewegung ermöglichen; Richtungshinweis/Re-Routing aktualisieren, keine teleportierende Rücksetzung auf die Route. Nicht erreichbare Abweichung ehrlich erklären.
- Keine KI-Aufrufe pro Frame. Simulationsuhr kontrollierbar, Bewegung bildratenunabhängig. Vorschau, Pause, Resume, Recenter, Übersicht und Zielwechsel bleiben möglich.

### POV und Avatar
Echte virtuelle Augenhöhe-Kamera, freies Sichtfeld, nachvollziehbare Perspektive, nicht nur Top-down mit einem Punkt. Ein leichter stilisierter Avatar für Außenansicht ist willkommen; vorhandene/lizenzierte Assets oder einfache prozedurale Form bevorzugen. Avatar im POV nicht vor die Kamera setzen.

Ruhige Kamera, kein erzwungenes Head-Bob. Blickrichtung darf weich geglättet werden. Die begehbare Linie nur ändern, wenn Geometrievalidierung danach weiter besteht. Schematische Treppen-/Liftabschnitte deutlich markieren; keine präzise Staircase-Geometrie aus fehlenden Daten erfinden. Wo echte Geometrie vorhanden ist, bessere Treppen-/Liftmodelle passend zur Route ergänzen.

### Developer-Ansicht
Desktop optional Split View: links tatsächlicher Nutzer-/POV-Render, rechts Gesamtgebäude mit Person, Route, Etage und Blickrichtung. Mobil zwischen beiden wechseln statt zwei winzige Ansichten. Beide zeigen dieselbe Pose. Keine doppelte Simulation.

## 6. Handy drehen: Orientierung, nicht erfundene Positionsmessung
Featureflag `deviceOrientation` oder vorhandenes Äquivalent. Auf einem geeigneten Gerät verändert Drehung die POV-Blickrichtung und den relativen Richtungspfeil.

- Capability Detection, Permission-Anforderung nur durch Nutzerbutton, HTTPS/sicherer Kontext. Keine automatische Freigabe oder falsche Erfolgsanzeige.
- Anfangsheading anhand einer bekannten Blickrichtung kalibrieren; Recenter/Kalibrieren anbieten. Portrait/Landscape, Achsenkonventionen, 0/360-Übergang und Sensorzittern berücksichtigen.
- Kamerarichtung, Geh-/Körperrichtung und Navigationstangente unterscheiden. Ein nach rechts gedrehtes Handy verschiebt nicht automatisch die Person.
- Pfeil aus Ziel-/Routentangente relativ zur relevanten Nutzerblickrichtung deterministisch berechnen. Kein „links“ aus der Bildschirmposition der Gesamtkarte ableiten.
- Bei verweigerten/fehlenden Sensoren Touch/Mouse und sichtbare manuelle Orientierung. Kein harter Fehler.
- QR-/bekannter Türanker darf eine bestätigte Position setzen. Keine Behauptung, Gyroskop allein messe zuverlässig Wege in Metern oder kenne die Gebäudekoordinaten.
- Reales Smartphone ist `NOT_TESTED`, bis tatsächlich darauf getestet wurde. Playwright-Injektion von Orientation-Events belegt nur Softwareverarbeitung. Responsives Layout ist kein Nachweis echter Sensor- oder Mikrofonfunktion.
- Vorhandene lokale sichere Testumgebung nutzen. Keine öffentlichen Tunnels/Deployments, System-Zertifikatstruständerungen oder Firewallfreigaben eigenmächtig erstellen; nötige Geräteaktion kurz dokumentieren, nicht den restlichen Auftrag blockieren.

## 7. Räumliche Assistenz, Sprache und Lokalisierungsexperiment
### Assistenz
„What's on my right/behind me?“ aus gültiger Pose, Blickrichtung und echten Objekt-/Raumdaten beantworten. Räumliche Nähe nicht automatisch als Sichtbarkeit ausgeben: Wand-/Sichtprüfung oder entsprechend vorsichtige Formulierung. Bei unbekanntem Heading fragen statt raten.

Wegbeschreibungen aus geprüften Segmenten: nächste Tür, Links/Rechts, Lift/Treppe, Ankunft. Zuverlässige Orientierung voraussetzen. Rollstuhl-Hinweise dürfen nie den validierten Constraints widersprechen. Sonnet darf normal auf einen Scherz antworten; keine erzwungenen Tools bei jeder Äußerung. Standard kurz, bei sinnvoller Erklärung länger. Keine rohe Toollimit-/Traceausgabe im Nutzermodus, keine angebliche Gedankenanzeige; nur echte Bearbeitungsphasen.

„Washing machine is leaking; which room below is affected?“ muss die vorhandene `room_stacked`-Semantik verwenden: größtmögliche geometrische Überlappung auf der nächsten Etage, **keine behauptete Wasserschadenssimulation**. Dieselbe Werkzeuglogik für Text, Voice und manuelle Tests.

### ElevenLabs
Bestehende STT/TTS weiterverwenden; Mikrofon, sichtbares Transkript, kurze Antwort, Modus Stumm/Repeat und getrennte Voice-/Gehgeschwindigkeit. Vorhandenen funktionierenden Bereich (bisher 0,7–1,2×, Standard etwa 1,1–1,15×) nutzen und gegen aktuelles Modell prüfen. Keine neue API nur wegen größerer Speedwerte.

Ansagen priorisieren: Abzweigung/Übergang/Ankunft; keine Überlagerung. Alte Ansagen bei Replan abbrechen. Vor Mikrofonaufnahme Playback stoppen. Kleine Audio-Caches nutzen; lange vollständige Routenvorlesung nicht als Standard. Wartezeiten von Modell/Tool/Audio getrennt messen.

### Optionales Bild-Lokalisierungsexperiment
Nur nach funktionierendem Hauptpfad und innerhalb Teilbudget: aus **gerenderten** Bildern eine Position in einer kleinen bekannten Szene schätzen. Eine kleine Galerie aus gültigen Posepunkten und separate Testbilder (andere Blickwinkel/Punkte) verwenden. Query-Dateiname/EXIF/Metadaten dürfen Zielpose nicht verraten. Ground Truth nur für Auswertung, nicht an den Schätzer.

Zuerst einfache vorhandene Bildmerkmale/Matching oder vorhandene Vision-Funktion prüfen. Keine große Modellinstallation, kein SLAM-Neubau, kein Training. Fehler in Metern, richtige Etage, Ausrichtungsfehler und Ablehnungen berichten; ähnliche Korridore und Verdeckung als negative Tests. Unsichere Schätzung nicht automatisch in Navigation übernehmen. Als `experimental / simulated imagery only` kennzeichnen. Das ist kein Nachweis realer Smartphone-Kameraortung. Fehlt Nutzen oder Hardware: Experiment mit Befund stoppen, Hauptprodukt bleibt unverändert. Echte WebXR-/Kamera-AR nur vorhandene funktionsfähige Unterstützung nutzen, nicht als notwendiges Ziel versprechen.

## 8. Schöne Gebäude ohne laufenden KI-Verbrauch
Bestehende Pipeline/Renderer zuerst lesen. Zwei bis drei tatsächlich geeignete, **repräsentative** Train-/Dev-Gebäude für Showcase wählen; mindestens eins mit Etagenwechsel und eins mit echter Precision-Hindernisumfahrung. Keine Geometrie verbiegen, damit eine Demo künstlich klappt.

- Strukturiertes JSON → deterministische Geometrie, Materialien, Licht, Schatten, Typ-Assetbibliothek.
- Vorhandene GLB-/Modellassets wiederverwenden/instanzieren. Unbekannte Typen bekommen sinnvolle Platzhalter. Lizenzen/Attribution erhalten. Keine pro-Gebäude-LLM-Generierung erforderlich.
- Füße/Position/Rotation/Abmessungen müssen zur Navigation passen. Rein dekorative Modelle dürfen keine nicht vorhandenen Hindernisse oder Durchgänge vortäuschen.
- Kleine performante mobile Qualitätsstufe, optional hochwertigere Desktopdarstellung. Wiederholte Geometrie bündeln; keine neue 3D-Engine.
- Speicherung unter Gebäude-ID, beispielsweise vorhandenes `workspace/locations/<building_id>/3d/`. **Keine neue Hashlogik für Caching**, keine neuen Cache-Hash-Verträge.
- Vorhandenes Modell direkt laden; `Rebuild` ausdrücklich auslösen, auch wenn Eingabedateien unverändert sind und nur Material/Generator geändert wurde. Keinen Hash-Wächter einbauen, der explizites Rebuild ignoriert. Bestehende Forschungs-/Testintegritätsnachweise nicht global entfernen – der Verzicht betrifft Modell-Caching.
- Modellaufbau beim normalen Öffnen nicht automatisch starten. Sofort einfache nutzbare Geometrie anbieten.
- Assetbewegungen dürfen HQ und Router nicht auseinanderlaufen lassen. Vorzugsweise dynamische Objekte getrennt rendern. Bei gebackenen untrennbaren Objekten HQ als veraltet markieren und die korrekte LQ-/dynamische Ansicht verwenden, bis bewusst neu erzeugt wurde. Keine falsche Kollisionsdarstellung und keine doppelt gezeichneten Möbel.
- Kein Massengenerieren aller 280 Gebäude. Große Modelle außerhalb Git speichern, aber Build-/Ladeanleitung mitliefern.

## 9. Kleines lokales Memory und Recap
Ein schlankes Modul, kein Benutzerkonto-/Cloud-Analytics-Projekt. Vorhandene Sitzung/Persistenz wiederverwenden oder einfache lokale JSONL/SQLite-Ablage wählen.

Trenne aktuellen Standort/Dialogkontext von freiwillig dauerhaft gemerkter History. Bei echter Nutzung persistente Chat-/Aktivitätshistorie sichtbar aktivieren; Demo darf mit klar gekennzeichneten synthetischen Sessions laufen. Private Roh-Audios, Kameravideos und vollständige Transkripte nicht standardmäßig speichern. Memory löschen/resetten, Export und „neue Sitzung“ anbieten; keine Wiederbelebung eines gelöschten Standorts aus alten Memories.

Nützliche Events: bestätigtes Ziel, Suchintention, tatsächlicher Navigationsstart, Pause, reale simulierte Bewegung, Reroute, Arrival, bestätigte Einstellung. Nur nötige Daten; sitzungsbezogen/pseudonym, keine übergreifende Nutzerverfolgung. Spracheingaben sind Inhalte, keine Anweisungen zur Ausweitung von Agentenrechten.

Recap deterministisch aus Events: tatsächlich erreichte Orte, tatsächlich zurückgelegte XY-Strecke, Etagen, Lieblingsziel dieser Session, Zeit im Simulationsmodus. Planung/Preview nicht als Gehen zählen. Official-Kosten mit Lift-Warteanteil sind **keine echte gelaufene Meterzahl**. Tests nicht in echte Benutzerhistorie mischen.

Erzeuge eine kleine ansprechende englische Recap-Karte, optional ein kurzes Highlight-Reel aus tatsächlichen Szenen. Kein Training und kein Vektorstore notwendig. LLM höchstens für einen kurzen Stiltext aus belegten Zahlen; Zahlen niemals erfinden. Inspirationsidee ist Jahres-/Sessionrückblick, keine Spotify-Marken-/Designkopie.

## 10. Breite, aber begrenzte Tests mit echter visueller Abnahme
Vor Produktänderungen aktuellen Baseline-Commit und die bestehenden kritischen Repros sichern. Test-/Checkeränderungen nicht nutzen, um Fehler nur grün zu machen. Wenn neue zulässige Portal-/Bewegungsfälle den unabhängigen Checker verändern müssen: separat begründen und absichtlich ungültige Wege als Negativkontrolle weiter erkennen.

Testpyramide:
1. Vorhandene offizielle 655/655 strukturierten Benchmarkfälle, 1338/1338 Warm-up-Routen, 553 Locate, 239 Count. Einmal Baseline beziehungsweise bestehende passend belegte Baseline und einmal final; nicht pro kleiner UI-Änderung den gesamten Bestand neu rechnen. Dazwischen betroffene Regressionen.
2. Reproduzierbare Navigation/Spielsteuerung auf 100–200 gezielt kombinierten Fällen, echte Gebäudefamilien, feste Seeds. Garantiert erreichbare Fälle von absichtlich blockierten/unsupported Fällen trennen. Gültigkeit ausgegebener Wege unabhängig prüfen.
3. 40–60 definierte Dialog-/State-/Tool-Szenarien überwiegend offline. Ein konstantes Mockmodell ohne Tools ist **kein NLU-Test**. Danach 12–20 repräsentative echte Claude-Dialoge innerhalb Kostenbudget; mehrere davon vorher nicht zur Korrektur verwendet.
4. Echte Browser- und Pixelprüfung auf Desktop, etwa 1000×860 (Nutzerscreenshot), Smartphone 390×844 und Landscape. Nutze die Matrix dieses Pakets. Öffne und **betrachte** tatsächliche Screenshots, einschließlich POV, Bewegung, Kartensicht mit Antwort, Touchsteuerung und Gerätdrehung. Grün im DOM ist keine visuelle Abnahme.

Mindestens drei zusammenhängende Szenen vollständig visuell kontrollieren: Kaffee/Route/Ankunft; Rollstuhl + Lift gesperrt; manuelle Bewegung/„what's on my right?“. Screenshots/Clips an sinnvollen Zuständen, nicht hunderte ungesichtete Bilder. Keine erfundenen Nutzerscreens. Wenn der Render schwarz/leer/verdeckt ist: FAIL, nicht screenshot-file-exists=PASS.

Physisches Smartphone, Mikrofon und Gerätesensoren getrennt als `VERIFIED_ON_DEVICE`, `SIMULATED` oder `NOT_TESTED` kennzeichnen. Softwareevents können unabhängig getestet werden. Bei fehlendem Gerät klare Testanweisung, kein Block des restlichen Projekts.

Performance getrennt messen: Renderframetime, kalter/warmer Gebäudeladevorgang, Suche, Route, Claude, erster hörbarer Ton. Ziele 30 FPS im gemessenen mobilen Testprofil / 60 Desktop sind Optimierungsziele, keine ungemessenen Versprechen. Browseremulation ist kein echter Telefon-GPU-Test. Diagnose und maximal wenige gezielte Verbesserungen statt neuer Performancearchitektur.

Fehler nach Ursache gruppieren. P0/State-/Constraintverletzungen zuerst, danach zentrale Interaktion, dann Gestaltung. Bei wiederholt erfolgloser optionaler Funktion ehrlich deaktivieren; kein endloses Probieren. Ein begrenzter getrennter Read-only-Audit zentraler Änderungen ist erlaubt, kein Review-of-review-Karussell.

## 11. Echte Demo und Abgabeunterlagen herstellen
`submission_drafts/` in diesem Paket enthält Rohfassungen. Zuerst ins Projekt übernehmen und mit Ist-Stand abgleichen, später finalisieren. **Nicht blind gewünschte Funktionen als fertig beschreiben.**

Pflichtgrundlage aus dem bereitgestellten Participant Guide: ein einminütiges Demovideo, ein einminütiges Technikvideo, Architektur-/Integrationsnotizen und Pitchfolien für einen möglichen 3-Minuten-Pitch. Veröffentlichung/Upload bleiben menschliche Aktion. Aktuelle Portalvorgaben haben Vorrang; ohne Zugriff lokale Entwürfe mit bekannten Vorgaben, keine erfundene Einreichung.

Medienplan:
- Früh eine funktionierende Rohaufnahme sichern.
- 60-s-Demofilm: Nutzerwunsch → echter Raumfokus → echte Route/kurze Vorschau → Start → POV/manual/mobile Orientierung → Ankunft; Recap nur wenn funktioniert. Simulierte Navigation sichtbar kennzeichnen.
- 60-s-Technikfilm: strukturierte Daten, gemeinsamer State, deterministische Tools/Router, Claude/ElevenLabs, maßgebliche Testbelege, klare Grenzen.
- 4–5 editierbare Pitchfolien + kurze englische Sprechnotizen; vorhandene PptxGenJS/Marp/andere funktionierende Werkzeugkette nutzen. Export PDF nach Möglichkeit, aber keinen zweiten Medienframeworkbau erzwingen.
- Technische Notizen mit tatsächlichen Komponenten, Modulen/Flags, Datenvertrag, Modell-/API-Nutzung, Tests und nicht unterstützten Fällen.

Vorhandenen Browser/Renderer zur Aufnahme verwenden; Playwright-Video beziehungsweise echte Frames + FFmpeg bevorzugen. Explizit Auflösung/Framerate setzen, tatsächlichen Ton separat sauber erfassen/mischen – nicht annehmen, dass Browservideo automatisch Audio enthält. Kein Remotion-Zwang und keine generative Szene als Funktionsnachweis. Vorhandene gestalterische Tools nur einsetzen, wenn unmittelbar nützlich.

Prüfen: Dateien tatsächlich abspielbar, 60-s-Ziele anhand Mediendaten, Bild/Ton synchron, verständlicher englischer Ton, lesbares Bild, kein Secret/Debugmüll. Finale Videos ganz ansehen/anhören, nicht nur Metadaten prüfen. Wenn Renderexport scheitert: echte verwendbare Rohclips, Audio, Schnittplan und klaren Fehler liefern; keinen fertigen Film behaupten. Recap-Video optional zusätzlich, nicht statt Pflichtvideos.

Keine unbekannten Nutzerdaten, unfreigegebenen Personenstimmen oder geschützte Musik verwenden. Nur eigene/freigegebene Assets. Kein Upload ins Veranstalterportal, YouTube oder öffentliches Hosting.

## 12. Abschlussübergabe für einen neuen Webchat – Pflicht
Dieser lange Webchat endet. Alles Relevante muss danach aus dem Repository und dem erzeugten Übergabepaket verständlich sein.

Erstelle bei Abschluss **oder vor einem Budget-/externen Stopp**:
- `docs/night/NIGHT_RESULT.md`: tatsächliche Änderungen, Branch/Commit, verifizierte Ergebnisse, Kostenquelle, Restfehler, unterstützte Varianten, Empfehlungen.
- `docs/night/START_NEXT_WEBCHAT_DE.md`: eigenständiger Startprompt für den nächsten Webchat. Rolle: Review/Entscheidungen, Claude bleibt Implementierungsleitung. Nur die notwendigen Dateien zuerst lesen; keine neue Analyse-/Agentenarchitektur.
- Kleine `CAPABILITIES.md` oder vorhandene entsprechende Tabelle: Core / Immersive / Showcase / Experiment; je Funktion implemented/tested/unsupported/off und tatsächlicher Start-/Flagweg.
- Kurze aktuelle englische Bedienhilfe mit 5–8 echten Screenshots/annotierten Schritten, keine lange zweite Benutzerbibliothek.
- Testzusammenfassung und reproduzierbare relevante Fehler; ausführliche JSON-/CSV-/Logbelege separat.
- Tatsächliche Abgabeentwürfe/Videos/Folien und deren Reifestand.

Ein einziges neues ZIP `ARINAV_NEXT_WEBCHAT_<local_timestamp>.zip` mit `00_START_HIER.md`, Startprompt, Ergebnis, aktueller Workplan-Spitze, Architektur-/Schnittstellenhinweisen, Modulübersicht, ausgewählten Screenshots und kleinen Testbelegen. Notwendige kuratierte Quellmodule nur bei offenen konkreten Fehlern. Keine ganzen Datenkits, venvs, BA-Unterlagen, Secrets, persönliche Gesprächsarchive oder rohe Nutzeraufnahmen. Große Videos/HQ-GLBs neben dem ZIP belassen und im Index genau aufführen; ZIP klein genug für einen Webchat halten. Dateienliste/Größen und erfolgreiches Wiederöffnen prüfen; keine neue Manifest-/Hashbürokratie.

Aktuellen Context, Entscheidungen, Arbeitsregeln und Claim-Grenzen erhalten – nicht bloß „alles funktioniert“. Es muss erkennbar sein, was vorher schon da war, was diese Runde ergänzt hat und welche Behauptungen tatsächlich belegt sind. Wiederanlaufbefehl und Pfade dürfen nicht vom Kenntnisstand dieses Chats abhängen.

## 13. Arbeitsfolge und Ausgabe an Noah
1. Zustand/Budget sichern, Kontext im Repo verankern, kleine Medien-/Featureentwürfe anlegen.
2. Harte Fehler/Constraints und POV-Manual/Mobile-Hauptpfad funktionsfähig machen.
3. Echte Screenshots prüfen, räumliche Guidance, Sensoradapter und Zustandsfortschreibung absichern.
4. Zwei bis drei gute Szenen, einfacher Cache und kleiner Recap; Experimente nur mit Restbudget.
5. Gesamttests, echte Aufnahmen und Abgabeentwürfe finalisieren.
6. Sicheren eigenen Branch/Commit, Modulstatus und neue Webchat-Übergabe liefern.

Root + höchstens zwei getrennte Writer, keine konkurrierende Dateiarbeit. Nach jedem sinnvollen Meilenstein startbar bleiben. Bei spontanen Nachrichten Absicht filtern, kein Planneustart wegen Wiederholungen. `WORKPLAN.md` ereignisgesteuert aktualisieren; kein permanentes LLM-Polling.

An Noah auf Deutsch knapp, aber nicht künstlich auf zwei Sätze begrenzt: Ergebnis/Empfehlung → Nachweis → echter Blocker → nächster Schritt. Produkt, Untertitel, Sprechertexte und Folien auf Englisch. Technische Vollberichte in Dateien. Keine normale Zwischenfreigabe pro Modul; offene riskante Außenaktionen nicht durchführen, unabhängig mögliche Arbeit fortsetzen.

Am Ende im Chat: **Welches Profil soll das Team verwenden? Was funktioniert nachweislich? Was ist nur Simulation/Experiment? Wo sind App, Videos, Folien, Bericht und neues Webchat-ZIP? Welche eine Entscheidung braucht Noah?** Danach keine neue Ausbaurunde.
