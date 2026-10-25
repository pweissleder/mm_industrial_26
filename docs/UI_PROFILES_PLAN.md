# UI profiles + HQ suggestions + asset culling — implementation plan

## Profiles (replaces the Assistant/Developer toggle)
`AppMode` in `frontend/src/lib/nav.ts` becomes `'navigator' | 'user' | 'developer'`.
One 3-way segmented toggle in the top bar (same place as before): Navigator · User · Developer.
Stored under `arinav:mode`; a stored legacy `'assistant'` maps to `'user'`. Default `'user'`.

| Feature | Navigator | User | Developer |
|---|---|---|---|
| Upload button | hidden | shown | shown |
| Sidebar tabs | Location, View | Location, View | Location, View, Developer |
| "Current location" (simulated) section on top of Location tab | – | shown | – |
| Details card: Activate/Deactivate (was Close/Reopen) | – | shown | shown |
| Details card: Route section "From here / To here" | – | shown | shown |
| Details card for rooms | not shown | auto-hides after 5 s (selection stays) | shown |
| Chat "Claude · live" status | – | – | shown |
| Chat "Set simulated location" chip | – | – (moved to sidebar) | shown |
| Chat "Speak replies" toggle | – | – (voice input ⇒ spoken reply; otherwise speaker button on each reply) | shown |
| Chat replies spoken | always | if asked by voice / per-reply button | as before |
| Chat conversation panel | hidden (bar only) | as before | as before |

All modes: Details card loses "Ask AriNav" and "Show floor".
Route section: dev → set start / target endpoint; user → From here = set simulated location, To here = chat "How do I get to X?".

## Layout (all modes)
- Building picker: custom list-style dropdown (list__row look) instead of native `<select>`.
- Floor rail shown at every width; floor section removed from the sidebar. Selected floor = blue text only (no orange fill).
- Camera tiles removed from the View tab; "Front" view added to the right HUD.
- Sidebar hide (close) button same size as other icon buttons.
- ≤1200px and mobile: logo hidden, mode toggle and upload become icon-only.
- Developer tab sections collapsible (same `CollapsibleSection` as View).
- Selected node attached to the chat as a visible, removable context chip (sent as `ui.inspected`).

## HQ pipeline
New `suggest` stage in the metadata pillar after `summarize`: 3 short chat prompts that fit the location,
stored as `metadata.suggestions`. Template fallback offline. The chat shows them instead of the static list.

## Rendering
Buildings with many assets (> MAX_VISIBLE_ASSETS): assets hidden unless their room or the asset itself is
selected, or the focused floor brings the count under the cap. Precomputed per-room/per-floor indexes, delta updates.

## Work split (parallel)
A backend suggestions · B scene.ts culling · C app shell (AriNavApp, Sidebar, DetailsCard, DevPanel, Icons, global.css) · D ChatDock.
