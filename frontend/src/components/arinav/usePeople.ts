// The people in the open building, and this person's presence there.
//
// People are global, but each is only in one building: the one where they are currently active.
// - Opening a building while logged in makes it your location (you leave the previous one). A
//   place you already had there is taken over; a place this tab remembers for it is shared.
// - Receives the building's people live (WebSocket /api/live; polling while it is down) and makes
//   map markers of everyone with a place, you included ("You").
// - While you walk a route, your position is published a few times a second.
// (Setting or clearing your place publishes it: see `setSimulated` in useNavigation.)
import { useEffect, useMemo, useRef, useState } from 'react';
import { api, type UserPresence } from '../../lib/api';
import type { SimulatedLocation } from '../../lib/nav';
import type { Guidance } from './useGuidance';
import type { Navigation } from './useNavigation';
import type { Person } from './usePerson';
import type { PersonMarker } from './scene';

/** Polling only while the live connection is down. */
const POLL_MS = 3000;
/** At most this often while walking (the others' figures glide in between). */
const WALK_PUBLISH_MS = 250;
const RECONNECT_MS = [1000, 2000, 5000];

function liveUrl(buildingId: string) {
  const base = import.meta.env.PUBLIC_API_BASE_URL || window.location.origin;
  const url = new URL(`/api/live?building_id=${encodeURIComponent(buildingId)}`, base);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  return url.toString();
}

export interface People {
  /** Everyone whose current location is this building, you first. */
  here: UserPresence[];
  /** Everyone with a place on the map, as markers. */
  markers: PersonMarker[];
}

const sameEndpoint = (a: SimulatedLocation | null, b: UserPresence['position']) =>
  !!a && !!b && JSON.stringify(a.endpoint) === JSON.stringify(b.endpoint);

export function usePeople(
  buildingId: string | null,
  person: Person,
  nav: Navigation,
  guidance: Guidance,
): People {
  const [all, setAll] = useState<UserPresence[]>([]);
  const [nonce, setNonce] = useState(0);
  const personId = person.profile?.id ?? null;
  const active = person.status === 'active';

  // Live: the server pushes the building's people on every change; reconnects with a backoff.
  useEffect(() => {
    setAll([]);
    if (!buildingId) return;
    let alive = true;
    let socket: WebSocket | null = null;
    let retry = 0;
    let retryTimer = 0;
    const load = () =>
      api
        .buildingUsers(buildingId)
        .then((r) => alive && setAll(r.users))
        .catch(() => undefined);
    const connect = () => {
      if (!alive) return;
      socket = new WebSocket(liveUrl(buildingId));
      socket.onopen = () => {
        retry = 0;
      };
      socket.onmessage = (event) => {
        const message = JSON.parse(String(event.data)) as { type: string; users?: UserPresence[] };
        if (alive && message.type === 'users' && message.users) setAll(message.users);
      };
      socket.onclose = () => {
        if (!alive) return;
        retryTimer = window.setTimeout(
          connect,
          RECONNECT_MS[Math.min(retry++, RECONNECT_MS.length - 1)],
        );
      };
    };
    void load();
    connect();
    const poll = window.setInterval(() => {
      if (socket?.readyState !== WebSocket.OPEN) void load();
    }, POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(poll);
      window.clearTimeout(retryTimer);
      socket?.close();
    };
  }, [buildingId, nonce]);

  // Walking a route: publish where you are (the stop/arrival place is set by setSimulated).
  const walking = guidance.navigating && guidance.snapshot.status === 'active';
  const progress = guidance.snapshot.progress;
  const lastWalk = useRef({ at: 0, busy: false });
  useEffect(() => {
    if (!walking || !active || !buildingId || !progress?.space_id) return;
    const now = performance.now();
    const last = lastWalk.current;
    if (last.busy || now - last.at < WALK_PUBLISH_MS) return;
    last.at = now;
    last.busy = true;
    api
      .setPosition({
        building_id: buildingId,
        endpoint: { kind: 'point', space_id: progress.space_id, position: progress.position },
        heading: progress.heading_deg,
        source: 'simulation',
        moving: true,
      })
      .catch(() => undefined) // e.g. mid-doorway outside both rooms: the next update follows
      .finally(() => {
        last.busy = false;
      });
  }, [walking, active, buildingId, progress]);

  // Enter the building: it becomes this person's location.
  const simulated = useRef(nav.simulated);
  simulated.current = nav.simulated;
  const { setSimulated } = nav;
  useEffect(() => {
    if (!personId || !buildingId || !active) return;
    let alive = true;
    api
      .setPosition({ building_id: buildingId })
      .then((presence) => {
        // Read after the await: by now the tab's remembered place for this building is loaded.
        const local = simulated.current;
        if (!alive) return;
        const known = presence.available ? presence.position : null;
        if (local && !sameEndpoint(local, known)) {
          setSimulated(local); // this tab's place wins, and is shared
        } else if (!local && known) {
          setSimulated(
            { endpoint: known.endpoint, label: known.label ?? 'Your place', source: known.source },
            { publish: false },
          );
        }
        setNonce((n) => n + 1);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [personId, buildingId, active, setSimulated]);

  const here = useMemo(
    () =>
      [...all].sort(
        (a, b) => Number(b.profile.id === personId) - Number(a.profile.id === personId),
      ),
    [all, personId],
  );

  const markers = useMemo(
    () =>
      here.flatMap((u): PersonMarker[] => {
        const at = u.position;
        if (!at || !u.available) return [];
        const self = u.profile.id === personId;
        return [
          {
            id: u.profile.id,
            name: self ? 'You' : u.profile.name,
            initials: u.profile.initials,
            color: u.profile.color,
            floorId: at.floor_id,
            position: at.position,
            headingDeg: at.heading,
            online: self || u.status === 'online',
            self,
          },
        ];
      }),
    [here, personId],
  );

  return { here, markers };
}
