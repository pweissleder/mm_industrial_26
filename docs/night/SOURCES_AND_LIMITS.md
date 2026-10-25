# Gezielte Primärquellen für die Ausführung
Online gezielt geprüft bei Erstellung des Pakets am25.10.2026. Der aktuelle lokale Code und konkrete Browser/SDK-Versionen bleiben zu prüfen. Keine allgemeine Recherchepflicht für jeden Unterpunkt.

1. MDN DeviceOrientationEvent und Permissions:
https://developer.mozilla.org/en-US/docs/Web/API/DeviceOrientationEvent
https://developer.mozilla.org/en-US/docs/Web/API/DeviceOrientationEvent/requestPermission_static
Orientierungsevents liefern Rotation, nicht automatisch eine Gebäudeposition. Permission je Browser, sicherer Kontext und aktive Nutzeraktion beachten.

2. Koordinatenkonventionen:
https://developer.mozilla.org/en-US/docs/Web/API/Device_orientation_events/Using_device_orientation_with_3D_transforms
Sensor-, CSS- und Renderkoordinaten nicht blind gleichsetzen. Rotationsreihenfolge und Bildschirmorientierung relevant.

3. WebXR:
https://developer.mozilla.org/en-US/docs/Web/API/WebXR_Device_API
https://developer.mozilla.org/en-US/docs/Web/API/WebXR_Device_API/Startup_and_shutdown
Hardware/Browserunterstützung eingeschränkt, sicherer Kontext nötig. Reale AR keine Voraussetzung für simulierte POV.

4. Testzeit und Aufnahme:
https://playwright.dev/docs/clock
https://playwright.dev/docs/videos
Clock kontrolliert unter anderem requestAnimationFrame. Videos werden nach Browsercontext-Abschluss finalisiert. Explizite Aufnahmegröße verwenden; Ton separat verifizieren.

5. Three.js InstancedMesh (nur bei bestehendem Three.js-Renderer):
https://threejs.org/docs/pages/InstancedMesh.html
Wiederholte Geometrie/Materialien mehrfach transformiert darstellen statt jedes Asset unabhängig teuer aufzubauen.

6. ElevenLabs Voice Speed:
https://elevenlabs.io/docs/overview/capabilities/text-to-speech/best-practices
https://elevenlabs.io/docs/eleven-agents/customization/voice/speed-control
Bisherige Produktdokumentation nennt0,7–1,2. Andere neuere Modell-/REST-Unterlagen können abweichen; tatsächliche bestehende API prüfen, kein unbedingter Modellwechsel.

7. Claude Code Kosten:
https://code.claude.com/docs/en/costs
https://code.claude.com/docs/en/agent-sdk/cost-tracking
Entwicklungsagenten- und Produktaufrufe getrennt messen, aber gemeinsam budgetieren. Workspace-Limits/Console maßgeblich, Tokenkostenschätzung kenntlich machen. Kein Prompt ist für sich ein garantierter Dollar-Hard-Cap.

## Mitgelieferte Projektquellen
- reference/PHASE_B_GUIDE.md: Originalguide des zuletzt gelieferten Phase-B-Prototyps.
- reference/QA_REPORT_PHASE_A.md: Original-Codex-QA zu ee2c219, vor B0-Fixes; Regressionen, nicht automatisch aktueller Defektstatus.
- reference/UI_OCCLUSION.png: Originalnutzerscreenshot der überladenen Navigationsansicht.

Die vollständigen langen alten Planungen und privaten BA-Transferarchive sind bewusst nicht nochmals beigepackt. Relevante Entscheidungen sind in02 konsolidiert; aktuelle Implementierung und Belege liegen lokal im Teamrepo.
